"""Orchestration for trusted inspection of quarantined media assets."""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.integrations.object_store import MediaObjectStore
from app.models.database import MediaAsset, MediaScanReport
from app.services.agent_runtime.contracts import ExecutionPrincipal
from app.services.media.assets import (
    MediaAssetConflict,
    MediaAssetForbidden,
    MediaAssetNotFound,
)
from app.services.media.contracts import MediaAssetKind
from app.services.media.inspection import MediaInspectionRunner


class MediaInspectionService:
    """Stages, inspects, and atomically records sanitized evidence."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def inspect_asset(
        self,
        asset_id: UUID,
        principal: ExecutionPrincipal,
        *,
        object_store: MediaObjectStore,
        runner: MediaInspectionRunner,
        now: Optional[datetime] = None,
    ) -> MediaScanReport:
        if not principal.roles.intersection({"media_scanner", "admin"}):
            raise MediaAssetForbidden("Media inspection requires scanner role")
        asset = self._asset(asset_id)
        if asset.org_id != principal.org_id:
            raise MediaAssetForbidden("Asset is outside the current organization")
        if not asset.quarantined or not asset.storage_key.startswith("quarantine/"):
            raise MediaAssetConflict("Only quarantined assets can be inspected")

        with object_store.stage_quarantined(
            asset.storage_key,
            expected_sha256=asset.sha256,
            expected_size_bytes=asset.size_bytes,
            expected_content_type=asset.mime_type,
        ) as path:
            result = runner.inspect(
                path,
                expected_kind=MediaAssetKind(asset.kind),
                expected_mime_type=asset.mime_type,
                expected_size_bytes=asset.size_bytes,
            )

        findings = {
            "reason_code": result.reason_code,
            "signatures": result.signatures,
            "probe": {
                "status": result.probe_status.value,
                "metadata": (
                    result.probe.model_dump(mode="json")
                    if result.probe is not None
                    else None
                ),
            },
        }
        report = MediaScanReport(
            org_id=asset.org_id,
            asset_id=asset.id,
            scanner=result.scanner,
            scanner_version=result.scanner_version,
            status=result.scan_status.value,
            asset_sha256=asset.sha256,
            findings_json=findings,
            created_by_user_id=principal.user_id,
            created_at=_naive_utc(now),
        )
        self._db.add(report)
        asset.scan_status = result.scan_status.value
        self._db.commit()
        self._db.refresh(report)
        return report

    def _asset(self, asset_id: UUID) -> MediaAsset:
        asset = self._db.get(MediaAsset, asset_id)
        if asset is None or asset.deleted_at is not None:
            raise MediaAssetNotFound("Media asset was not found")
        return asset


def _naive_utc(value: Optional[datetime]) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("media inspection timestamps must be timezone-aware")
    return current.astimezone(timezone.utc).replace(tzinfo=None)
