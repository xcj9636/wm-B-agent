"""Token-aware context assembly with explicit trust and provenance."""

import json
from enum import Enum
from hashlib import sha256
from typing import List, Protocol

import tiktoken
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContextRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ContextTrust(str, Enum):
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


class ContextSection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    section_id: str = Field(min_length=1, max_length=255)
    source_type: str = Field(min_length=1, max_length=100)
    source_id: str = Field(min_length=1, max_length=255)
    source_version: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=200000)
    priority: int = Field(ge=0, le=100)
    trust: ContextTrust
    role: ContextRole
    sensitivity: str = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def enforce_instruction_boundary(self) -> "ContextSection":
        if self.trust == ContextTrust.UNTRUSTED and self.role == ContextRole.SYSTEM:
            raise ValueError("Untrusted context cannot use the system role")
        return self


class ContextBudgetPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_context_tokens: int = Field(ge=32)
    reserved_output_tokens: int = Field(ge=1)
    safety_margin_tokens: int = Field(default=32, ge=0)

    @model_validator(mode="after")
    def validate_reserve(self) -> "ContextBudgetPolicy":
        if self.reserved_output_tokens + self.safety_margin_tokens >= self.model_context_tokens:
            raise ValueError("Output reserve and safety margin exhaust model context")
        return self

    @property
    def input_token_budget(self) -> int:
        return (
            self.model_context_tokens
            - self.reserved_output_tokens
            - self.safety_margin_tokens
        )


class TokenCounter(Protocol):
    def count(self, value: str) -> int:
        ...


class TiktokenCounter:
    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        self.encoding_name = encoding_name
        self._encoding = tiktoken.get_encoding(encoding_name)

    def count(self, value: str) -> int:
        return len(self._encoding.encode(value))


class ContextMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: ContextRole
    content: str


class ContextManifestItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    section_id: str
    source_type: str
    source_id: str
    source_version: str
    priority: int
    trust: ContextTrust
    role: ContextRole
    sensitivity: str
    token_count: int
    reason: str


class ContextSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tokenizer: str
    input_token_budget: int
    reserved_output_tokens: int
    used_input_tokens: int
    messages: List[ContextMessage]
    included: List[ContextManifestItem]
    dropped: List[ContextManifestItem]
    content_hash: str


class ContextAssembler:
    MESSAGE_OVERHEAD_TOKENS = 4

    def __init__(
        self,
        counter: TokenCounter,
        policy: ContextBudgetPolicy,
    ) -> None:
        self._counter = counter
        self._policy = policy

    def assemble(
        self,
        *,
        system_messages: List[str],
        sections: List[ContextSection],
    ) -> ContextSnapshot:
        messages: List[ContextMessage] = []
        used = 0
        for content in system_messages:
            tokens = self._message_tokens(content)
            if used + tokens > self._policy.input_token_budget:
                raise ValueError("System prompt exceeds input token budget")
            messages.append(ContextMessage(role=ContextRole.SYSTEM, content=content))
            used += tokens

        included: List[ContextManifestItem] = []
        dropped: List[ContextManifestItem] = []
        selected = []
        ordered = sorted(
            enumerate(sections),
            key=lambda item: (-item[1].priority, item[0]),
        )
        for position, section in ordered:
            rendered = self._render(section)
            token_count = self._message_tokens(rendered)
            manifest = ContextManifestItem(
                section_id=section.section_id,
                source_type=section.source_type,
                source_id=section.source_id,
                source_version=section.source_version,
                priority=section.priority,
                trust=section.trust,
                role=section.role,
                sensitivity=section.sensitivity,
                token_count=token_count,
                reason="included",
            )
            if used + token_count <= self._policy.input_token_budget:
                included.append(manifest)
                selected.append((position, section.role, rendered))
                used += token_count
            else:
                dropped.append(
                    manifest.model_copy(update={"reason": "token_budget_exceeded"})
                )

        for _, role, rendered in sorted(selected, key=lambda item: item[0]):
            messages.append(ContextMessage(role=role, content=rendered))

        payload = {
            "tokenizer": getattr(self._counter, "encoding_name", "custom"),
            "input_token_budget": self._policy.input_token_budget,
            "reserved_output_tokens": self._policy.reserved_output_tokens,
            "used_input_tokens": used,
            "messages": [item.model_dump(mode="json") for item in messages],
            "included": [item.model_dump(mode="json") for item in included],
            "dropped": [item.model_dump(mode="json") for item in dropped],
        }
        content_hash = sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return ContextSnapshot(**payload, content_hash=content_hash)

    def _message_tokens(self, content: str) -> int:
        return self._counter.count(content) + self.MESSAGE_OVERHEAD_TOKENS

    @staticmethod
    def _render(section: ContextSection) -> str:
        if section.trust == ContextTrust.TRUSTED:
            return section.content
        return (
            "<UNTRUSTED_CONTEXT "
            f'source_type="{section.source_type}" '
            f'source_id="{section.source_id}" '
            f'version="{section.source_version}">\n'
            f"{section.content}\n"
            "</UNTRUSTED_CONTEXT>"
        )
