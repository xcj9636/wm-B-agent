import pytest
from pydantic import ValidationError

from app.services.llm.contracts import (
    GatewayErrorKind,
    LLMMessage,
    LLMRequest,
    LLMUsage,
    LLMUseCase,
)


def test_llm_request_exposes_use_cases_without_provider_or_model_ids():
    request = LLMRequest(
        use_case=LLMUseCase.LEAD_CLASSIFICATION,
        messages=[LLMMessage(role="user", content="Classify this lead")],
    )

    assert request.use_case == LLMUseCase.LEAD_CLASSIFICATION
    assert "model" not in request.model_dump()
    assert "provider" not in request.model_dump()


@pytest.mark.parametrize("field", ["model", "provider"])
def test_llm_request_rejects_gateway_routing_details(field):
    with pytest.raises(ValidationError):
        LLMRequest.model_validate(
            {
                "use_case": "message_draft",
                "messages": [{"role": "user", "content": "Draft a reply"}],
                field: "auto/cheapest",
            }
        )


def test_usage_derives_and_validates_total_tokens():
    usage = LLMUsage(input_tokens=12, output_tokens=8)

    assert usage.total_tokens == 20

    with pytest.raises(ValidationError):
        LLMUsage(input_tokens=12, output_tokens=8, total_tokens=19)


def test_gateway_error_taxonomy_is_frozen():
    assert {kind.value for kind in GatewayErrorKind} == {
        "timeout",
        "rate_limit",
        "auth",
        "content_policy",
        "invalid_response",
        "upstream_unavailable",
    }
