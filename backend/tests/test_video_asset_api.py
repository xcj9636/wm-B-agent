from uuid import uuid4

from app.config import settings
from app.integrations.object_store import (
    PresignedDownload,
    PresignedUpload,
    StoredObjectMetadata,
)
from app.main import app
from app.models.database import MediaAsset
from app.services.media.assets import MediaAssetService
from app.api.v1.video import get_media_asset_service, get_media_object_store


class FakeUploadStore:
    backend_name = "s3"

    def __init__(self):
        self.uploads = []
        self.objects = {}

    def create_upload(
        self,
        *,
        key,
        content_type,
        size_bytes,
        sha256,
        expires_seconds=900,
    ):
        self.uploads.append(
            {
                "key": key,
                "content_type": content_type,
                "size_bytes": size_bytes,
                "sha256": sha256,
                "expires_seconds": expires_seconds,
            }
        )
        self.objects[key] = StoredObjectMetadata(
            key=key,
            size_bytes=size_bytes,
            content_type=content_type,
            sha256=sha256,
        )
        return PresignedUpload(
            url="https://objects.example.test/upload",
            fields={
                "Content-Type": content_type,
                "x-amz-meta-sha256": sha256,
            },
            key=key,
            expires_seconds=expires_seconds,
        )

    def head(self, key):
        return self.objects[key]

    def create_download(self, **kwargs):
        self.download = kwargs
        return PresignedDownload(
            url="https://objects.example.test/signed-download",
            expires_seconds=kwargs["expires_seconds"],
        )


def payload(**overrides):
    values = {
        "idempotency_key": "media-upload:api-product-image",
        "kind": "image",
        "expected_mime_type": "image/png",
        "expected_size_bytes": 2048,
        "expected_sha256": "c" * 64,
        "requested_sensitivity_floor": "internal",
        "consent_required": False,
    }
    values.update(overrides)
    return values


def test_upload_api_is_fail_closed_by_default(api_context):
    client, _, _ = api_context

    response = client.post("/api/v1/video/assets/uploads", json=payload())

    assert response.status_code == 503
    assert response.json()["detail"] == "Media upload is disabled"


def test_upload_api_rejects_client_routing_fields(api_context):
    client, db, _ = api_context
    store = FakeUploadStore()
    app.dependency_overrides[get_media_asset_service] = lambda: MediaAssetService(
        db,
        upload_enabled=True,
    )
    app.dependency_overrides[get_media_object_store] = lambda: store

    response = client.post(
        "/api/v1/video/assets/uploads",
        json=payload(provider="fal", model="auto/latest"),
    )

    assert response.status_code == 422

    sensitivity = client.post(
        "/api/v1/video/assets/uploads",
        json={
            **payload(),
            "sensitivity": "public",
        },
    )
    assert sensitivity.status_code == 422


def test_create_and_complete_upload_stays_quarantined(api_context):
    client, db, _ = api_context
    store = FakeUploadStore()
    app.dependency_overrides[get_media_asset_service] = lambda: MediaAssetService(
        db,
        upload_enabled=True,
    )
    app.dependency_overrides[get_media_object_store] = lambda: store

    created = client.post("/api/v1/video/assets/uploads", json=payload())

    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "pending"
    assert body["upload"]["url"] == "https://objects.example.test/upload"
    assert body["upload"]["key"].startswith("quarantine/")
    assert body["upload"]["fields"]["x-amz-meta-sha256"] == "c" * 64
    assert store.uploads[0]["size_bytes"] == 2048

    completed = client.post(
        f"/api/v1/video/assets/uploads/{body['id']}/complete"
    )

    assert completed.status_code == 200
    asset = completed.json()
    assert asset["quarantined"] is True
    assert asset["scan_status"] == "pending"
    assert asset["rights_status"] == "unknown"
    assert asset["consent_status"] == "not_required"
    assert "storage_key" not in asset


def test_upload_idempotency_conflict_maps_to_409(api_context):
    client, db, _ = api_context
    store = FakeUploadStore()
    app.dependency_overrides[get_media_asset_service] = lambda: MediaAssetService(
        db,
        upload_enabled=True,
    )
    app.dependency_overrides[get_media_object_store] = lambda: store

    first = client.post("/api/v1/video/assets/uploads", json=payload())
    conflict = client.post(
        "/api/v1/video/assets/uploads",
        json=payload(expected_size_bytes=4096),
    )

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "Upload request conflicts with existing input"


def test_download_api_returns_only_short_lived_credential(api_context):
    client, db, user = api_context
    store = FakeUploadStore()
    asset = MediaAsset(
        org_id=settings.AGENT_ORG_ID,
        owner_user_id=user.id,
        kind="image",
        source="user_upload",
        storage_backend="s3",
        storage_key=f"assets/{settings.AGENT_ORG_ID}/download-target",
        sha256="f" * 64,
        mime_type="image/png",
        size_bytes=2048,
        sensitivity="internal",
        quarantined=False,
        scan_status="passed",
        rights_status="verified",
        consent_required=False,
        consent_status="not_required",
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    app.dependency_overrides[get_media_object_store] = lambda: store

    response = client.post(f"/api/v1/video/assets/{asset.id}/download")

    assert response.status_code == 200
    assert response.json() == {
        "url": "https://objects.example.test/signed-download",
        "expires_seconds": 120,
    }
    assert "storage_key" not in response.text
    assert "bucket" not in response.text


def test_download_api_hides_cross_tenant_asset_location(api_context):
    client, db, user = api_context
    asset = MediaAsset(
        org_id=uuid4(),
        owner_user_id=user.id,
        kind="image",
        source="user_upload",
        storage_backend="s3",
        storage_key=f"assets/{uuid4()}/cross-tenant",
        sha256="f" * 64,
        mime_type="image/png",
        size_bytes=2048,
        sensitivity="internal",
        quarantined=False,
        scan_status="passed",
        rights_status="verified",
        consent_required=False,
        consent_status="not_required",
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    app.dependency_overrides[get_media_object_store] = FakeUploadStore

    response = client.post(f"/api/v1/video/assets/{asset.id}/download")

    assert response.status_code == 404
    assert response.json()["detail"] == "Media asset not found"
