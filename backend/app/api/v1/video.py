"""Authenticated video studio asset APIs."""

from datetime import datetime
from functools import lru_cache
from hashlib import sha256
import json
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.v1.auth import get_current_active_user
from app.config import settings
from app.db import get_db
from app.integrations.object_store import (
    MediaObjectStore,
    ObjectStoreConfigurationError,
    ObjectStoreIntegrityError,
    PresignedUpload,
    S3CompatibleMediaObjectStore,
)
from app.models.database import MediaAsset, MediaUploadIntent, User
from app.services.agent_runtime.contracts import (
    ExecutionPrincipal,
    Sensitivity,
    derive_sensitivity,
)
from app.services.idempotency import IdempotencyConflict
from app.services.media.assets import (
    MediaAssetConflict,
    MediaAssetForbidden,
    MediaAssetNotFound,
    MediaAssetService,
    UploadIntentCommand,
)
from app.services.media.contracts import MediaAssetKind


router = APIRouter()


class UploadIntentRequest(BaseModel):
    """Browser input; storage, identity, and final sensitivity are server-owned."""

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=8, max_length=255)
    kind: MediaAssetKind
    expected_mime_type: str = Field(min_length=1, max_length=255)
    expected_size_bytes: int = Field(ge=1, le=2_000_000_000)
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    requested_sensitivity_floor: Optional[Sensitivity] = None
    consent_required: bool = False


class UploadIntentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    expires_at: datetime
    upload: PresignedUpload


class MediaAssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    mime_type: str
    size_bytes: int
    sensitivity: str
    quarantined: bool
    scan_status: str
    rights_status: str
    consent_required: bool
    consent_status: str
    created_at: datetime


def get_media_asset_service(db: Session = Depends(get_db)) -> MediaAssetService:
    return MediaAssetService(db, upload_enabled=settings.MEDIA_UPLOAD_ENABLED)


@lru_cache(maxsize=1)
def get_media_object_store() -> Optional[MediaObjectStore]:
    if settings.MEDIA_OBJECT_STORE_BACKEND != "s3":
        return None
    return S3CompatibleMediaObjectStore(
        quarantine_bucket=settings.MEDIA_S3_QUARANTINE_BUCKET,
        asset_bucket=settings.MEDIA_S3_ASSET_BUCKET,
        key_prefix=settings.MEDIA_S3_KEY_PREFIX,
        kms_key_id=settings.MEDIA_S3_KMS_KEY_ID,
        endpoint_url=settings.MEDIA_S3_ENDPOINT_URL,
        region_name=settings.MEDIA_S3_REGION,
    )


def _principal(user: User) -> ExecutionPrincipal:
    role = str(user.role or "user").strip().lower() or "user"
    roles = {role}
    if user.is_superuser:
        roles.add("admin")
    entitlements = {
        "org_id": str(settings.AGENT_ORG_ID),
        "user_id": user.id,
        "roles": sorted(roles),
        "is_active": bool(user.is_active),
        "is_superuser": bool(user.is_superuser),
    }
    return ExecutionPrincipal(
        org_id=settings.AGENT_ORG_ID,
        user_id=user.id,
        roles=roles,
        entitlements_hash=sha256(
            json.dumps(
                entitlements,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        authn_context="jwt",
    )


@router.post(
    "/assets/uploads",
    response_model=UploadIntentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_upload_intent(
    request: UploadIntentRequest,
    current_user: User = Depends(get_current_active_user),
    asset_service: MediaAssetService = Depends(get_media_asset_service),
    object_store: Optional[MediaObjectStore] = Depends(get_media_object_store),
):
    try:
        command = UploadIntentCommand(
            idempotency_key=request.idempotency_key,
            kind=request.kind,
            expected_mime_type=request.expected_mime_type,
            expected_size_bytes=request.expected_size_bytes,
            expected_sha256=request.expected_sha256,
            sensitivity=derive_sensitivity(
                Sensitivity.INTERNAL,
                request.requested_sensitivity_floor or Sensitivity.INTERNAL,
            ),
            consent_required=request.consent_required,
        )
        upload = asset_service.create_upload_intent(
            command,
            _principal(current_user),
        )
        if object_store is None:
            raise ObjectStoreConfigurationError(
                "Media object store is unavailable"
            )
        presigned = object_store.create_upload(
            key=upload.storage_key,
            content_type=upload.expected_mime_type,
            size_bytes=upload.expected_size_bytes,
            sha256=upload.expected_sha256,
            expires_seconds=900,
        )
        return UploadIntentResponse(
            id=upload.id,
            status=upload.status,
            expires_at=upload.expires_at,
            upload=presigned,
        )
    except MediaAssetForbidden as exc:
        if "disabled" in str(exc).lower():
            raise HTTPException(
                status_code=503,
                detail="Media upload is disabled",
            ) from exc
        raise HTTPException(status_code=403, detail="Media upload forbidden") from exc
    except IdempotencyConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="Upload request conflicts with existing input",
        ) from exc
    except ObjectStoreConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail="Media object store is unavailable",
        ) from exc


@router.post(
    "/assets/uploads/{upload_id}/complete",
    response_model=MediaAssetResponse,
)
async def complete_upload(
    upload_id: UUID,
    current_user: User = Depends(get_current_active_user),
    asset_service: MediaAssetService = Depends(get_media_asset_service),
    object_store: Optional[MediaObjectStore] = Depends(get_media_object_store),
):
    if object_store is None:
        raise HTTPException(
            status_code=503,
            detail="Media object store is unavailable",
        )
    try:
        asset: MediaAsset = asset_service.complete_upload(
            upload_id,
            _principal(current_user),
            object_store,
        )
        return MediaAssetResponse.model_validate(asset)
    except MediaAssetNotFound as exc:
        raise HTTPException(status_code=404, detail="Upload was not found") from exc
    except MediaAssetForbidden as exc:
        if "disabled" in str(exc).lower():
            raise HTTPException(
                status_code=503,
                detail="Media upload is disabled",
            ) from exc
        raise HTTPException(status_code=403, detail="Media upload forbidden") from exc
    except (MediaAssetConflict, ObjectStoreIntegrityError) as exc:
        raise HTTPException(
            status_code=409,
            detail="Upload cannot be completed",
        ) from exc
