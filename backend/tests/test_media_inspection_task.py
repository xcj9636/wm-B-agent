from contextlib import contextmanager
from pathlib import Path

from app.config import Settings
from app.models.database import MediaAsset
from app.services.media.contracts import AssetScanStatus
from app.services.media.inspection import MediaInspectionResult, ProbeStatus
from app.tasks.media_tasks import inspect_media_asset_task, run_media_inspection


class FakeStore:
    @contextmanager
    def stage_quarantined(self, key, **expected):
        yield Path("/private/tmp/media-stage/input.bin")


class FakeRunner:
    def inspect(self, path, **expected):
        return MediaInspectionResult(
            scanner_version="1.4.2",
            scan_status=AssetScanStatus.PASSED,
            probe_status=ProbeStatus.PASSED,
            probe={
                "format_name": "png_pipe",
                "duration_seconds": 0,
                "size_bytes": 1024,
                "video_streams": [
                    {
                        "codec": "png",
                        "width": 1000,
                        "height": 1000,
                        "frame_rate": "25/1",
                    }
                ],
            },
            reason_code="inspection_passed",
        )


def create_asset(db, org_id):
    value = MediaAsset(
        org_id=org_id,
        owner_user_id=42,
        kind="image",
        source="user_upload",
        storage_backend="s3",
        storage_key=f"quarantine/{org_id}/task-source",
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


def test_worker_derives_tenant_and_only_accepts_asset_and_requester_ids(db_session):
    config = Settings(_env_file=None)
    target = create_asset(db_session, config.AGENT_ORG_ID)

    result = run_media_inspection(
        db_session,
        asset_id=target.id,
        requested_by_user_id=42,
        object_store=FakeStore(),
        runner=FakeRunner(),
    )

    assert result == {
        "asset_id": str(target.id),
        "report_id": str(result["report_id"]),
        "scan_status": "passed",
        "probe_status": "passed",
    }
    assert inspect_media_asset_task.name == (
        "app.tasks.media_tasks.inspect_media_asset_task"
    )
