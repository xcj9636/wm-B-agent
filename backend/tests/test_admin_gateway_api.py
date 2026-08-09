from datetime import datetime, timezone

from app.main import app
from app.services.llm.status import (
    GatewayReadiness,
    get_gateway_status_service,
)


class FakeGatewayStatusService:
    def __init__(self):
        self.calls = 0

    async def check(self):
        self.calls += 1
        return GatewayReadiness(
            backend="omniroute",
            enabled=True,
            ready=True,
            reachable=True,
            checked_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
            configured_aliases={"live_reply": "b-agent-reply-v1"},
            allowed_providers=["approved-provider"],
        )


def test_gateway_status_requires_superuser_before_probe(api_context):
    client, _, _ = api_context
    service = FakeGatewayStatusService()
    app.dependency_overrides[get_gateway_status_service] = lambda: service

    response = client.get("/api/v1/admin/ai-gateway/status")

    assert response.status_code == 403
    assert service.calls == 0


def test_superuser_can_read_secret_free_gateway_status(api_context):
    client, db, user = api_context
    user.is_superuser = True
    db.commit()
    service = FakeGatewayStatusService()
    app.dependency_overrides[get_gateway_status_service] = lambda: service

    response = client.get("/api/v1/admin/ai-gateway/status")

    assert response.status_code == 200
    assert response.json() == {
        "backend": "omniroute",
        "enabled": True,
        "ready": True,
        "reachable": True,
        "checked_at": "2026-08-09T00:00:00Z",
        "configured_aliases": {"live_reply": "b-agent-reply-v1"},
        "missing_aliases": [],
        "missing_models": [],
        "allowed_providers": ["approved-provider"],
        "issues": [],
    }
    assert "secret" not in response.text.lower()
    assert service.calls == 1
