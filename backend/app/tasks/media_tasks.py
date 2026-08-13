"""Celery entry points for server-derived media processing."""

import asyncio
from hashlib import sha256
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.integrations.object_store import (
    MediaObjectStore,
    ObjectStoreConfigurationError,
    S3CompatibleMediaObjectStore,
)
from app.integrations.provider_media import SafeProviderMediaURLPolicy
from app.models.database import MediaAsset, User
from app.services.agent_runtime.contracts import ExecutionPrincipal
from app.services.media.assets import MediaAssetForbidden
from app.services.media.inspection import MediaInspectionRunner
from app.services.media.inspection_service import MediaInspectionService
from app.services.media.intent_vault import EncryptedMediaIntentVault
from app.services.media.jobs import MediaGenerationJobService
from app.services.media.lifecycle import MediaAssetLifecycleService
from app.services.media.policy import MediaSubmissionPolicy
from app.services.media.provider_inputs import MediaProviderInputResolver
from app.services.media.reconcile_runtime import MediaReconciliationCoordinator
from app.services.media.reconciliation import MediaReconciliationService
from app.services.media.reconciliation_worker import (
    run_media_reconciliation_batch,
)
from app.services.media.result_ingestion import (
    HttpxMediaRemoteFetcher,
    ProviderResultIngestor,
)
from app.services.media.submission import MediaSubmissionCoordinator
from app.services.media.submission_authorizer import MediaSubmissionAuthorizer
from app.services.media.submission_worker import run_media_submission_batch
from app.services.media.thumbnail import MediaThumbnailRunner, MediaThumbnailService
from app.services.media.usage_receipts import MediaUsageReceiptService
from app.services.media.usage_receipts import MediaUsagePricingService
from app.services.media.worker_runtime import (
    PinnedMediaRuntimeFactory,
)
from app.tasks.celery_worker import celery


async def _run_configured_media_submission(
    db: Session,
    *,
    worker_id: str,
    now: datetime,
) -> dict[str, int]:
    jobs = MediaGenerationJobService(db)
    vault = EncryptedMediaIntentVault(
        root=settings.MEDIA_INTENT_VAULT_DIR,
        key_file=settings.MEDIA_INTENT_VAULT_KEY_FILE,
    )
    policy = MediaSubmissionPolicy(
        submission_enabled=settings.MEDIA_SUBMIT_ENABLED,
        policy_version=settings.MEDIA_POLICY_VERSION,
        signing_key=settings.MEDIA_POLICY_SIGNING_KEY.encode("utf-8"),
        decision_ttl_seconds=settings.MEDIA_POLICY_DECISION_TTL_SECONDS,
    )
    authorizer = MediaSubmissionAuthorizer(
        db,
        policy=policy,
        deployment_org_id=settings.AGENT_ORG_ID,
    )
    input_resolver = MediaProviderInputResolver(
        db,
        object_store=_object_store(),
        asset_authorizer=authorizer,
        expires_seconds=settings.MEDIA_PROVIDER_INPUT_TTL_SECONDS,
    )
    return await run_media_submission_batch(
        jobs=jobs,
        vault=vault,
        authorizer=authorizer,
        runtime_factory=PinnedMediaRuntimeFactory(db),
        coordinator_builder=lambda adapter: MediaSubmissionCoordinator(
            db,
            jobs=jobs,
            vault=vault,
            policy=policy,
            adapter=adapter,
            input_resolver=input_resolver,
        ),
        worker_id=worker_id,
        now=now,
        batch_size=settings.MEDIA_SUBMIT_BATCH_SIZE,
        lease_seconds=settings.MEDIA_SUBMIT_LEASE_SECONDS,
    )


@celery.task(
    bind=True,
    name="app.tasks.media_tasks.submit_media_jobs_task",
    acks_late=True,
    max_retries=0,
    soft_time_limit=240,
    time_limit=270,
)
def submit_media_jobs_task(self):
    """Submit queued jobs without accepting identity or prompt task arguments."""
    if not settings.MEDIA_SUBMIT_ENABLED:
        return {
            "claimed": 0,
            "submitted": 0,
            "submission_unknown": 0,
            "failed_before_submission": 0,
            "deferred": 0,
            "status": "disabled",
        }
    db = SessionLocal()
    try:
        hostname = str(getattr(self.request, "hostname", "media-submitter"))[
            :100
        ]
        return asyncio.run(
            _run_configured_media_submission(
                db,
                worker_id=hostname,
                now=datetime.utcnow(),
            )
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def _run_configured_media_reconciliation(
    db: Session,
    *,
    worker_id: str,
    now: datetime,
) -> dict[str, int]:
    reconciliation = MediaReconciliationService(db)
    fetcher = HttpxMediaRemoteFetcher(
        timeout_seconds=settings.MEDIA_RESULT_DOWNLOAD_TIMEOUT_SECONDS,
    )
    object_store = _object_store()
    ingestor = ProviderResultIngestor(
        db,
        fetcher=fetcher,
        object_store=object_store,
        url_policy=SafeProviderMediaURLPolicy(
            allowed_hosts={"fal.media", "*.fal.media"},
        ),
        max_bytes=settings.MEDIA_RESULT_MAX_BYTES,
    )
    cost_resolver = MediaUsagePricingService(db)
    try:
        return await run_media_reconciliation_batch(
            reconciliation=reconciliation,
            runtime_factory=PinnedMediaRuntimeFactory(db),
            coordinator_builder=lambda adapter: MediaReconciliationCoordinator(
                db,
                reconciliation=reconciliation,
                adapter=adapter,
                ingestor=ingestor,
                usage_recorder=MediaUsageReceiptService(db),
                cost_resolver=cost_resolver,
                poll_after_seconds=settings.MEDIA_RECONCILE_POLL_SECONDS,
                retry_after_seconds=settings.MEDIA_RECONCILE_RETRY_SECONDS,
            ),
            worker_id=worker_id,
            now=now,
            batch_size=settings.MEDIA_RECONCILE_BATCH_SIZE,
            lease_seconds=settings.MEDIA_RECONCILE_LEASE_SECONDS,
            retry_after_seconds=settings.MEDIA_RECONCILE_RETRY_SECONDS,
        )
    finally:
        await fetcher.aclose()


@celery.task(
    bind=True,
    name="app.tasks.media_tasks.reconcile_media_jobs_task",
    acks_late=True,
    max_retries=0,
    soft_time_limit=240,
    time_limit=270,
)
def reconcile_media_jobs_task(self):
    """Poll submitted jobs only when the full external submission plane is on."""
    if not settings.MEDIA_SUBMIT_ENABLED:
        return {
            "claimed": 0,
            "pending": 0,
            "succeeded": 0,
            "failed": 0,
            "retry_scheduled": 0,
            "status": "disabled",
        }
    db = SessionLocal()
    try:
        hostname = str(
            getattr(self.request, "hostname", "media-reconciler")
        )[:100]
        return asyncio.run(
            _run_configured_media_reconciliation(
                db,
                worker_id=hostname,
                now=datetime.utcnow(),
            )
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


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
    requester = db.get(User, requested_by_user_id)
    if (
        requester is None
        or not requester.is_active
        or (
            requester.id != asset.owner_user_id
            and not requester.is_superuser
        )
    ):
        raise MediaAssetForbidden("Thumbnail task requester is not authorized")
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


def run_media_cleanup(
    db: Session,
    *,
    org_id: UUID,
    maintenance_user_id: int,
    object_store: MediaObjectStore,
    retention_days: int,
    batch_size: int,
    now: Optional[datetime] = None,
) -> dict[str, int | str]:
    """Clean one configured organization under a durable admin identity."""
    maintainer = db.get(User, maintenance_user_id)
    if (
        maintainer is None
        or not maintainer.is_active
        or not maintainer.is_superuser
    ):
        raise MediaAssetForbidden("Media maintenance identity is not authorized")
    principal = ExecutionPrincipal(
        org_id=org_id,
        user_id=maintainer.id,
        roles={"media_maintainer"},
        entitlements_hash=sha256(
            f"media-cleanup:{org_id}:{maintainer.id}".encode()
        ).hexdigest(),
        authn_context="worker:celery-beat",
    )
    cleaned = MediaAssetLifecycleService(db).cleanup_expired(
        principal,
        object_store=object_store,
        retention_days=retention_days,
        batch_size=batch_size,
        now=now,
    )
    return {"cleaned": len(cleaned), "status": "completed"}


@celery.task(
    name="app.tasks.media_tasks.cleanup_media_assets_task",
    acks_late=True,
    soft_time_limit=600,
    time_limit=660,
)
def cleanup_media_assets_task():
    if not settings.MEDIA_LIFECYCLE_ENABLED:
        return {"cleaned": 0, "status": "disabled"}
    db = SessionLocal()
    try:
        return run_media_cleanup(
            db,
            org_id=settings.AGENT_ORG_ID,
            maintenance_user_id=settings.MEDIA_MAINTENANCE_USER_ID,
            object_store=_object_store(),
            retention_days=settings.MEDIA_RETENTION_DAYS,
            batch_size=settings.MEDIA_CLEANUP_BATCH_SIZE,
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
