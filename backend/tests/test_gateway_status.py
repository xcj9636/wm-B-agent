import pytest

from app.config import Settings
from app.services.llm.status import GatewayStatusService


class FakeGatewayClient:
    def __init__(self, models=None, error=None):
        self.models = set(models or [])
        self.error = error
        self.closed = False

    async def list_models(self):
        if self.error:
            raise self.error
        return self.models

    async def aclose(self):
        self.closed = True


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


@pytest.mark.asyncio
async def test_disabled_gateway_reports_without_connecting():
    called = False

    def unexpected_factory():
        nonlocal called
        called = True
        raise AssertionError("disabled gateway must not connect")

    status = await GatewayStatusService(
        Settings(_env_file=None),
        client_factory=unexpected_factory,
    ).check()

    assert status.enabled is False
    assert status.ready is False
    assert status.reachable is None
    assert status.issues == ["gateway_disabled"]
    assert called is False


@pytest.mark.asyncio
async def test_enabled_gateway_fails_closed_on_incomplete_policy():
    client = FakeGatewayClient({"b-agent-draft-v1"})
    settings = gateway_settings(
        OMNIROUTE_ALLOWED_PROVIDERS=[],
        OMNIROUTE_MODEL_LIVE_REPLY="",
    )

    status = await GatewayStatusService(
        settings,
        client_factory=lambda: client,
    ).check()

    assert status.ready is False
    assert status.reachable is None
    assert status.missing_aliases == ["live_reply"]
    assert "provider_allowlist_empty" in status.issues
    assert "required_aliases_missing" in status.issues
    assert client.closed is False


@pytest.mark.asyncio
async def test_enabled_gateway_requires_all_configured_aliases_to_be_exposed():
    client = FakeGatewayClient({"b-agent-draft-v1"})

    status = await GatewayStatusService(
        gateway_settings(),
        client_factory=lambda: client,
    ).check()

    assert status.enabled is True
    assert status.ready is False
    assert status.reachable is True
    assert status.missing_models == ["b-agent-reply-v1"]
    assert status.issues == ["configured_aliases_not_exposed"]
    assert client.closed is True


@pytest.mark.asyncio
async def test_enabled_gateway_is_ready_when_policy_and_models_match():
    client = FakeGatewayClient({"b-agent-draft-v1", "b-agent-reply-v1"})

    status = await GatewayStatusService(
        gateway_settings(),
        client_factory=lambda: client,
    ).check()

    assert status.ready is True
    assert status.reachable is True
    assert status.missing_aliases == []
    assert status.missing_models == []
    assert status.allowed_providers == ["approved-provider"]
    assert status.issues == []
    assert client.closed is True


@pytest.mark.asyncio
async def test_gateway_probe_failure_is_sanitized_and_client_is_closed():
    client = FakeGatewayClient(error=RuntimeError("secret upstream detail"))

    status = await GatewayStatusService(
        gateway_settings(),
        client_factory=lambda: client,
    ).check()

    assert status.ready is False
    assert status.reachable is False
    assert status.issues == ["gateway_probe_failed"]
    assert "secret upstream detail" not in status.model_dump_json()
    assert client.closed is True
