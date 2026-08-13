import json
from decimal import Decimal

import httpx
import pytest

from app.integrations.fal_media import (
    FalMediaAdapter,
    MediaProviderError,
    MediaQueueState,
)
from app.integrations.provider_media import ProviderMediaURLDenied
from app.services.media.runtime import (
    MediaCapabilityCatalog,
    MediaModelCapability,
    MediaWorkflowMode,
)


MODEL_ID = "fal-ai/acme-video/text-to-video"


def capability_catalog() -> MediaCapabilityCatalog:
    return MediaCapabilityCatalog(
        provider="fal",
        schema_version="fixture-v1",
        models=[
            MediaModelCapability(
                id=MODEL_ID,
                display_name="Acme Video",
                modes=[MediaWorkflowMode.TEXT_TO_VIDEO],
            )
        ],
    )


def adapter(handler, *, resolver=None, max_response_bytes=1_000_000):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return FalMediaAdapter(
        "provider-secret",
        catalog=capability_catalog(),
        http_client=client,
        media_url_resolver=resolver or (lambda _host: ["93.184.216.34"]),
        max_response_bytes=max_response_bytes,
    )


@pytest.mark.asyncio
async def test_fal_submit_adds_only_the_server_configured_webhook_query():
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"request_id": "request-1"})

    client = FalMediaAdapter(
        "provider-secret",
        catalog=capability_catalog(),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        webhook_url="https://agent.example.com/api/v1/webhooks/media/fal",
        media_url_resolver=lambda _host: ["93.184.216.34"],
    )
    try:
        await client.submit(model_id=MODEL_ID, arguments={"prompt": "safe prompt"})
    finally:
        await client.aclose()

    assert observed["url"] == (
        f"https://queue.fal.run/{MODEL_ID}"
        "?fal_webhook=https%3A%2F%2Fagent.example.com%2Fapi%2Fv1%2Fwebhooks%2Fmedia%2Ffal"
    )
    assert observed["payload"] == {"prompt": "safe prompt"}


@pytest.mark.parametrize(
    "webhook_url",
    [
        "http://agent.example.com/webhook",
        "https://user:pass@agent.example.com/webhook",
        "https://agent.example.com/webhook?token=secret",
        "https://agent.example.com/webhook#fragment",
    ],
)
def test_fal_adapter_rejects_unsafe_webhook_urls(webhook_url):
    with pytest.raises(ValueError, match="webhook URL"):
        FalMediaAdapter(
            "provider-secret",
            catalog=capability_catalog(),
            webhook_url=webhook_url,
        )


@pytest.mark.asyncio
async def test_fal_submit_uses_fixed_queue_origin_and_ignores_provider_urls():
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["method"] = request.method
        observed["url"] = str(request.url)
        observed["authorization"] = request.headers["Authorization"]
        observed["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "request_id": "764cabcf-b745-4b3e-ae38-1200304cf45b",
                "response_url": "https://attacker.example/result",
                "status_url": "http://127.0.0.1/admin",
                "cancel_url": "https://attacker.example/cancel",
                "queue_position": 2,
            },
        )

    client = adapter(handler)
    try:
        receipt = await client.submit(
            model_id=MODEL_ID,
            arguments={"prompt": "A compliant export product film"},
        )
    finally:
        await client.aclose()

    assert observed == {
        "method": "POST",
        "url": f"https://queue.fal.run/{MODEL_ID}",
        "authorization": "Key provider-secret",
        "payload": {"prompt": "A compliant export product film"},
    }
    assert receipt.request_id == "764cabcf-b745-4b3e-ae38-1200304cf45b"
    assert receipt.queue_position == 2
    assert "url" not in receipt.model_dump_json().lower()


@pytest.mark.asyncio
async def test_fal_adapter_rejects_unapproved_models_and_request_id_injection():
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    client = adapter(handler)
    try:
        with pytest.raises(ValueError, match="approved"):
            await client.submit(
                model_id="fal-ai/unapproved",
                arguments={"prompt": "test"},
            )
        with pytest.raises(ValueError, match="request ID"):
            await client.status(
                model_id=MODEL_ID,
                request_id="../../internal",
            )
    finally:
        await client.aclose()

    assert calls == 0


@pytest.mark.asyncio
async def test_fal_status_normalizes_states_without_exposing_logs_or_error_detail():
    responses = iter(
        [
            {"status": "IN_QUEUE", "queue_position": 3, "logs": [{"message": "PII"}]},
            {"status": "IN_PROGRESS", "logs": [{"message": "secret prompt"}]},
            {
                "status": "COMPLETED",
                "error": "provider stack trace and customer payload",
                "error_type": "runner_connection_timeout",
            },
        ]
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(responses))

    client = adapter(handler)
    try:
        queued = await client.status(model_id=MODEL_ID, request_id="request-1")
        running = await client.status(model_id=MODEL_ID, request_id="request-1")
        failed = await client.status(model_id=MODEL_ID, request_id="request-1")
    finally:
        await client.aclose()

    assert queued.state is MediaQueueState.QUEUED
    assert queued.queue_position == 3
    assert running.state is MediaQueueState.RUNNING
    assert failed.state is MediaQueueState.FAILED
    assert failed.error_code == "runner_connection_timeout"
    serialized = queued.model_dump_json() + running.model_dump_json() + failed.model_dump_json()
    assert "logs" not in serialized
    assert "customer payload" not in serialized


@pytest.mark.asyncio
async def test_fal_result_returns_only_validated_provider_media_urls():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"X-Fal-Billable-Units": "5.25"},
            json={
                "video": {
                    "url": "https://v3.fal.media/files/video.mp4",
                    "content_type": "video/mp4",
                    "width": 1280,
                    "height": 720,
                },
                "seed": 42,
                "prompt": "must not be persisted in normalized result",
            },
        )

    client = adapter(handler)
    try:
        result = await client.result(model_id=MODEL_ID, request_id="request-1")
    finally:
        await client.aclose()

    assert len(result.outputs) == 1
    assert result.outputs[0].url == "https://v3.fal.media/files/video.mp4"
    assert result.outputs[0].content_type == "video/mp4"
    assert result.provider_request_id == "request-1"
    assert result.model_id == MODEL_ID
    assert result.billable_units == Decimal("5.25")
    assert "prompt" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_units",
    [None, "", "-1", "+1", "01", "1e3", "nan", "1.1234567890"],
)
async def test_fal_result_rejects_missing_or_ambiguous_billable_units(raw_units):
    def handler(_request: httpx.Request) -> httpx.Response:
        headers = {} if raw_units is None else {"X-Fal-Billable-Units": raw_units}
        return httpx.Response(
            200,
            headers=headers,
            json={"video": {"url": "https://v3.fal.media/files/video.mp4"}},
        )

    client = adapter(handler)
    try:
        with pytest.raises(MediaProviderError) as invalid:
            await client.result(model_id=MODEL_ID, request_id="request-1")
    finally:
        await client.aclose()

    assert invalid.value.error_code == "invalid_provider_billing_receipt"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "resolver"),
    [
        ("https://attacker.example/result.mp4", lambda _host: ["93.184.216.34"]),
        ("https://v3.fal.media/result.mp4", lambda _host: ["127.0.0.1"]),
    ],
)
async def test_fal_result_fails_closed_for_unapproved_or_private_media_urls(
    url,
    resolver,
):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"X-Fal-Billable-Units": "1"},
            json={"video": {"url": url}},
        )

    client = adapter(handler, resolver=resolver)
    try:
        with pytest.raises(ProviderMediaURLDenied):
            await client.result(model_id=MODEL_ID, request_id="request-1")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_fal_adapter_rejects_redirects_and_oversized_responses():
    responses = iter(
        [
            httpx.Response(307, headers={"location": "http://127.0.0.1"}),
            httpx.Response(200, content=b"{" + b"x" * 2048 + b"}"),
        ]
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return next(responses)

    client = adapter(handler, max_response_bytes=1024)
    try:
        with pytest.raises(MediaProviderError) as redirected:
            await client.status(model_id=MODEL_ID, request_id="request-1")
        with pytest.raises(MediaProviderError) as oversized:
            await client.status(model_id=MODEL_ID, request_id="request-1")
    finally:
        await client.aclose()

    assert redirected.value.error_code == "provider_redirect_denied"
    assert oversized.value.error_code == "provider_response_too_large"


@pytest.mark.asyncio
async def test_fal_adapter_normalizes_transport_and_http_failures():
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("contains internal details", request=request)

    timeout_client = adapter(timeout_handler)
    try:
        with pytest.raises(MediaProviderError) as timeout:
            await timeout_client.status(model_id=MODEL_ID, request_id="request-1")
    finally:
        await timeout_client.aclose()
    assert timeout.value.error_code == "provider_timeout"
    assert timeout.value.retryable is True
    assert "internal details" not in str(timeout.value)

    def auth_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "provider-secret is invalid"})

    auth_client = adapter(auth_handler)
    try:
        with pytest.raises(MediaProviderError) as auth:
            await auth_client.status(model_id=MODEL_ID, request_id="request-1")
    finally:
        await auth_client.aclose()
    assert auth.value.error_code == "provider_authentication_failed"
    assert auth.value.retryable is False
    assert "provider-secret" not in str(auth.value)


@pytest.mark.asyncio
async def test_fal_cancel_uses_documented_put_endpoint():
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["method"] = request.method
        observed["url"] = str(request.url)
        return httpx.Response(202, json={"status": "CANCELLATION_REQUESTED"})

    client = adapter(handler)
    try:
        cancelled = await client.cancel(model_id=MODEL_ID, request_id="request-1")
    finally:
        await client.aclose()

    assert cancelled.accepted is True
    assert observed == {
        "method": "PUT",
        "url": f"https://queue.fal.run/{MODEL_ID}/requests/request-1/cancel",
    }


@pytest.mark.asyncio
async def test_fal_completed_status_keeps_only_safe_metrics():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"X-Fal-Billable-Units": "1"},
            json={
                "status": "COMPLETED",
                "metrics": {"inference_time": 3.42, "private_metric": "drop"},
                "logs": [{"message": "drop"}],
            },
        )

    client = adapter(handler)
    try:
        status = await client.status(model_id=MODEL_ID, request_id="request-1")
    finally:
        await client.aclose()

    assert status.state is MediaQueueState.COMPLETED
    assert status.inference_seconds == 3.42
    assert "private_metric" not in status.model_dump_json()


@pytest.mark.asyncio
async def test_fal_probe_treats_documented_queue_404_as_authenticated_reachability():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"status": "NOT_FOUND"})

    client = adapter(handler)
    try:
        discovered = await client.discover_capabilities("provider-secret")
        probe = await client.probe(
            api_key="provider-secret",
            model_ids=[MODEL_ID],
        )
        denied = await client.probe(
            api_key="different-secret",
            model_ids=[MODEL_ID],
        )
    finally:
        await client.aclose()

    assert discovered == capability_catalog()
    assert probe.ready is True
    assert probe.reachable is True
    assert denied.ready is False
    assert denied.issues == ["provider_authentication_failed"]


@pytest.mark.asyncio
async def test_fal_invalid_json_and_rate_limit_are_normalized():
    responses = iter(
        [
            httpx.Response(200, content=b"not-json"),
            httpx.Response(429, json={"detail": "account and request details"}),
        ]
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return next(responses)

    client = adapter(handler)
    try:
        with pytest.raises(MediaProviderError) as invalid:
            await client.status(model_id=MODEL_ID, request_id="request-1")
        with pytest.raises(MediaProviderError) as limited:
            await client.status(model_id=MODEL_ID, request_id="request-1")
    finally:
        await client.aclose()

    assert invalid.value.error_code == "invalid_provider_response"
    assert limited.value.error_code == "provider_rate_limited"
    assert limited.value.retryable is True
