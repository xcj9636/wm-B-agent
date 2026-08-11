"""Soft deletion and delayed object cleanup for auditable media assets."""

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.integrations.object_store import MediaObjectStore
from app.models.database import (
    MediaAsset,
    MediaAssetRelation,
    MediaConsentRecord,
)
from app.services.agent_runtime.contracts import ExecutionPrincipal
from app.services.media.assets import (
    MediaAssetConflict,
    MediaAssetForbidden,
    MediaAssetNotFound,
)


class MediaAssetLifecycleService:
    """Preserve database evidence while expiring unreferenced object bytes."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def soft_delete(
        self,
        asset_id: UUID,
        principal: ExecutionPrincipal,
        *,
        now: Optional[datetime] = None,
    ) -> MediaAsset:
        target = self._db.get(MediaAsset, asset_id)
        if target is None:
            raise MediaAssetNotFound("Media asset was not found")
        self._authorize_owner(target, principal)
        if target.deleted_at is not None:
            return target
        if target.quarantined or not target.storage_key.startswith("assets/"):
            raise MediaAssetConflict("Only promoted assets can be soft deleted")
        self._require_unreferenced(target.id)
        target.deleted_at = _naive_utc(now)
        self._db.commit()
        self._db.refresh(target)
        return target

    def cleanup_expired(
        self,
        principal: ExecutionPrincipal,
        *,
        object_store: MediaObjectStore,
        retention_days: int,
        batch_size: int,
        now: Optional[datetime] = None,
    ) -> list[UUID]:
        roles = {role.strip().lower() for role in principal.roles}
        if not roles.intersection({"media_maintainer", "admin"}):
            raise MediaAssetForbidden("Media cleanup requires maintenance role")
        if not 1 <= retention_days <= 3650:
            raise ValueError("Media retention must be between 1 and 3650 days")
        if not 1 <= batch_size <= 1000:
            raise ValueError("Media cleanup batch must be between 1 and 1000")
        current = _aware_utc(now)
        cutoff = (current - timedelta(days=retention_days)).replace(tzinfo=None)
        candidates = (
            self._db.query(MediaAsset)
            .filter(
                MediaAsset.org_id == principal.org_id,
                MediaAsset.deleted_at.is_not(None),
                MediaAsset.deleted_at <= cutoff,
            )
            .order_by(MediaAsset.deleted_at.asc(), MediaAsset.id.asc())
            .limit(batch_size)
            .all()
        )
        cleaned: list[UUID] = []
        for target in candidates:
            metadata = dict(target.metadata_json or {})
            if metadata.get("object_deleted_at"):
                continue
            if self._is_referenced(target.id):
                continue
            object_store.delete_asset(target.storage_key)
            metadata.update(
                {
                    "object_deleted_at": current.strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                    "object_deleted_by_user_id": principal.user_id,
                }
            )
            target.metadata_json = metadata
            self._db.commit()
            cleaned.append(target.id)
        return cleaned

    @staticmethod
    def _authorize_owner(
        target: MediaAsset,
        principal: ExecutionPrincipal,
    ) -> None:
        roles = {role.strip().lower() for role in principal.roles}
        if target.org_id != principal.org_id:
            raise MediaAssetForbidden("Asset is outside the current organization")
        if target.owner_user_id != principal.user_id and "admin" not in roles:
            raise MediaAssetForbidden("Asset deletion requires ownership")

    def _require_unreferenced(self, asset_id: UUID) -> None:
        if self._has_active_consent_evidence(asset_id):
            raise MediaAssetConflict("Active consent evidence cannot be deleted")
        if self._has_live_derived_child(asset_id):
            raise MediaAssetConflict(
                "Asset with live derived children cannot be deleted"
            )

    def _is_referenced(self, asset_id: UUID) -> bool:
        return self._has_active_consent_evidence(
            asset_id
        ) or self._has_live_derived_child(asset_id)

    def _has_active_consent_evidence(self, asset_id: UUID) -> bool:
        return (
            self._db.query(MediaConsentRecord.id)
            .filter(
                MediaConsentRecord.evidence_asset_id == asset_id,
                MediaConsentRecord.status == "valid",
                MediaConsentRecord.revoked_at.is_(None),
            )
            .first()
            is not None
        )

    def _has_live_derived_child(self, asset_id: UUID) -> bool:
        return (
            self._db.query(MediaAssetRelation.id)
            .join(
                MediaAsset,
                MediaAsset.id == MediaAssetRelation.child_asset_id,
            )
            .filter(
                MediaAssetRelation.parent_asset_id == asset_id,
                MediaAsset.deleted_at.is_(None),
            )
            .first()
            is not None
        )


def _aware_utc(value: Optional[datetime]) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("Media lifecycle timestamps must be timezone-aware")
    return current.astimezone(timezone.utc)


def _naive_utc(value: Optional[datetime]) -> datetime:
    return _aware_utc(value).replace(tzinfo=None)
