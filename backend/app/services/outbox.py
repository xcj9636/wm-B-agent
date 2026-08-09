"""Transactional producer for idempotent external delivery events."""
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.models.database import OutboxEvent, OutboxStatus
from app.services.idempotency import IdempotencyConflict, canonical_hash


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
