"""Evidence-backed, two-person dead-letter resolution workflow."""
from datetime import datetime
from typing import Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.models.database import (
    Account,
    Customer,
    OutreachLog,
    OutreachStatus,
    OutboxEvent,
    OutboxResolutionAction,
    OutboxResolutionApproval,
    OutboxResolutionRequest,
    OutboxResolutionStatus,
    OutboxStatus,
)


REFERENCE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,127}$"
REQUIRED_APPROVALS = 2


class ResolutionConflict(RuntimeError):
    """The requested resolution conflicts with durable state."""


class ResolutionEventNotFound(RuntimeError):
    """The referenced outbox event does not exist."""


class ResolutionApprovalCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: OutboxResolutionAction
    evidence_reference: str = Field(pattern=REFERENCE_PATTERN)
    external_message_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=255,
        pattern=REFERENCE_PATTERN,
    )

    @model_validator(mode="after")
    def validate_external_identity(self) -> "ResolutionApprovalCommand":
        if (
            self.action == OutboxResolutionAction.CONFIRMED_SENT
            and self.external_message_id is None
        ):
            raise ValueError("confirmed_sent requires external_message_id")
        if (
            self.action == OutboxResolutionAction.CONFIRMED_NOT_SENT
            and self.external_message_id is not None
        ):
            raise ValueError(
                "confirmed_not_sent cannot include external_message_id"
            )
        return self


class ResolutionApprovalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    event_id: str
    action: OutboxResolutionAction
    status: OutboxResolutionStatus
    approvals: int
    required_approvals: int = REQUIRED_APPROVALS


class DeadLetterResolutionService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def approve(
        self,
        *,
        event_id: uuid.UUID,
        admin_user_id: int,
        command: ResolutionApprovalCommand,
        now: Optional[datetime] = None,
    ) -> ResolutionApprovalResponse:
        now = now or datetime.utcnow()
        event = (
            self._session.query(OutboxEvent)
            .filter(OutboxEvent.id == event_id)
            .with_for_update()
            .one_or_none()
        )
        if event is None:
            raise ResolutionEventNotFound("Outbox event not found")
        if event.status != OutboxStatus.DEAD_LETTER:
            raise ResolutionConflict("Only dead-letter events can be resolved")

        dead_letter_version = event.updated_at or event.created_at
        resolution = (
            self._session.query(OutboxResolutionRequest)
            .filter(
                OutboxResolutionRequest.event_id == event.id,
                OutboxResolutionRequest.dead_letter_version
                == dead_letter_version,
            )
            .with_for_update()
            .one_or_none()
        )
        if resolution is None:
            resolution = OutboxResolutionRequest(
                event_id=event.id,
                dead_letter_version=dead_letter_version,
                action=command.action,
                evidence_reference=command.evidence_reference,
                external_message_id=command.external_message_id,
                status=OutboxResolutionStatus.PENDING,
                requested_by_user_id=admin_user_id,
            )
            self._session.add(resolution)
            self._session.flush()
        elif (
            resolution.action != command.action
            or resolution.evidence_reference != command.evidence_reference
            or resolution.external_message_id != command.external_message_id
        ):
            raise ResolutionConflict(
                "A different resolution is already pending for this event"
            )

        prior_approval = (
            self._session.query(OutboxResolutionApproval)
            .filter(
                OutboxResolutionApproval.request_id == resolution.id,
                OutboxResolutionApproval.approved_by_user_id
                == admin_user_id,
            )
            .one_or_none()
        )
        if prior_approval is not None:
            raise ResolutionConflict(
                "Second approval must come from a different administrator"
            )

        self._session.add(
            OutboxResolutionApproval(
                request_id=resolution.id,
                approved_by_user_id=admin_user_id,
            )
        )
        self._session.flush()
        approval_count = (
            self._session.query(OutboxResolutionApproval)
            .filter(OutboxResolutionApproval.request_id == resolution.id)
            .count()
        )

        if approval_count >= REQUIRED_APPROVALS:
            self._execute(
                event=event,
                resolution=resolution,
                now=now,
            )

        return ResolutionApprovalResponse(
            request_id=str(resolution.id),
            event_id=str(event.id),
            action=resolution.action,
            status=resolution.status,
            approvals=approval_count,
        )

    def _execute(
        self,
        *,
        event: OutboxEvent,
        resolution: OutboxResolutionRequest,
        now: datetime,
    ) -> None:
        if resolution.action == OutboxResolutionAction.CONFIRMED_NOT_SENT:
            event.status = OutboxStatus.RETRY
            event.available_at = now
            event.last_error = "manual_requeue_approved"
            event.leased_by = None
            event.lease_until = None
            self._sync_outreach_requeue(event)
        else:
            event.status = OutboxStatus.SENT
            event.external_message_id = resolution.external_message_id
            event.sent_at = now
            event.last_error = None
            event.leased_by = None
            event.lease_until = None
            self._sync_outreach_sent(
                event,
                external_message_id=resolution.external_message_id,
                now=now,
            )

        resolution.status = OutboxResolutionStatus.EXECUTED
        resolution.executed_at = now
        self._session.flush()

    def _sync_outreach_requeue(self, event: OutboxEvent) -> None:
        outreach = self._get_outreach(event)
        if outreach is None:
            return
        outreach.status = OutreachStatus.PENDING
        outreach.error_msg = None

    def _sync_outreach_sent(
        self,
        event: OutboxEvent,
        *,
        external_message_id: Optional[str],
        now: datetime,
    ) -> None:
        outreach = self._get_outreach(event)
        if outreach is None:
            return
        outreach.status = OutreachStatus.SENT
        outreach.message_id = external_message_id
        outreach.sent_at = now
        outreach.error_msg = None

        if outreach.account_id is not None:
            account = (
                self._session.query(Account)
                .filter(Account.id == outreach.account_id)
                .with_for_update()
                .one_or_none()
            )
            if account is not None:
                account.today_sent = (account.today_sent or 0) + 1
                account.last_used = now
        customer = self._session.get(Customer, outreach.customer_id)
        if customer is not None:
            customer.first_contacted_at = customer.first_contacted_at or now
            customer.last_contacted_at = now

    def _get_outreach(self, event: OutboxEvent) -> Optional[OutreachLog]:
        if event.aggregate_type != "outreach_log":
            return None
        try:
            outreach_id = uuid.UUID(event.aggregate_id)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ResolutionConflict(
                "Invalid outreach aggregate identity"
            ) from exc
        outreach = self._session.get(
            OutreachLog,
            outreach_id,
        )
        if outreach is None:
            raise ResolutionConflict(
                "Outbox event has no outreach business record"
            )
        return outreach
