from datetime import datetime, timedelta
from uuid import uuid4

from app.main import app
from app.models.database import (
    LLMInvocation,
    LLMInvocationStatus,
    OutboxEvent,
    OutboxStatus,
)
from app.services.reliable_execution_status import (
    get_reliable_execution_status_service,
)


def test_reliable_execution_status_requires_superuser(api_context):
    client, _, _ = api_context

    response = client.get("/api/v1/admin/reliable-execution/status")

    assert response.status_code == 403


def test_superuser_status_reports_counts_without_sensitive_payload(api_context):
    client, db, user = api_context
    user.is_superuser = True
    db.add(
        LLMInvocation(
            request_id=uuid4(),
            idempotency_key="status-llm-unknown",
            use_case="message_draft",
            backend="omniroute",
            status=LLMInvocationStatus.UNKNOWN,
            input_hash="a" * 64,
            response_json={"content": "private generated response"},
        )
    )
    db.add(
        OutboxEvent(
            aggregate_type="generated_message",
            aggregate_id="status-event",
            event_type="send",
            business_key="status-outbox-dead",
            channel="email",
            payload_json={"to": "private@example.com", "body": "secret body"},
            payload_hash="b" * 64,
            status=OutboxStatus.DEAD_LETTER,
            available_at=datetime.utcnow() - timedelta(minutes=5),
        )
    )
    db.add(
        OutboxEvent(
            aggregate_type="generated_message",
            aggregate_id="expired-event",
            event_type="send",
            business_key="status-outbox-expired",
            channel="email",
            payload_json={"to": "another-private@example.com"},
            payload_hash="c" * 64,
            status=OutboxStatus.PROCESSING,
            available_at=datetime.utcnow() - timedelta(minutes=10),
            leased_by="lost-worker",
            lease_until=datetime.utcnow() - timedelta(seconds=1),
        )
    )
    db.commit()

    response = client.get("/api/v1/admin/reliable-execution/status")

    assert response.status_code == 200
    data = response.json()
    assert data["outbox_counts"]["dead_letter"] == 1
    assert data["outbox_counts"]["processing"] == 1
    assert data["llm_invocation_counts"]["unknown"] == 1
    assert data["expired_outbox_leases"] == 1
    assert data["oldest_pending_at"] is None
    assert "private@example.com" not in response.text
    assert "secret body" not in response.text
    assert "private generated response" not in response.text

    app.dependency_overrides.pop(
        get_reliable_execution_status_service,
        None,
    )
