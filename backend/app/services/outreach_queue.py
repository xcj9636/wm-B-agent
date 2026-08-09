"""Transactional producer for outreach logs and delivery outbox events."""
from datetime import datetime
from typing import Literal, Optional, Tuple
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.models.database import Account, OutreachLog, OutreachStatus
from app.services.outbox import OutboxCommand, OutboxService


def delivery_spacing_seconds(schedule) -> int:
    """Return deterministic batch spacing from legacy interval settings."""
    minimum = max(int(schedule.get("interval_min", 30)), 0)
    maximum = max(int(schedule.get("interval_max", 120)), minimum)
    return (minimum + maximum) // 2


class QueueOutreachCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: int
    channel: Literal["email", "whatsapp"]
    recipient: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1)
    business_key: str = Field(min_length=1, max_length=255)
    subject: Optional[str] = Field(default=None, max_length=255)
    template_id: Optional[str] = Field(default=None, max_length=100)
    account_id: Optional[int] = None
    available_at: Optional[datetime] = None

    @model_validator(mode="after")
    def require_email_subject(self) -> "QueueOutreachCommand":
        if self.channel == "email" and not self.subject:
            raise ValueError("email outreach requires a subject")
        return self


class OutreachQuotaExceeded(RuntimeError):
    """The selected account has no unreserved daily capacity."""

    error_code = "account_daily_limit_reached"

    def __init__(self) -> None:
        super().__init__(self.error_code)


class OutreachQueueService:
    """Create the business record and external side effect atomically."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._outbox = OutboxService(session)

    def queue(
        self,
        command: QueueOutreachCommand,
    ) -> Tuple[OutreachLog, bool]:
        log_id = uuid.uuid4()
        available_at = command.available_at or datetime.utcnow()
        payload = self._delivery_payload(command)
        event, created = self._outbox.enqueue(
            OutboxCommand(
                aggregate_type="outreach_log",
                aggregate_id=str(log_id),
                event_type="send",
                business_key=command.business_key,
                channel=command.channel,
                payload=payload,
                available_at=available_at,
            )
        )
        if not created:
            existing = self._session.get(
                OutreachLog,
                uuid.UUID(event.aggregate_id),
            )
            if existing is None:
                raise RuntimeError("Outbox event has no outreach business record")
            return existing, False

        if command.account_id is not None:
            account = (
                self._session.query(Account)
                .filter(Account.id == command.account_id)
                .with_for_update()
                .one_or_none()
            )
            if account is None:
                self._discard_new_event(event)
                raise ValueError("outreach account does not exist")
            reserved = (
                self._session.query(OutreachLog)
                .filter(
                    OutreachLog.account_id == account.id,
                    OutreachLog.status.in_(
                        [OutreachStatus.PENDING, OutreachStatus.SCHEDULED]
                    ),
                )
                .count()
            )
            if (account.today_sent or 0) + reserved >= account.daily_limit:
                self._discard_new_event(event)
                raise OutreachQuotaExceeded()

        status = (
            OutreachStatus.SCHEDULED
            if available_at > datetime.utcnow()
            else OutreachStatus.PENDING
        )
        outreach = OutreachLog(
            id=log_id,
            customer_id=command.customer_id,
            channel=command.channel,
            status=status,
            subject=command.subject,
            content=command.body,
            template_id=command.template_id,
            account_id=command.account_id,
            scheduled_at=available_at,
        )
        self._session.add(outreach)
        self._session.flush()
        return outreach, True

    def _discard_new_event(self, event) -> None:
        self._session.delete(event)
        self._session.flush()

    @staticmethod
    def _delivery_payload(command: QueueOutreachCommand):
        if command.channel == "email":
            return {
                "to": command.recipient,
                "subject": command.subject,
                "body": command.body,
            }
        return {"to": command.recipient, "text": command.body}
