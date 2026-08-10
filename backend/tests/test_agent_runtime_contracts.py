import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.database import (
    LLMAttempt,
    LLMAttemptStatus,
    LLMInvocation,
    LLMInvocationStatus,
)
from app.services.agent_runtime.contracts import (
    AgentIngressRequest,
    AgentRequest,
    AgentResult,
    AgentUseCase,
    ExecutionPrincipal,
    Sensitivity,
    derive_sensitivity,
)
from app.services.agent_runtime.runtime import AgentRuntime
from app.services.llm.contracts import (
    GatewayError,
    GatewayErrorKind,
    LLMRequest,
    LLMResponse,
    LLMStreamChunk,
    LLMUsage,
    LLMUseCase,
)
from app.services.llm.instrumented import SessionInvocationAuditSink
from app.services.llm.audit import InvocationAuditService
from app.services.llm.service import LLMService


def principal():
    return ExecutionPrincipal(
        org_id=uuid4(),
        user_id=42,
        roles={"sales"},
        entitlements_hash="e" * 64,
        authn_context="jwt:mfa",
    )


def agent_request(**overrides):
    values = {
        "idempotency_key": "agent:test:turn-1",
        "principal": principal(),
        "session_id": uuid4(),
        "turn_id": uuid4(),
        "use_case": AgentUseCase.LIVE_REPLY,
        "locale": "zh-CN",
        "input": {"message": "Which market should we enter?"},
        "sensitivity": Sensitivity.INTERNAL,
        "deadline_at": datetime.now(timezone.utc) + timedelta(seconds=30),
        "stream": True,
    }
    values.update(overrides)
    return AgentRequest(**values)


def test_external_agent_request_cannot_assert_identity_or_lower_sensitivity():
    with pytest.raises(ValidationError):
        AgentIngressRequest.model_validate(
            {
                "idempotency_key": "agent:test:turn-1",
                "session_id": str(uuid4()),
                "use_case": "live_reply",
                "locale": "zh-CN",
                "input": {"message": "hello"},
                "org_id": str(uuid4()),
                "user_id": 1,
                "sensitivity": "public",
            }
        )


def test_server_derived_sensitivity_can_only_move_upward():
    assert derive_sensitivity(
        Sensitivity.PUBLIC,
        Sensitivity.CONFIDENTIAL,
        Sensitivity.INTERNAL,
    ) == Sensitivity.CONFIDENTIAL


def test_internal_agent_request_rejects_naive_or_expired_deadlines():
    with pytest.raises(ValidationError):
        agent_request(deadline_at=datetime.utcnow() + timedelta(seconds=30))
    with pytest.raises(ValidationError):
        agent_request(
            deadline_at=datetime.now(timezone.utc) - timedelta(milliseconds=1)
        )


class SuccessfulExecutor:
    async def execute(self, request):
        return AgentResult(
            content="Prioritize verified EU distributor evidence.",
            metadata={"mode": "adapter"},
        )


class FailingExecutor:
    async def execute(self, request):
        raise RuntimeError("secret provider stack detail")


@pytest.mark.asyncio
async def test_agent_runtime_emits_ordered_stable_events():
    events = [event async for event in AgentRuntime(SuccessfulExecutor()).run(agent_request())]

    assert [event.type for event in events] == ["run.started", "run.completed"]
    assert [event.sequence for event in events] == [1, 2]
    assert events[0].run_id == events[1].run_id
    assert events[1].payload["content"].startswith("Prioritize verified")


@pytest.mark.asyncio
async def test_agent_runtime_failure_event_does_not_expose_exception_text():
    events = [event async for event in AgentRuntime(FailingExecutor()).run(agent_request())]

    assert [event.type for event in events] == ["run.started", "run.failed"]
    assert events[-1].payload == {"code": "AGENT_EXECUTION_FAILED"}
    assert "secret" not in events[-1].model_dump_json()


class SuccessfulBackend:
    async def complete(self, request: LLMRequest) -> LLMResponse:
        await asyncio.sleep(0.01)
        return LLMResponse(
            request_id=request.request_id,
            content="audited response",
            resolved_provider="approved-provider",
            resolved_model="reply-v1",
            usage=LLMUsage(input_tokens=5, output_tokens=3),
        )


class FailingBackend:
    async def complete(self, request: LLMRequest) -> LLMResponse:
        raise GatewayError(
            GatewayErrorKind.RATE_LIMIT,
            "provider detail must not be persisted",
            request_id=request.request_id,
            retryable=True,
        )


class CapturingBackend:
    def __init__(self):
        self.requests = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(
            request_id=request.request_id,
            content="recovered response",
        )


@pytest.mark.asyncio
async def test_llm_service_centrally_audits_success_without_raw_prompt(db_session):
    service = LLMService(
        SuccessfulBackend(),
        audit_sink=SessionInvocationAuditSink(db_session),
        backend_name="omniroute",
    )

    response = await service.complete(
        LLMUseCase.LIVE_REPLY,
        [{"role": "user", "content": "private buyer request"}],
        idempotency_key="conversation-1:turn-1:reply",
    )

    invocation = db_session.query(LLMInvocation).one()
    attempt = db_session.query(LLMAttempt).one()
    assert response.content == "audited response"
    assert invocation.status == LLMInvocationStatus.SUCCEEDED
    assert attempt.status == LLMAttemptStatus.SUCCEEDED
    assert attempt.provider == "approved-provider"
    assert attempt.latency_ms >= 5
    assert attempt.ttft_ms is None
    assert "private buyer request" not in repr(invocation.__dict__)


class SuccessfulStreamingBackend:
    async def stream(self, request: LLMRequest):
        await asyncio.sleep(0.01)
        yield LLMStreamChunk(request_id=request.request_id, delta="first ")
        await asyncio.sleep(0.01)
        yield LLMStreamChunk(request_id=request.request_id, delta="second")


@pytest.mark.asyncio
async def test_llm_service_audits_stream_ttft_and_total_latency(db_session):
    service = LLMService(
        SuccessfulStreamingBackend(),
        audit_sink=SessionInvocationAuditSink(db_session),
        backend_name="omniroute",
    )

    chunks = [
        chunk
        async for chunk in service.stream(
            LLMUseCase.LIVE_REPLY,
            [{"role": "user", "content": "measure this response"}],
            idempotency_key="conversation-1:turn-stream:reply",
        )
    ]

    attempt = db_session.query(LLMAttempt).one()
    assert "".join(chunk.delta for chunk in chunks) == "first second"
    assert attempt.ttft_ms >= 5
    assert attempt.latency_ms >= attempt.ttft_ms


@pytest.mark.asyncio
async def test_llm_service_centrally_audits_normalized_failure(db_session):
    service = LLMService(
        FailingBackend(),
        audit_sink=SessionInvocationAuditSink(db_session),
        backend_name="omniroute",
    )

    with pytest.raises(GatewayError):
        await service.complete(
            LLMUseCase.LIVE_REPLY,
            [{"role": "user", "content": "rate limited request"}],
            idempotency_key="conversation-1:turn-2:reply",
        )

    invocation = db_session.query(LLMInvocation).one()
    attempt = db_session.query(LLMAttempt).one()
    assert invocation.status == LLMInvocationStatus.FAILED
    assert invocation.error_kind == GatewayErrorKind.RATE_LIMIT.value
    assert invocation.retryable is True
    assert attempt.status == LLMAttemptStatus.FAILED
    assert "provider detail" not in repr(invocation.__dict__)


@pytest.mark.asyncio
async def test_llm_service_takeover_reuses_original_provider_request_id(db_session):
    run_id = uuid4()
    messages = [{"role": "user", "content": "recover buyer request"}]
    pending_request = LLMRequest(
        use_case=LLMUseCase.LIVE_REPLY,
        messages=messages,
        temperature=0.3,
        max_output_tokens=1600,
    )
    pending, _ = InvocationAuditService(db_session).start(
        idempotency_key="ai-chat:recovered-turn:llm",
        request=pending_request,
        backend="omniroute",
        run_id=run_id,
        fencing_token=1,
    )
    original_request_id = pending.request_id
    db_session.commit()
    backend = CapturingBackend()
    service = LLMService(
        backend,
        audit_sink=SessionInvocationAuditSink(
            db_session,
            run_id=run_id,
            fencing_token=2,
        ),
        backend_name="omniroute",
    )

    response = await service.complete(
        LLMUseCase.LIVE_REPLY,
        messages,
        temperature=0.3,
        max_output_tokens=1600,
        idempotency_key="ai-chat:recovered-turn:llm",
    )

    assert backend.requests[0].request_id == original_request_id
    assert response.request_id == original_request_id
    db_session.refresh(pending)
    assert pending.status == LLMInvocationStatus.SUCCEEDED
    assert pending.fencing_token == 2
