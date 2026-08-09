import json

import httpx
import pytest

from app.integrations.llm_gateway import LLMGatewayClient
from app.services.llm.contracts import (
    GatewayError,
    GatewayErrorKind,
    LLMMessage,
    LLMRequest,
    LLMUseCase,
)


def llm_request(**overrides):
    values = {
        "use_case": LLMUseCase.LEAD_CLASSIFICATION,
        "messages": [LLMMessage(role="user", content="Classify this lead")],
    }
    values.update(overrides)
    return LLMRequest(**values)


def gateway_client(handler, aliases=None):
    http_client = httpx.AsyncClient(
        base_url="http://omniroute.test",
        transport=httpx.MockTransport(handler),
    )
    return LLMGatewayClient(
        base_url="http://omniroute.test",
        api_key="gateway-secret",
        model_aliases=aliases
        or {LLMUseCase.LEAD_CLASSIFICATION: "b-agent-intent-cheap-v1"},
        http_client=http_client,
    )


@pytest.mark.asyncio
async def test_completion_translates_the_openai_compatible_contract():
    captured = {}

    def handler(request):
        captured["headers"] = request.headers
        captured["json"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "gw-123",
                "model": "resolved-model",
                "provider": "approved-provider",
                "choices": [
                    {
                        "message": {"content": '{"intent":"price_inquiry"}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
        )

    client = gateway_client(handler)
    request = llm_request(
        response_schema={
            "type": "object",
            "properties": {"intent": {"type": "string"}},
            "required": ["intent"],
        }
    )

    response = await client.complete(request)

    assert captured["headers"]["authorization"] == "Bearer gateway-secret"
    assert captured["headers"]["x-request-id"] == str(request.request_id)
    assert captured["json"]["model"] == "b-agent-intent-cheap-v1"
    assert captured["json"]["response_format"]["type"] == "json_schema"
    assert response.gateway_request_id == "gw-123"
    assert response.resolved_model == "resolved-model"
    assert response.resolved_provider == "approved-provider"
    assert response.usage.total_tokens == 15


@pytest.mark.asyncio
async def test_stream_parses_sse_comments_done_and_final_usage():
    def handler(request):
        assert json.loads(request.content)["stream"] is True
        body = "\n".join(
            [
                ": heartbeat",
                "",
                'data: {"id":"gw-stream","choices":[{"delta":{"content":"hel"}}]}',
                "",
                'data: {"id":"gw-stream","choices":[{"delta":{"content":"lo"},"finish_reason":"stop"}]}',
                "",
                'data: {"id":"gw-stream","choices":[],"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}',
                "",
                "data: [DONE]",
                "",
            ]
        )
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    client = gateway_client(handler)

    chunks = [chunk async for chunk in client.stream(llm_request())]

    assert "".join(chunk.delta for chunk in chunks) == "hello"
    assert chunks[-2].finish_reason == "stop"
    assert chunks[-1].usage.total_tokens == 3
    assert all(chunk.gateway_request_id == "gw-stream" for chunk in chunks)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "kind", "retryable"),
    [
        (401, GatewayErrorKind.AUTH, False),
        (429, GatewayErrorKind.RATE_LIMIT, True),
        (503, GatewayErrorKind.UPSTREAM_UNAVAILABLE, True),
    ],
)
async def test_http_failures_are_normalized(status_code, kind, retryable):
    client = gateway_client(lambda request: httpx.Response(status_code))

    with pytest.raises(GatewayError) as raised:
        await client.complete(llm_request())

    assert raised.value.kind == kind
    assert raised.value.status_code == status_code
    assert raised.value.retryable is retryable


@pytest.mark.asyncio
async def test_missing_or_dynamic_route_fails_closed_before_network_call():
    def unexpected_request(request):
        raise AssertionError("network must not be called")

    missing = gateway_client(unexpected_request, aliases={})
    dynamic = gateway_client(
        unexpected_request,
        aliases={LLMUseCase.LEAD_CLASSIFICATION: "auto/cheapest"},
    )

    for client in (missing, dynamic):
        with pytest.raises(GatewayError) as raised:
            await client.complete(llm_request())
        assert raised.value.kind == GatewayErrorKind.INVALID_RESPONSE
        assert raised.value.retryable is False
