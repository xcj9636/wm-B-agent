from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.models.database import MediaAsset
from app.services.agent_runtime.contracts import ExecutionPrincipal
from app.services.media.assets import MediaAssetConflict, MediaAssetForbidden
from app.services.media.contracts import (
    AssetConsentStatus,
    AssetRightsStatus,
    AssetScanStatus,
)
from app.services.media.review import (
    ConsentEvidenceCommand,
    MediaReviewService,
    RightsEvidenceCommand,
    ScanEvidenceCommand,
)
from app.integrations.object_store import StoredObjectMetadata


def principal(*, org_id=None, user_id=7, roles=None):
    return ExecutionPrincipal(
        org_id=org_id or uuid4(),
        user_id=user_id,
        roles=roles or {"admin"},
        entitlements_hash="c" * 64,
        authn_context="jwt:mfa",
    )


def asset(db_session, actor, *, consent_required=False, suffix="source"):
    value = MediaAsset(
        org_id=actor.org_id,
        owner_user_id=actor.user_id,
        kind="image",
        source="user_upload",
        storage_backend="test",
        storage_key=f"quarantine/{actor.org_id}/{suffix}",
        sha256="a" * 64,
        mime_type="image/png",
        size_bytes=1024,
        sensitivity="internal",
        quarantined=True,
        scan_status="pending",
        rights_status="unknown",
        consent_required=consent_required,
        consent_status="unknown" if consent_required else "not_required",
    )
    db_session.add(value)
    db_session.commit()
    db_session.refresh(value)
    return value


def scan_command(target):
    return ScanEvidenceCommand(
        scanner="clamav",
        scanner_version="1.4.2",
        status=AssetScanStatus.PASSED,
        asset_sha256=target.sha256,
        findings={
            "signatures": [],
            "probe": {"status": "passed", "metadata": {}},
        },
    )


def rights_command(now):
    return RightsEvidenceCommand(
        status=AssetRightsStatus.VERIFIED,
        basis="owned_product_media",
        territories=["GLOBAL"],
        channels=["paid_social", "website"],
        source_ref="contract:MSA-2026-0042",
        valid_from=now - timedelta(days=1),
        valid_until=now + timedelta(days=365),
    )


def consent_command(now, evidence_asset_id):
    return ConsentEvidenceCommand(
        subject_ref="talent:masked-9f2a",
        purpose="product marketing video",
        regions=["GLOBAL"],
        media_types=["video", "image"],
        status=AssetConsentStatus.VALID,
        valid_from=now - timedelta(days=1),
        valid_until=now + timedelta(days=90),
        evidence_asset_id=evidence_asset_id,
    )


class FakePromotionStore:
    backend_name = "s3"

    def __init__(self):
        self.calls = []

    def promote(
        self,
        key,
        *,
        expected_sha256,
        expected_size_bytes,
        expected_content_type,
    ):
        self.calls.append(
            (key, expected_sha256, expected_size_bytes, expected_content_type)
        )
        return StoredObjectMetadata(
            key=key.replace("quarantine/", "assets/", 1),
            size_bytes=expected_size_bytes,
            content_type=expected_content_type,
            sha256=expected_sha256,
        )


def test_promotion_uses_persisted_scan_rights_and_consent_evidence(db_session):
    now = datetime.now(timezone.utc)
    admin = principal()
    target = asset(db_session, admin, consent_required=True)
    consent_document = asset(db_session, admin, suffix="consent-document")
    service = MediaReviewService(db_session)

    scan = service.record_scan(target.id, scan_command(target), admin, now=now)
    rights = service.record_rights(target.id, rights_command(now), admin, now=now)
    consent = service.record_consent(
        target.id,
        consent_command(now, consent_document.id),
        admin,
        now=now,
    )
    promoted = service.promote(
        target.id,
        scan_report_id=scan.id,
        rights_record_id=rights.id,
        consent_record_id=consent.id,
        principal=admin,
        object_store=FakePromotionStore(),
        now=now,
    )

    assert promoted.quarantined is False
    assert promoted.scan_status == AssetScanStatus.PASSED.value
    assert promoted.rights_status == AssetRightsStatus.VERIFIED.value
    assert promoted.consent_status == AssetConsentStatus.VALID.value
    assert promoted.scan_report_id == scan.id
    assert promoted.rights_record_id == rights.id
    assert promoted.consent_record_id == consent.id
    assert promoted.reviewed_by_user_id == admin.user_id


def test_scan_evidence_must_match_asset_hash_and_authorized_role(db_session):
    admin = principal()
    target = asset(db_session, admin)
    service = MediaReviewService(db_session)

    with pytest.raises(MediaAssetConflict, match="hash"):
        service.record_scan(
            target.id,
            scan_command(target).model_copy(update={"asset_sha256": "b" * 64}),
            admin,
        )

    operator = principal(org_id=admin.org_id, roles={"media_operator"})
    with pytest.raises(MediaAssetForbidden, match="scanner"):
        service.record_scan(target.id, scan_command(target), operator)


def test_promotion_rejects_expired_or_cross_tenant_evidence(db_session):
    now = datetime.now(timezone.utc)
    first_admin = principal()
    second_admin = principal()
    target = asset(db_session, first_admin)
    other_target = asset(db_session, second_admin)
    service = MediaReviewService(db_session)

    scan = service.record_scan(target.id, scan_command(target), first_admin, now=now)
    expired_rights = service.record_rights(
        target.id,
        rights_command(now).model_copy(
            update={"valid_until": now - timedelta(seconds=1)}
        ),
        first_admin,
        now=now,
    )
    other_rights = service.record_rights(
        other_target.id,
        rights_command(now),
        second_admin,
        now=now,
    )

    with pytest.raises(MediaAssetConflict, match="expired"):
        service.promote(
            target.id,
            scan_report_id=scan.id,
            rights_record_id=expired_rights.id,
            consent_record_id=None,
            principal=first_admin,
            object_store=FakePromotionStore(),
            now=now,
        )

    with pytest.raises(MediaAssetForbidden, match="organization"):
        service.promote(
            target.id,
            scan_report_id=scan.id,
            rights_record_id=other_rights.id,
            consent_record_id=None,
            principal=first_admin,
            object_store=FakePromotionStore(),
            now=now,
        )


def test_required_consent_must_cover_video_and_reference_same_org_evidence(db_session):
    now = datetime.now(timezone.utc)
    admin = principal()
    target = asset(db_session, admin, consent_required=True)
    consent_document = asset(db_session, admin, suffix="consent-document")
    service = MediaReviewService(db_session)
    scan = service.record_scan(target.id, scan_command(target), admin, now=now)
    rights = service.record_rights(target.id, rights_command(now), admin, now=now)
    image_only = service.record_consent(
        target.id,
        consent_command(now, consent_document.id).model_copy(
            update={"media_types": ["image"]}
        ),
        admin,
        now=now,
    )

    with pytest.raises(MediaAssetConflict, match="video"):
        service.promote(
            target.id,
            scan_report_id=scan.id,
            rights_record_id=rights.id,
            consent_record_id=image_only.id,
            principal=admin,
            object_store=FakePromotionStore(),
            now=now,
        )


def test_promotion_rejects_scan_evidence_without_passed_probe(db_session):
    now = datetime.now(timezone.utc)
    admin = principal()
    target = asset(db_session, admin)
    service = MediaReviewService(db_session)
    scan = service.record_scan(
        target.id,
        scan_command(target).model_copy(update={"findings": {"signatures": []}}),
        admin,
        now=now,
    )
    rights = service.record_rights(target.id, rights_command(now), admin, now=now)

    with pytest.raises(MediaAssetConflict, match="probe"):
        service.promote(
            target.id,
            scan_report_id=scan.id,
            rights_record_id=rights.id,
            consent_record_id=None,
            principal=admin,
            object_store=FakePromotionStore(),
            now=now,
        )
