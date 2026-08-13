from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from app.api.v1.media_webhooks import get_fal_webhook_authenticator
from app.config import settings
from app.main import app
from app.services.media.callbacks import FalVerifiedCallback, FalWebhookVerificationError


@dataclass
class FakeAuthenticator:
    failure: Exception | None = None

    async def authenticate(self, *, body, headers, now):
        if self.failure:
            raise self.failure
        return FalVerifiedCallback(
            provider_request_id=headers.request_id,
            provider_account_ref_hash="a" * 64,
            body_sha256="b" * 64,
            delivery_hint="OK",
            signature_timestamp=datetime.now(timezone.utc),
        )


def callback_headers(request_id):
    return {
        "X-Fal-Webhook-Request-Id": request_id,
        "X-Fal-Webhook-User-Id": "fal-user-123",
        "X-Fal-Webhook-Timestamp": "1786603200",
        "X-Fal-Webhook-Signature": "00" * 64,
    }


def test_media_callback_endpoint_is_disabled_by_default(api_context):
    client, _, _ = api_context
    response = client.post(
        "/api/v1/webhooks/media/fal",
        content=b"{}",
        headers=callback_headers("request-1"),
    )
    assert response.status_code == 404


def test_media_callback_endpoint_accepts_verified_unknown_request_generically(
    api_context,
    monkeypatch,
):
    client, _, _ = api_context
    monkeypatch.setattr(settings, "MEDIA_CALLBACK_ENABLED", True)
    app.dependency_overrides[get_fal_webhook_authenticator] = FakeAuthenticator
    response = client.post(
        "/api/v1/webhooks/media/fal",
        content=b'{"request_id":"unknown","status":"OK"}',
        headers=callback_headers(f"unknown-{uuid4().hex}"),
    )
    assert response.status_code == 202
    assert response.json() == {"accepted": True}
    assert "job_id" not in response.text


def test_media_callback_endpoint_rejects_bad_signature_and_oversized_body(
    api_context,
    monkeypatch,
):
    client, _, _ = api_context
    monkeypatch.setattr(settings, "MEDIA_CALLBACK_ENABLED", True)
    monkeypatch.setattr(settings, "MEDIA_CALLBACK_MAX_BODY_BYTES", 1024)
    app.dependency_overrides[get_fal_webhook_authenticator] = lambda: FakeAuthenticator(
        FalWebhookVerificationError("private verifier detail")
    )
    rejected = client.post(
        "/api/v1/webhooks/media/fal",
        content=b"{}",
        headers=callback_headers("request-1"),
    )
    oversized = client.post(
        "/api/v1/webhooks/media/fal",
        content=b"x" * 1025,
        headers=callback_headers("request-2"),
    )
    assert rejected.status_code == 401
    assert "private verifier detail" not in rejected.text
    assert oversized.status_code == 413
