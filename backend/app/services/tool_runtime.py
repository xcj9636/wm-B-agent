"""Server-owned tool registry, authorization, and approval contracts.

The model may only propose a registered tool name and arguments. Identity,
version, risk, authorization, fencing, and idempotency are derived here.
"""

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, FrozenSet, Set
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.services.agent_runtime.contracts import ExecutionPrincipal


class ToolRisk(str, Enum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class ProvenanceKind(str, Enum):
    USER_INPUT = "user_input"
    TRUSTED_SYSTEM = "trusted_system"
    APPROVED_DOCUMENT = "approved_document"
    UNTRUSTED_RETRIEVAL = "untrusted_retrieval"
    EXTERNAL_WEB = "external_web"


_UNTRUSTED_PROVENANCE = {
    ProvenanceKind.UNTRUSTED_RETRIEVAL,
    ProvenanceKind.EXTERNAL_WEB,
}


class ToolPolicyDenied(RuntimeError):
    """Fail-closed policy result safe to map to a generic client error."""


class ToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,127}$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    risk: ToolRisk
    allowed_roles: FrozenSet[str] = Field(min_length=1)
    requires_approval: bool = False


class ModelToolProposal(BaseModel):
    """The complete and intentionally narrow model-controlled surface."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=3, max_length=128)
    arguments: Dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    turn_id: UUID
    generation_epoch: int = Field(ge=1)
    org_id: UUID
    actor_user_id: int = Field(gt=0)
    tool_name: str
    tool_version: str
    risk: ToolRisk
    arguments: Dict[str, Any]
    provenance: FrozenSet[ProvenanceKind] = Field(min_length=1)
    purpose: str = Field(min_length=1, max_length=255)
    idempotency_key: str = Field(pattern=r"^tool:[a-f0-9]{64}$")
    approval_required: bool
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class ApprovalEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: UUID
    idempotency_key: str = Field(pattern=r"^tool:[a-f0-9]{64}$")
    approver_user_id: int = Field(gt=0)
    approved_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def resolve(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolPolicyDenied("Tool is not registered") from exc


class ToolCallFactory:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def create(
        self,
        *,
        proposal: ModelToolProposal,
        principal: ExecutionPrincipal,
        run_id: UUID,
        turn_id: UUID,
        generation_epoch: int,
        provenance: Set[ProvenanceKind],
        purpose: str,
    ) -> ToolCall:
        spec = self._registry.resolve(proposal.name)
        if not principal.roles.intersection(spec.allowed_roles):
            raise ToolPolicyDenied("Actor is not authorized for this tool")
        if not provenance:
            raise ToolPolicyDenied("Tool provenance is required")
        if spec.risk != ToolRisk.READ and provenance.intersection(
            _UNTRUSTED_PROVENANCE
        ):
            raise ToolPolicyDenied("Write tools cannot be authorized by untrusted data")

        canonical = {
            "org_id": str(principal.org_id),
            "actor_user_id": principal.user_id,
            "run_id": str(run_id),
            "turn_id": str(turn_id),
            "generation_epoch": generation_epoch,
            "tool_name": spec.name,
            "tool_version": spec.version,
            "arguments": proposal.arguments,
            "purpose": purpose,
        }
        digest = hashlib.sha256(
            json.dumps(
                canonical,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()

        return ToolCall(
            run_id=run_id,
            turn_id=turn_id,
            generation_epoch=generation_epoch,
            org_id=principal.org_id,
            actor_user_id=principal.user_id,
            tool_name=spec.name,
            tool_version=spec.version,
            risk=spec.risk,
            arguments=proposal.arguments,
            provenance=frozenset(provenance),
            purpose=purpose,
            idempotency_key=f"tool:{digest}",
            approval_required=(
                spec.requires_approval or spec.risk == ToolRisk.DESTRUCTIVE
            ),
        )

    def validate_approval(
        self,
        call: ToolCall,
        approval: ApprovalEnvelope,
    ) -> None:
        if not call.approval_required:
            return
        if (
            approval.call_id != call.call_id
            or approval.idempotency_key != call.idempotency_key
        ):
            raise ToolPolicyDenied("Approval does not match the exact tool call")
