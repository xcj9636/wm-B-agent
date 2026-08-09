from datetime import datetime

from app.api.v1.auth import get_current_active_user
from app.main import app
from app.models.database import (
    Account,
    Customer,
    OutreachLog,
    OutreachStatus,
    OutboxEvent,
    OutboxStatus,
    User,
)


def create_admin(db, *, username):
    admin = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password="unused",
        is_active=True,
        is_superuser=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


def create_dead_outreach(db, *, key):
    customer = Customer(
        username=f"buyer-{key}",
        platform="email",
        email=f"buyer-{key}@example.com",
    )
    account = Account(
        account_type="email",
        name=f"account-{key}",
        email=f"sender-{key}@example.com",
        credentials_json={},
        is_active=True,
        today_sent=0,
        daily_limit=100,
    )
    db.add_all([customer, account])
    db.flush()
    outreach = OutreachLog(
        customer_id=customer.id,
        channel="email",
        status=OutreachStatus.FAILED,
        subject="Hello",
        content="Private message body",
        account_id=account.id,
        error_msg="provider_response_lost",
    )
    db.add(outreach)
    db.flush()
    event = OutboxEvent(
        aggregate_type="outreach_log",
        aggregate_id=str(outreach.id),
        event_type="send",
        business_key=f"resolution-{key}",
        channel="email",
        payload_json={
            "to": customer.email,
            "subject": "Hello",
            "body": "Private message body",
        },
        payload_hash="d" * 64,
        status=OutboxStatus.DEAD_LETTER,
        available_at=datetime.utcnow(),
        attempt_count=1,
        max_attempts=5,
        last_error="provider_response_lost",
    )
    db.add(event)
    db.commit()
    return event, outreach, account


def approval_url(event):
    return (
        "/api/v1/admin/reliable-execution/dead-letters/"
        f"{event.id}/resolution-approvals"
    )


def test_dead_letter_resolution_requires_superuser(api_context):
    client, db, _ = api_context
    event, _, _ = create_dead_outreach(db, key="forbidden")

    response = client.post(
        approval_url(event),
        json={
            "action": "confirmed_not_sent",
            "evidence_reference": "ticket/INC-001",
        },
    )

    assert response.status_code == 403


def test_confirmed_not_sent_requires_two_distinct_admins(api_context):
    client, db, first_admin = api_context
    first_admin.is_superuser = True
    second_admin = create_admin(db, username="second-requeue-admin")
    event, outreach, _ = create_dead_outreach(db, key="requeue")
    request_body = {
        "action": "confirmed_not_sent",
        "evidence_reference": "provider-audit/NOT-SENT-001",
    }

    first = client.post(approval_url(event), json=request_body)
    duplicate = client.post(approval_url(event), json=request_body)
    db.refresh(event)
    db.refresh(outreach)

    assert first.status_code == 200
    assert first.json()["status"] == "pending"
    assert first.json()["approvals"] == 1
    assert first.json()["required_approvals"] == 2
    assert duplicate.status_code == 409
    assert event.status == OutboxStatus.DEAD_LETTER
    assert outreach.status == OutreachStatus.FAILED

    app.dependency_overrides[get_current_active_user] = lambda: second_admin
    second = client.post(approval_url(event), json=request_body)
    db.refresh(event)
    db.refresh(outreach)

    assert second.status_code == 200
    assert second.json()["status"] == "executed"
    assert second.json()["approvals"] == 2
    assert event.status == OutboxStatus.RETRY
    assert event.last_error == "manual_requeue_approved"
    assert event.leased_by is None
    assert event.lease_until is None
    assert outreach.status == OutreachStatus.PENDING
    assert outreach.error_msg is None
    assert "provider-audit/NOT-SENT-001" not in second.text


def test_confirmed_sent_reconciles_without_external_delivery(api_context):
    client, db, first_admin = api_context
    first_admin.is_superuser = True
    second_admin = create_admin(db, username="second-sent-admin")
    event, outreach, account = create_dead_outreach(db, key="sent")
    request_body = {
        "action": "confirmed_sent",
        "evidence_reference": "provider-audit/SENT-001",
        "external_message_id": "provider-message-001",
    }

    first = client.post(approval_url(event), json=request_body)
    app.dependency_overrides[get_current_active_user] = lambda: second_admin
    second = client.post(approval_url(event), json=request_body)
    db.refresh(event)
    db.refresh(outreach)
    db.refresh(account)

    assert first.status_code == 200
    assert first.json()["status"] == "pending"
    assert second.status_code == 200
    assert second.json()["status"] == "executed"
    assert event.status == OutboxStatus.SENT
    assert event.external_message_id == "provider-message-001"
    assert event.sent_at is not None
    assert outreach.status == OutreachStatus.SENT
    assert outreach.message_id == "provider-message-001"
    assert outreach.sent_at is not None
    assert account.today_sent == 1
    assert "Private message body" not in second.text
    assert "provider-audit/SENT-001" not in second.text

