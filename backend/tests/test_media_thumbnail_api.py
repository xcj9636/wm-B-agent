from uuid import uuid4

from app.api.v1.video import get_media_thumbnail_dispatcher
from app.config import settings
from app.main import app
from app.models.database import MediaAsset


class FakeThumbnailDispatcher:
    def __init__(self):
        self.calls = []

    def __call__(self, asset_id, requested_by_user_id):
        self.calls.append((asset_id, requested_by_user_id))
        return "thumbnail-task-123"


def create_promoted_asset(db, user, *, org_id=None, owner_user_id=None):
    asset = MediaAsset(
        org_id=org_id or settings.AGENT_ORG_ID,
        owner_user_id=owner_user_id or user.id,
        kind="video",
        source="user_upload",
        storage_backend="s3",
        storage_key=f"assets/{org_id or settings.AGENT_ORG_ID}/{uuid4().hex}",
        sha256="7" * 64,
        mime_type="video/mp4",
        size_bytes=4096,
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
    return asset


def test_asset_owner_can_queue_server_side_thumbnail_job(api_context):
    client, db, user = api_context
    asset = create_promoted_asset(db, user)
    dispatcher = FakeThumbnailDispatcher()
    app.dependency_overrides[get_media_thumbnail_dispatcher] = lambda: dispatcher

    queued = client.post(
        f"/api/v1/video/assets/{asset.id}/thumbnail",
        json={},
    )
    spoofed = client.post(
        f"/api/v1/video/assets/{asset.id}/thumbnail",
        json={"ffmpeg_args": ["-i", "https://attacker.test/input"]},
    )

    assert queued.status_code == 202
    assert queued.json() == {
        "asset_id": str(asset.id),
        "task_id": "thumbnail-task-123",
        "status": "queued",
    }
    assert dispatcher.calls == [(asset.id, user.id)]
    assert spoofed.status_code == 422


def test_thumbnail_api_hides_cross_tenant_and_non_owned_assets(api_context):
    client, db, user = api_context
    cross_tenant = create_promoted_asset(db, user, org_id=uuid4())
    non_owned = create_promoted_asset(db, user, owner_user_id=user.id + 99)
    dispatcher = FakeThumbnailDispatcher()
    app.dependency_overrides[get_media_thumbnail_dispatcher] = lambda: dispatcher

    cross_response = client.post(
        f"/api/v1/video/assets/{cross_tenant.id}/thumbnail",
        json={},
    )
    owner_response = client.post(
        f"/api/v1/video/assets/{non_owned.id}/thumbnail",
        json={},
    )

    assert cross_response.status_code == 404
    assert owner_response.status_code == 404
    assert dispatcher.calls == []
