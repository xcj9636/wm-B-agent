import pytest

from app.config import Settings
from app.integrations.llm_gateway import LLMGatewayClient
from app.services.llm.factory import build_gateway_client


def gateway_settings(**overrides):
    values = {
        "_env_file": None,
        "LLM_BACKEND": "omniroute",
        "OMNIROUTE_API_KEY": "gateway-secret",
        "OMNIROUTE_ALLOWED_PROVIDERS": ["approved-provider"],
        "OMNIROUTE_MODEL_MESSAGE_DRAFT": "b-agent-draft-v1",
        "OMNIROUTE_MODEL_LIVE_REPLY": "b-agent-reply-v1",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"OMNIROUTE_ALLOWED_PROVIDERS": []}, "provider allowlist"),
        ({"OMNIROUTE_MODEL_MESSAGE_DRAFT": ""}, "message_draft"),
        ({"OMNIROUTE_MODEL_LIVE_REPLY": ""}, "live_reply"),
    ],
)
def test_gateway_client_factory_rejects_incomplete_routing_policy(
    overrides,
    message,
):
    with pytest.raises(RuntimeError, match=message):
        build_gateway_client(gateway_settings(**overrides))


@pytest.mark.asyncio
async def test_gateway_client_factory_accepts_complete_routing_policy():
    client = build_gateway_client(gateway_settings())

    assert isinstance(client, LLMGatewayClient)

    await client.aclose()
