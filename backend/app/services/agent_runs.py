"""Durable agent-run leasing, fencing, and crash recovery."""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.database import AgentRun
from app.services.agent_runtime.contracts import Sensitivity
from app.services.idempotency import IdempotencyConflict, canonical_hash


class RunLeaseConflict(RuntimeError):
    """A worker tried to mutate a run without its current live lease."""


class RunNotFound(LookupError):
    """A durable run is absent or outside the requesting user's boundary."""


class AgentRunSummary(BaseModel):
    """User-visible run metadata with all execution inputs intentionally absent."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    session_id: Optional[UUID] = None
    turn_id: Optional[UUID] = None
    use_case: str
    sensitivity: str
    generation_epoch: int
    status: str
    effect_state: str
    deadline_at: datetime
    error_code: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None


class AgentRunCommand(BaseModel):
    """Immutable scheduling input; raw input is hashed and never persisted here."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    idempotency_key: str = Field(min_length=8, max_length=255)
    org_id: UUID
    user_id: int = Field(gt=0)
    session_id: Optional[UUID] = None
    turn_id: Optional[UUID] = None
    use_case: str = Field(min_length=1, max_length=50)
    input: Dict[str, object]
    sensitivity: Sensitivity
    generation_epoch: int = Field(ge=1)
    deadline_at: datetime

    @field_validator("deadline_at")
    @classmethod
    def normalize_deadline(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)


class AgentRunService:
    """Coordinates durable work without trusting an in-memory worker state."""

    def __init__(self, session: Session) -> None:
        self._db = session

    def create(self, command: AgentRunCommand) -> Tuple[AgentRun, bool]:
        input_hash = self._command_hash(command)
        existing = (
            self._db.query(AgentRun)
            .filter(AgentRun.idempotency_key == command.idempotency_key)
            .one_or_none()
        )
        if existing is not None:
            if existing.input_hash != input_hash:
                raise IdempotencyConflict(
                    "Agent run idempotency key was reused for different input"
                )
            return existing, False

        row = AgentRun(
            idempotency_key=command.idempotency_key,
            input_hash=input_hash,
            org_id=command.org_id,
            user_id=command.user_id,
            session_id=command.session_id,
            turn_id=command.turn_id,
            use_case=command.use_case,
            sensitivity=command.sensitivity.value,
            generation_epoch=command.generation_epoch,
            status="queued",
            fencing_token=0,
            effect_state="none",
            state_json={},
            deadline_at=command.deadline_at,
        )
        try:
            self._db.add(row)
            self._db.commit()
            self._db.refresh(row)
        except Exception:
            self._db.rollback()
            raise
        return row, True

    def list_for_user(
        self,
        *,
        user_id: int,
        limit: int = 100,
    ) -> List[AgentRunSummary]:
        if limit <= 0 or limit > 100:
            raise ValueError("limit must contain 1 to 100 runs")
        rows = (
            self._db.query(AgentRun)
            .filter(AgentRun.user_id == user_id)
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .limit(limit)
            .all()
        )
        return [AgentRunSummary.model_validate(row) for row in rows]

    def get_for_user(self, run_id: UUID, *, user_id: int) -> AgentRunSummary:
        row = (
            self._db.query(AgentRun)
            .filter(AgentRun.id == run_id, AgentRun.user_id == user_id)
            .one_or_none()
        )
        if row is None:
            raise RunNotFound("Agent run not found")
        return AgentRunSummary.model_validate(row)

    def count_active_for_user(self, *, user_id: int) -> int:
        return (
            self._db.query(AgentRun)
            .filter(
                AgentRun.user_id == user_id,
                AgentRun.status.in_(("queued", "running")),
            )
            .count()
        )

    def claim_batch(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
        use_cases: Optional[Sequence[str]] = None,
    ) -> List[AgentRun]:
        self._validate_lease_request(worker_id, limit, lease_seconds)
        now = self._naive_utc(now)
        self.recover_expired(now=now)
        query = (
            self._db.query(AgentRun)
            .filter(
                AgentRun.status == "queued",
                AgentRun.deadline_at > now,
            )
        )
        if use_cases is not None:
            normalized_use_cases = tuple(dict.fromkeys(use_cases))
            if not normalized_use_cases or any(
                not value or len(value) > 50 for value in normalized_use_cases
            ):
                raise ValueError("use_cases must contain valid execution intents")
            query = query.filter(AgentRun.use_case.in_(normalized_use_cases))
        rows = (
            query.order_by(AgentRun.created_at, AgentRun.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
            .all()
        )
        lease_until = now + timedelta(seconds=lease_seconds)
        for row in rows:
            row.status = "running"
            row.fencing_token += 1
            row.leased_by = worker_id
            row.lease_until = lease_until
            row.heartbeat_at = now
            row.error_code = None
        self._db.commit()
        for row in rows:
            self._db.refresh(row)
        return rows

    def claim_one(
        self,
        run_id: UUID,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> AgentRun:
        self._validate_lease_request(worker_id, 1, lease_seconds)
        now = self._naive_utc(now)
        row = (
            self._db.query(AgentRun)
            .filter(AgentRun.id == run_id)
            .with_for_update()
            .one_or_none()
        )
        if row is None or row.status != "queued" or row.deadline_at <= now:
            raise RunLeaseConflict("Agent run is not available for claiming")
        row.status = "running"
        row.fencing_token += 1
        row.leased_by = worker_id
        row.lease_until = now + timedelta(seconds=lease_seconds)
        row.heartbeat_at = now
        row.error_code = None
        self._db.commit()
        self._db.refresh(row)
        return row

    def heartbeat(
        self,
        run_id: UUID,
        *,
        worker_id: str,
        fencing_token: int,
        now: datetime,
        lease_seconds: int,
    ) -> AgentRun:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now = self._naive_utc(now)
        row = self._leased_row(run_id, worker_id, fencing_token, now)
        row.heartbeat_at = now
        row.lease_until = now + timedelta(seconds=lease_seconds)
        self._db.commit()
        self._db.refresh(row)
        return row

    def mark_effect_started(
        self,
        run_id: UUID,
        *,
        worker_id: str,
        fencing_token: int,
        now: datetime,
    ) -> AgentRun:
        now = self._naive_utc(now)
        row = self._leased_row(run_id, worker_id, fencing_token, now)
        row.effect_state = "started"
        row.heartbeat_at = now
        self._db.commit()
        self._db.refresh(row)
        return row

    def complete(
        self,
        run_id: UUID,
        *,
        worker_id: str,
        fencing_token: int,
        now: datetime,
        commit: bool = True,
    ) -> AgentRun:
        now = self._naive_utc(now)
        row = self._leased_row(run_id, worker_id, fencing_token, now)
        row.status = "completed"
        if row.effect_state == "started":
            row.effect_state = "confirmed"
        row.completed_at = now
        row.error_code = None
        self._clear_lease(row)
        if commit:
            self._db.commit()
            self._db.refresh(row)
        return row

    def fail(
        self,
        run_id: UUID,
        *,
        worker_id: str,
        fencing_token: int,
        now: datetime,
        error_code: str = "agent_execution_failed",
        commit: bool = True,
    ) -> AgentRun:
        now = self._naive_utc(now)
        row = self._leased_row(run_id, worker_id, fencing_token, now)
        row.status = "failed"
        row.error_code = error_code
        row.completed_at = now
        self._clear_lease(row)
        if commit:
            self._db.commit()
            self._db.refresh(row)
        return row

    def requeue(
        self,
        run_id: UUID,
        *,
        worker_id: str,
        fencing_token: int,
        now: datetime,
        error_code: str,
    ) -> AgentRun:
        """Release safe leased work for a later attempt without closing it."""
        if not error_code or len(error_code) > 100:
            raise ValueError("error_code must contain 1 to 100 characters")
        now = self._naive_utc(now)
        row = self._leased_row(run_id, worker_id, fencing_token, now)
        if row.effect_state != "none":
            raise RunLeaseConflict(
                "Agent run cannot be requeued after an effect started"
            )
        row.status = "queued"
        row.error_code = error_code
        row.completed_at = None
        self._clear_lease(row)
        self._db.commit()
        self._db.refresh(row)
        return row

    def recover_expired(self, *, now: datetime) -> List[AgentRun]:
        now = self._naive_utc(now)
        rows = (
            self._db.query(AgentRun)
            .filter(
                or_(
                    and_(
                        AgentRun.status.in_(("queued", "running")),
                        AgentRun.deadline_at <= now,
                    ),
                    and_(
                        AgentRun.status == "running",
                        AgentRun.lease_until <= now,
                    ),
                )
            )
            .order_by(AgentRun.created_at, AgentRun.id)
            .with_for_update(skip_locked=True)
            .all()
        )
        for row in rows:
            deadline_expired = row.deadline_at <= now
            if row.effect_state == "started":
                row.status = "unknown"
                row.error_code = (
                    "deadline_after_effect_started"
                    if deadline_expired
                    else "lease_expired_after_effect_started"
                )
                row.completed_at = now
            elif deadline_expired:
                row.status = "cancelled"
                row.error_code = "deadline_exceeded"
                row.completed_at = now
            else:
                row.status = "queued"
                row.error_code = "lease_expired_requeued"
                row.completed_at = None
            self._clear_lease(row)
        self._db.commit()
        for row in rows:
            self._db.refresh(row)
        return rows

    def _leased_row(
        self,
        run_id: UUID,
        worker_id: str,
        fencing_token: int,
        now: datetime,
    ) -> AgentRun:
        row = (
            self._db.query(AgentRun)
            .filter(AgentRun.id == run_id)
            .with_for_update()
            .one_or_none()
        )
        if (
            row is None
            or row.status != "running"
            or row.leased_by != worker_id
            or row.fencing_token != fencing_token
            or row.lease_until is None
            or row.lease_until <= now
            or row.deadline_at <= now
        ):
            raise RunLeaseConflict("Agent run lease is no longer current")
        return row

    @staticmethod
    def _clear_lease(row: AgentRun) -> None:
        row.leased_by = None
        row.lease_until = None
        row.heartbeat_at = None

    @staticmethod
    def _validate_lease_request(
        worker_id: str,
        limit: int,
        lease_seconds: int,
    ) -> None:
        if not worker_id or len(worker_id) > 100:
            raise ValueError("worker_id must contain 1 to 100 characters")
        if limit <= 0:
            raise ValueError("limit must be positive")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")

    @staticmethod
    def _command_hash(command: AgentRunCommand) -> str:
        return canonical_hash(
            {
                "org_id": str(command.org_id),
                "user_id": command.user_id,
                "session_id": str(command.session_id) if command.session_id else None,
                "turn_id": str(command.turn_id) if command.turn_id else None,
                "use_case": command.use_case,
                "input": command.input,
                "sensitivity": command.sensitivity.value,
                "generation_epoch": command.generation_epoch,
                "deadline_at": command.deadline_at.isoformat(),
            }
        )

    @staticmethod
    def _naive_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
