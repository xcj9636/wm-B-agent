from uuid import uuid4

from app.config import settings
from app.models.database import MediaAsset


def create_asset(db, user, *, org_id=None, owner_user_id=None):
    target_org = org_id or settings.AGENT_ORG_ID
    value = MediaAsset(
        org_id=target_org,
        owner_user_id=owner_user_id or user.id,
        kind="image",
        source="user_upload",
        storage_backend="s3",
        storage_key=f"assets/{target_org}/{uuid4().hex}",
        sha256="5" * 64,
        mime_type="image/png",
        size_bytes=2048,
        sensitivity="internal",
        quarantined=False,
        scan_status="passed",
        rights_status="verified",
        consent_required=False,
        consent_status="not_required",
    )
    db.add(value)
    db.commit()
    db.refresh(value)
    return value


def test_asset_owner_can_soft_delete_media(api_context):
    client, db, user = api_context
    target = create_asset(db, user)

    response = client.delete(f"/api/v1/video/assets/{target.id}")

    assert response.status_code == 200
    assert response.json()["id"] == str(target.id)
    assert response.json()["status"] == "deleted"
    assert response.json()["deleted_at"]
    db.refresh(target)
    assert target.deleted_at is not None


def test_delete_api_hides_non_owned_and_cross_tenant_assets(api_context):
    client, db, user = api_context
    non_owned = create_asset(db, user, owner_user_id=user.id + 100)
    cross_tenant = create_asset(db, user, org_id=uuid4())

    non_owner_response = client.delete(
        f"/api/v1/video/assets/{non_owned.id}"
    )
    cross_response = client.delete(
        f"/api/v1/video/assets/{cross_tenant.id}"
    )

    assert non_owner_response.status_code == 404
    assert cross_response.status_code == 404
