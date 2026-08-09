import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models.database import (
    LLMInvocation,
    LLMInvocationStatus,
    OutboxEvent,
    OutboxStatus,
)
from app.services.idempotency import IdempotencyConflict
from app.services.llm.audit import InvocationAuditService
from app.services.llm.contracts import (
    LLMRequest,
    LLMResponse,
    LLMUsage,
    LLMUseCase,
)
from app.services.outbox import OutboxCommand, OutboxService


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


def llm_request(content="Draft a short introduction"):
    return LLMRequest(
        use_case=LLMUseCase.MESSAGE_DRAFT,
        messages=[{"role": "user", "content": content}],
    )


def test_invocation_start_is_idempotent_without_storing_the_prompt(db_session):
    service = InvocationAuditService(db_session)
    request = llm_request()

    first, first_created = service.start(
        idempotency_key="workflow-42:message-draft:customer-7",
        request=request,
        backend="omniroute",
    )
    second, second_created = service.start(
        idempotency_key="workflow-42:message-draft:customer-7",
        request=request,
        backend="omniroute",
    )

    assert first_created is True
    assert second_created is False
    assert second.id == first.id
    assert first.status == LLMInvocationStatus.PENDING
    assert len(first.input_hash) == 64
    assert "Draft a short introduction" not in repr(first.__dict__)
    assert db_session.query(LLMInvocation).count() == 1


def test_invocation_rejects_idempotency_key_reuse_for_different_input(db_session):
    service = InvocationAuditService(db_session)
    service.start(
        idempotency_key="stable-business-key",
        request=llm_request("first payload"),
        backend="omniroute",
    )

    with pytest.raises(IdempotencyConflict):
        service.start(
            idempotency_key="stable-business-key",
            request=llm_request("different payload"),
            backend="omniroute",
        )


def test_invocation_success_records_a_single_provider_attempt(db_session):
    service = InvocationAuditService(db_session)
    invocation, _ = service.start(
        idempotency_key="draft-success",
        request=llm_request(),
        backend="omniroute",
    )
    response = LLMResponse(
        request_id=invocation.request_id,
        content="Hello from B-agent",
        gateway_request_id="gw-request-1",
        resolved_provider="openai",
        resolved_model="gpt-approved",
        usage=LLMUsage(
            input_tokens=12,
            output_tokens=4,
            total_tokens=16,
            cost=0.002,
            cost_status="actual",
        ),
    )

    first_attempt = service.succeed(invocation, response)
    second_attempt = service.succeed(invocation, response)

    assert invocation.status == LLMInvocationStatus.SUCCEEDED
    assert invocation.response_json["content"] == "Hello from B-agent"
    assert first_attempt.id == second_attempt.id
    assert first_attempt.attempt_number == 1
    assert first_attempt.provider == "openai"
    assert first_attempt.total_tokens == 16
    assert db_session.query(type(first_attempt)).count() == 1


def outbox_command(**overrides):
    values = {
        "aggregate_type": "generated_message",
        "aggregate_id": "message-99",
        "event_type": "send",
        "business_key": "campaign-5:customer-7:initial",
        "channel": "email",
        "payload": {
            "to": "buyer@example.com",
            "subject": "Introduction",
            "body": "Hello",
        },
    }
    values.update(overrides)
    return OutboxCommand(**values)


def test_outbox_enqueue_is_transactional_and_idempotent(db_session):
    service = OutboxService(db_session)

    first, first_created = service.enqueue(outbox_command())
    second, second_created = service.enqueue(outbox_command())

    assert first_created is True
    assert second_created is False
    assert second.id == first.id
    assert first.status == OutboxStatus.PENDING
    assert len(first.payload_hash) == 64
    assert db_session.query(OutboxEvent).count() == 1

    db_session.rollback()
    assert db_session.query(OutboxEvent).count() == 0


def test_outbox_rejects_changed_payload_under_the_same_business_key(db_session):
    service = OutboxService(db_session)
    service.enqueue(outbox_command())

    with pytest.raises(IdempotencyConflict):
        service.enqueue(outbox_command(payload={"body": "changed"}))
