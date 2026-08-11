from contextlib import contextmanager
from pathlib import Path

from app.config import Settings
from app.integrations.object_store import StoredObjectMetadata
from app.models.database import MediaAsset
from app.services.media.thumbnail import GeneratedThumbnail
from app.tasks.media_tasks import generate_media_thumbnail_task, run_media_thumbnail


class FakeStore:
    backend_name = "s3"

    @contextmanager
    def stage_asset(self, _key, **_expected):
        yield Path("/private/tmp/media-stage/input.bin")

    def put_derived(self, *, key, path, content_type, sha256):
        return StoredObjectMetadata(
            key=key,
            size_bytes=14,
            content_type=content_type,
            sha256=sha256,
        )


class FakeRunner:
    def generate(self, _source_path, output_path):
        output_path.write_bytes(b"generated-jpeg")
        return GeneratedThumbnail(
            sha256="9" * 64,
            size_bytes=14,
            mime_type="image/jpeg",
        )


def create_source(db, org_id):
    asset = MediaAsset(
        org_id=org_id,
        owner_user_id=42,
        kind="video",
        source="user_upload",
        storage_backend="s3",
        storage_key=f"assets/{org_id}/thumbnail-source",
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


def test_thumbnail_worker_derives_tenant_from_durable_asset(db_session):
    source = create_source(
        db_session,
        Settings(_env_file=None).AGENT_ORG_ID,
    )

    result = run_media_thumbnail(
        db_session,
        asset_id=source.id,
        requested_by_user_id=source.owner_user_id,
        object_store=FakeStore(),
        runner=FakeRunner(),
    )

    assert result["source_asset_id"] == str(source.id)
    assert result["thumbnail_asset_id"]
    assert result["status"] == "completed"
    assert generate_media_thumbnail_task.name == (
        "app.tasks.media_tasks.generate_media_thumbnail_task"
    )
