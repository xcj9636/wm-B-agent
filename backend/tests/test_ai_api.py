from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.main import app
from app.services.ai_chat import get_ai_chat_service
from app.services.agent_concurrency import (
    ConcurrencyLimitExceeded,
    ConcurrencyUnavailable,
)
from app.services.ai_runtime import (
    AIRuntimeConfig,
    AIRuntimeProbe,
    get_ai_runtime_service,
)
from app.services.llm.contracts import LLMResponse, LLMUsage


class FakeRuntimeService:
    def __init__(self):
        self.updated = None

    def get_config(self):
        return AIRuntimeConfig(
            backend="omniroute",
            base_url="http://omniroute.test",
            allowed_providers=["approved-provider"],
            model_aliases={"message_draft": "draft-v1", "live_reply": "reply-v1"},
            timeout_seconds=60,
            source="runtime",
            version=4,
            api_key_configured=True,
            updated_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        )

    def update_config(self, update, updated_by_user_id):
        self.updated = (update, updated_by_user_id)
        return self.get_config().model_copy(update={"version": 5})

    async def probe(self):
        return AIRuntimeProbe(ready=True, reachable=True, models=["draft-v1", "reply-v1"], issues=[])

    async def list_models(self):
        return ["draft-v1", "reply-v1"]


class FakeChatService:
    def __init__(self):
        self.session_id = uuid4()

    def list_sessions(self, user_id):
        return []

    def create_session(self, user_id, title=None):
        return {
            "id": self.session_id,
            "title": title or "New conversation",
            "use_case": "live_reply",
            "created_at": datetime(2026, 8, 9, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 8, 9, tzinfo=timezone.utc),
            "messages": [],
        }

    def get_session(self, session_id, user_id):
        return self.create_session(user_id, "Export planning")

    async def complete(self, session_id, user_id, content, *, idempotency_key=None):
        return {
            "id": uuid4(),
            "session_id": session_id,
            "role": "assistant",
            "content": "Prioritize EU distributors.",
            "resolved_model": "reply-v1",
            "resolved_provider": "approved-provider",
            "usage": {"input_tokens": 5, "output_tokens": 4, "total_tokens": 9},
            "created_at": datetime(2026, 8, 9, tzinfo=timezone.utc),
        }

    async def stream(self, session_id, user_id, content, *, idempotency_key=None):
        yield {
            "id": 1,
            "event": "run.started",
            "data": {"run_id": str(uuid4())},
        }
        yield {"id": 2, "event": "delta", "data": {"delta": "Prioritize "}}
        yield {"id": 3, "event": "delta", "data": {"delta": "EU distributors."}}
        yield {"id": 4, "event": "done", "data": {"session_id": str(session_id)}}


class FailingChatService(FakeChatService):
    def __init__(self, error):
        super().__init__()
        self.error = error

    async def complete(self, session_id, user_id, content):
        raise self.error


def test_ai_runtime_config_is_admin_only_and_never_echoes_key(api_context):
    client, db, user = api_context
    runtime = FakeRuntimeService()
    app.dependency_overrides[get_ai_runtime_service] = lambda: runtime

    assert client.get("/api/v1/ai/config").status_code == 403
    user.is_superuser = True
    db.commit()

    response = client.get("/api/v1/ai/config")
    assert response.status_code == 200
    assert response.json()["version"] == 4
    assert "api_key" not in response.json()
    assert "secret" not in response.text.lower()


def test_admin_can_hot_apply_and_probe_runtime_config(api_context):
    client, db, user = api_context
    user.is_superuser = True
    db.commit()
    runtime = FakeRuntimeService()
    app.dependency_overrides[get_ai_runtime_service] = lambda: runtime

    payload = {
        "backend": "omniroute",
        "base_url": "http://omniroute.test",
        "allowed_providers": ["approved-provider"],
        "model_aliases": {"message_draft": "draft-v2", "live_reply": "reply-v2"},
        "timeout_seconds": 30,
        "api_key": "write-only-value",
    }
    updated = client.put("/api/v1/ai/config", json=payload)
    probed = client.post("/api/v1/ai/config/test")
    models = client.get("/api/v1/ai/models")

    assert updated.status_code == 200
    assert updated.json()["version"] == 5
    assert "write-only-value" not in updated.text
    assert runtime.updated[1] == user.id
    assert probed.json()["ready"] is True
    assert models.json() == {"models": ["draft-v1", "reply-v1"]}


def test_ai_chat_supports_session_completion_and_sse_without_browser_gateway_access(api_context):
    client, _, user = api_context
    chat = FakeChatService()
    app.dependency_overrides[get_ai_chat_service] = lambda: chat

    created = client.post("/api/v1/ai/chat/sessions", json={"title": "Export planning"})
    session_id = created.json()["id"]
    completed = client.post(
        f"/api/v1/ai/chat/sessions/{session_id}/messages",
        json={"content": "Which market should we enter first?"},
    )
    streamed = client.post(
        f"/api/v1/ai/chat/sessions/{session_id}/messages/stream",
        json={"content": "Give me the top priority."},
    )

    assert created.status_code == 201
    assert completed.status_code == 200
    assert completed.json()["resolved_provider"] == "approved-provider"
    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("text/event-stream")
    assert "id: 1" in streamed.text
    assert "event: run.started" in streamed.text
    assert "event: delta" in streamed.text
    assert "event: done" in streamed.text
    assert user.id


def test_agent_run_event_endpoint_replays_only_after_last_event_id(api_context):
    client, db, user = api_context
    now = datetime.utcnow()
    from app.services.agent_run_events import AgentRunEventService
    from app.services.agent_runs import AgentRunCommand, AgentRunService
    from app.services.agent_runtime.contracts import Sensitivity

    run, _ = AgentRunService(db).create(
        AgentRunCommand(
            idempotency_key=f"agent-run:api-events:{uuid4()}",
            org_id=uuid4(),
            user_id=user.id,
            use_case="live_reply",
            input={"content": "do not expose"},
            sensitivity=Sensitivity.INTERNAL,
            generation_epoch=1,
            deadline_at=now.replace(tzinfo=timezone.utc) + timedelta(minutes=5),
        )
    )
    run = AgentRunService(db).claim_one(
        run.id,
        worker_id="api-event-worker",
        now=now,
        lease_seconds=60,
    )
    event_log = AgentRunEventService(db)
    event_log.append(
        run.id,
        worker_id="api-event-worker",
        fencing_token=run.fencing_token,
        event_type="run.started",
        data={"run_id": str(run.id)},
        now=now,
    )
    event_log.append(
        run.id,
        worker_id="api-event-worker",
        fencing_token=run.fencing_token,
        event_type="message.delta",
        data={"delta": "safe replay"},
        now=now,
    )

    response = client.get(
        f"/api/v1/agent/runs/{run.id}/events",
        headers={"Last-Event-ID": "1", "Accept": "text/event-stream"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "id: 1" not in response.text
    assert "id: 2" in response.text
    assert "event: message.delta" in response.text
    assert "safe replay" in response.text


def test_ai_chat_returns_retryable_429_when_concurrency_budget_is_full(api_context):
    client, _, _ = api_context
    chat = FailingChatService(ConcurrencyLimitExceeded("user"))
    app.dependency_overrides[get_ai_chat_service] = lambda: chat

    response = client.post(
        f"/api/v1/ai/chat/sessions/{chat.session_id}/messages",
        json={"content": "Draft a distributor reply."},
    )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"
    assert response.json() == {"detail": "AI capacity is temporarily full"}
    assert "user" not in response.text


def test_ai_chat_returns_503_when_concurrency_coordination_is_unavailable(api_context):
    client, _, _ = api_context
    chat = FailingChatService(
        ConcurrencyUnavailable("redis://internal-host:6379 is unavailable")
    )
    app.dependency_overrides[get_ai_chat_service] = lambda: chat

    response = client.post(
        f"/api/v1/ai/chat/sessions/{chat.session_id}/messages",
        json={"content": "Draft a distributor reply."},
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.json() == {
        "detail": "AI capacity coordination is temporarily unavailable"
    }
    assert "internal-host" not in response.text
