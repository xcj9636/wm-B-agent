import pytest
from pydantic import ValidationError

from app.config import Settings


def test_gateway_is_disabled_by_default_and_has_no_dynamic_routes():
    settings = Settings(_env_file=None)

    assert settings.LLM_BACKEND == "direct"
    assert settings.OMNIROUTE_BASE_URL == "http://omniroute:20128"
    assert settings.omniroute_model_aliases() == {}


def test_only_explicit_business_aliases_are_exposed():
    settings = Settings(
        _env_file=None,
        OMNIROUTE_MODEL_LEAD_CLASSIFICATION="b-agent-intent-cheap-v1",
        OMNIROUTE_MODEL_LIVE_REPLY="b-agent-reply-reliable-v1",
    )

    assert settings.omniroute_model_aliases() == {
        "lead_classification": "b-agent-intent-cheap-v1",
        "live_reply": "b-agent-reply-reliable-v1",
    }


@pytest.mark.parametrize("route", ["auto/cheapest", " AUTO/fastest "])
def test_dynamic_routes_are_rejected_at_configuration_time(route):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            OMNIROUTE_MODEL_LIVE_REPLY=route,
        )
