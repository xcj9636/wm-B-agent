from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.database import AgentToolExecution, OutboxEvent
from app.services.agent_concurrency import (
    ConcurrencyLimitExceeded,
    ConcurrencyUnavailable,
)
from app.services.agent_runtime.contracts import ExecutionPrincipal
from app.services.idempotency import IdempotencyConflict
from app.services.tool_execution import (
    DurableToolExecutionService,
    SideEffectToolsDisabled,
    StaleToolCall,
)
from app.services.tool_runtime import (
    ApprovalEnvelope,
    ModelToolProposal,
    ProvenanceKind,
    ToolCallFactory,
    ToolPolicyDenied,
    ToolRegistry,
    ToolRisk,
    ToolSpec,
)


def principal(org_id=None, user_id=41, *roles):
    return ExecutionPrincipal(
        org_id=org_id or uuid4(),
        user_id=user_id,
        roles=frozenset(roles or ("sales",)),
        entitlements_hash="c" * 64,
        authn_context="jwt:mfa",
    )


def registry():
    value = ToolRegistry()
    value.register(
        ToolSpec(
            name="catalog.product.read",
            version="1.0.0",
            risk=ToolRisk.READ,
            allowed_roles={"sales"},
        )
    )
    value.register(
        ToolSpec(
            name="crm.contact.update",
            version="2.1.0",
            risk=ToolRisk.WRITE,
            allowed_roles={"sales"},
            requires_approval=True,
        )
    )
    return value


class ConcurrencyGate:
    def __init__(self, *, acquire_error=None, release_error=None):
        self.acquire_error = acquire_error
        self.release_error = release_error
        self.acquired = []
        self.released = []

    async def acquire(self, request, *, now, lease_seconds):
        self.acquired.append((request, now, lease_seconds))
        if self.acquire_error is not None:
            raise self.acquire_error
        return SimpleNamespace(lease_id="tool-concurrency-lease")

    async def release(self, lease):
        self.released.append(lease)
        if self.release_error is not None:
            raise self.release_error
        return True


def tool_call(*, actor=None, name="catalog.product.read", arguments=None, epoch=3):
    actor = actor or principal()
    return ToolCallFactory(registry()).create(
        proposal=ModelToolProposal(
            name=name,
            arguments=arguments or {"sku": "AX-7"},
        ),
        principal=actor,
        run_id=uuid4(),
        turn_id=uuid4(),
        generation_epoch=epoch,
        provenance={ProvenanceKind.USER_INPUT},
        purpose="serve verified customer request",
    )


def test_tool_proposal_is_durable_idempotent_and_payload_bound(db_session):
    service = DurableToolExecutionService(db_session, registry())
    call = tool_call()

    first, created = service.record(call)
    replay, replay_created = service.record(call)

    assert created is True
    assert replay_created is False
    assert replay.id == first.id
    assert first.status == "ready"
    assert db_session.query(AgentToolExecution).count() == 1

    changed = call.model_copy(update={"arguments": {"sku": "OTHER"}})
    with pytest.raises(IdempotencyConflict):
        service.record(changed)


def test_write_approval_requires_same_org_exact_call_and_four_eyes(db_session):
    org_id = uuid4()
    actor = principal(org_id, 41, "sales")
    call = tool_call(actor=actor, name="crm.contact.update")
    service = DurableToolExecutionService(db_session, registry())
    row, _ = service.record(call)
    approval = ApprovalEnvelope(
        call_id=call.call_id,
        idempotency_key=call.idempotency_key,
        approver_user_id=99,
    )

    with pytest.raises(ToolPolicyDenied, match="organization"):
        service.approve(call, approval, principal(uuid4(), 99, "sales"))
    self_approval = ApprovalEnvelope(
        call_id=call.call_id,
        idempotency_key=call.idempotency_key,
        approver_user_id=41,
    )
    with pytest.raises(ToolPolicyDenied, match="separate"):
        service.approve(call, self_approval, principal(org_id, 41, "sales"))

    approved = service.approve(call, approval, principal(org_id, 99, "sales"))
    assert row.status == "ready"
    assert approved.approved_by_user_id == 99


@pytest.mark.asyncio
async def test_read_tool_executes_once_and_replays_persisted_result(db_session):
    call = tool_call()
    concurrency = ConcurrencyGate()
    service = DurableToolExecutionService(
        db_session,
        registry(),
        concurrency=concurrency,
    )
    service.record(call)
    executions = 0

    async def handler(received):
        nonlocal executions
        executions += 1
        assert received.call_id == call.call_id
        return {"sku": "AX-7", "verified": True}

    first = await service.execute_read(call, current_generation_epoch=3, handler=handler)
    replay = await service.execute_read(
        call,
        current_generation_epoch=3,
        handler=handler,
    )

    assert first == {"sku": "AX-7", "verified": True}
    assert replay == first
    assert executions == 1
    assert len(concurrency.acquired) == 1
    request = concurrency.acquired[0][0]
    assert request.org_id == call.org_id
    assert request.user_id == call.actor_user_id
    assert request.tool_name == call.tool_name
    assert request.provider_id is None
    assert len(concurrency.released) == 1
    assert db_session.get(AgentToolExecution, call.call_id).status == "succeeded"


@pytest.mark.asyncio
async def test_read_tool_capacity_rejection_is_retryable_without_handler_execution(
    db_session,
):
    call = tool_call()
    concurrency = ConcurrencyGate(
        acquire_error=ConcurrencyLimitExceeded("tool"),
    )
    service = DurableToolExecutionService(
        db_session,
        registry(),
        concurrency=concurrency,
    )
    service.record(call)
    called = False

    async def handler(_):
        nonlocal called
        called = True
        return {}

    with pytest.raises(ConcurrencyLimitExceeded):
        await service.execute_read(
            call,
            current_generation_epoch=3,
            handler=handler,
        )

    assert called is False
    assert db_session.get(AgentToolExecution, call.call_id).status == "ready"
    assert concurrency.released == []


@pytest.mark.asyncio
async def test_read_tool_release_failure_defers_cleanup_to_lease_ttl(db_session):
    call = tool_call()
    concurrency = ConcurrencyGate(
        release_error=ConcurrencyUnavailable("redis endpoint unavailable"),
    )
    service = DurableToolExecutionService(
        db_session,
        registry(),
        concurrency=concurrency,
    )
    service.record(call)

    async def handler(_):
        return {"sku": "AX-7"}

    result = await service.execute_read(
        call,
        current_generation_epoch=3,
        handler=handler,
    )

    assert result == {"sku": "AX-7"}
    assert len(concurrency.released) == 1
    assert db_session.get(AgentToolExecution, call.call_id).status == "succeeded"


@pytest.mark.asyncio
async def test_stale_fence_prevents_tool_handler_execution(db_session):
    call = tool_call(epoch=3)
    service = DurableToolExecutionService(db_session, registry())
    service.record(call)
    called = False

    async def handler(_):
        nonlocal called
        called = True
        return {}

    with pytest.raises(StaleToolCall):
        await service.execute_read(call, current_generation_epoch=4, handler=handler)
    assert called is False


def test_write_tool_is_disabled_by_default_then_transactionally_enqueued(db_session):
    org_id = uuid4()
    actor = principal(org_id, 41, "sales")
    call = tool_call(actor=actor, name="crm.contact.update")
    approval = ApprovalEnvelope(
        call_id=call.call_id,
        idempotency_key=call.idempotency_key,
        approver_user_id=99,
    )
    service = DurableToolExecutionService(db_session, registry())
    service.record(call)
    service.approve(call, approval, principal(org_id, 99, "sales"))

    with pytest.raises(SideEffectToolsDisabled):
        service.enqueue_side_effect(call, current_generation_epoch=3)
    assert db_session.query(OutboxEvent).count() == 0

    canary = DurableToolExecutionService(
        db_session,
        registry(),
        allow_side_effects=True,
    )
    first = canary.enqueue_side_effect(call, current_generation_epoch=3)
    replay = canary.enqueue_side_effect(call, current_generation_epoch=3)

    assert replay.id == first.id
    assert db_session.query(OutboxEvent).count() == 1
    assert db_session.get(AgentToolExecution, call.call_id).status == "enqueued"
