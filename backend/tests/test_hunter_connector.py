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


@pytest.mark.asyncio
async def test_hunter_domain_search_maps_business_filters_and_pagination():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return hunter_response(
            200,
            {"data": {"domain": "acme.com", "organization": "Acme", "emails": []}},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        await HunterClient("hunter-secret", http_client=http_client).domain_search(
            domain="acme.com",
            limit=25,
            offset=50,
            contact_type="personal",
            seniorities=["executive", "senior"],
            departments=["sales", "management"],
            decision_maker=True,
            verification_statuses=["valid"],
        )

    params = requests[0].url.params
    assert params["domain"] == "acme.com"
    assert params["limit"] == "25"
    assert params["offset"] == "50"
    assert params["type"] == "personal"
    assert params["seniority"] == "executive,senior"
    assert params["department"] == "sales,management"
    assert params["decision_maker"] == "true"
    assert params["verification_status"] == "valid"
    assert "linkedin_handle" not in params


@pytest.mark.asyncio
async def test_hunter_domain_search_page_preserves_provider_total_for_resumption():
    def handler(_: httpx.Request):
        return hunter_response(
            200,
            {
                "data": {
                    "domain": "acme.com",
                    "organization": "Acme",
                    "emails": [{"value": "buyer@acme.com"}],
                },
                "meta": {"results": 37},
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        page = await HunterClient(
            "hunter-secret",
            http_client=http_client,
        ).domain_search_page(domain="acme.com", limit=10, offset=20)

    assert page.total_results == 37
    assert page.data["emails"][0]["value"] == "buyer@acme.com"


@pytest.mark.asyncio
async def test_hunter_usage_normalizes_search_or_unified_credit_remaining():
    responses = iter(
        [
            {
                "data": {
                    "requests": {
                        "searches": {"used": 25, "available": 100},
                    }
                }
            },
            {
                "data": {
                    "requests": {
                        "credits": {
                            "used": 40.5,
                            "available": 100.0,
                            "remaining": 59.5,
                        },
                    }
                }
            },
        ]
    )

    def handler(_: httpx.Request):
        return hunter_response(200, next(responses))

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = HunterClient("hunter-secret", http_client=http_client)
        searches = await client.usage()
        credits = await client.usage()

    assert searches.remaining == 75
    assert searches.unit == "searches"
    assert credits.remaining == 59.5
    assert credits.unit == "credits"


@pytest.mark.asyncio
async def test_hunter_email_finder_supports_named_person_without_linkedin_input():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return hunter_response(
            200,
            {
                "data": {
                    "email": "buyer@acme.com",
                    "first_name": "Ada",
                    "last_name": "Buyer",
                    "verification": {"status": "valid"},
                }
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        result = await HunterClient("hunter-secret", http_client=http_client).email_finder(
            domain="acme.com",
            first_name="Ada",
            last_name="Buyer",
            max_duration=12,
        )

    assert result["email"] == "buyer@acme.com"
    params = requests[0].url.params
    assert params["max_duration"] == "12"
    assert "linkedin_handle" not in params
