"""Approval-gated delivery control for evidence-bound outreach drafts."""

from datetime import datetime, timezone
from typing import List, Literal, Optional, Tuple
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.models.database import (
    Account,
    AgentOutreachDelivery,
    ResearchOutreachDraft,
)
from app.services.idempotency import canonical_hash
from app.services.outbox import OutboxCommand, OutboxService


class DeliveryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: int = Field(gt=0)
    scheduled_at: datetime
    idempotency_key: str = Field(min_length=8, max_length=255)

    @field_validator("scheduled_at")
    @classmethod
    def normalize_schedule(cls, value: datetime) -> datetime:
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        if value < datetime.utcnow():
            raise ValueError("scheduled_at must not be in the past")
        return value


class DeliveryReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=3, max_length=1000)


class DeliveryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    draft_id: UUID
    customer_id: int
    account_id: int
    channel: str
    provider: str
    account_name: str
    sender: str
    recipient: str
    subject: Optional[str] = None
    body: str
    status: str
    scheduled_at: datetime
    outbox_event_id: Optional[UUID] = None
    external_message_id: Optional[str] = None
    error_code: Optional[str] = None
    review_reason: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    verified_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class DeliveryNotFound(LookupError):
    pass


class DeliveryConflict(RuntimeError):
    pass


class AgentDeliveryService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._outbox = OutboxService(db)

    def list_deliveries(self, *, user_id: int) -> List[DeliveryResponse]:
        rows = (
            self._db.query(AgentOutreachDelivery)
            .filter(AgentOutreachDelivery.user_id == user_id)
            .order_by(AgentOutreachDelivery.updated_at.desc())
            .all()
        )
        return [self._response(row) for row in rows]

    def prepare(
        self,
        draft_id: UUID,
        command: DeliveryCreate,
        *,
        user_id: int,
    ) -> Tuple[DeliveryResponse, bool]:
        draft = self._owned_draft(draft_id, user_id=user_id)
        account = self._owned_account(command.account_id, user_id=user_id)
        existing = (
            self._db.query(AgentOutreachDelivery)
            .filter(
                AgentOutreachDelivery.user_id == user_id,
                AgentOutreachDelivery.idempotency_key == command.idempotency_key,
            )
            .one_or_none()
        )
        self._validate_current_context(
            draft,
            account,
            exclude_delivery_id=existing.id if existing is not None else None,
        )
        customer = draft.research_job.customer
        snapshot = {
            "draft_id": str(draft.id),
            "research_version": draft.research_version,
            "account_id": account.id,
            "provider": account.account_type,
            "account_name": account.name,
            "sender": account.email,
            "recipient": customer.email,
            "subject": draft.subject,
            "body": draft.body,
            "scheduled_at": command.scheduled_at.isoformat(),
        }
        input_hash = canonical_hash(snapshot)
        if existing is not None:
            if existing.input_hash != input_hash:
                raise DeliveryConflict(
                    "Delivery idempotency key was reused for different input"
                )
            return self._response(existing), False

        delivery = AgentOutreachDelivery(
            user_id=user_id,
            draft_id=draft.id,
            customer_id=draft.customer_id,
            account_id=account.id,
            idempotency_key=command.idempotency_key,
            input_hash=input_hash,
            channel=draft.channel,
            provider=account.account_type,
            account_name=account.name,
            sender=account.email,
            recipient=customer.email,
            subject=draft.subject,
            body=draft.body,
            research_version=draft.research_version,
            status="approval_pending",
            scheduled_at=command.scheduled_at,
        )
        self._db.add(delivery)
        self._db.commit()
        self._db.refresh(delivery)
        return self._response(delivery), True

    def review(
        self,
        delivery_id: UUID,
        command: DeliveryReview,
        *,
        user_id: int,
    ) -> DeliveryResponse:
        delivery = self._owned_delivery(delivery_id, user_id=user_id)
        if delivery.status != "approval_pending":
            raise DeliveryConflict("Only approval-pending delivery can be reviewed")
        if command.decision == "reject":
            delivery.status = "rejected"
            delivery.review_reason = command.reason.strip()
            delivery.reviewed_by_user_id = user_id
            delivery.reviewed_at = self._now()
            self._db.commit()
            self._db.refresh(delivery)
            return self._response(delivery)

        draft = self._owned_draft(delivery.draft_id, user_id=user_id)
        account = self._owned_account(delivery.account_id, user_id=user_id)
        self._validate_current_context(
            draft,
            account,
            exclude_delivery_id=delivery.id,
        )
        self._validate_snapshot(delivery, draft, account)
        event, _ = self._outbox.enqueue(
            OutboxCommand(
                aggregate_type="agent_outreach_delivery",
                aggregate_id=str(delivery.id),
                event_type="send",
                business_key=f"agent-delivery:{delivery.id}",
                channel="email",
                payload={
                    "account_id": delivery.account_id,
                    "to": delivery.recipient,
                    "subject": delivery.subject,
                    "body": delivery.body,
                },
                available_at=delivery.scheduled_at,
            )
        )
        delivery.outbox_event_id = event.id
        delivery.status = "scheduled"
        delivery.review_reason = command.reason.strip()
        delivery.reviewed_by_user_id = user_id
        delivery.reviewed_at = self._now()
        self._db.commit()
        self._db.refresh(delivery)
        return self._response(delivery)

    def _validate_current_context(
        self,
        draft: ResearchOutreachDraft,
        account: Account,
        *,
        exclude_delivery_id: Optional[UUID] = None,
    ) -> None:
        job = draft.research_job
        customer = job.customer
        fields = dict(customer.custom_fields or {})
        icp = dict((customer.source_data_json or {}).get("icp") or {})
        if draft.channel != "email":
            raise DeliveryConflict("Mailbox delivery requires an email draft")
        if draft.status != "approved":
            raise DeliveryConflict("Outreach draft must be approved")
        if job.status != "completed" or draft.research_version != job.version:
            raise DeliveryConflict("Outreach draft or research dossier is stale")
        if fields.get("contact_suppressed") is True:
            raise DeliveryConflict("Contact is suppressed")
        if icp.get("stale") is True or fields.get("icp_recommended") is not True:
            raise DeliveryConflict("Current approved ICP context is required")
        if not customer.email or fields.get("email_verification_status") != "valid":
            raise DeliveryConflict("A valid verified email is required")
        if account.account_type not in {"gmail", "outlook"}:
            raise DeliveryConflict("Selected account is not a mailbox")
        if not account.is_active:
            raise DeliveryConflict("Selected sender account is inactive")
        if not account.is_verified:
            raise DeliveryConflict("Selected sender account is not verified")
        if not account.email:
            raise DeliveryConflict("Selected sender account has no email address")
        if not (
            account.connection_status == "connected"
            and account.credential_secret_ref
        ):
            raise DeliveryConflict("Selected sender account is not connected")
        reserved_query = self._db.query(AgentOutreachDelivery).filter(
            AgentOutreachDelivery.account_id == account.id,
            AgentOutreachDelivery.status.in_(
                ["approval_pending", "scheduled", "dispatching"]
            ),
        )
        if exclude_delivery_id is not None:
            reserved_query = reserved_query.filter(
                AgentOutreachDelivery.id != exclude_delivery_id
            )
        reserved = reserved_query.count()
        if (account.today_sent or 0) + reserved >= (account.daily_limit or 0):
            raise DeliveryConflict("Selected sender account has no daily capacity")

    @staticmethod
    def _validate_snapshot(delivery, draft, account) -> None:
        customer = draft.research_job.customer
        current = {
            "research_version": draft.research_version,
            "provider": account.account_type,
            "account_name": account.name,
            "sender": account.email,
            "recipient": customer.email,
            "subject": draft.subject,
            "body": draft.body,
        }
        stored = {key: getattr(delivery, key) for key in current}
        if stored != current:
            raise DeliveryConflict("Delivery snapshot is stale and must be recreated")

    def _owned_draft(self, draft_id: UUID, *, user_id: int):
        row = (
            self._db.query(ResearchOutreachDraft)
            .filter(
                ResearchOutreachDraft.id == draft_id,
                ResearchOutreachDraft.user_id == user_id,
            )
            .one_or_none()
        )
        if row is None:
            raise DeliveryNotFound("Outreach draft not found")
        return row

    def _owned_account(self, account_id: int, *, user_id: int):
        row = (
            self._db.query(Account)
            .filter(Account.id == account_id, Account.user_id == user_id)
            .one_or_none()
        )
        if row is None:
            raise DeliveryNotFound("Sender account not found")
        return row

    def _owned_delivery(self, delivery_id: UUID, *, user_id: int):
        row = (
            self._db.query(AgentOutreachDelivery)
            .filter(
                AgentOutreachDelivery.id == delivery_id,
                AgentOutreachDelivery.user_id == user_id,
            )
            .one_or_none()
        )
        if row is None:
            raise DeliveryNotFound("Delivery not found")
        return row

    @staticmethod
    def _response(row: AgentOutreachDelivery) -> DeliveryResponse:
        return DeliveryResponse(
            id=row.id,
            draft_id=row.draft_id,
            customer_id=row.customer_id,
            account_id=row.account_id,
            channel=row.channel,
            provider=row.provider,
            account_name=row.account_name,
            sender=row.sender,
            recipient=row.recipient,
            subject=row.subject,
            body=row.body,
            status=row.status,
            scheduled_at=row.scheduled_at,
            outbox_event_id=row.outbox_event_id,
            external_message_id=row.external_message_id,
            error_code=row.error_code,
            review_reason=row.review_reason,
            reviewed_at=row.reviewed_at,
            verified_at=row.verified_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)
