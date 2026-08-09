"""Three-tier memory contracts with immediate logical invalidation."""

from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

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
