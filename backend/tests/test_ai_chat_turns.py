from types import SimpleNamespace

import pytest

from app.models.database import AgentTurn, User
from app.services.ai_chat import AIChatService
from app.services.llm.contracts import (
    GatewayError,
    GatewayErrorKind,
    LLMResponse,
)


class ChatBackend:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.closed = False

    async def complete(self, request):
        if self.fail:
            raise GatewayError(
                GatewayErrorKind.UPSTREAM_UNAVAILABLE,
                "provider details",
                request_id=request.request_id,
                retryable=True,
            )
        return LLMResponse(request_id=request.request_id, content="safe response")

    async def aclose(self):
        self.closed = True


class ChatRuntime:
    def __init__(self, backend):
        self.backend = backend

    def get_config(self):
        return SimpleNamespace(backend="omniroute")

    def build_backend(self):
        return self.backend


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
    assert response.content == "safe response"
    assert turn.status == "completed"
    assert turn.sequence == 1
    assert turn.generation_epoch == 1
    assert turn.session.generation_epoch == 1
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
    assert turn.status == "failed"
    assert turn.completed_at is not None
    assert backend.closed is True
