from types import SimpleNamespace

import pytest

from app.models.database import AIChatMessage, AgentRun, AgentTurn, User
from app.services.ai_chat import AIChatService
from app.services.data_policy import DataPolicyUnavailable
from app.services.idempotency import IdempotencyConflict
from app.services.llm.contracts import (
    GatewayError,
    GatewayErrorKind,
    LLMResponse,
    LLMStreamChunk,
)


class ChatBackend:
    def __init__(self, *, fail=False, content="safe response"):
        self.fail = fail
        self.content = content
        self.closed = False
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        if self.fail:
            raise GatewayError(
                GatewayErrorKind.UPSTREAM_UNAVAILABLE,
                "provider details",
                request_id=request.request_id,
                retryable=True,
            )
        return LLMResponse(request_id=request.request_id, content=self.content)

    async def aclose(self):
        self.closed = True


class ChatRuntime:
    def __init__(self, backend):
        self.backend = backend

    def get_config(self):
        return SimpleNamespace(backend="omniroute")

    def build_backend(self):
        return self.backend


class BrokenRuntime(ChatRuntime):
    def build_backend(self):
        raise RuntimeError("backend construction failed")


class StreamingChatBackend(ChatBackend):
    async def stream(self, request):
        self.requests.append(request)
        yield LLMStreamChunk(request_id=request.request_id, delta="Contact ")
        yield LLMStreamChunk(request_id=request.request_id, delta="[[EMAIL_1]]")


def user_and_session(db_session, backend):
    user = User(
        username="turn-user",
        email="turn-user@example.com",
        hashed_password="unused",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    service = AIChatService(db_session, ChatRuntime(backend))
    session = service.create_session(user.id, "Fenced chat")
    return user, session, service


@pytest.mark.asyncio
async def test_ai_chat_completion_atomically_completes_a_fenced_turn(db_session):
    backend = ChatBackend()
    user, session, service = user_and_session(db_session, backend)

    response = await service.complete(
        session.id,
        user.id,
        "Hello",
        idempotency_key="chat-complete-turn-1",
    )

    turn = db_session.query(AgentTurn).one()
    run = db_session.query(AgentRun).one()
    assert response.content == "safe response"
    assert turn.status == "completed"
    assert turn.sequence == 1
    assert turn.generation_epoch == 1
    assert turn.session.generation_epoch == 1
    assert run.status == "completed"
    assert run.session_id == session.id
    assert run.turn_id == turn.id
    assert run.generation_epoch == turn.generation_epoch
    assert "Hello" not in repr(run.__dict__)
    assert backend.closed is True


@pytest.mark.asyncio
async def test_ai_chat_failure_leaves_a_durable_failed_turn(db_session):
    backend = ChatBackend(fail=True)
    user, session, service = user_and_session(db_session, backend)

    with pytest.raises(GatewayError):
        await service.complete(
            session.id,
            user.id,
            "Hello",
            idempotency_key="chat-failed-turn-1",
        )

    turn = db_session.query(AgentTurn).one()
    run = db_session.query(AgentRun).one()
    assert turn.status == "failed"
    assert turn.completed_at is not None
    assert run.status == "failed"
    assert run.error_code == "agent_execution_failed"
    assert run.completed_at is not None
    assert backend.closed is True


@pytest.mark.asyncio
async def test_ai_chat_idempotent_replay_returns_persisted_answer_without_new_llm_call(
    db_session,
):
    backend = ChatBackend()
    backend.call_count = 0
    original_complete = backend.complete

    async def counted_complete(request):
        backend.call_count += 1
        return await original_complete(request)

    backend.complete = counted_complete
    user, session, service = user_and_session(db_session, backend)

    first = await service.complete(
        session.id,
        user.id,
        "Hello",
        idempotency_key="chat-idempotent-replay",
    )
    second = await service.complete(
        session.id,
        user.id,
        "Hello",
        idempotency_key="chat-idempotent-replay",
    )

    assert second.id == first.id
    assert backend.call_count == 1
    assert db_session.query(AgentTurn).count() == 1
    assert db_session.query(AgentRun).count() == 1


@pytest.mark.asyncio
async def test_ai_chat_idempotency_key_rejects_changed_input(db_session):
    backend = ChatBackend()
    user, session, service = user_and_session(db_session, backend)
    await service.complete(
        session.id,
        user.id,
        "First message",
        idempotency_key="chat-input-conflict",
    )

    with pytest.raises(IdempotencyConflict):
        await service.complete(
            session.id,
            user.id,
            "Changed message",
            idempotency_key="chat-input-conflict",
        )


@pytest.mark.asyncio
async def test_blank_chat_input_does_not_create_a_running_turn(db_session):
    backend = ChatBackend()
    user, session, service = user_and_session(db_session, backend)

    with pytest.raises(ValueError, match="cannot be empty"):
        await service.complete(
            session.id,
            user.id,
            "   ",
            idempotency_key="chat-blank-input",
        )

    assert db_session.query(AgentTurn).count() == 0


@pytest.mark.asyncio
async def test_backend_construction_failure_closes_the_durable_turn(db_session):
    backend = ChatBackend()
    user, session, service = user_and_session(db_session, backend)
    service._runtime = BrokenRuntime(backend)

    with pytest.raises(RuntimeError, match="construction failed"):
        await service.complete(
            session.id,
            user.id,
            "Hello",
            idempotency_key="chat-backend-construction-failure",
        )

    turn = db_session.query(AgentTurn).one()
    assert turn.status == "failed"
    assert turn.completed_at is not None


@pytest.mark.asyncio
async def test_chat_redacts_pii_before_llm_and_rehydrates_final_response(db_session):
    backend = ChatBackend(content="Contact [[EMAIL_1]] after review.")
    user, session, service = user_and_session(db_session, backend)

    response = await service.complete(
        session.id,
        user.id,
        "Please contact buyer@example.com",
        idempotency_key="chat-redacted-pii",
    )

    serialized = backend.requests[0].model_dump_json()
    assert "buyer@example.com" not in serialized
    assert "[[EMAIL_1]]" in serialized
    assert response.content == "Contact buyer@example.com after review."


@pytest.mark.asyncio
async def test_restricted_secret_is_rejected_before_message_or_llm_persistence(db_session):
    backend = ChatBackend()
    user, session, service = user_and_session(db_session, backend)

    with pytest.raises(DataPolicyUnavailable):
        await service.complete(
            session.id,
            user.id,
            "Use token sk-abcdefghijklmnopqrstuvwxyz123456",
            idempotency_key="chat-restricted-secret",
        )

    assert backend.requests == []
    assert db_session.query(AIChatMessage).count() == 0
    assert db_session.query(AgentTurn).one().status == "failed"
    run = db_session.query(AgentRun).one()
    assert run.status == "failed"
    assert run.sensitivity == "restricted"
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in repr(run.__dict__)


@pytest.mark.asyncio
async def test_stream_redacts_provider_input_and_rehydrates_done_snapshot(db_session):
    backend = StreamingChatBackend()
    user, session, service = user_and_session(db_session, backend)

    events = [
        event
        async for event in service.stream(
            session.id,
            user.id,
            "Email buyer@example.com",
            idempotency_key="chat-stream-redacted-pii",
        )
    ]

    assert "buyer@example.com" not in backend.requests[0].model_dump_json()
    assert "[[EMAIL_1]]" in "".join(
        event["data"]["delta"] for event in events if event["event"] == "delta"
    )
    assert events[-1]["event"] == "done"
    assert events[-1]["data"]["content"] == "Contact buyer@example.com"
    assert db_session.query(AgentRun).one().status == "completed"


@pytest.mark.asyncio
async def test_chat_never_rehydrates_historical_pii_placeholders(db_session):
    backend = ChatBackend(content="Acknowledged.")
    user, session, service = user_and_session(db_session, backend)
    await service.complete(
        session.id,
        user.id,
        "Previous buyer was old@example.com",
        idempotency_key="chat-historical-pii-first",
    )
    backend.content = "Current [[EMAIL_2]], historical [[EMAIL_1]]."

    response = await service.complete(
        session.id,
        user.id,
        "Current buyer is new@example.com",
        idempotency_key="chat-historical-pii-second",
    )

    assert response.content == "Current new@example.com, historical [[EMAIL_1]]."
    assert "old@example.com" not in response.content
