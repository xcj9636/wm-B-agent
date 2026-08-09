from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.services.agent_runtime.contracts import ExecutionPrincipal
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


def principal(*roles: str) -> ExecutionPrincipal:
    return ExecutionPrincipal(
        org_id=uuid4(),
        user_id=42,
        roles=frozenset(roles or ("sales",)),
        entitlements_hash="e" * 64,
        authn_context="jwt:mfa",
    )


def registry() -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(
        ToolSpec(
            name="crm.contact.read",
            version="1.2.0",
            risk=ToolRisk.READ,
            allowed_roles={"sales", "admin"},
        )
    )
    tools.register(
        ToolSpec(
            name="crm.contact.update",
            version="2.0.0",
            risk=ToolRisk.WRITE,
            allowed_roles={"sales", "admin"},
            requires_approval=True,
        )
    )
    return tools


def test_model_cannot_assert_server_owned_tool_metadata():
    with pytest.raises(ValidationError):
        ModelToolProposal.model_validate(
            {
                "name": "crm.contact.update",
                "arguments": {"contact_id": "c-1", "status": "qualified"},
                "risk": "read",
                "approved": True,
                "org_id": str(uuid4()),
            }
        )


def test_tool_call_stamps_identity_version_epoch_and_stable_idempotency_key():
    proposal = ModelToolProposal(
        name="crm.contact.read",
        arguments={"contact_id": "c-1"},
    )
    factory = ToolCallFactory(registry())
    actor = principal("sales")
    run_id = uuid4()
    turn_id = uuid4()

    first = factory.create(
        proposal=proposal,
        principal=actor,
        run_id=run_id,
        turn_id=turn_id,
        generation_epoch=7,
        provenance={ProvenanceKind.USER_INPUT},
        purpose="show contact",
    )
    replay = factory.create(
        proposal=proposal,
        principal=actor,
        run_id=run_id,
        turn_id=turn_id,
        generation_epoch=7,
        provenance={ProvenanceKind.USER_INPUT},
        purpose="show contact",
    )

    assert first.tool_version == "1.2.0"
    assert first.org_id == actor.org_id
    assert first.actor_user_id == actor.user_id
    assert first.generation_epoch == 7
    assert first.idempotency_key == replay.idempotency_key
    assert first.call_id != replay.call_id


def test_untrusted_retrieval_cannot_authorize_write_tool():
    proposal = ModelToolProposal(
        name="crm.contact.update",
        arguments={"contact_id": "c-1", "status": "qualified"},
    )
    with pytest.raises(ToolPolicyDenied, match="untrusted"):
        ToolCallFactory(registry()).create(
            proposal=proposal,
            principal=principal("sales"),
            run_id=uuid4(),
            turn_id=uuid4(),
            generation_epoch=3,
            provenance={ProvenanceKind.UNTRUSTED_RETRIEVAL},
            purpose="update CRM",
        )


def test_write_tool_requires_approval_bound_to_exact_call():
    proposal = ModelToolProposal(
        name="crm.contact.update",
        arguments={"contact_id": "c-1", "status": "qualified"},
    )
    factory = ToolCallFactory(registry())
    call = factory.create(
        proposal=proposal,
        principal=principal("sales"),
        run_id=uuid4(),
        turn_id=uuid4(),
        generation_epoch=3,
        provenance={ProvenanceKind.USER_INPUT},
        purpose="update CRM",
    )

    assert call.approval_required is True
    wrong = ApprovalEnvelope(
        call_id=uuid4(),
        idempotency_key=call.idempotency_key,
        approver_user_id=99,
    )
    with pytest.raises(ToolPolicyDenied, match="does not match"):
        factory.validate_approval(call, wrong)

    approval = ApprovalEnvelope(
        call_id=call.call_id,
        idempotency_key=call.idempotency_key,
        approver_user_id=99,
    )
    factory.validate_approval(call, approval)


def test_unknown_tool_and_missing_role_fail_closed():
    factory = ToolCallFactory(registry())
    common = {
        "principal": principal("viewer"),
        "run_id": uuid4(),
        "turn_id": uuid4(),
        "generation_epoch": 1,
        "provenance": {ProvenanceKind.USER_INPUT},
        "purpose": "read CRM",
    }
    with pytest.raises(ToolPolicyDenied):
        factory.create(
            proposal=ModelToolProposal(name="crm.contact.read", arguments={}),
            **common,
        )
    with pytest.raises(ToolPolicyDenied):
        factory.create(
            proposal=ModelToolProposal(name="shell.exec", arguments={}),
            **{**common, "principal": principal("admin")},
        )
