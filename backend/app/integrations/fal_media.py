"""Strict fal queue adapter with secret-safe errors and validated media outputs."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import Enum
import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.integrations.provider_media import SafeProviderMediaURLPolicy
from app.services.media.runtime import (
    MediaCapabilityCatalog,
    MediaModelCapability,
    MediaProviderProbe,
    MediaProviderPrice,
    MediaWorkflowMode,
)


class MediaProviderError(RuntimeError):
    """Normalized provider failure that never includes response bodies or keys."""

    def __init__(
        self,
        *,
        error_code: str,
        retryable: bool,
        status_code: Optional[int] = None,
    ) -> None:
        self.error_code = error_code
        self.retryable = retryable
        self.status_code = status_code
        super().__init__(error_code)


class MediaQueueState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class MediaSubmissionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    queue_position: Optional[int] = Field(default=None, ge=0)


class MediaQueueStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: MediaQueueState
    queue_position: Optional[int] = Field(default=None, ge=0)
    inference_seconds: Optional[float] = Field(default=None, ge=0)
    error_code: Optional[str] = Field(default=None, max_length=100)


class MediaOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=8000)
    content_type: Optional[str] = Field(default=None, max_length=100)
    width: Optional[int] = Field(default=None, ge=1, le=100_000)
    height: Optional[int] = Field(default=None, ge=1, le=100_000)


class MediaProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_request_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    model_id: str = Field(
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9._/-]+$",
    )
    billable_units: Decimal = Field(ge=0, max_digits=21, decimal_places=9)
    outputs: List[MediaOutput] = Field(min_length=1, max_length=20)


class MediaCancellation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool


class FalMediaAdapter:
    """Call only documented fal queue endpoints derived from approved model IDs."""

    BASE_URL = "https://queue.fal.run"
    REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
    TRANSIENT_ERROR_TYPES = {
        "request_timeout",
        "startup_timeout",
        "runner_scheduling_failure",
        "runner_connection_timeout",
        "runner_disconnected",
        "runner_connection_refused",
        "runner_connection_error",
        "runner_incomplete_response",
        "runner_server_error",
        "internal_error",
    }
    SAFE_ERROR_TYPES = TRANSIENT_ERROR_TYPES | {
        "client_disconnected",
        "client_cancelled",
        "bad_request",
    }

    def __init__(
        self,
        api_key: str,
        *,
        catalog: MediaCapabilityCatalog,
        http_client: Optional[httpx.AsyncClient] = None,
        timeout_seconds: float = 30.0,
        media_url_resolver=None,
        max_response_bytes: int = 1_000_000,
        max_request_bytes: int = 1_000_000,
        webhook_url: Optional[str] = None,
    ) -> None:
        key = api_key.strip()
        if not key:
            raise ValueError("fal API key is required")
        if catalog.provider != "fal":
            raise ValueError("fal adapter requires a fal capability catalog")
        if timeout_seconds <= 0 or timeout_seconds > 300:
            raise ValueError("fal timeout must be between 0 and 300 seconds")
        if max_response_bytes < 1024:
            raise ValueError("fal response limit is too small")
        if max_request_bytes < 1024:
            raise ValueError("fal request limit is too small")
        self._api_key = key
        self._catalog = catalog
        self._model_ids = frozenset(model.id for model in catalog.models)
        self._timeout = httpx.Timeout(timeout_seconds)
        self._client = http_client or httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=False,
        )
        self._owns_client = http_client is None
        self._max_response_bytes = max_response_bytes
        self._max_request_bytes = max_request_bytes
        self._webhook_url = self._validate_webhook_url(webhook_url)
        self._media_policy = SafeProviderMediaURLPolicy(
            allowed_hosts={"fal.media", "*.fal.media"},
            resolver=media_url_resolver,
        )

    async def discover_capabilities(self, api_key: str) -> MediaCapabilityCatalog:
        if api_key.strip() != self._api_key:
            raise ValueError("fal adapter key does not match runtime key")
        return self._catalog.model_copy(deep=True)

    async def probe(
        self,
        *,
        api_key: str,
        model_ids: List[str],
    ) -> MediaProviderProbe:
        if api_key.strip() != self._api_key:
            return MediaProviderProbe(
                ready=False,
                reachable=False,
                issues=["provider_authentication_failed"],
            )
        for model_id in model_ids:
            self._approve_model(model_id)
        sentinel = "00000000-0000-0000-0000-000000000000"
        model_id = model_ids[0]
        try:
            response = await self._raw_request(
                "GET",
                self._request_path(model_id, sentinel, "status"),
            )
        except MediaProviderError as exc:
            return MediaProviderProbe(
                ready=False,
                reachable=exc.status_code is not None,
                issues=[exc.error_code],
            )
        if response.status_code in {200, 404}:
            return MediaProviderProbe(ready=True, reachable=True, issues=[])
        try:
            self._raise_http_error(response)
        except MediaProviderError as exc:
            return MediaProviderProbe(
                ready=False,
                reachable=True,
                issues=[exc.error_code],
            )
        return MediaProviderProbe(
            ready=False,
            reachable=True,
            issues=["provider_probe_inconclusive"],
        )

    async def submit(
        self,
        *,
        model_id: str,
        arguments: Dict[str, Any],
    ) -> MediaSubmissionReceipt:
        self._approve_model(model_id)
        payload = self._encode_arguments(arguments)
        path = f"/{model_id}"
        if self._webhook_url is not None:
            path = f"{path}?{urlencode({'fal_webhook': self._webhook_url})}"
        response = await self._request("POST", path, content=payload)
        body = self._json_object(response)
        request_id = body.get("request_id")
        if not isinstance(request_id, str) or not self.REQUEST_ID_PATTERN.fullmatch(
            request_id
        ):
            raise self._invalid_response()
        queue_position = body.get("queue_position")
        if queue_position is not None and (
            not isinstance(queue_position, int) or queue_position < 0
        ):
            raise self._invalid_response()
        return MediaSubmissionReceipt(
            request_id=request_id,
            queue_position=queue_position,
        )

    async def status(
        self,
        *,
        model_id: str,
        request_id: str,
    ) -> MediaQueueStatus:
        response = await self._request(
            "GET",
            self._request_path(model_id, request_id, "status"),
        )
        body = self._json_object(response)
        raw_status = body.get("status")
        if raw_status == "IN_QUEUE":
            position = body.get("queue_position")
            if position is not None and (
                not isinstance(position, int) or position < 0
            ):
                raise self._invalid_response()
            return MediaQueueStatus(
                state=MediaQueueState.QUEUED,
                queue_position=position,
            )
        if raw_status == "IN_PROGRESS":
            return MediaQueueStatus(state=MediaQueueState.RUNNING)
        if raw_status == "COMPLETED":
            error_type = body.get("error_type")
            if body.get("error") is not None or error_type is not None:
                return MediaQueueStatus(
                    state=MediaQueueState.FAILED,
                    error_code=self._safe_error_type(error_type),
                )
            inference_seconds = None
            metrics = body.get("metrics")
            if isinstance(metrics, dict):
                raw_inference = metrics.get("inference_time")
                if isinstance(raw_inference, (int, float)) and raw_inference >= 0:
                    inference_seconds = float(raw_inference)
            return MediaQueueStatus(
                state=MediaQueueState.COMPLETED,
                inference_seconds=inference_seconds,
            )
        raise self._invalid_response()

    async def result(
        self,
        *,
        model_id: str,
        request_id: str,
    ) -> MediaProviderResult:
        response = await self._request(
            "GET",
            self._request_path(model_id, request_id),
        )
        body = self._json_object(response)
        billable_units = self._billable_units(response)
        outputs: List[MediaOutput] = []
        candidates: List[Any] = []
        images = body.get("images")
        if isinstance(images, list):
            candidates.extend(images)
        for key in ("image", "video"):
            if body.get(key) is not None:
                candidates.append(body[key])
        for candidate in candidates[:20]:
            if not isinstance(candidate, dict):
                raise self._invalid_response()
            raw_url = candidate.get("url")
            if not isinstance(raw_url, str):
                raise self._invalid_response()
            approved = self._media_policy.validate(raw_url)
            try:
                outputs.append(
                    MediaOutput(
                        url=approved.url,
                        content_type=candidate.get("content_type"),
                        width=candidate.get("width"),
                        height=candidate.get("height"),
                    )
                )
            except Exception as exc:
                raise self._invalid_response() from exc
        if not outputs:
            raise self._invalid_response()
        return MediaProviderResult(
            provider_request_id=request_id,
            model_id=model_id,
            billable_units=billable_units,
            outputs=outputs,
        )

    async def cancel(
        self,
        *,
        model_id: str,
        request_id: str,
    ) -> MediaCancellation:
        response = await self._request(
            "PUT",
            self._request_path(model_id, request_id, "cancel"),
            accepted_statuses={202},
        )
        body = self._json_object(response)
        if body.get("status") != "CANCELLATION_REQUESTED":
            raise self._invalid_response()
        return MediaCancellation(accepted=True)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _approve_model(self, model_id: str) -> None:
        if model_id not in self._model_ids:
            raise ValueError("media model is not approved by this runtime revision")

    @staticmethod
    def _validate_webhook_url(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or len(value) > 2000
        ):
            raise ValueError("fal webhook URL is invalid")
        return value

    def _request_path(
        self,
        model_id: str,
        request_id: str,
        operation: Optional[str] = None,
    ) -> str:
        self._approve_model(model_id)
        if not self.REQUEST_ID_PATTERN.fullmatch(request_id):
            raise ValueError("fal request ID is invalid")
        suffix = f"/{operation}" if operation else ""
        return f"/{model_id}/requests/{request_id}{suffix}"

    def _encode_arguments(self, arguments: Dict[str, Any]) -> bytes:
        if not isinstance(arguments, dict) or not arguments:
            raise ValueError("fal arguments must be a non-empty object")
        try:
            payload = json.dumps(
                arguments,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("fal arguments must be valid JSON") from exc
        if len(payload) > self._max_request_bytes:
            raise ValueError("fal request exceeds the configured size limit")
        return payload

    async def _request(
        self,
        method: str,
        path: str,
        *,
        content: Optional[bytes] = None,
        accepted_statuses: Optional[set[int]] = None,
    ) -> httpx.Response:
        response = await self._raw_request(method, path, content=content)
        allowed = accepted_statuses or set(range(200, 300))
        if 300 <= response.status_code < 400:
            raise MediaProviderError(
                error_code="provider_redirect_denied",
                retryable=False,
                status_code=response.status_code,
            )
        if response.status_code not in allowed:
            self._raise_http_error(response)
        self._assert_response_size(response)
        return response

    async def _raw_request(
        self,
        method: str,
        path: str,
        *,
        content: Optional[bytes] = None,
    ) -> httpx.Response:
        try:
            return await self._client.request(
                method,
                f"{self.BASE_URL}{path}",
                headers={
                    "Authorization": f"Key {self._api_key}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                content=content,
                timeout=self._timeout,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise MediaProviderError(
                error_code="provider_timeout",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise MediaProviderError(
                error_code="provider_transport_error",
                retryable=True,
            ) from exc

    def _raise_http_error(self, response: httpx.Response) -> None:
        status = response.status_code
        if status in {401, 403}:
            code, retryable = "provider_authentication_failed", False
        elif status == 404:
            code, retryable = "provider_request_not_found", False
        elif status == 409:
            code, retryable = "provider_conflict", False
        elif status == 422:
            code, retryable = "provider_validation_failed", False
        elif status == 429:
            code, retryable = "provider_rate_limited", True
        elif status >= 500:
            code, retryable = "provider_unavailable", True
        else:
            code, retryable = "provider_request_rejected", False
        raise MediaProviderError(
            error_code=code,
            retryable=retryable,
            status_code=status,
        )

    def _assert_response_size(self, response: httpx.Response) -> None:
        raw_length = response.headers.get("content-length")
        if raw_length is not None:
            try:
                if int(raw_length) > self._max_response_bytes:
                    raise MediaProviderError(
                        error_code="provider_response_too_large",
                        retryable=False,
                        status_code=response.status_code,
                    )
            except ValueError:
                raise self._invalid_response()
        if len(response.content) > self._max_response_bytes:
            raise MediaProviderError(
                error_code="provider_response_too_large",
                retryable=False,
                status_code=response.status_code,
            )

    def _json_object(self, response: httpx.Response) -> Dict[str, Any]:
        self._assert_response_size(response)
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise self._invalid_response() from exc
        if not isinstance(body, dict):
            raise self._invalid_response()
        return body

    def _safe_error_type(self, value: Any) -> str:
        if isinstance(value, str) and value in self.SAFE_ERROR_TYPES:
            return value
        return "provider_request_failed"

    @staticmethod
    def _billable_units(response: httpx.Response) -> Decimal:
        raw = response.headers.get("X-Fal-Billable-Units")
        if raw is None or not re.fullmatch(
            r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,9})?",
            raw,
        ):
            raise MediaProviderError(
                error_code="invalid_provider_billing_receipt",
                retryable=False,
                status_code=response.status_code,
            )
        try:
            value = Decimal(raw)
        except InvalidOperation as exc:
            raise MediaProviderError(
                error_code="invalid_provider_billing_receipt",
                retryable=False,
                status_code=response.status_code,
            ) from exc
        return value

    @staticmethod
    def _invalid_response() -> MediaProviderError:
        return MediaProviderError(
            error_code="invalid_provider_response",
            retryable=False,
        )


def curated_fal_catalog() -> MediaCapabilityCatalog:
    """Small server-owned allowlist verified against fal docs on 2026-08-11."""
    return MediaCapabilityCatalog(
        provider="fal",
        schema_version="curated-2026-08-11",
        models=[
            MediaModelCapability(
                id="fal-ai/flux/schnell",
                display_name="FLUX.1 schnell",
                modes=[MediaWorkflowMode.TEXT_TO_IMAGE],
            ),
            MediaModelCapability(
                id="fal-ai/kling-video/v2/master/image-to-video",
                display_name="Kling Video V2 Master · Image to Video",
                modes=[MediaWorkflowMode.IMAGE_TO_VIDEO],
            ),
            MediaModelCapability(
                id="fal-ai/kling-video/v2/master/text-to-video",
                display_name="Kling Video V2 Master · Text to Video",
                modes=[MediaWorkflowMode.TEXT_TO_VIDEO],
            ),
        ],
    )


class FalMediaProviderControl:
    """Construct key-scoped adapters without retaining a secret on the service."""

    def __init__(
        self,
        catalog: Optional[MediaCapabilityCatalog] = None,
    ) -> None:
        self._catalog = catalog or curated_fal_catalog()

    def get_capabilities(self) -> MediaCapabilityCatalog:
        return self._catalog.model_copy(deep=True)

    async def discover_capabilities(self, api_key: str) -> MediaCapabilityCatalog:
        if not api_key.strip():
            raise ValueError("fal API key is required")
        return self.get_capabilities()

    async def discover_pricing(
        self,
        *,
        api_key: str,
        model_ids: List[str],
    ) -> List[MediaProviderPrice]:
        if not api_key.strip() or not 1 <= len(model_ids) <= 50:
            raise ValueError("fal pricing request is invalid")
        if len(set(model_ids)) != len(model_ids):
            raise ValueError("fal pricing model IDs must be unique")
        for model_id in model_ids:
            if model_id not in {model.id for model in self._catalog.models}:
                raise ValueError("fal pricing model is not approved")
        params = [("endpoint_id", model_id) for model_id in model_ids]
        try:
            async with httpx.AsyncClient(
                base_url="https://api.fal.ai/v1",
                timeout=15.0,
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    "https://api.fal.ai/v1/models/pricing",
                    params=params,
                    headers={
                        "Authorization": f"Key {api_key}",
                        "Accept": "application/json",
                    },
                )
        except httpx.HTTPError as exc:
            raise RuntimeError("media_provider_pricing_unavailable") from exc
        if response.status_code != 200 or len(response.content) > 262_144:
            raise RuntimeError("media_provider_pricing_unavailable")
        try:
            payload = response.json()
            prices = payload["prices"]
            if (
                not isinstance(prices, list)
                or payload.get("has_more") is not False
                or payload.get("next_cursor") is not None
            ):
                raise ValueError
            parsed = [MediaProviderPrice.model_validate(item) for item in prices]
            endpoint_ids = [price.endpoint_id for price in parsed]
            if len(set(endpoint_ids)) != len(endpoint_ids):
                raise ValueError
            return parsed
        except Exception as exc:
            raise RuntimeError("media_provider_pricing_invalid") from exc

    async def probe(
        self,
        *,
        api_key: str,
        model_ids: List[str],
    ) -> MediaProviderProbe:
        adapter = FalMediaAdapter(api_key, catalog=self._catalog)
        try:
            return await adapter.probe(api_key=api_key, model_ids=model_ids)
        finally:
            await adapter.aclose()
