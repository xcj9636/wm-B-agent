"""Celery entry points for server-derived media inspection."""

from hashlib import sha256
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.integrations.object_store import (
    MediaObjectStore,
    ObjectStoreConfigurationError,
    S3CompatibleMediaObjectStore,
)
from app.models.database import MediaAsset
from app.services.agent_runtime.contracts import ExecutionPrincipal
from app.services.media.inspection import MediaInspectionRunner
from app.services.media.inspection_service import MediaInspectionService
from app.services.media.thumbnail import MediaThumbnailRunner, MediaThumbnailService
from app.tasks.celery_worker import celery


def run_media_inspection(
    db: Session,
    *,
    asset_id: UUID,
    requested_by_user_id: int,
    object_store: MediaObjectStore,
    runner: MediaInspectionRunner,
) -> dict[str, str]:
    """Derive the tenant from durable state, never from the task payload."""
    asset = db.get(MediaAsset, asset_id)
    if asset is None or asset.deleted_at is not None:
        raise LookupError("Media asset was not found")
    principal = ExecutionPrincipal(
        org_id=asset.org_id,
        user_id=requested_by_user_id,
        roles={"media_scanner"},
        entitlements_hash=sha256(
            f"media-inspection:{asset.org_id}:{requested_by_user_id}".encode()
        ).hexdigest(),
        authn_context="worker:celery",
    )
    report = MediaInspectionService(db).inspect_asset(
        asset.id,
        principal,
        object_store=object_store,
        runner=runner,
    )
    probe = report.findings_json.get("probe") or {}
    return {
        "asset_id": str(asset.id),
        "report_id": str(report.id),
        "scan_status": report.status,
        "probe_status": str(probe.get("status") or "unavailable"),
    }


@celery.task(
    name="app.tasks.media_tasks.inspect_media_asset_task",
    acks_late=True,
    soft_time_limit=300,
    time_limit=330,
)
def inspect_media_asset_task(asset_id: str, requested_by_user_id: int):
    if not settings.MEDIA_INSPECTION_ENABLED:
        raise RuntimeError("Media inspection is disabled")
    db = SessionLocal()
    try:
        return run_media_inspection(
            db,
            asset_id=UUID(asset_id),
            requested_by_user_id=requested_by_user_id,
            object_store=_object_store(),
            runner=_inspection_runner(),
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run_media_thumbnail(
    db: Session,
    *,
    asset_id: UUID,
    requested_by_user_id: int,
    object_store: MediaObjectStore,
    runner: MediaThumbnailRunner,
) -> dict[str, str]:
    """Derive thumbnail tenant and sensitivity only from durable source state."""
    asset = db.get(MediaAsset, asset_id)
    if asset is None or asset.deleted_at is not None:
        raise LookupError("Media asset was not found")
    principal = ExecutionPrincipal(
        org_id=asset.org_id,
        user_id=requested_by_user_id,
        roles={"media_worker"},
        entitlements_hash=sha256(
            f"media-thumbnail:{asset.org_id}:{requested_by_user_id}".encode()
        ).hexdigest(),
        authn_context="worker:celery",
    )
    thumbnail = MediaThumbnailService(db).generate(
        asset.id,
        principal,
        object_store=object_store,
        runner=runner,
    )
    return {
        "source_asset_id": str(asset.id),
        "thumbnail_asset_id": str(thumbnail.id),
        "status": "completed",
    }


@celery.task(
    name="app.tasks.media_tasks.generate_media_thumbnail_task",
    acks_late=True,
    soft_time_limit=300,
    time_limit=330,
)
def generate_media_thumbnail_task(asset_id: str, requested_by_user_id: int):
    if not settings.MEDIA_THUMBNAIL_ENABLED:
        raise RuntimeError("Media thumbnails are disabled")
    db = SessionLocal()
    try:
        return run_media_thumbnail(
            db,
            asset_id=UUID(asset_id),
            requested_by_user_id=requested_by_user_id,
            object_store=_object_store(),
            runner=_thumbnail_runner(),
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _object_store() -> S3CompatibleMediaObjectStore:
    if settings.MEDIA_OBJECT_STORE_BACKEND != "s3":
        raise ObjectStoreConfigurationError(
            "Media processing requires the S3 object store"
        )
    return S3CompatibleMediaObjectStore(
        quarantine_bucket=settings.MEDIA_S3_QUARANTINE_BUCKET,
        asset_bucket=settings.MEDIA_S3_ASSET_BUCKET,
        key_prefix=settings.MEDIA_S3_KEY_PREFIX,
        kms_key_id=settings.MEDIA_S3_KMS_KEY_ID,
        endpoint_url=settings.MEDIA_S3_ENDPOINT_URL,
        region_name=settings.MEDIA_S3_REGION,
    )


def _inspection_runner() -> MediaInspectionRunner:
    return MediaInspectionRunner(
        clamscan_path=settings.MEDIA_CLAMSCAN_PATH,
        ffprobe_path=settings.MEDIA_FFPROBE_PATH,
        timeout_seconds=settings.MEDIA_INSPECTION_TIMEOUT_SECONDS,
        max_output_bytes=settings.MEDIA_INSPECTION_MAX_OUTPUT_BYTES,
        max_duration_seconds=settings.MEDIA_MAX_DURATION_SECONDS,
        max_dimension_pixels=settings.MEDIA_MAX_DIMENSION_PIXELS,
    )


def _thumbnail_runner() -> MediaThumbnailRunner:
    return MediaThumbnailRunner(
        ffmpeg_path=settings.MEDIA_FFMPEG_PATH,
        timeout_seconds=settings.MEDIA_THUMBNAIL_TIMEOUT_SECONDS,
        max_output_bytes=settings.MEDIA_THUMBNAIL_PROCESS_OUTPUT_BYTES,
        max_thumbnail_bytes=settings.MEDIA_THUMBNAIL_MAX_BYTES,
    )
