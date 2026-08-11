from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.models.database import (
    MediaAsset,
    MediaAssetRelation,
    MediaConsentRecord,
)
from app.services.agent_runtime.contracts import ExecutionPrincipal
from app.services.media.assets import MediaAssetConflict, MediaAssetForbidden
from app.services.media.lifecycle import MediaAssetLifecycleService


class FakeDeleteStore:
    def __init__(self):
        self.calls = []

    def delete_asset(self, key):
        self.calls.append(key)


def principal(org_id, *, user_id=7, roles=None):
    return ExecutionPrincipal(
        org_id=org_id,
        user_id=user_id,
        roles=set(roles or {"user"}),
        entitlements_hash="a" * 64,
        authn_context="jwt",
    )


def asset(db, org_id, *, owner_user_id=7, suffix=None):
    value = MediaAsset(
        org_id=org_id,
        owner_user_id=owner_user_id,
        kind="image",
        source="user_upload",
        storage_backend="s3",
        storage_key=f"assets/{org_id}/{suffix or uuid4().hex}",
        sha256="6" * 64,
        mime_type="image/png",
        size_bytes=4096,
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


def test_owner_soft_deletes_asset_without_erasing_audit_row(db_session):
    org_id = uuid4()
    target = asset(db_session, org_id)
    deleted_at = datetime(2026, 8, 1, tzinfo=timezone.utc)

    deleted = MediaAssetLifecycleService(db_session).soft_delete(
        target.id,
        principal(org_id),
        now=deleted_at,
    )

    assert deleted.id == target.id
    assert deleted.deleted_at == deleted_at.replace(tzinfo=None)
    assert db_session.get(MediaAsset, target.id) is not None


def test_soft_delete_rejects_non_owner_and_cross_tenant(db_session):
    org_id = uuid4()
    target = asset(db_session, org_id)
    service = MediaAssetLifecycleService(db_session)

    with pytest.raises(MediaAssetForbidden):
        service.soft_delete(target.id, principal(org_id, user_id=8))
    with pytest.raises(MediaAssetForbidden):
        service.soft_delete(target.id, principal(uuid4()))


def test_soft_delete_blocks_active_consent_evidence(db_session):
    org_id = uuid4()
    evidence = asset(db_session, org_id, suffix="consent-evidence")
    target = asset(db_session, org_id, suffix="consent-target")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db_session.add(
        MediaConsentRecord(
            org_id=org_id,
            asset_id=target.id,
            subject_ref="talent:masked",
            purpose="marketing",
            regions=["GLOBAL"],
            media_types=["image"],
            status="valid",
            valid_from=now - timedelta(days=1),
            valid_until=now + timedelta(days=30),
            evidence_asset_id=evidence.id,
            created_by_user_id=7,
        )
    )
    db_session.commit()

    with pytest.raises(MediaAssetConflict, match="evidence"):
        MediaAssetLifecycleService(db_session).soft_delete(
            evidence.id,
            principal(org_id),
        )


def test_soft_delete_blocks_source_with_live_derived_children(db_session):
    org_id = uuid4()
    source = asset(db_session, org_id, suffix="source")
    child = asset(db_session, org_id, suffix="child")
    db_session.add(
        MediaAssetRelation(
            org_id=org_id,
            parent_asset_id=source.id,
            child_asset_id=child.id,
            relation_type="thumbnail_of",
        )
    )
    db_session.commit()

    with pytest.raises(MediaAssetConflict, match="derived"):
        MediaAssetLifecycleService(db_session).soft_delete(
            source.id,
            principal(org_id),
        )


def test_cleanup_deletes_only_objects_past_retention_and_marks_audit_metadata(
    db_session,
):
    org_id = uuid4()
    old = asset(db_session, org_id, suffix="old")
    recent = asset(db_session, org_id, suffix="recent")
    already_cleaned = asset(db_session, org_id, suffix="cleaned")
    now = datetime(2026, 8, 11, tzinfo=timezone.utc)
    old.deleted_at = (now - timedelta(days=31)).replace(tzinfo=None)
    recent.deleted_at = (now - timedelta(days=29)).replace(tzinfo=None)
    already_cleaned.deleted_at = (now - timedelta(days=60)).replace(tzinfo=None)
    already_cleaned.metadata_json = {
        "object_deleted_at": "2026-07-01T00:00:00Z"
    }
    db_session.commit()
    store = FakeDeleteStore()

    cleaned = MediaAssetLifecycleService(db_session).cleanup_expired(
        principal(org_id, user_id=1, roles={"media_maintainer"}),
        object_store=store,
        retention_days=30,
        batch_size=100,
        now=now,
    )

    assert cleaned == [old.id]
    assert store.calls == [old.storage_key]
    db_session.refresh(old)
    assert old.metadata_json["object_deleted_at"] == "2026-08-11T00:00:00Z"
    assert old.metadata_json["object_deleted_by_user_id"] == 1
    assert db_session.get(MediaAsset, old.id) is not None


def test_cleanup_requires_maintenance_role_and_valid_limits(db_session):
    org_id = uuid4()
    service = MediaAssetLifecycleService(db_session)

    with pytest.raises(MediaAssetForbidden):
        service.cleanup_expired(
            principal(org_id),
            object_store=FakeDeleteStore(),
            retention_days=30,
            batch_size=100,
        )
    with pytest.raises(ValueError):
        service.cleanup_expired(
            principal(org_id, roles={"admin"}),
            object_store=FakeDeleteStore(),
            retention_days=0,
            batch_size=100,
        )
