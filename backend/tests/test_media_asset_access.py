from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.integrations.object_store import PresignedDownload
from app.models.database import MediaAsset
from app.services.agent_runtime.contracts import ExecutionPrincipal, Sensitivity
from app.services.media.access import MediaAssetAccessService
from app.services.media.assets import (
    MediaAssetConflict,
    MediaAssetForbidden,
    MediaAssetNotFound,
)


class FakeDownloadStore:
    def __init__(self):
        self.calls = []

    def create_download(self, **kwargs):
        self.calls.append(kwargs)
        return PresignedDownload(
            url="https://objects.example.test/signed-download",
            expires_seconds=kwargs["expires_seconds"],
        )


def principal(org_id, user_id=7, roles=None):
    return ExecutionPrincipal(
        org_id=org_id,
        user_id=user_id,
        roles=set(roles or {"user"}),
        entitlements_hash="a" * 64,
        authn_context="jwt",
    )


def promoted_asset(db, org_id, *, owner_user_id=7, sensitivity="internal"):
    asset = MediaAsset(
        org_id=org_id,
        owner_user_id=owner_user_id,
        kind="image",
        source="user_upload",
        storage_backend="s3",
        storage_key=f"assets/{org_id}/{uuid4().hex}",
        sha256="e" * 64,
        mime_type="image/png",
        size_bytes=4096,
        sensitivity=sensitivity,
        quarantined=False,
        scan_status="passed",
        rights_status="verified",
        consent_required=False,
        consent_status="not_required",
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


def test_internal_asset_download_uses_server_owned_filename_and_short_ttl(db_session):
    org_id = uuid4()
    asset = promoted_asset(db_session, org_id)
    store = FakeDownloadStore()

    result = MediaAssetAccessService(db_session).create_download(
        asset.id,
        principal(org_id),
        store,
        expires_seconds=120,
    )

    assert result.url == "https://objects.example.test/signed-download"
    assert result.model_dump() == {
        "url": "https://objects.example.test/signed-download",
        "expires_seconds": 120,
    }
    assert store.calls == [
        {
            "key": asset.storage_key,
            "content_type": "image/png",
            "download_name": f"{asset.id}.png",
            "expires_seconds": 120,
        }
    ]


@pytest.mark.parametrize("quarantined,deleted", [(True, False), (False, True)])
def test_download_rejects_quarantined_or_deleted_assets(
    db_session,
    quarantined,
    deleted,
):
    org_id = uuid4()
    asset = promoted_asset(db_session, org_id)
    asset.quarantined = quarantined
    if deleted:
        asset.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db_session.commit()
    store = FakeDownloadStore()

    error = MediaAssetConflict if quarantined else MediaAssetNotFound
    with pytest.raises(error):
        MediaAssetAccessService(db_session).create_download(
            asset.id,
            principal(org_id),
            store,
        )

    assert store.calls == []


def test_download_rejects_cross_tenant_access(db_session):
    asset = promoted_asset(db_session, uuid4())

    with pytest.raises(MediaAssetForbidden):
        MediaAssetAccessService(db_session).create_download(
            asset.id,
            principal(uuid4()),
            FakeDownloadStore(),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scan_status", "failed"),
        ("rights_status", "unknown"),
        ("consent_status", "unknown"),
    ],
)
def test_download_fails_closed_when_approval_evidence_is_incomplete(
    db_session,
    field,
    value,
):
    org_id = uuid4()
    asset = promoted_asset(db_session, org_id)
    if field == "consent_status":
        asset.consent_required = True
    setattr(asset, field, value)
    db_session.commit()
    store = FakeDownloadStore()

    with pytest.raises(MediaAssetConflict):
        MediaAssetAccessService(db_session).create_download(
            asset.id,
            principal(org_id),
            store,
        )

    assert store.calls == []


@pytest.mark.parametrize(
    ("sensitivity", "actor_id", "roles", "allowed"),
    [
        (Sensitivity.CONFIDENTIAL, 7, {"user"}, True),
        (Sensitivity.CONFIDENTIAL, 8, {"user"}, False),
        (Sensitivity.CONFIDENTIAL, 8, {"compliance_reviewer"}, True),
        (Sensitivity.RESTRICTED, 7, {"user"}, False),
        (Sensitivity.RESTRICTED, 8, {"media_security"}, True),
        (Sensitivity.RESTRICTED, 8, {"admin"}, True),
    ],
)
def test_sensitive_download_authorization_matrix(
    db_session,
    sensitivity,
    actor_id,
    roles,
    allowed,
):
    org_id = uuid4()
    asset = promoted_asset(
        db_session,
        org_id,
        owner_user_id=7,
        sensitivity=sensitivity.value,
    )
    action = lambda: MediaAssetAccessService(db_session).create_download(
        asset.id,
        principal(org_id, actor_id, roles),
        FakeDownloadStore(),
    )

    if allowed:
        assert action().expires_seconds == 120
    else:
        with pytest.raises(MediaAssetForbidden):
            action()
