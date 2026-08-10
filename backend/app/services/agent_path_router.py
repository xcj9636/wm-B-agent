"""Conservative, deterministic execution-path routing for agent requests."""

import re
from typing import Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.services.agent_runtime.contracts import Sensitivity


class AgentExecutionProfile(BaseModel):
    """Safe persisted metadata that controls bounded execution, never policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Literal["fast", "deep"]
    reason_code: str = Field(min_length=1, max_length=80)
    route_version: Literal["v1"] = "v1"
    max_output_tokens: int = Field(ge=256, le=1600)
    history_message_limit: Optional[int] = Field(default=None, ge=1, le=20)

    @classmethod
    def deep_fallback(
        cls,
        reason_code: str = "invalid_persisted_profile",
        *,
        max_output_tokens: int = 1600,
    ) -> "AgentExecutionProfile":
        return cls(
            path="deep",
            reason_code=reason_code,
            max_output_tokens=max_output_tokens,
            history_message_limit=None,
        )

    @classmethod
    def from_state(
        cls,
        state: object,
        *,
        deep_max_output_tokens: int = 1600,
    ) -> "AgentExecutionProfile":
        """Validate durable state and fail closed for legacy or corrupt runs."""
        if not isinstance(state, Mapping):
            return cls.deep_fallback(
                max_output_tokens=deep_max_output_tokens,
            )
        try:
            profile = cls.model_validate(dict(state))
        except (ValidationError, TypeError, ValueError):
            return cls.deep_fallback(
                max_output_tokens=deep_max_output_tokens,
            )
        if profile.path == "deep" and profile.history_message_limit is not None:
            return cls.deep_fallback(
                max_output_tokens=deep_max_output_tokens,
            )
        if profile.path == "fast" and profile.history_message_limit is None:
            return cls.deep_fallback(
                max_output_tokens=deep_max_output_tokens,
            )
        return profile


class AgentPathRouter:
    """Select a bounded fast path only when the request is plainly low-risk."""

    _BUSINESS_EVIDENCE_PATTERN = re.compile(
        r"\b(?:quote|quotation|price|pricing|moq|payment|incoterm|inventory|"
        r"order|contract|certificat(?:e|ion)|compliance|lead\s*time|shipment|"
        r"customs|tariff)\b|报价|价格|起订量|付款|交期|库存|订单|合同|证书|"
        r"认证|合规|物流|报关|关税",
        re.IGNORECASE,
    )
    _ACTION_PATTERN = re.compile(
        r"\b(?:send|email|publish|post|delete|approve|reject|refund|cancel|"
        r"create|update|execute|run|call|book|schedule)\b|发送|发邮件|发布|"
        r"删除|批准|拒绝|退款|取消|创建|更新|执行|调用|预订|安排",
        re.IGNORECASE,
    )

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_input_chars: int = 240,
        max_history_messages: int = 6,
        fast_max_output_tokens: int = 800,
        deep_max_output_tokens: int = 1600,
    ) -> None:
        if max_input_chars < 1 or max_history_messages < 1:
            raise ValueError("Routing limits must be positive")
        self._enabled = enabled
        self._max_input_chars = max_input_chars
        self._max_history_messages = max_history_messages
        self._fast_max_output_tokens = fast_max_output_tokens
        self._deep_max_output_tokens = deep_max_output_tokens

    def route(
        self,
        *,
        content: str,
        sensitivity: Sensitivity,
        prior_message_count: int,
    ) -> AgentExecutionProfile:
        if sensitivity in {Sensitivity.CONFIDENTIAL, Sensitivity.RESTRICTED}:
            return self._deep("sensitive_input")
        if not self._enabled:
            return self._deep("fast_path_disabled")
        if len(content) > self._max_input_chars:
            return self._deep("long_input")
        if prior_message_count > self._max_history_messages:
            return self._deep("long_conversation")
        if self._BUSINESS_EVIDENCE_PATTERN.search(content):
            return self._deep("business_evidence_required")
        if self._ACTION_PATTERN.search(content):
            return self._deep("tool_or_action_intent")
        return AgentExecutionProfile(
            path="fast",
            reason_code="short_simple_request",
            max_output_tokens=self._fast_max_output_tokens,
            history_message_limit=self._max_history_messages,
        )

    def _deep(self, reason_code: str) -> AgentExecutionProfile:
        return AgentExecutionProfile.deep_fallback(
            reason_code,
            max_output_tokens=self._deep_max_output_tokens,
        )
