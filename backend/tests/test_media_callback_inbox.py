import base64
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.models.database import (
    MediaCallbackInbox,
    MediaGenerationEvent,
    MediaGenerationJob,
)
from app.services.media.callbacks import (
    MediaCallbackConflict,
    FalWebhookHeaders,
    FalWebhookVerificationError,
    FalWebhookVerifier,
    MediaCallbackInboxService,
)


NOW = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)
EXPECTED_USER_ID = "fal-user-123"


def submitted_job(db_session, **overrides):
    values = {
        "org_id": uuid4(),
        "owner_user_id": 7,
        "project_id": uuid4(),
        "storyboard_version_id": uuid4(),
        "shot_id": uuid4(),
        "runtime_revision_id": uuid4(),
        "idempotency_key": f"media-callback:{uuid4()}",
        "input_hash": "a" * 64,
        "intent_hash": "b" * 64,
        "payload_ref": "vault://media-intents/callback/test",
        "mode": "text_to_video",
        "provider": "fal",
        "model_id": "fal-ai/veo3/fast",
        "sensitivity": "internal",
        "status": "submitted",
        "effect_state": "confirmed",
        "provider_request_id": f"fal-{uuid4().hex}",
        "reserved_cost_microusd": 1_000_000,
        "estimate_hash": "c" * 64,
        "budget_period_start": date(2026, 8, 1),
        "deadline_at": NOW.replace(tzinfo=None) + timedelta(hours=1),
        "next_reconcile_at": NOW.replace(tzinfo=None) + timedelta(minutes=5),
    }
    values.update(overrides)
    job = MediaGenerationJob(**values)
    db_session.add(job)
    db_session.commit()
    return job


def signed_callback(private_key, request_id, *, status="OK", timestamp=None):
    timestamp = timestamp or int(NOW.timestamp())
    body = json.dumps(
        {
            "request_id": request_id,
            "gateway_request_id": "gateway-private-id",
            "status": status,
            "payload": {"video": {"url": "https://private.example/video.mp4"}},
        },
        separators=(",", ":"),
    ).encode()
    digest = hashlib.sha256(body).hexdigest()
    message = f"{request_id}\n{EXPECTED_USER_ID}\n{timestamp}\n{digest}".encode()
    signature = private_key.sign(message).hex()
    return body, FalWebhookHeaders(
        request_id=request_id,
        user_id=EXPECTED_USER_ID,
        timestamp=str(timestamp),
        signature=signature,
    )


@pytest.fixture
def signing_material():
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes_raw()
    encoded = base64.urlsafe_b64encode(public_bytes).rstrip(b"=").decode()
    return private_key, {"kty": "OKP", "crv": "Ed25519", "x": encoded}


def test_verified_callback_only_wakes_polling_and_stores_secret_free_receipt(
    db_session,
    signing_material,
):
    private_key, jwk = signing_material
    job = submitted_job(db_session)
    body, headers = signed_callback(private_key, job.provider_request_id)
    verified = FalWebhookVerifier(EXPECTED_USER_ID).verify(
        body=body,
        headers=headers,
        jwks=[jwk],
        now=NOW,
    )

    result = MediaCallbackInboxService(db_session).accept(verified, now=NOW)

    db_session.refresh(job)
    receipt = db_session.query(MediaCallbackInbox).one()
    assert result.created is True
    assert result.job_id == job.id
    assert job.status == "submitted"
    assert job.provider_state is None
    assert job.actual_cost_microusd is None
    assert job.next_reconcile_at == NOW.replace(tzinfo=None)
    assert receipt.provider == "fal"
    assert receipt.provider_account_ref_hash == hashlib.sha256(
        EXPECTED_USER_ID.encode()
    ).hexdigest()
    assert receipt.body_sha256 == hashlib.sha256(body).hexdigest()
    assert receipt.delivery_hint == "OK"
    serialized = " ".join(str(value) for value in receipt.__dict__.values())
    assert "private.example" not in serialized
    assert "gateway-private-id" not in serialized
    event = db_session.query(MediaGenerationEvent).one()
    assert event.event_type == "provider.callback_verified"
    assert event.data_json == {}


def test_callback_retry_is_idempotent_and_does_not_append_a_second_event(
    db_session,
    signing_material,
):
    private_key, jwk = signing_material
    job = submitted_job(db_session)
    body, headers = signed_callback(private_key, job.provider_request_id)
    verifier = FalWebhookVerifier(EXPECTED_USER_ID)
    verified = verifier.verify(body=body, headers=headers, jwks=[jwk], now=NOW)
    service = MediaCallbackInboxService(db_session)

    first = service.accept(verified, now=NOW)
    second = service.accept(verified, now=NOW + timedelta(seconds=1))

    assert first.created is True
    assert second.created is False
    assert first.receipt_id == second.receipt_id
    assert db_session.query(MediaCallbackInbox).count() == 1
    assert db_session.query(MediaGenerationEvent).count() == 1


def test_same_request_id_with_different_signed_body_is_not_a_retry(
    db_session,
    signing_material,
):
    private_key, jwk = signing_material
    job = submitted_job(db_session)
    verifier = FalWebhookVerifier(EXPECTED_USER_ID)
    service = MediaCallbackInboxService(db_session)
    ok_body, ok_headers = signed_callback(private_key, job.provider_request_id)
    error_body, error_headers = signed_callback(
        private_key, job.provider_request_id, status="ERROR"
    )
    service.accept(
        verifier.verify(body=ok_body, headers=ok_headers, jwks=[jwk], now=NOW),
        now=NOW,
    )

    with pytest.raises(MediaCallbackConflict):
        service.accept(
            verifier.verify(
                body=error_body,
                headers=error_headers,
                jwks=[jwk],
                now=NOW,
            ),
            now=NOW,
        )

    assert db_session.query(MediaCallbackInbox).count() == 1
    assert db_session.query(MediaGenerationEvent).count() == 1


@pytest.mark.parametrize(
    "mutation",
    ["signature", "stale", "future", "user", "request_id", "status"],
)
def test_invalid_or_unbound_callback_is_rejected_before_database_mutation(
    db_session,
    signing_material,
    mutation,
):
    private_key, jwk = signing_material
    request_id = f"fal-{uuid4().hex}"
    body, headers = signed_callback(private_key, request_id)
    if mutation == "signature":
        headers = headers.model_copy(update={"signature": "00" * 64})
    elif mutation == "stale":
        body, headers = signed_callback(
            private_key, request_id, timestamp=int(NOW.timestamp()) - 301
        )
    elif mutation == "future":
        body, headers = signed_callback(
            private_key, request_id, timestamp=int(NOW.timestamp()) + 301
        )
    elif mutation == "user":
        headers = headers.model_copy(update={"user_id": "other-user"})
    elif mutation == "request_id":
        payload = json.loads(body)
        payload["request_id"] = "different-request"
        body = json.dumps(payload, separators=(",", ":")).encode()
    elif mutation == "status":
        body, headers = signed_callback(private_key, request_id, status="RUNNING")

    with pytest.raises(FalWebhookVerificationError):
        FalWebhookVerifier(EXPECTED_USER_ID).verify(
            body=body, headers=headers, jwks=[jwk], now=NOW
        )

    assert db_session.query(MediaCallbackInbox).count() == 0


def test_verified_unknown_or_terminal_callback_never_changes_job_truth(
    db_session,
    signing_material,
):
    private_key, jwk = signing_material
    terminal = submitted_job(
        db_session,
        status="succeeded",
        completed_at=NOW.replace(tzinfo=None),
        next_reconcile_at=None,
        actual_cost_microusd=500_000,
    )
    verifier = FalWebhookVerifier(EXPECTED_USER_ID)
    service = MediaCallbackInboxService(db_session)

    terminal_body, terminal_headers = signed_callback(
        private_key, terminal.provider_request_id
    )
    terminal_result = service.accept(
        verifier.verify(
            body=terminal_body,
            headers=terminal_headers,
            jwks=[jwk],
            now=NOW,
        ),
        now=NOW,
    )
    unknown_body, unknown_headers = signed_callback(
        private_key, f"unknown-{uuid4().hex}"
    )
    unknown_result = service.accept(
        verifier.verify(
            body=unknown_body,
            headers=unknown_headers,
            jwks=[jwk],
            now=NOW,
        ),
        now=NOW,
    )

    db_session.refresh(terminal)
    assert terminal_result.job_id == terminal.id
    assert unknown_result.job_id is None
    assert terminal.status == "succeeded"
    assert terminal.actual_cost_microusd == 500_000
    assert terminal.next_reconcile_at is None
    assert db_session.query(MediaCallbackInbox).count() == 2
    assert db_session.query(MediaGenerationEvent).count() == 0
