"""Stable business-facing contracts for all LLM backends."""
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LLMUseCase(str, Enum):
    """Approved business intents; these are not provider model names."""

    LEAD_CLASSIFICATION = "lead_classification"
    MESSAGE_DRAFT = "message_draft"
    LIVE_REPLY = "live_reply"
    RAG_QUERY_REWRITE = "rag_query_rewrite"
    SUMMARIZATION = "summarization"


REQUIRED_GATEWAY_USE_CASES = (
    LLMUseCase.MESSAGE_DRAFT,
    LLMUseCase.LIVE_REPLY,
)


class LLMMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: Optional[str] = None


class LLMRequest(BaseModel):
    """A provider-neutral request accepted by the internal LLM service."""

    model_config = ConfigDict(extra="forbid")

    use_case: LLMUseCase
    messages: List[LLMMessage] = Field(min_length=1)
    request_id: UUID = Field(default_factory=uuid4)
    temperature: Optional[float] = Field(default=None, ge=0, le=2)
    max_output_tokens: Optional[int] = Field(default=None, ge=1)
    response_schema: Optional[Dict[str, Any]] = None


class LLMUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: Optional[int] = Field(default=None, ge=0)
    cost: Optional[float] = Field(default=None, ge=0)
    cost_status: Literal["unknown", "estimated", "actual"] = "unknown"

    @model_validator(mode="after")
    def validate_total_tokens(self) -> "LLMUsage":
        expected = self.input_tokens + self.output_tokens
        if self.total_tokens is None:
            self.total_tokens = expected
        elif self.total_tokens != expected:
            raise ValueError("total_tokens must equal input_tokens + output_tokens")
        return self


class LLMResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    content: str
    finish_reason: Optional[str] = None
    usage: LLMUsage = Field(default_factory=LLMUsage)
    gateway_request_id: Optional[str] = None
    resolved_model: Optional[str] = None
    resolved_provider: Optional[str] = None


class LLMStreamChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    delta: str = ""
    finish_reason: Optional[str] = None
    usage: Optional[LLMUsage] = None
    gateway_request_id: Optional[str] = None
    resolved_model: Optional[str] = None
    resolved_provider: Optional[str] = None


class GatewayErrorKind(str, Enum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"
    CONTENT_POLICY = "content_policy"
    INVALID_RESPONSE = "invalid_response"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"


class GatewayError(RuntimeError):
    """Normalized failure raised by an LLM backend adapter."""

    def __init__(
        self,
        kind: GatewayErrorKind,
        message: str,
        *,
        request_id: Optional[UUID] = None,
        status_code: Optional[int] = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.request_id = request_id
        self.status_code = status_code
        self.retryable = retryable
