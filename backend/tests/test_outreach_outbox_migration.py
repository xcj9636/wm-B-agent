from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.context import ExecutionContext
from app.db import Base
from app.models.database import (
    Account,
    Customer,
    OutreachLog,
    OutreachStatus,
    OutboxEvent,
    OutboxStatus,
)
from app.services.outbox import OutboxCommand, OutboxService
from app.services.outbox_delivery import DeliveryResult
from app.skills import skill_auto_sender
from app.skills.skill_auto_sender import AutoSenderSkill
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
    monkeypatch.setattr(
        skill_auto_sender,
        "SessionLocal",
        factory,
        raising=False,
    )
    try:
        yield factory
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def create_customer_and_account(factory):
    session = factory()
    try:
        customer = Customer(
            username="buyer-1",
            platform="email",
            email="buyer@example.com",
            whatsapp="+1234567890",
        )
        account = Account(
            account_type="email",
            name="central-email",
            email="sender@example.com",
            credentials_json={"smtp_password": "must-not-enter-outbox"},
            is_active=True,
            today_sent=0,
            daily_limit=100,
        )
        session.add_all([customer, account])
        session.commit()
        return customer.id, account.id
    finally:
        session.close()


def test_schedule_task_queues_once_without_network_or_early_quota_update(
    session_factory,
    monkeypatch,
):
    customer_id, account_id = create_customer_and_account(session_factory)

    schedule = {"idempotency_key": "campaign-2026-08", "interval_min": 0, "interval_max": 0}

    first = task_functions.schedule_outreach_task.run(
        customer_ids=[customer_id],
        channel="email",
        template_id="intro-v1",
        schedule_config=schedule,
    )
    second = task_functions.schedule_outreach_task.run(
        customer_ids=[customer_id],
        channel="email",
        template_id="intro-v1",
        schedule_config=schedule,
    )

    session = session_factory()
    try:
        logs = session.query(OutreachLog).all()
        events = session.query(OutboxEvent).all()
        account = session.get(Account, account_id)
        assert first["results"][0]["status"] == "queued"
        assert second["results"][0]["status"] == "queued"
        assert len(logs) == 1
        assert logs[0].status == OutreachStatus.PENDING
        assert len(events) == 1
        assert events[0].status == OutboxStatus.PENDING
        assert events[0].aggregate_type == "outreach_log"
        assert "smtp_password" not in events[0].payload_json
        assert account.today_sent == 0
    finally:
        session.close()


@pytest.mark.asyncio
async def test_auto_sender_queues_instead_of_opening_smtp(
    session_factory,
    monkeypatch,
):
    customer_id, account_id = create_customer_and_account(session_factory)
    context = ExecutionContext(
        workflow_id="workflow-1",
        execution_id="execution-1",
        input_data={
            "customers": [
                {
                    "id": customer_id,
                    "username": "buyer-1",
                    "email": "buyer@example.com",
                }
            ],
            "messages": {"subject": "Hello", "body": "Introduction"},
            "channel": "email",
            "accounts": [{"id": account_id, "account_type": "email"}],
            "send_immediately": True,
        },
    )

    result = await AutoSenderSkill(
        {"dry_run": False, "enable_account_rotation": True}
    ).execute(context)

    session = session_factory()
    try:
        assert result["results"][0]["status"] == "queued"
        assert result["success_count"] == 0
        assert result["queued_count"] == 1
        assert context.metrics["messages_queued"] == 1
        assert session.query(OutreachLog).count() == 1
        assert session.query(OutboxEvent).count() == 1
    finally:
        session.close()


class SuccessfulRouter:
    async def deliver(self, event):
        return DeliveryResult.sent(external_message_id="provider-42")


def test_worker_marks_outreach_sent_and_consumes_quota_after_delivery(
    session_factory,
    monkeypatch,
):
    customer_id, account_id = create_customer_and_account(session_factory)
    session = session_factory()
    try:
        log = OutreachLog(
            customer_id=customer_id,
            channel="email",
            status=OutreachStatus.PENDING,
            subject="Hello",
            content="Introduction",
            account_id=account_id,
        )
        session.add(log)
        session.flush()
        OutboxService(session).enqueue(
            OutboxCommand(
                aggregate_type="outreach_log",
                aggregate_id=str(log.id),
                event_type="send",
                business_key="worker-outreach-success",
                channel="email",
                payload={
                    "to": "buyer@example.com",
                    "subject": "Hello",
                    "body": "Introduction",
                },
            )
        )
        log_id = log.id
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr(
        task_functions,
        "get_outbox_delivery_router",
        lambda: SuccessfulRouter(),
    )
    task_functions.dispatch_outbox_task.run(worker_id="worker-1", batch_size=10)

    session = session_factory()
    try:
        log = session.get(OutreachLog, log_id)
        account = session.get(Account, account_id)
        assert log.status == OutreachStatus.SENT
        assert log.message_id == "provider-42"
        assert isinstance(log.sent_at, datetime)
        assert account.today_sent == 1
    finally:
        session.close()


def test_schedule_task_reserves_daily_quota_before_queuing(
    session_factory,
):
    first_customer_id, account_id = create_customer_and_account(session_factory)
    session = session_factory()
    try:
        second_customer = Customer(
            username="buyer-2",
            platform="email",
            email="buyer-2@example.com",
        )
        account = session.get(Account, account_id)
        account.daily_limit = 1
        session.add(second_customer)
        session.commit()
        second_customer_id = second_customer.id
    finally:
        session.close()

    result = task_functions.schedule_outreach_task.run(
        customer_ids=[first_customer_id, second_customer_id],
        channel="email",
        template_id="intro-v1",
        schedule_config={"idempotency_key": "quota-campaign"},
    )

    session = session_factory()
    try:
        assert [item["status"] for item in result["results"]] == [
            "queued",
            "failed",
        ]
        assert result["results"][1]["error"] == "account_daily_limit_reached"
        assert session.query(OutreachLog).count() == 1
        assert session.query(OutboxEvent).count() == 1
    finally:
        session.close()


class UnknownRouter:
    async def deliver(self, event):
        return DeliveryResult.unknown_after_send("provider_response_lost")


def test_dead_letter_marks_outreach_failed_without_consuming_quota(
    session_factory,
    monkeypatch,
):
    customer_id, account_id = create_customer_and_account(session_factory)
    session = session_factory()
    try:
        log = OutreachLog(
            customer_id=customer_id,
            channel="email",
            status=OutreachStatus.PENDING,
            subject="Hello",
            content="Introduction",
            account_id=account_id,
        )
        session.add(log)
        session.flush()
        OutboxService(session).enqueue(
            OutboxCommand(
                aggregate_type="outreach_log",
                aggregate_id=str(log.id),
                event_type="send",
                business_key="worker-outreach-unknown",
                channel="email",
                payload={
                    "to": "buyer@example.com",
                    "subject": "Hello",
                    "body": "Introduction",
                },
            )
        )
        log_id = log.id
        session.commit()
    finally:
        session.close()


@pytest.mark.asyncio
async def test_auto_sender_sanitizes_queue_failures(
    session_factory,
    monkeypatch,
):
    customer_id, account_id = create_customer_and_account(session_factory)
    private_detail = "private message body and buyer@example.com"

    class FailingQueue:
        def __init__(self, session):
            pass

        def queue(self, command):
            raise RuntimeError(private_detail)

    monkeypatch.setattr(skill_auto_sender, "OutreachQueueService", FailingQueue)
    context = ExecutionContext(
        workflow_id="workflow-private",
        execution_id="execution-private",
        input_data={
            "customers": [
                {
                    "id": customer_id,
                    "username": "buyer-1",
                    "email": "buyer@example.com",
                }
            ],
            "messages": {
                "subject": "Private",
                "body": "private message body",
            },
            "channel": "email",
            "accounts": [{"id": account_id, "account_type": "email"}],
            "send_immediately": True,
        },
    )

    result = await AutoSenderSkill(
        {"dry_run": False, "enable_account_rotation": True}
    ).execute(context)

    assert result["results"][0]["status"] == "failed"
    assert result["results"][0]["error"] == "outreach_queue_failed"
    assert private_detail not in str(result)


def test_schedule_task_sanitizes_queue_failures(
    session_factory,
    monkeypatch,
    caplog,
):
    customer_id, _ = create_customer_and_account(session_factory)
    private_detail = "private message body and buyer@example.com"

    class FailingQueue:
        def __init__(self, session):
            pass

        def queue(self, command):
            raise RuntimeError(private_detail)

    monkeypatch.setattr(task_functions, "OutreachQueueService", FailingQueue)
    result = task_functions.schedule_outreach_task.run(
        customer_ids=[customer_id],
        channel="email",
        template_id="intro-v1",
        schedule_config={"idempotency_key": "private-campaign"},
    )

    assert result["results"][0]["status"] == "failed"
    assert result["results"][0]["error"] == "outreach_queue_failed"
    assert private_detail not in str(result)
    assert private_detail not in caplog.text

    monkeypatch.setattr(
        task_functions,
        "get_outbox_delivery_router",
        lambda: UnknownRouter(),
    )
    task_functions.dispatch_outbox_task.run(worker_id="worker-1", batch_size=10)

    session = session_factory()
    try:
        log = session.get(OutreachLog, log_id)
        account = session.get(Account, account_id)
        assert log.status == OutreachStatus.FAILED
        assert log.error_msg == "provider_response_lost"
        assert log.sent_at is None
        assert account.today_sent == 0
    finally:
        session.close()
