"""Three-tier memory contracts with immediate logical invalidation."""

from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
from typing import Dict, List, Optional, Tuple
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.models.database import (
    AgentMemory,
    AgentMemoryEpoch,
    AgentMemoryPurgeJob,
)
from app.services.agent_runtime.contracts import Sensitivity


class MemoryTier(str, Enum):
    WORKING = "working"
    SESSION = "session"
    LONG_TERM = "long_term"


class MemoryScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    org_id: UUID
    user_id: Optional[int] = None
    session_id: Optional[UUID] = None

    def key(self) -> Tuple[str, Optional[int], Optional[str]]:
        return (
            str(self.org_id),
            self.user_id,
            str(self.session_id) if self.session_id else None,
        )


class MemoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID = Field(default_factory=uuid4)
    scope: MemoryScope
    tier: MemoryTier
    kind: str
    content: Dict[str, object]
    source_type: str
    source_ref: str
    sensitivity: Sensitivity
    version: int = Field(default=1, ge=1)
    status: str = "active"
    correction_of: Optional[UUID] = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class MemoryPurgeJob(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID = Field(default_factory=uuid4)
    memory_id: UUID
    tombstone_epoch: int
    targets: List[str]
    status: str = "pending"
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class MemoryEpochChanged(RuntimeError):
    pass


class MemoryAdmissionDenied(RuntimeError):
    pass


class MemoryAdmissionPolicy:
    _TRUSTED_LONG_TERM_SOURCES = {
        "authoritative_connector",
        "approved_document",
        "user_explicit",
    }

    def allow_long_term(
        self,
        *,
        source_type: str,
        sensitivity: Sensitivity,
        approved: bool,
    ) -> bool:
        if sensitivity == Sensitivity.RESTRICTED:
            return False
        if source_type not in self._TRUSTED_LONG_TERM_SOURCES:
            return False
        if source_type in {"authoritative_connector", "approved_document"}:
            return approved
        return approved


class MemoryStore:
    """Reference store implementing invariants before DB/Redis adapters."""

    PURGE_TARGETS = ["vector", "bm25", "cache", "summary"]

    def __init__(self) -> None:
        self._items: Dict[UUID, MemoryItem] = {}
        self._epochs: Dict[Tuple[str, Optional[int], Optional[str]], int] = {}

    def epoch(self, scope: MemoryScope) -> int:
        return self._epochs.get(scope.key(), 0)

    def assert_epoch(self, scope: MemoryScope, expected: int) -> None:
        if self.epoch(scope) != expected:
            raise MemoryEpochChanged("Memory changed after context assembly")

    def write(
        self,
        *,
        scope: MemoryScope,
        tier: MemoryTier,
        kind: str,
        content: Dict[str, object],
        source_type: str,
        source_ref: str,
        sensitivity: Sensitivity,
    ) -> MemoryItem:
        item = MemoryItem(
            scope=scope,
            tier=tier,
            kind=kind,
            content=content,
            source_type=source_type,
            source_ref=source_ref,
            sensitivity=sensitivity,
        )
        self._items[item.id] = item
        self._increment_epoch(scope)
        return item

    def read(self, *, scope: MemoryScope) -> List[MemoryItem]:
        return sorted(
            [
                item
                for item in self._items.values()
                if item.scope.key() == scope.key() and item.status == "active"
            ],
            key=lambda item: item.created_at,
        )

    def correct(
        self,
        memory_id: UUID,
        *,
        scope: MemoryScope,
        content: Dict[str, object],
        source_ref: str,
    ) -> MemoryItem:
        original = self._owned_active(memory_id, scope)
        self._items[memory_id] = original.model_copy(update={"status": "superseded"})
        corrected = MemoryItem(
            scope=scope,
            tier=original.tier,
            kind=original.kind,
            content=content,
            source_type="user_correction",
            source_ref=source_ref,
            sensitivity=original.sensitivity,
            version=original.version + 1,
            correction_of=original.id,
        )
        self._items[corrected.id] = corrected
        self._increment_epoch(scope)
        return corrected

    def delete(self, memory_id: UUID, *, scope: MemoryScope) -> MemoryPurgeJob:
        item = self._owned_active(memory_id, scope)
        self._items[memory_id] = item.model_copy(update={"status": "tombstoned"})
        epoch = self._increment_epoch(scope)
        return MemoryPurgeJob(
            memory_id=memory_id,
            tombstone_epoch=epoch,
            targets=list(self.PURGE_TARGETS),
        )

    def _owned_active(self, memory_id: UUID, scope: MemoryScope) -> MemoryItem:
        item = self._items.get(memory_id)
        if item is None or item.scope.key() != scope.key() or item.status != "active":
            raise LookupError("Active memory item not found in this scope")
        return item

    def _increment_epoch(self, scope: MemoryScope) -> int:
        key = scope.key()
        self._epochs[key] = self._epochs.get(key, 0) + 1
        return self._epochs[key]


class PersistentMemoryStore:
    """SQL-backed memory truth source with durable invalidation and purge work."""

    PURGE_TARGETS = MemoryStore.PURGE_TARGETS

    def __init__(
        self,
        session: Session,
        admission_policy: Optional[MemoryAdmissionPolicy] = None,
    ) -> None:
        self._db = session
        self._admission = admission_policy or MemoryAdmissionPolicy()

    def epoch(self, scope: MemoryScope) -> int:
        row = self._db.get(AgentMemoryEpoch, self._scope_key(scope))
        return row.epoch if row is not None else 0

    def assert_epoch(self, scope: MemoryScope, expected: int) -> None:
        if self.epoch(scope) != expected:
            raise MemoryEpochChanged("Memory changed after context assembly")

    def write(
        self,
        *,
        scope: MemoryScope,
        tier: MemoryTier,
        kind: str,
        content: Dict[str, object],
        source_type: str,
        source_ref: str,
        sensitivity: Sensitivity,
        approved: bool = False,
    ) -> MemoryItem:
        if tier == MemoryTier.LONG_TERM and not self._admission.allow_long_term(
            source_type=source_type,
            sensitivity=sensitivity,
            approved=approved,
        ):
            raise MemoryAdmissionDenied("Long-term memory admission was denied")

        row = AgentMemory(
            scope_key=self._scope_key(scope),
            org_id=scope.org_id,
            user_id=scope.user_id,
            session_id=scope.session_id,
            tier=tier.value,
            kind=kind,
            content=content,
            source_type=source_type,
            source_ref=source_ref,
            sensitivity=sensitivity.value,
            version=1,
            status="active",
        )
        try:
            self._db.add(row)
            self._increment_epoch(scope)
            self._db.commit()
            self._db.refresh(row)
        except Exception:
            self._db.rollback()
            raise
        return self._item(row)

    def read(self, *, scope: MemoryScope) -> List[MemoryItem]:
        rows = (
            self._db.query(AgentMemory)
            .filter(
                AgentMemory.scope_key == self._scope_key(scope),
                AgentMemory.org_id == scope.org_id,
                AgentMemory.status == "active",
            )
            .order_by(AgentMemory.created_at, AgentMemory.id)
            .all()
        )
        return [self._item(row) for row in rows]

    def correct(
        self,
        memory_id: UUID,
        *,
        scope: MemoryScope,
        content: Dict[str, object],
        source_ref: str,
    ) -> MemoryItem:
        original = self._owned_active(memory_id, scope)
        original.status = "superseded"
        corrected = AgentMemory(
            scope_key=original.scope_key,
            org_id=original.org_id,
            user_id=original.user_id,
            session_id=original.session_id,
            tier=original.tier,
            kind=original.kind,
            content=content,
            source_type="user_correction",
            source_ref=source_ref,
            sensitivity=original.sensitivity,
            version=original.version + 1,
            status="active",
            correction_of=original.id,
        )
        try:
            self._db.add(corrected)
            self._increment_epoch(scope)
            self._db.commit()
            self._db.refresh(corrected)
        except Exception:
            self._db.rollback()
            raise
        return self._item(corrected)

    def delete(self, memory_id: UUID, *, scope: MemoryScope) -> MemoryPurgeJob:
        item = self._owned_active(memory_id, scope)
        item.status = "tombstoned"
        try:
            epoch = self._increment_epoch(scope)
            row = AgentMemoryPurgeJob(
                memory_id=item.id,
                tombstone_epoch=epoch,
                targets=list(self.PURGE_TARGETS),
                status="pending",
            )
            self._db.add(row)
            self._db.commit()
            self._db.refresh(row)
        except Exception:
            self._db.rollback()
            raise
        return self._purge_job(row)

    def _owned_active(self, memory_id: UUID, scope: MemoryScope) -> AgentMemory:
        row = (
            self._db.query(AgentMemory)
            .filter(
                AgentMemory.id == memory_id,
                AgentMemory.scope_key == self._scope_key(scope),
                AgentMemory.org_id == scope.org_id,
                AgentMemory.status == "active",
            )
            .with_for_update()
            .one_or_none()
        )
        if row is None:
            raise LookupError("Active memory item not found in this scope")
        return row

    def _increment_epoch(self, scope: MemoryScope) -> int:
        key = self._scope_key(scope)
        row = (
            self._db.query(AgentMemoryEpoch)
            .filter(AgentMemoryEpoch.scope_key == key)
            .with_for_update()
            .one_or_none()
        )
        if row is None:
            row = AgentMemoryEpoch(
                scope_key=key,
                org_id=scope.org_id,
                user_id=scope.user_id,
                session_id=scope.session_id,
                epoch=1,
            )
            self._db.add(row)
        else:
            row.epoch += 1
        self._db.flush()
        return row.epoch

    @staticmethod
    def _scope_key(scope: MemoryScope) -> str:
        canonical = json.dumps(
            scope.key(),
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _item(row: AgentMemory) -> MemoryItem:
        return MemoryItem(
            id=row.id,
            scope=MemoryScope(
                org_id=row.org_id,
                user_id=row.user_id,
                session_id=row.session_id,
            ),
            tier=MemoryTier(row.tier),
            kind=row.kind,
            content=dict(row.content or {}),
            source_type=row.source_type,
            source_ref=row.source_ref,
            sensitivity=Sensitivity(row.sensitivity),
            version=row.version,
            status=row.status,
            correction_of=row.correction_of,
            created_at=PersistentMemoryStore._utc(row.created_at),
        )

    @staticmethod
    def _purge_job(row: AgentMemoryPurgeJob) -> MemoryPurgeJob:
        return MemoryPurgeJob(
            id=row.id,
            memory_id=row.memory_id,
            tombstone_epoch=row.tombstone_epoch,
            targets=list(row.targets or []),
            status=row.status,
            created_at=PersistentMemoryStore._utc(row.created_at),
        )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
