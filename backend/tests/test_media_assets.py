from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.services.agent_runtime.contracts import ExecutionPrincipal, Sensitivity
from app.services.idempotency import IdempotencyConflict
from app.services.media.assets import (
    MediaAssetConflict,
    MediaAssetForbidden,
    MediaAssetService,
    UploadIntentCommand,
)
from app.services.media.contracts import (
    AssetConsentStatus,
    AssetRightsStatus,
    AssetScanStatus,
    MediaAssetKind,
)
from app.integrations.object_store import StoredObjectMetadata


def principal(*, user_id=42, org_id=None, roles=None):
    return ExecutionPrincipal(
        org_id=org_id or uuid4(),
        user_id=user_id,
        roles=roles or {"media_operator"},
        entitlements_hash="b" * 64,
        authn_context="jwt:mfa",
    )


def upload_command(**overrides):
    values = {
        "idempotency_key": "media-upload:test-product-image",
        "kind": MediaAssetKind.IMAGE,
        "expected_mime_type": "image/png",
        "expected_size_bytes": 1024,
        "expected_sha256": "a" * 64,
        "sensitivity": Sensitivity.INTERNAL,
        "consent_required": False,
    }
    values.update(overrides)
    return UploadIntentCommand(**values)


class FakeObjectStore:
    def __init__(self, metadata):
        self.metadata = metadata
        self.requested_keys = []

    def head(self, key):
        self.requested_keys.append(key)
        return self.metadata


def test_upload_intent_is_server_keyed_idempotent_and_scoped(db_session):
    actor = principal()
    service = MediaAssetService(db_session, upload_enabled=True)
    command = upload_command()
    now = datetime.now(timezone.utc)

    first = service.create_upload_intent(command, actor, now=now)
    repeated = service.create_upload_intent(command, actor, now=now)

    assert repeated.id == first.id
    assert first.org_id == actor.org_id
    assert first.actor_user_id == actor.user_id
    assert first.storage_key.startswith(f"quarantine/{actor.org_id}/")
    assert "test-product-image" not in first.storage_key
    assert first.expires_at == now.replace(tzinfo=None) + timedelta(minutes=15)

    with pytest.raises(IdempotencyConflict):
        service.create_upload_intent(
            command.model_copy(update={"expected_size_bytes": 2048}),
            actor,
            now=now,
        )


def test_upload_intent_creation_fails_when_upload_plane_is_disabled(db_session):
    with pytest.raises(MediaAssetForbidden):
        MediaAssetService(db_session, upload_enabled=False).create_upload_intent(
            upload_command(),
            principal(),
        )


def test_complete_rejects_wrong_actor_expiry_and_metadata_mismatch(db_session):
    actor = principal()
    service = MediaAssetService(db_session, upload_enabled=True)
    now = datetime.now(timezone.utc)
    upload = service.create_upload_intent(upload_command(), actor, now=now)
    matching = StoredObjectMetadata(
        key=upload.storage_key,
        size_bytes=1024,
        content_type="image/png",
        sha256="a" * 64,
    )

    with pytest.raises(MediaAssetForbidden):
        service.complete_upload(
            upload.id,
            principal(user_id=99, org_id=actor.org_id),
            FakeObjectStore(matching),
            now=now,
        )

    with pytest.raises(MediaAssetConflict):
        service.complete_upload(
            upload.id,
            actor,
            FakeObjectStore(
                matching.model_copy(update={"size_bytes": 1025})
            ),
            now=now,
        )

    with pytest.raises(MediaAssetConflict):
        service.complete_upload(
            upload.id,
            actor,
            FakeObjectStore(matching),
            now=now + timedelta(minutes=16),
        )


def test_complete_creates_quarantined_asset_and_is_idempotent(db_session):
    actor = principal()
    service = MediaAssetService(db_session, upload_enabled=True)
    upload = service.create_upload_intent(upload_command(), actor)
    store = FakeObjectStore(
        StoredObjectMetadata(
            key=upload.storage_key,
            size_bytes=1024,
            content_type="image/png",
            sha256="a" * 64,
        )
    )

    asset = service.complete_upload(upload.id, actor, store)
    repeated = service.complete_upload(upload.id, actor, store)

    assert repeated.id == asset.id
    assert asset.quarantined is True
    assert asset.scan_status == AssetScanStatus.PENDING.value
    assert asset.rights_status == AssetRightsStatus.UNKNOWN.value
    assert asset.consent_status == AssetConsentStatus.NOT_REQUIRED.value
    assert asset.storage_key == upload.storage_key


def test_quarantined_asset_policy_snapshot_exposes_pending_review(db_session):
    actor = principal()
    service = MediaAssetService(db_session, upload_enabled=True)
    upload = service.create_upload_intent(
        upload_command(consent_required=True),
        actor,
    )
    asset = service.complete_upload(
        upload.id,
        actor,
        FakeObjectStore(
            StoredObjectMetadata(
                key=upload.storage_key,
                size_bytes=1024,
                content_type="image/png",
                sha256="a" * 64,
            )
        ),
    )

    snapshot = service.policy_snapshot(asset.id, actor)
    assert snapshot.scan_status == AssetScanStatus.PENDING
    assert snapshot.rights_status == AssetRightsStatus.UNKNOWN
    assert snapshot.consent_status == AssetConsentStatus.UNKNOWN
