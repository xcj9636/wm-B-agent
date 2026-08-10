"""Stable public-ingress and internal Agent Runtime contracts."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, FrozenSet, Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentUseCase(str, Enum):
    LEAD_CLASSIFICATION = "lead_classification"
    MESSAGE_DRAFT = "message_draft"
    LIVE_REPLY = "live_reply"
    RAG_QUERY_REWRITE = "rag_query_rewrite"
    SUMMARIZATION = "summarization"
    RESEARCH = "research"


class Sensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


_SENSITIVITY_RANK = {
    Sensitivity.PUBLIC: 0,
    Sensitivity.INTERNAL: 1,
    Sensitivity.CONFIDENTIAL: 2,
    Sensitivity.RESTRICTED: 3,
}


def derive_sensitivity(*values: Sensitivity) -> Sensitivity:
    """Return the strictest server-observed sensitivity label."""
    if not values:
        raise ValueError("At least one sensitivity label is required")
    return max(values, key=_SENSITIVITY_RANK.__getitem__)


class AgentIngressRequest(BaseModel):
    """Browser/API request. Identity and policy fields are intentionally absent."""

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=8, max_length=255)
    session_id: Optional[UUID] = None
    use_case: AgentUseCase
    locale: str = Field(default="zh-CN", min_length=2, max_length=35)
    input: Dict[str, Any]
    requested_sensitivity_floor: Optional[Sensitivity] = None
    stream: bool = True


class ExecutionPrincipal(BaseModel):
    """Server-derived authenticated identity and authorization snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    org_id: UUID
    user_id: int = Field(gt=0)
    roles: FrozenSet[str] = Field(min_length=1)
    entitlements_hash: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    authn_context: str = Field(min_length=1, max_length=255)


class AgentRequest(BaseModel):
    """Internal request created only after authentication and data classification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: UUID = Field(default_factory=uuid4)
    idempotency_key: str = Field(min_length=8, max_length=255)
    principal: ExecutionPrincipal
    session_id: Optional[UUID] = None
    turn_id: UUID
    use_case: AgentUseCase
    locale: str = Field(default="zh-CN", min_length=2, max_length=35)
    input: Dict[str, Any]
    sensitivity: Sensitivity
    deadline_at: datetime
    stream: bool = True

    @model_validator(mode="after")
    def validate_deadline(self) -> "AgentRequest":
        if self.deadline_at.tzinfo is None:
            raise ValueError("deadline_at must be timezone-aware")
        if self.deadline_at <= datetime.now(timezone.utc):
            raise ValueError("deadline_at must be in the future")
        return self


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


AgentEventType = Literal[
    "run.started",
    "stream.reset",
    "context.ready",
    "message.delta",
    "tool.proposed",
    "tool.awaiting_approval",
    "tool.started",
    "tool.succeeded",
    "tool.failed",
    "run.completed",
    "run.failed",
    "run.cancelled",
]


class AgentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    turn_id: UUID
    sequence: int = Field(ge=1)
    type: AgentEventType
    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    trace_id: UUID
    payload: Dict[str, Any] = Field(default_factory=dict)
