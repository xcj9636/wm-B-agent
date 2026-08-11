from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest

from app.models.database import MediaAsset
from app.services.agent_runtime.contracts import ExecutionPrincipal
from app.services.media.assets import MediaAssetForbidden
from app.services.media.contracts import AssetScanStatus, MediaAssetKind
from app.services.media.inspection import (
    MediaInspectionResult,
    MediaProbe,
    ProbeStatus,
    VideoStreamProbe,
)
from app.services.media.inspection_service import MediaInspectionService


def principal(*, org_id=None, roles=None):
    return ExecutionPrincipal(
        org_id=org_id or uuid4(),
        user_id=17,
        roles=roles or {"media_scanner"},
        entitlements_hash="e" * 64,
        authn_context="worker:mTLS",
    )


def asset(db, actor):
    value = MediaAsset(
        org_id=actor.org_id,
        owner_user_id=actor.user_id,
        kind="image",
        source="user_upload",
        storage_backend="s3",
        storage_key=f"quarantine/{actor.org_id}/source",
        sha256="a" * 64,
        mime_type="image/png",
        size_bytes=1024,
        sensitivity="internal",
        quarantined=True,
        scan_status="pending",
        rights_status="unknown",
        consent_required=False,
        consent_status="not_required",
    )
    db.add(value)
    db.commit()
    db.refresh(value)
    return value


class FakeStagingStore:
    def __init__(self):
        self.calls = []
        self.cleaned_up = False

    @contextmanager
    def stage_quarantined(self, key, **expected):
        self.calls.append((key, expected))
        try:
            yield Path("/private/tmp/b-agent-stage/input.bin")
        finally:
            self.cleaned_up = True


class FakeInspectionRunner:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def inspect(self, path, **expected):
        self.calls.append((path, expected))
        return self.result


def clean_result():
    return MediaInspectionResult(
        scanner_version="1.4.2",
        scan_status=AssetScanStatus.PASSED,
        probe_status=ProbeStatus.PASSED,
        probe=MediaProbe(
            format_name="png_pipe",
            duration_seconds=0,
            size_bytes=1024,
            video_streams=[
                VideoStreamProbe(
                    codec="png",
                    width=1200,
                    height=1200,
                    frame_rate="25/1",
                )
            ],
        ),
        reason_code="inspection_passed",
    )


def test_inspection_stages_object_and_persists_sanitized_evidence(db_session):
    scanner = principal()
    target = asset(db_session, scanner)
    object_store = FakeStagingStore()
    runner = FakeInspectionRunner(clean_result())

    report = MediaInspectionService(db_session).inspect_asset(
        target.id,
        scanner,
        object_store=object_store,
        runner=runner,
    )

    assert report.asset_id == target.id
    assert report.status == "passed"
    assert report.scanner == "clamav"
    assert report.scanner_version == "1.4.2"
    assert report.asset_sha256 == target.sha256
    assert report.findings_json["probe"]["status"] == "passed"
    assert report.findings_json["probe"]["metadata"]["format_name"] == "png_pipe"
    assert report.findings_json["reason_code"] == "inspection_passed"
    assert object_store.cleaned_up is True
    assert object_store.calls[0][1] == {
        "expected_sha256": target.sha256,
        "expected_size_bytes": 1024,
        "expected_content_type": "image/png",
    }
    assert runner.calls[0][1]["expected_kind"] == MediaAssetKind.IMAGE
    db_session.refresh(target)
    assert target.scan_status == "passed"


def test_inspection_failure_is_persisted_but_never_marked_passed(db_session):
    scanner = principal()
    target = asset(db_session, scanner)
    unavailable = clean_result().model_copy(
        update={
            "scan_status": AssetScanStatus.UNAVAILABLE,
            "probe_status": ProbeStatus.BLOCKED,
            "probe": None,
            "reason_code": "scanner_timeout",
        }
    )

    report = MediaInspectionService(db_session).inspect_asset(
        target.id,
        scanner,
        object_store=FakeStagingStore(),
        runner=FakeInspectionRunner(unavailable),
    )

    assert report.status == "unavailable"
    assert report.findings_json["probe"]["status"] == "blocked"
    db_session.refresh(target)
    assert target.scan_status == "unavailable"
    assert target.quarantined is True


def test_inspection_rejects_wrong_tenant_or_unprivileged_actor(db_session):
    scanner = principal()
    target = asset(db_session, scanner)
    service = MediaInspectionService(db_session)

    for actor in [
        principal(org_id=uuid4()),
        principal(org_id=scanner.org_id, roles={"media_operator"}),
    ]:
        with pytest.raises(MediaAssetForbidden):
            service.inspect_asset(
                target.id,
                actor,
                object_store=FakeStagingStore(),
                runner=FakeInspectionRunner(clean_result()),
            )

