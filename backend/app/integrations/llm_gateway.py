"""OpenAI-compatible inference adapter for the internal OmniRoute gateway."""
import json
from typing import Any, AsyncIterator, Dict, Iterable, Mapping, Optional, Set, Tuple
from uuid import UUID, uuid4

import httpx

from app.services.llm.contracts import (
    GatewayError,
    GatewayErrorKind,
    LLMRequest,
    LLMResponse,
    LLMStreamChunk,
    LLMUsage,
    LLMUseCase,
)


class LLMGatewayClient:
    """Translate stable B-agent contracts to the gateway inference API."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_aliases: Mapping[LLMUseCase, str],
        allowed_providers: Iterable[str] = (),
        timeout_seconds: float = 60.0,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._model_aliases = dict(model_aliases)
        self._allowed_providers = {
            provider.strip().lower()
            for provider in allowed_providers
            if provider.strip()
        }
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
        )
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def list_models(self) -> Set[str]:
        """Return model and combo IDs exposed to this gateway API key."""
        request_id = uuid4()
        try:
            response = await self._client.get(
                "/v1/models",
                headers=self._headers_for_request_id(request_id),
            )
        except httpx.TimeoutException as exc:
            raise self._timeout_error(request_id) from exc
        except httpx.HTTPError as exc:
            raise GatewayError(
                GatewayErrorKind.UPSTREAM_UNAVAILABLE,
                "Gateway model discovery failed",
                request_id=request_id,
                retryable=True,
            ) from exc

        self._raise_for_status(response, request_id)
        try:
            data = response.json()["data"]
            models = {item["id"] for item in data}
            if not models or not all(isinstance(model, str) for model in models):
                raise ValueError("model list is empty or invalid")
            return models
        except (KeyError, TypeError, ValueError) as exc:
            raise GatewayError(
                GatewayErrorKind.INVALID_RESPONSE,
                "Gateway returned an invalid model list",
                request_id=request_id,
                status_code=response.status_code,
                retryable=False,
            ) from exc

    async def complete(self, request: LLMRequest) -> LLMResponse:
        payload = self._payload(request, stream=False)
        try:
            response = await self._client.post(
                "/v1/chat/completions",
                json=payload,
                headers=self._request_headers(request),
            )
        except httpx.TimeoutException as exc:
            raise self._timeout_error(request) from exc
        except httpx.HTTPError as exc:
            raise GatewayError(
                GatewayErrorKind.UPSTREAM_UNAVAILABLE,
                "Gateway request failed before a response was received",
                request_id=request.request_id,
                retryable=True,
            ) from exc

        self._raise_for_status(response, request.request_id)
        try:
            data = response.json()
            choice = data["choices"][0]
            usage = self._usage(data.get("usage"))
            resolved_provider = response.headers.get("x-omniroute-provider")
            self._validate_resolved_provider(
                resolved_provider,
                request.request_id,
            )
            return LLMResponse(
                request_id=request.request_id,
                content=choice["message"]["content"],
                finish_reason=choice.get("finish_reason"),
                usage=usage,
                gateway_request_id=(
                    response.headers.get("x-omniroute-request-id")
                    or data.get("id")
                    or response.headers.get("x-request-id")
                ),
                resolved_model=(
                    response.headers.get("x-omniroute-model")
                    or data.get("model")
                ),
                resolved_provider=resolved_provider,
            )
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise GatewayError(
                GatewayErrorKind.INVALID_RESPONSE,
                "Gateway returned an invalid completion response",
                request_id=request.request_id,
                status_code=response.status_code,
                retryable=False,
            ) from exc

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        if self._allowed_providers:
            raise GatewayError(
                GatewayErrorKind.INVALID_RESPONSE,
                "Streaming is disabled when provider verification is required",
                request_id=request.request_id,
                retryable=False,
            )

        payload = self._payload(request, stream=True)
        emitted = False
        saw_done = False
        gateway_request_id: Optional[str] = None
        data_lines = []

        try:
            async with self._client.stream(
                "POST",
                "/v1/chat/completions",
                json=payload,
                headers=self._request_headers(request),
            ) as response:
                if not response.is_success:
                    await response.aread()
                self._raise_for_status(response, request.request_id)
                async for line in response.aiter_lines():
                    if line.startswith(":"):
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                        continue
                    if line or not data_lines:
                        continue

                    event = "\n".join(data_lines)
                    data_lines.clear()
                    if event == "[DONE]":
                        saw_done = True
                        break

                    chunk, gateway_request_id = self._stream_chunk(
                        event,
                        request,
                        gateway_request_id,
                    )
                    if chunk is not None:
                        emitted = True
                        yield chunk

                if data_lines and not saw_done:
                    event = "\n".join(data_lines)
                    if event == "[DONE]":
                        saw_done = True
                    else:
                        chunk, gateway_request_id = self._stream_chunk(
                            event,
                            request,
                            gateway_request_id,
                        )
                        if chunk is not None:
                            emitted = True
                            yield chunk
        except GatewayError:
            raise
        except httpx.TimeoutException as exc:
            if emitted:
                raise GatewayError(
                    GatewayErrorKind.TIMEOUT,
                    "Gateway stream timed out after output started",
                    request_id=request.request_id,
                    retryable=False,
                ) from exc
            raise self._timeout_error(request) from exc
        except httpx.HTTPError as exc:
            raise GatewayError(
                GatewayErrorKind.UPSTREAM_UNAVAILABLE,
                "Gateway stream failed",
                request_id=request.request_id,
                retryable=not emitted,
            ) from exc

        if not saw_done:
            raise GatewayError(
                GatewayErrorKind.INVALID_RESPONSE,
                "Gateway stream ended without a completion marker",
                request_id=request.request_id,
                retryable=not emitted,
            )

    def _payload(self, request: LLMRequest, *, stream: bool) -> Dict[str, Any]:
        model = self._resolve_model(request)
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [message.model_dump(exclude_none=True) for message in request.messages],
            "stream": stream,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens
        if request.response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.use_case.value,
                    "strict": True,
                    "schema": request.response_schema,
                },
            }
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _resolve_model(self, request: LLMRequest) -> str:
        model = self._model_aliases.get(request.use_case)
        model = model.strip() if model else ""
        if not model or model.lower().startswith("auto/"):
            raise GatewayError(
                GatewayErrorKind.INVALID_RESPONSE,
                f"No approved fixed route for use case '{request.use_case.value}'",
                request_id=request.request_id,
                retryable=False,
            )
        return model

    def _request_headers(self, request: LLMRequest) -> Dict[str, str]:
        return self._headers_for_request_id(request.request_id)

    def _headers_for_request_id(self, request_id: UUID) -> Dict[str, str]:
        return {**self._headers, "X-Request-Id": str(request_id)}

    def _validate_resolved_provider(
        self,
        provider: Optional[str],
        request_id: UUID,
    ) -> None:
        if not self._allowed_providers:
            return

        normalized = provider.strip().lower() if provider else ""
        if normalized not in self._allowed_providers:
            raise GatewayError(
                GatewayErrorKind.INVALID_RESPONSE,
                "Gateway resolved an unapproved or unknown provider",
                request_id=request_id,
                retryable=False,
            )

    def _stream_chunk(
        self,
        event: str,
        request: LLMRequest,
        current_gateway_request_id: Optional[str],
    ) -> Tuple[Optional[LLMStreamChunk], Optional[str]]:
        try:
            data = json.loads(event)
            gateway_request_id = data.get("id") or current_gateway_request_id
            choice = data.get("choices", [{}])[0] if data.get("choices") else {}
            delta = choice.get("delta", {}).get("content", "")
            finish_reason = choice.get("finish_reason")
            usage = self._usage(data["usage"]) if data.get("usage") else None
            if not delta and finish_reason is None and usage is None:
                return None, gateway_request_id
            return (
                LLMStreamChunk(
                    request_id=request.request_id,
                    delta=delta,
                    finish_reason=finish_reason,
                    usage=usage,
                    gateway_request_id=gateway_request_id,
                ),
                gateway_request_id,
            )
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise GatewayError(
                GatewayErrorKind.INVALID_RESPONSE,
                "Gateway returned an invalid stream event",
                request_id=request.request_id,
                retryable=False,
            ) from exc

    @staticmethod
    def _usage(data: Optional[Dict[str, Any]]) -> LLMUsage:
        data = data or {}
        return LLMUsage(
            input_tokens=data.get("prompt_tokens", 0),
            output_tokens=data.get("completion_tokens", 0),
            total_tokens=data.get("total_tokens"),
            cost=data.get("cost"),
            cost_status=data.get("cost_status", "unknown"),
        )

    @staticmethod
    def _timeout_error(request: LLMRequest | UUID) -> GatewayError:
        request_id = (
            request.request_id if isinstance(request, LLMRequest) else request
        )
        return GatewayError(
            GatewayErrorKind.TIMEOUT,
            "Gateway request timed out",
            request_id=request_id,
            retryable=True,
        )

    @staticmethod
    def _raise_for_status(response: httpx.Response, request_id: UUID) -> None:
        if response.is_success:
            return

        status_code = response.status_code
        if status_code in (401, 403):
            kind = GatewayErrorKind.AUTH
            retryable = False
        elif status_code == 429:
            kind = GatewayErrorKind.RATE_LIMIT
            retryable = True
        elif status_code >= 500:
            kind = GatewayErrorKind.UPSTREAM_UNAVAILABLE
            retryable = True
        else:
            try:
                error_code = str(response.json().get("error", {}).get("code", ""))
            except (TypeError, ValueError):
                error_code = ""
            kind = (
                GatewayErrorKind.CONTENT_POLICY
                if "content_policy" in error_code
                else GatewayErrorKind.INVALID_RESPONSE
            )
            retryable = False

        raise GatewayError(
            kind,
            f"Gateway returned HTTP {status_code}",
            request_id=request_id,
            status_code=status_code,
            retryable=retryable,
        )
