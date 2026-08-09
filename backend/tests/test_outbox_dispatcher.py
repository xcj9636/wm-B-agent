from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models.database import OutboxEvent, OutboxStatus
from app.services.outbox import (
    DeliveryFailureKind,
    OutboxCommand,
    OutboxLeaseConflict,
    OutboxService,
)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def enqueue(service, key, *, available_at=None, max_attempts=3):
    event, _ = service.enqueue(
        OutboxCommand(
            aggregate_type="generated_message",
            aggregate_id=key,
            event_type="send",
            business_key=key,
            channel="email",
            payload={"to": "buyer@example.com", "body": "Hello"},
            available_at=available_at,
            max_attempts=max_attempts,
        )
    )
    return event


def test_claim_batch_leases_only_due_events(db_session):
    service = OutboxService(db_session)
    now = datetime(2026, 8, 9, 10, 0, 0)
    due = enqueue(service, "due", available_at=now)
    enqueue(service, "future", available_at=now + timedelta(minutes=1))

    claimed = service.claim_batch(
        worker_id="worker-a",
        now=now,
        limit=10,
        lease_seconds=30,
    )

    assert [event.id for event in claimed] == [due.id]
    assert due.status == OutboxStatus.PROCESSING
    assert due.leased_by == "worker-a"
    assert due.lease_until == now + timedelta(seconds=30)
    assert due.attempt_count == 1

    assert service.claim_batch(
        worker_id="worker-b",
        now=now,
        limit=10,
        lease_seconds=30,
    ) == []


def test_expired_lease_is_dead_lettered_instead_of_retried(db_session):
    service = OutboxService(db_session)
    started = datetime(2026, 8, 9, 10, 0, 0)
    event = enqueue(service, "uncertain-delivery", available_at=started)
    service.claim_batch(
        worker_id="crashed-worker",
        now=started,
        limit=1,
        lease_seconds=10,
    )

    claimed = service.claim_batch(
        worker_id="replacement-worker",
        now=started + timedelta(seconds=11),
        limit=1,
        lease_seconds=10,
    )

    assert claimed == []
    assert event.status == OutboxStatus.DEAD_LETTER
    assert event.last_error == "lease_expired_unknown_delivery_state"
    assert event.leased_by is None


def test_retryable_before_send_failure_uses_backoff(db_session):
    service = OutboxService(db_session)
    now = datetime(2026, 8, 9, 10, 0, 0)
    event = enqueue(service, "retryable", available_at=now)
    service.claim_batch(
        worker_id="worker-a",
        now=now,
        limit=1,
        lease_seconds=30,
    )

    service.mark_failure(
        event,
        worker_id="worker-a",
        kind=DeliveryFailureKind.RETRYABLE_BEFORE_SEND,
        error_code="provider_unavailable_before_send",
        now=now,
    )

    assert event.status == OutboxStatus.RETRY
    assert event.available_at == now + timedelta(seconds=30)
    assert event.lease_until is None
    assert event.leased_by is None


@pytest.mark.parametrize(
    "kind",
    [
        DeliveryFailureKind.PERMANENT,
        DeliveryFailureKind.UNKNOWN_AFTER_SEND,
    ],
)
def test_non_retryable_or_unknown_failure_goes_to_dead_letter(db_session, kind):
    service = OutboxService(db_session)
    now = datetime(2026, 8, 9, 10, 0, 0)
    event = enqueue(service, f"dead-{kind.value}", available_at=now)
    service.claim_batch(
        worker_id="worker-a",
        now=now,
        limit=1,
        lease_seconds=30,
    )

    service.mark_failure(
        event,
        worker_id="worker-a",
        kind=kind,
        error_code="delivery_state_not_safe_to_retry",
        now=now,
    )

    assert event.status == OutboxStatus.DEAD_LETTER
    assert event.last_error == "delivery_state_not_safe_to_retry"


def test_max_attempts_and_lease_owner_are_enforced(db_session):
    service = OutboxService(db_session)
    now = datetime(2026, 8, 9, 10, 0, 0)
    event = enqueue(service, "maxed", available_at=now, max_attempts=1)
    service.claim_batch(
        worker_id="worker-a",
        now=now,
        limit=1,
        lease_seconds=30,
    )

    with pytest.raises(OutboxLeaseConflict):
        service.mark_sent(
            event,
            worker_id="worker-b",
            external_message_id="provider-message-1",
            now=now,
        )

    service.mark_failure(
        event,
        worker_id="worker-a",
        kind=DeliveryFailureKind.RETRYABLE_BEFORE_SEND,
        error_code="not_sent",
        now=now,
    )
    assert event.status == OutboxStatus.DEAD_LETTER


def test_mark_sent_records_external_identity_and_clears_lease(db_session):
    service = OutboxService(db_session)
    now = datetime(2026, 8, 9, 10, 0, 0)
    event = enqueue(service, "sent", available_at=now)
    service.claim_batch(
        worker_id="worker-a",
        now=now,
        limit=1,
        lease_seconds=30,
    )

    service.mark_sent(
        event,
        worker_id="worker-a",
        external_message_id="provider-message-1",
        now=now + timedelta(seconds=2),
    )

    assert event.status == OutboxStatus.SENT
    assert event.external_message_id == "provider-message-1"
    assert event.sent_at == now + timedelta(seconds=2)
    assert event.leased_by is None
    assert event.lease_until is None
    assert db_session.query(OutboxEvent).count() == 1
