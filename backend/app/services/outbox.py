"""Transactional producer and fail-closed dispatcher state machine."""
from datetime import datetime, timedelta
from enum import Enum
import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.database import OutboxEvent, OutboxStatus
from app.services.idempotency import IdempotencyConflict, canonical_hash


class DeliveryFailureKind(str, Enum):
    RETRYABLE_BEFORE_SEND = "retryable_before_send"
    PERMANENT = "permanent"
    UNKNOWN_AFTER_SEND = "unknown_after_send"


class OutboxLeaseConflict(RuntimeError):
    """A stale or different worker attempted to finalize an event."""


class OutboxCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aggregate_type: str = Field(min_length=1, max_length=50)
    aggregate_id: str = Field(min_length=1, max_length=255)
    event_type: str = Field(min_length=1, max_length=50)
    business_key: str = Field(min_length=1, max_length=255)
    channel: str = Field(min_length=1, max_length=50)
    payload: Dict[str, Any]
    available_at: Optional[datetime] = None
    max_attempts: int = Field(default=5, ge=1, le=100)


class OutboxService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(self, command: OutboxCommand) -> Tuple[OutboxEvent, bool]:
        payload_hash = canonical_hash(command.payload)
        existing = self._session.query(OutboxEvent).filter(
            OutboxEvent.channel == command.channel,
            OutboxEvent.business_key == command.business_key,
            OutboxEvent.event_type == command.event_type,
        ).one_or_none()
        if existing is not None:
            if existing.payload_hash != payload_hash:
                raise IdempotencyConflict(
                    "Outbox business action was reused for a different payload"
                )
            return existing, False

        event = OutboxEvent(
            aggregate_type=command.aggregate_type,
            aggregate_id=command.aggregate_id,
            event_type=command.event_type,
            business_key=command.business_key,
            channel=command.channel,
            payload_json=command.payload,
            payload_hash=payload_hash,
            status=OutboxStatus.PENDING,
            available_at=command.available_at or datetime.utcnow(),
            max_attempts=command.max_attempts,
        )
        self._session.add(event)
        self._session.flush()
        return event, True

    def claim_batch(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
    ) -> List[OutboxEvent]:
        """Lease due work; caller must commit before making network calls."""
        self.expire_stale_leases(now=now)

        due = (
            self._session.query(OutboxEvent)
            .filter(
                OutboxEvent.status.in_(
                    [OutboxStatus.PENDING, OutboxStatus.RETRY]
                ),
                OutboxEvent.available_at <= now,
            )
            .order_by(OutboxEvent.available_at, OutboxEvent.created_at)
            .with_for_update(skip_locked=True)
            .limit(limit)
            .all()
        )
        lease_until = now + timedelta(seconds=lease_seconds)
        for event in due:
            event.status = OutboxStatus.PROCESSING
            event.leased_by = worker_id
            event.lease_until = lease_until
            event.attempt_count += 1

        self._session.flush()
        return due

    def expire_stale_leases(self, *, now: datetime) -> List[OutboxEvent]:
        """Dead-letter leases whose external delivery state is unknowable."""
        expired = (
            self._session.query(OutboxEvent)
            .filter(
                OutboxEvent.status == OutboxStatus.PROCESSING,
                OutboxEvent.lease_until <= now,
            )
            .with_for_update(skip_locked=True)
            .all()
        )
        for event in expired:
            event.status = OutboxStatus.DEAD_LETTER
            event.last_error = "lease_expired_unknown_delivery_state"
            self._clear_lease(event)

        self._session.flush()
        return expired

    def mark_sent(
        self,
        event: OutboxEvent,
        *,
        worker_id: str,
        external_message_id: Optional[str],
        now: datetime,
    ) -> None:
        self._require_active_lease(event, worker_id, now)
        event.status = OutboxStatus.SENT
        event.external_message_id = external_message_id
        event.sent_at = now
        event.last_error = None
        self._clear_lease(event)
        self._session.flush()

    def mark_failure(
        self,
        event: OutboxEvent,
        *,
        worker_id: str,
        kind: DeliveryFailureKind,
        error_code: str,
        now: datetime,
    ) -> None:
        self._require_active_lease(event, worker_id, now)
        event.last_error = self._safe_error_code(error_code)
        should_retry = (
            kind == DeliveryFailureKind.RETRYABLE_BEFORE_SEND
            and event.attempt_count < event.max_attempts
        )
        if should_retry:
            event.status = OutboxStatus.RETRY
            backoff_seconds = min(
                30 * (2 ** max(event.attempt_count - 1, 0)),
                3600,
            )
            event.available_at = now + timedelta(seconds=backoff_seconds)
        else:
            event.status = OutboxStatus.DEAD_LETTER
        self._clear_lease(event)
        self._session.flush()

    @staticmethod
    def _require_active_lease(
        event: OutboxEvent,
        worker_id: str,
        now: datetime,
    ) -> None:
        if (
            event.status != OutboxStatus.PROCESSING
            or event.leased_by != worker_id
            or event.lease_until is None
            or event.lease_until <= now
        ):
            raise OutboxLeaseConflict("Outbox lease is not owned by this worker")

    @staticmethod
    def _clear_lease(event: OutboxEvent) -> None:
        event.leased_by = None
        event.lease_until = None

    @staticmethod
    def _safe_error_code(error_code: str) -> str:
        normalized = error_code.strip().lower()
        if re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,99}", normalized):
            return normalized
        return "delivery_failure"
