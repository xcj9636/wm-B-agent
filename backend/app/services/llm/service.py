"""Business-facing LLM service and direct-provider compatibility adapter."""
from typing import Any, Dict, List, Optional, Protocol

from app.services.llm.contracts import (
    LLMRequest,
    LLMResponse,
    LLMUsage,
    LLMUseCase,
)


class CompletionBackend(Protocol):
    async def complete(self, request: LLMRequest) -> LLMResponse:
        ...


class DirectProviderAdapter:
    """Keep the legacy provider available behind the stable response contract."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    async def complete(self, request: LLMRequest) -> LLMResponse:
        messages = [
            message.model_dump(exclude_none=True) for message in request.messages
        ]
        content = await self._provider.chat_completion(messages)
        return LLMResponse(
            request_id=request.request_id,
            content=content,
            usage=LLMUsage(cost_status="unknown"),
        )


class LLMService:
    """The only LLM entry point business services and skills should consume."""

    def __init__(self, backend: CompletionBackend) -> None:
        self._backend = backend

    async def complete(
        self,
        use_case: LLMUseCase,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        response_schema: Optional[Dict[str, Any]] = None,
    ) -> LLMResponse:
        request = LLMRequest(
            use_case=use_case,
            messages=messages,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_schema=response_schema,
        )
        return await self._backend.complete(request)
