from datetime import datetime, timedelta, timezone
import uuid

import pytest

from app.models.database import (
    Account,
    AgentOutreachDelivery,
    AgentResearchJob,
    Customer,
    OutboxEvent,
    ResearchOutreachDraft,
    User,
)
from app.services.outbox_delivery import OutboxDeliveryRouter


def seed_approved_email_draft(db, user, *, suffix="one"):
    customer = Customer(
        username=f"delivery-buyer-{suffix}",
        platform="hunter",
        email=f"buyer-{suffix}@example.com",
        company_name="Acme Distribution",
        custom_fields={
            "email_verification_status": "valid",
            "contact_suppressed": False,
            "icp_recommended": True,
        },
        source_data_json={"icp": {"stale": False}},
    )
    db.add(customer)
    db.flush()
    job = AgentResearchJob(
        user_id=user.id,
        customer_id=customer.id,
        objective="Validate distributor fit",
        status="completed",
        profile_evidence_json=[{"id": str(uuid.uuid4())}],
        market_signals_json=[{"id": str(uuid.uuid4())}],
        missing_fields_json=[],
        version=2,
    )
    db.add(job)
    db.flush()
    draft = ResearchOutreachDraft(
        research_job_id=job.id,
        user_id=user.id,
        customer_id=customer.id,
        idempotency_key=f"draft-{suffix}",
        input_hash="a" * 64,
        channel="email",
        language="en",
        goal="Request a short call",
        subject="A distribution fit question",
        body="Hi Ada, would a short distribution-fit call be useful?",
        personalization_points_json=[],
        evidence_ids_json=[],
        status="approved",
        research_version=2,
        usage_json={},
        reviewed_by_user_id=user.id,
        reviewed_at=datetime.utcnow(),
    )
    account = Account(
        user_id=user.id,
        account_type="gmail",
        name="Export sales",
        email="sales@example.com",
        credentials_json={"access_token": "backend-only-secret"},
        is_active=True,
        is_verified=True,
        daily_limit=100,
        today_sent=0,
    )
    db.add_all([draft, account])
    db.commit()
    return customer, job, draft, account


def delivery_payload(account_id, *, key="delivery-acme-0001"):
    return {
        "account_id": account_id,
        "scheduled_at": (
            datetime.now(timezone.utc) + timedelta(minutes=30)
        ).isoformat(),
        "idempotency_key": key,
    }


def test_delivery_is_prepared_then_approved_before_outbox_enqueue(api_context):
    client, db, user = api_context
    customer, _, draft, account = seed_approved_email_draft(db, user)
    payload = delivery_payload(account.id)

    prepared = client.post(
        f"/api/v1/agent/outreach-drafts/{draft.id}/deliveries",
        json=payload,
    )
    repeated = client.post(
        f"/api/v1/agent/outreach-drafts/{draft.id}/deliveries",
        json=payload,
    )

    assert prepared.status_code == 201, prepared.text
    body = prepared.json()
    assert body["status"] == "approval_pending"
    assert body["recipient"] == customer.email
    assert body["sender"] == account.email
    assert body["account_name"] == "Export sales"
    assert body["provider"] == "gmail"
    assert body["outbox_event_id"] is None
    assert "credentials" not in body
    assert "backend-only-secret" not in prepared.text
    assert repeated.status_code == 200
    assert repeated.json()["id"] == body["id"]
    assert db.query(OutboxEvent).count() == 0

    approved = client.patch(
        f"/api/v1/agent/deliveries/{body['id']}/review",
        json={"decision": "approve", "reason": "Recipient and sender checked"},
    )

    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "scheduled"
    event = db.query(OutboxEvent).one()
    assert event.aggregate_type == "agent_outreach_delivery"
    assert event.aggregate_id == body["id"]
    assert event.available_at == datetime.fromisoformat(
        payload["scheduled_at"]
    ).replace(tzinfo=None)
    assert event.payload_json["account_id"] == account.id
    assert event.payload_json["to"] == customer.email
    assert "credentials" not in event.payload_json
    assert "backend-only-secret" not in str(event.payload_json)

    listed = client.get("/api/v1/agent/deliveries")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [body["id"]]


def test_delivery_revalidates_draft_contact_and_account_at_approval(api_context):
    client, db, user = api_context
    customer, job, draft, account = seed_approved_email_draft(db, user)
    prepared = client.post(
        f"/api/v1/agent/outreach-drafts/{draft.id}/deliveries",
        json=delivery_payload(account.id, key="delivery-revalidate"),
    )
    assert prepared.status_code == 201, prepared.text

    job.version += 1
    customer.custom_fields = {
        **customer.custom_fields,
        "contact_suppressed": True,
    }
    account.is_active = False
    db.commit()
    blocked = client.patch(
        f"/api/v1/agent/deliveries/{prepared.json()['id']}/review",
        json={"decision": "approve", "reason": "Try sending anyway"},
    )

    assert blocked.status_code == 409
    assert db.query(OutboxEvent).count() == 0
    db.refresh(db.get(AgentOutreachDelivery, uuid.UUID(prepared.json()["id"])))
    assert db.get(
        AgentOutreachDelivery,
        uuid.UUID(prepared.json()["id"]),
    ).status == "approval_pending"


def test_delivery_rejects_unverified_or_foreign_sender_account(api_context):
    client, db, user = api_context
    _, _, draft, account = seed_approved_email_draft(db, user)
    account.is_verified = False
    other = User(
        username="mailbox-owner",
        email="mailbox-owner@example.com",
        hashed_password="unused",
        is_active=True,
    )
    db.add(other)
    db.flush()
    foreign = Account(
        user_id=other.id,
        account_type="gmail",
        name="Foreign mailbox",
        email="foreign@example.com",
        credentials_json={"access_token": "secret"},
        is_active=True,
        is_verified=True,
    )
    db.add(foreign)
    db.commit()

    unverified = client.post(
        f"/api/v1/agent/outreach-drafts/{draft.id}/deliveries",
        json=delivery_payload(account.id, key="delivery-unverified"),
    )
    not_owned = client.post(
        f"/api/v1/agent/outreach-drafts/{draft.id}/deliveries",
        json=delivery_payload(foreign.id, key="delivery-foreign"),
    )

    assert unverified.status_code == 409
    assert not_owned.status_code == 404
    assert db.query(AgentOutreachDelivery).count() == 0


def test_single_daily_slot_does_not_count_the_same_delivery_twice(api_context):
    client, db, user = api_context
    _, _, draft, account = seed_approved_email_draft(db, user, suffix="quota")
    account.daily_limit = 1
    db.commit()
    payload = delivery_payload(account.id, key="delivery-only-slot")

    prepared = client.post(
        f"/api/v1/agent/outreach-drafts/{draft.id}/deliveries",
        json=payload,
    )
    repeated = client.post(
        f"/api/v1/agent/outreach-drafts/{draft.id}/deliveries",
        json=payload,
    )
    approved = client.patch(
        f"/api/v1/agent/deliveries/{prepared.json()['id']}/review",
        json={"decision": "approve", "reason": "Use the only daily slot"},
    )

    assert prepared.status_code == 201, prepared.text
    assert repeated.status_code == 200, repeated.text
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "scheduled"


class FakeVerifiedEmailService:
    def __init__(self, *, verified):
        self.verified = verified
        self.sent = []
        self.verified_ids = []

    async def send_email(self, **kwargs):
        self.sent.append(kwargs)
        return {"success": True, "message_id": "gmail-message-7"}

    async def verify_sent(self, *, message_id, access_token):
        self.verified_ids.append((message_id, access_token))
        return self.verified


@pytest.mark.asyncio
async def test_email_delivery_uses_selected_account_and_requires_sent_verification(
    api_context,
):
    _, db, user = api_context
    _, _, _, account = seed_approved_email_draft(db, user, suffix="router")
    service = FakeVerifiedEmailService(verified=True)
    router = OutboxDeliveryRouter(
        account_loader=lambda account_id: db.get(Account, account_id),
        email_service_factory=lambda provider: service,
    )
    event = OutboxEvent(
        channel="email",
        payload_json={
            "account_id": account.id,
            "to": "buyer@example.com",
            "subject": "Hello",
            "body": "Introduction",
        },
    )

    result = await router.deliver(event)

    assert result.success is True
    assert result.external_message_id == "gmail-message-7"
    assert service.sent[0]["from_email"] == account.email
    assert service.sent[0]["access_token"] == "backend-only-secret"
    assert service.verified_ids == [
        ("gmail-message-7", "backend-only-secret")
    ]

    service.verified = False
    not_verified = await router.deliver(event)
    assert not_verified.success is False
    assert not_verified.failure_kind.value == "unknown_after_send"
    assert not_verified.error_code == "sent_copy_not_verified"
