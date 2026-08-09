import httpx
import pytest

from app.integrations.hunter import HunterClient, HunterConnectorError


def hunter_response(status_code: int, payload: dict):
    return httpx.Response(status_code, json=payload)


@pytest.mark.asyncio
async def test_hunter_probe_uses_header_auth_and_never_puts_secret_in_url():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return hunter_response(
            200,
            {"data": {"plan_name": "Growth", "requests": {"searches": {"available": 50}}}},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        result = await HunterClient("hunter-secret", http_client=http_client).probe()

    assert result["plan_name"] == "Growth"
    assert len(requests) == 1
    assert requests[0].headers["X-API-KEY"] == "hunter-secret"
    assert "hunter-secret" not in str(requests[0].url)
    assert "api_key" not in requests[0].url.params


@pytest.mark.asyncio
async def test_hunter_email_verifier_normalizes_success_response():
    def handler(_: httpx.Request):
        return hunter_response(
            200,
            {
                "data": {
                    "status": "valid",
                    "score": 96,
                    "regexp": True,
                    "gibberish": False,
                    "disposable": False,
                    "webmail": False,
                    "mx_records": True,
                    "smtp_server": True,
                    "smtp_check": True,
                    "accept_all": False,
                    "block": False,
                }
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        result = await HunterClient("hunter-secret", http_client=http_client).verify_email(
            "buyer@example.com"
        )

    assert result.status == "valid"
    assert result.score == 96
    assert result.retryable is False
    assert result.details["smtp_check"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "error_code", "retryable", "legal_restriction"),
    [
        (202, "verification_in_progress", True, False),
        (222, "smtp_result_unexpected", True, False),
        (403, "rate_limited", True, False),
        (429, "quota_exhausted", False, False),
        (451, "legal_restriction", False, True),
    ],
)
async def test_hunter_email_verifier_preserves_retry_and_legal_semantics(
    status_code,
    error_code,
    retryable,
    legal_restriction,
):
    def handler(_: httpx.Request):
        return hunter_response(status_code, {"errors": [{"details": "provider detail"}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = HunterClient("hunter-secret", http_client=http_client)
        with pytest.raises(HunterConnectorError) as error:
            await client.verify_email("buyer@example.com")

    assert error.value.error_code == error_code
    assert error.value.retryable is retryable
    assert error.value.legal_restriction is legal_restriction
    assert "hunter-secret" not in str(error.value)

