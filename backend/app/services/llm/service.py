"""Business-facing LLM service and direct-provider compatibility adapter."""
import math
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Protocol

from app.services.llm.contracts import (
    LLMRequest,
    LLMResponse,
    LLMStreamChunk,
    LLMUsage,
    LLMUseCase,
)
from app.services.llm.instrumented import (
    InvocationAuditSink,
    InvocationInProgress,
)


class CompletionBackend(Protocol):
    async def complete(self, request: LLMRequest) -> LLMResponse:
        ...


class StreamingUnavailable(RuntimeError):
    """The configured backend cannot provide genuine incremental output."""


class DirectProviderAdapter:
    """Keep the legacy provider available behind the stable response contract."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider
        self.supports_stream = callable(
            getattr(provider, "chat_completion_with_stream", None)
        )

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

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        messages = [
            message.model_dump(exclude_none=True) for message in request.messages
        ]
        stream_method = getattr(
            self._provider,
            "chat_completion_with_stream",
            None,
        )
        if stream_method is None:
            raise StreamingUnavailable(
                "Direct provider does not support incremental streaming"
            )
        async for delta in stream_method(messages):
            if delta:
                yield LLMStreamChunk(
                    request_id=request.request_id,
                    delta=delta,
                )


class LLMService:
    """The only LLM entry point business services and skills should consume."""

    def __init__(
        self,
        backend: CompletionBackend,
        *,
        audit_sink: Optional[InvocationAuditSink] = None,
        backend_name: str = "unknown",
    ) -> None:
        self._backend = backend
        self._audit_sink = audit_sink
        self._backend_name = backend_name

    async def complete(
        self,
        use_case: LLMUseCase,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        response_schema: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> LLMResponse:
        request = LLMRequest(
            use_case=use_case,
            messages=messages,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_schema=response_schema,
        )
        audit = self._start_audit(request, idempotency_key)
        if audit is not None and audit.cached_response is not None:
            return audit.cached_response
        if audit is not None and request.request_id != audit.request_id:
            request = request.model_copy(
                update={"request_id": audit.request_id}
            )
        provider_started = time.perf_counter()
        try:
            response = await self._backend.complete(request)
        except Exception as exc:
            if audit is not None:
                elapsed_ms = self._elapsed_ms(provider_started)
                self._audit_sink.fail(
                    audit.invocation_id,
                    exc,
                    latency_ms=elapsed_ms,
                    e2e_latency_ms=elapsed_ms,
                    consumer_backpressure_ms=0,
                )
            raise
        if audit is not None:
            elapsed_ms = self._elapsed_ms(provider_started)
            self._audit_sink.succeed(
                audit.invocation_id,
                response,
                latency_ms=elapsed_ms,
                e2e_latency_ms=elapsed_ms,
                consumer_backpressure_ms=0,
            )
        return response

    async def stream(
        self,
        use_case: LLMUseCase,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        idempotency_key: Optional[str] = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        request = LLMRequest(
            use_case=use_case,
            messages=messages,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        audit = self._start_audit(request, idempotency_key)
        if audit is not None and audit.cached_response is not None:
            response = audit.cached_response
            yield LLMStreamChunk(
                request_id=response.request_id,
                delta=response.content,
                finish_reason=response.finish_reason,
                usage=response.usage,
                gateway_request_id=response.gateway_request_id,
                resolved_model=response.resolved_model,
                resolved_provider=response.resolved_provider,
            )
            return
        if audit is not None and request.request_id != audit.request_id:
            request = request.model_copy(
                update={"request_id": audit.request_id}
            )

        fragments: List[str] = []
        final_chunk: Optional[LLMStreamChunk] = None
        ttft_ms: Optional[int] = None
        provider_started = time.perf_counter()
        consumer_backpressure_seconds = 0.0
        try:
            stream_method = getattr(self._backend, "stream", None)
            if stream_method is None:
                raise StreamingUnavailable(
                    "Configured backend does not support incremental streaming"
                )
            async for chunk in stream_method(request):
                final_chunk = chunk
                if chunk.delta:
                    if ttft_ms is None:
                        ttft_ms = self._elapsed_ms(provider_started)
                    fragments.append(chunk.delta)
                consumer_started = time.perf_counter()
                yield chunk
                consumer_backpressure_seconds += (
                    time.perf_counter() - consumer_started
                )
        except Exception as exc:
            if audit is not None:
                timing = self._stream_timing(
                    provider_started,
                    consumer_backpressure_seconds,
                )
                self._audit_sink.fail(
                    audit.invocation_id,
                    exc,
                    **timing,
                )
            raise

        if audit is not None:
            response = LLMResponse(
                request_id=request.request_id,
                content="".join(fragments),
                finish_reason=final_chunk.finish_reason if final_chunk else None,
                usage=(
                    final_chunk.usage
                    if final_chunk and final_chunk.usage is not None
                    else LLMUsage()
                ),
                gateway_request_id=(
                    final_chunk.gateway_request_id if final_chunk else None
                ),
                resolved_model=(final_chunk.resolved_model if final_chunk else None),
                resolved_provider=(
                    final_chunk.resolved_provider if final_chunk else None
                ),
            )
            timing = self._stream_timing(
                provider_started,
                consumer_backpressure_seconds,
            )
            self._audit_sink.succeed(
                audit.invocation_id,
                response,
                ttft_ms=ttft_ms,
                **timing,
            )

    def _start_audit(self, request: LLMRequest, idempotency_key: Optional[str]):
        if self._audit_sink is None:
            return None
        audit = self._audit_sink.start(
            idempotency_key=idempotency_key or f"llm:{request.request_id}",
            request=request,
            backend=self._backend_name,
        )
        if not audit.created and audit.cached_response is None:
            raise InvocationInProgress(
                "LLM invocation with this idempotency key is already in progress"
            )
        return audit

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, math.ceil((time.perf_counter() - started) * 1000))

    @staticmethod
    def _stream_timing(
        started: float,
        consumer_backpressure_seconds: float,
    ) -> Dict[str, int]:
        e2e_seconds = max(0.0, time.perf_counter() - started)
        provider_seconds = max(
            0.0,
            e2e_seconds - consumer_backpressure_seconds,
        )
        return {
            "latency_ms": math.ceil(provider_seconds * 1000),
            "e2e_latency_ms": math.ceil(e2e_seconds * 1000),
            "consumer_backpressure_ms": math.ceil(
                consumer_backpressure_seconds * 1000
            ),
        }
