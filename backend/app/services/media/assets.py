"""Durable, scoped ingestion and quarantine lifecycle for media assets."""

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.integrations.object_store import MediaObjectStore
from app.models.database import MediaAsset, MediaUploadIntent
from app.services.agent_runtime.contracts import ExecutionPrincipal, Sensitivity
from app.services.idempotency import IdempotencyConflict, canonical_hash
from app.services.media.contracts import (
    AssetConsentStatus,
    AssetRightsStatus,
    AssetScanStatus,
    MediaAssetKind,
    MediaAssetPolicySnapshot,
)


class MediaAssetForbidden(RuntimeError):
    pass


class MediaAssetConflict(RuntimeError):
    pass


class MediaAssetNotFound(LookupError):
    pass


class UploadIntentCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    idempotency_key: str = Field(min_length=8, max_length=255)
    kind: MediaAssetKind
    expected_mime_type: str = Field(min_length=1, max_length=255)
    expected_size_bytes: int = Field(ge=1, le=2_000_000_000)
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    sensitivity: Sensitivity
    consent_required: bool = False


class MediaAssetService:
    UPLOAD_TTL = timedelta(minutes=15)

    def __init__(self, db: Session, *, upload_enabled: bool) -> None:
        self._db = db
        self._upload_enabled = upload_enabled

    def create_upload_intent(
        self,
        command: UploadIntentCommand,
        principal: ExecutionPrincipal,
        *,
        now: Optional[datetime] = None,
    ) -> MediaUploadIntent:
        self._require_upload_enabled()
        created_at = self._naive_utc(now)
        input_hash = canonical_hash(command.model_dump(mode="json"))
        existing = (
            self._db.query(MediaUploadIntent)
            .filter(
                MediaUploadIntent.org_id == principal.org_id,
                MediaUploadIntent.actor_user_id == principal.user_id,
                MediaUploadIntent.idempotency_key == command.idempotency_key,
            )
            .one_or_none()
        )
        if existing is not None:
            if existing.input_hash != input_hash:
                raise IdempotencyConflict(
                    "Upload idempotency key was reused with different input"
                )
            return existing

        upload = MediaUploadIntent(
            org_id=principal.org_id,
            actor_user_id=principal.user_id,
            idempotency_key=command.idempotency_key,
            input_hash=input_hash,
            storage_key=f"quarantine/{principal.org_id}/{uuid4().hex}",
            kind=command.kind.value,
            expected_mime_type=command.expected_mime_type.strip().lower(),
            expected_size_bytes=command.expected_size_bytes,
            expected_sha256=command.expected_sha256,
            sensitivity=command.sensitivity.value,
            consent_required=command.consent_required,
            status="pending",
            expires_at=created_at + self.UPLOAD_TTL,
            created_at=created_at,
        )
        self._db.add(upload)
        self._db.commit()
        self._db.refresh(upload)
        return upload

    def complete_upload(
        self,
        upload_id: UUID,
        principal: ExecutionPrincipal,
        object_store: MediaObjectStore,
        *,
        now: Optional[datetime] = None,
    ) -> MediaAsset:
        self._require_upload_enabled()
        checked_at = self._naive_utc(now)
        upload = self._upload(upload_id)
        self._authorize_upload(upload, principal)
        if upload.status == "completed" and upload.asset_id is not None:
            return self._asset(upload.asset_id)
        if upload.status != "pending":
            raise MediaAssetConflict("Upload intent is not pending")
        if checked_at >= upload.expires_at:
            upload.status = "expired"
            self._db.commit()
            raise MediaAssetConflict("Upload intent expired")

        metadata = object_store.head(upload.storage_key)
        if metadata.key != upload.storage_key:
            raise MediaAssetConflict("Stored object key does not match upload intent")
        if metadata.size_bytes != upload.expected_size_bytes:
            raise MediaAssetConflict("Stored object size does not match upload intent")
        if metadata.content_type.strip().lower() != upload.expected_mime_type:
            raise MediaAssetConflict("Stored object MIME does not match upload intent")
        if metadata.sha256 != upload.expected_sha256:
            raise MediaAssetConflict("Stored object checksum does not match upload intent")

        asset = MediaAsset(
            org_id=principal.org_id,
            owner_user_id=principal.user_id,
            kind=upload.kind,
            source="user_upload",
            storage_backend=getattr(object_store, "backend_name", "test"),
            storage_key=upload.storage_key,
            sha256=metadata.sha256,
            mime_type=metadata.content_type.strip().lower(),
            size_bytes=metadata.size_bytes,
            sensitivity=upload.sensitivity,
            quarantined=True,
            scan_status=AssetScanStatus.PENDING.value,
            rights_status=AssetRightsStatus.UNKNOWN.value,
            consent_required=upload.consent_required,
            consent_status=(
                AssetConsentStatus.UNKNOWN.value
                if upload.consent_required
                else AssetConsentStatus.NOT_REQUIRED.value
            ),
            created_at=checked_at,
        )
        self._db.add(asset)
        self._db.flush()
        upload.asset_id = asset.id
        upload.status = "completed"
        upload.completed_at = checked_at
        self._db.commit()
        self._db.refresh(asset)
        return asset

    def policy_snapshot(
        self,
        asset_id: UUID,
        principal: ExecutionPrincipal,
    ) -> MediaAssetPolicySnapshot:
        asset = self._asset(asset_id)
        if asset.org_id != principal.org_id:
            raise MediaAssetForbidden("Asset is outside the current organization")
        return MediaAssetPolicySnapshot(
            asset_id=asset.id,
            org_id=asset.org_id,
            scan_status=AssetScanStatus(asset.scan_status),
            rights_status=AssetRightsStatus(asset.rights_status),
            consent_required=asset.consent_required,
            consent_status=AssetConsentStatus(asset.consent_status),
            sensitivity=Sensitivity(asset.sensitivity),
        )

    def _upload(self, upload_id: UUID) -> MediaUploadIntent:
        upload = self._db.get(MediaUploadIntent, upload_id)
        if upload is None:
            raise MediaAssetNotFound("Upload intent was not found")
        return upload

    def _asset(self, asset_id: UUID) -> MediaAsset:
        asset = self._db.get(MediaAsset, asset_id)
        if asset is None or asset.deleted_at is not None:
            raise MediaAssetNotFound("Media asset was not found")
        return asset

    @staticmethod
    def _authorize_upload(
        upload: MediaUploadIntent,
        principal: ExecutionPrincipal,
    ) -> None:
        if (
            upload.org_id != principal.org_id
            or upload.actor_user_id != principal.user_id
        ):
            raise MediaAssetForbidden("Upload intent is not owned by the actor")

    def _require_upload_enabled(self) -> None:
        if not self._upload_enabled:
            raise MediaAssetForbidden("Media upload is disabled")

    @staticmethod
    def _naive_utc(value: Optional[datetime]) -> datetime:
        current = value or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("media timestamps must be timezone-aware")
        return current.astimezone(timezone.utc).replace(tzinfo=None)
