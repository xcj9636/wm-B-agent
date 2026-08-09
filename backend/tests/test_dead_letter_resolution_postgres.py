"""PostgreSQL-only concurrency coverage for dead-letter approvals."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import os
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.models.database import (
    OutboxEvent,
    OutboxResolutionApproval,
    OutboxResolutionRequest,
    OutboxResolutionStatus,
    OutboxStatus,
    User,
)
from app.services.dead_letter_resolution import (
    DeadLetterResolutionService,
    ResolutionApprovalCommand,
    ResolutionConflict,
)


TEST_DATABASE_URL = os.getenv("TEST_POSTGRES_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_POSTGRES_DATABASE_URL is required for row-lock coverage",
)


@pytest.fixture(scope="module")
def postgres_session_factory():
    url = make_url(TEST_DATABASE_URL)
    if not url.drivername.startswith("postgresql"):
        pytest.fail("Concurrency test requires PostgreSQL")
    if "test" not in (url.database or "").lower():
        pytest.fail("Refusing to mutate a database whose name lacks 'test'")

    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with engine.connect() as connection:
        revision = connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one()
    assert revision == "0004_outbox_resolution_approvals"

    yield session_factory
    engine.dispose()


def test_concurrent_second_approvals_execute_exactly_once(
    postgres_session_factory,
):
    unique = uuid.uuid4().hex
    user_ids: list[int] = []
    event_id: uuid.UUID | None = None

    try:
        with postgres_session_factory() as session:
            admins = [
                User(
                    username=f"concurrency-admin-{position}-{unique}",
                    email=f"concurrency-admin-{position}-{unique}@example.com",
                    hashed_password="unused",
                    is_active=True,
                    is_superuser=True,
                )
                for position in range(3)
            ]
            session.add_all(admins)
            session.flush()
            user_ids = [admin.id for admin in admins]
            event = OutboxEvent(
                aggregate_type="concurrency_test",
                aggregate_id=unique,
                event_type="send",
                business_key=f"concurrency-{unique}",
                channel="email",
                payload_json={"test_marker": unique},
                payload_hash="c" * 64,
                status=OutboxStatus.DEAD_LETTER,
                attempt_count=1,
                max_attempts=5,
                last_error="provider_response_lost",
            )
            session.add(event)
            session.commit()
            event_id = event.id

        command = ResolutionApprovalCommand(
            action="confirmed_sent",
            evidence_reference=f"provider-audit/{unique}",
            external_message_id=f"provider-message-{unique}",
        )
        with postgres_session_factory() as session:
            first = DeadLetterResolutionService(session).approve(
                event_id=event_id,
                admin_user_id=user_ids[0],
                command=command,
            )
            session.commit()
        assert first.status == OutboxResolutionStatus.PENDING

        start_together = Barrier(2)

        def compete(admin_user_id: int) -> str:
            with postgres_session_factory() as session:
                start_together.wait(timeout=10)
                try:
                    result = DeadLetterResolutionService(session).approve(
                        event_id=event_id,
                        admin_user_id=admin_user_id,
                        command=command,
                    )
                    session.commit()
                    return result.status.value
                except ResolutionConflict:
                    session.rollback()
                    return "conflict"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(compete, user_ids[1:]))

        assert sorted(outcomes) == ["conflict", "executed"]
        with postgres_session_factory() as session:
            event = session.get(OutboxEvent, event_id)
            resolution = (
                session.query(OutboxResolutionRequest)
                .filter(OutboxResolutionRequest.event_id == event_id)
                .one()
            )
            approval_count = (
                session.query(OutboxResolutionApproval)
                .filter(
                    OutboxResolutionApproval.request_id == resolution.id
                )
                .count()
            )

            assert event is not None
            assert event.status == OutboxStatus.SENT
            assert resolution.status == OutboxResolutionStatus.EXECUTED
            assert approval_count == 2
    finally:
        if user_ids:
            with postgres_session_factory() as session:
                if event_id is not None:
                    resolution_ids = [
                        row[0]
                        for row in session.query(OutboxResolutionRequest.id)
                        .filter(
                            OutboxResolutionRequest.event_id == event_id
                        )
                        .all()
                    ]
                    if resolution_ids:
                        session.query(OutboxResolutionApproval).filter(
                            OutboxResolutionApproval.request_id.in_(
                                resolution_ids
                            )
                        ).delete(synchronize_session=False)
                    session.query(OutboxResolutionRequest).filter(
                        OutboxResolutionRequest.event_id == event_id
                    ).delete(synchronize_session=False)
                    session.query(OutboxEvent).filter(
                        OutboxEvent.id == event_id
                    ).delete(synchronize_session=False)
                session.query(User).filter(User.id.in_(user_ids)).delete(
                    synchronize_session=False
                )
                session.commit()
