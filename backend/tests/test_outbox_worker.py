import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models.database import OutboxEvent, OutboxStatus
from app.services.outbox import OutboxCommand, OutboxService
from app.services.outbox_delivery import (
    DeliveryResult,
    OutboxDeliveryRouter,
)
from app.tasks import task_functions


@pytest.fixture
def session_factory(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(task_functions, "SessionLocal", factory)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def enqueue(factory, business_key):
    session = factory()
    try:
        event, _ = OutboxService(session).enqueue(
            OutboxCommand(
                aggregate_type="generated_message",
                aggregate_id=business_key,
                event_type="send",
                business_key=business_key,
                channel="email",
                payload={
                    "to": "buyer@example.com",
                    "subject": "Hello",
                    "body": "Introduction",
                },
            )
        )
        session.commit()
        return event.id
    finally:
        session.close()


class FakeRouter:
    def __init__(self, result):
        self.result = result
        self.seen = []

    async def deliver(self, event):
        self.seen.append(event.id)
        return self.result


def test_worker_commits_successful_delivery_identity(
    session_factory,
    monkeypatch,
):
    event_id = enqueue(session_factory, "worker-success")
    router = FakeRouter(
        DeliveryResult.sent(external_message_id="provider-message-7")
    )
    monkeypatch.setattr(
        task_functions,
        "get_outbox_delivery_router",
        lambda: router,
    )

    result = task_functions.dispatch_outbox_task.run(
        worker_id="worker-test",
        batch_size=10,
    )

    session = session_factory()
    try:
        event = session.get(OutboxEvent, event_id)
        assert result == {
            "claimed": 1,
            "sent": 1,
            "retry": 0,
            "dead_letter": 0,
            "expired_dead_letter": 0,
        }
        assert event.status == OutboxStatus.SENT
        assert event.external_message_id == "provider-message-7"
        assert router.seen == [event_id]
    finally:
        session.close()


def test_worker_never_retries_unknown_delivery_state(
    session_factory,
    monkeypatch,
):
    event_id = enqueue(session_factory, "worker-unknown")
    router = FakeRouter(
        DeliveryResult.unknown_after_send("provider_response_lost")
    )
    monkeypatch.setattr(
        task_functions,
        "get_outbox_delivery_router",
        lambda: router,
    )

    result = task_functions.dispatch_outbox_task.run(
        worker_id="worker-test",
        batch_size=10,
    )

    session = session_factory()
    try:
        event = session.get(OutboxEvent, event_id)
        assert result["dead_letter"] == 1
        assert result["retry"] == 0
        assert event.status == OutboxStatus.DEAD_LETTER
        assert event.last_error == "provider_response_lost"
    finally:
        session.close()


class FakeEmailService:
    def __init__(self, result):
        self.result = result

    async def send_email(self, **kwargs):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.asyncio
async def test_delivery_router_treats_smtp_failure_as_unknown_after_send():
    router = OutboxDeliveryRouter(
        email_service=FakeEmailService(
            {"success": False, "error": "sensitive SMTP detail"}
        )
    )
    event = OutboxEvent(
        channel="email",
        payload_json={
            "to": "buyer@example.com",
            "subject": "Hello",
            "body": "Introduction",
        },
    )

    result = await router.deliver(event)

    assert result.success is False
    assert result.failure_kind.value == "unknown_after_send"
    assert result.error_code == "email_delivery_failed"
    assert "sensitive" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_delivery_router_retries_only_connect_failure_before_send():
    request = httpx.Request("POST", "https://graph.facebook.com/messages")

    class FailingWhatsAppService:
        async def send_message(self, **kwargs):
            raise httpx.ConnectError("connection refused", request=request)

    router = OutboxDeliveryRouter(
        whatsapp_service=FailingWhatsAppService(),
    )
    event = OutboxEvent(
        channel="whatsapp",
        payload_json={"to": "+1234567890", "text": "Introduction"},
    )

    result = await router.deliver(event)

    assert result.success is False
    assert result.failure_kind.value == "retryable_before_send"
    assert result.error_code == "provider_connect_failed"
