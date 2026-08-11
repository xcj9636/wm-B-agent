from datetime import datetime, timedelta, timezone

from app.config import settings
from app.models.database import MediaAsset
from app.integrations.object_store import StoredObjectMetadata
from app.main import app
from app.api.v1.video import get_media_object_store


class FakePromotionStore:
    backend_name = "s3"

    def promote(
        self,
        key,
        *,
        expected_sha256,
        expected_size_bytes,
        expected_content_type,
    ):
        return StoredObjectMetadata(
            key=key.replace("quarantine/", "assets/", 1),
            size_bytes=expected_size_bytes,
            content_type=expected_content_type,
            sha256=expected_sha256,
        )


def create_asset(db, user, *, consent_required=False, suffix="review-target"):
    value = MediaAsset(
        org_id=settings.AGENT_ORG_ID,
        owner_user_id=user.id,
        kind="image",
        source="user_upload",
        storage_backend="s3",
        storage_key=f"quarantine/{settings.AGENT_ORG_ID}/{suffix}",
        sha256="d" * 64,
        mime_type="image/png",
        size_bytes=4096,
        sensitivity="internal",
        quarantined=True,
        scan_status="pending",
        rights_status="unknown",
        consent_required=consent_required,
        consent_status="unknown" if consent_required else "not_required",
    )
    db.add(value)
    db.commit()
    db.refresh(value)
    return value


def test_review_api_requires_privileged_server_side_identity(api_context):
    client, db, user = api_context
    target = create_asset(db, user)

    response = client.post(
        f"/api/v1/video/assets/{target.id}/reviews/scan",
        json={
            "scanner": "clamav",
            "scanner_version": "1.4.2",
            "status": "passed",
            "asset_sha256": target.sha256,
            "findings": {},
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Media review forbidden"


def test_review_api_records_evidence_then_promotes(api_context):
    client, db, user = api_context
    user.is_superuser = True
    db.commit()
    app.dependency_overrides[get_media_object_store] = FakePromotionStore
    target = create_asset(db, user)
    now = datetime.now(timezone.utc)

    scan = client.post(
        f"/api/v1/video/assets/{target.id}/reviews/scan",
        json={
            "scanner": "clamav",
            "scanner_version": "1.4.2",
            "status": "passed",
            "asset_sha256": target.sha256,
            "findings": {
                "signatures": [],
                "probe": {"status": "passed", "metadata": {}},
            },
        },
    )
    rights = client.post(
        f"/api/v1/video/assets/{target.id}/reviews/rights",
        json={
            "status": "verified",
            "basis": "owned_product_media",
            "territories": ["GLOBAL"],
            "channels": ["paid_social", "website"],
            "source_ref": "contract:MSA-2026-0042",
            "valid_from": (now - timedelta(days=1)).isoformat(),
            "valid_until": (now + timedelta(days=365)).isoformat(),
        },
    )

    assert scan.status_code == 201
    assert scan.json()["status"] == "passed"
    assert rights.status_code == 201
    assert rights.json()["status"] == "verified"

    promoted = client.post(
        f"/api/v1/video/assets/{target.id}/promote",
        json={
            "scan_report_id": scan.json()["id"],
            "rights_record_id": rights.json()["id"],
            "consent_record_id": None,
        },
    )

    assert promoted.status_code == 200
    assert promoted.json()["quarantined"] is False
    assert promoted.json()["scan_status"] == "passed"
    assert promoted.json()["rights_status"] == "verified"


def test_consent_review_api_persists_evidence_asset_reference(api_context):
    client, db, user = api_context
    user.is_superuser = True
    db.commit()
    target = create_asset(db, user, consent_required=True)
    document = create_asset(db, user, suffix="consent-evidence")
    now = datetime.now(timezone.utc)

    consent = client.post(
        f"/api/v1/video/assets/{target.id}/reviews/consent",
        json={
            "subject_ref": "talent:masked-9f2a",
            "purpose": "product marketing video",
            "regions": ["GLOBAL"],
            "media_types": ["video", "image"],
            "status": "valid",
            "valid_from": (now - timedelta(days=1)).isoformat(),
            "valid_until": (now + timedelta(days=90)).isoformat(),
            "evidence_asset_id": str(document.id),
        },
    )

    assert consent.status_code == 201
    assert consent.json()["asset_id"] == str(target.id)
    assert consent.json()["evidence_asset_id"] == str(document.id)
    assert consent.json()["status"] == "valid"
