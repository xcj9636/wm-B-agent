"""Authenticated video studio asset APIs."""

from datetime import datetime
from functools import lru_cache
from hashlib import sha256
import json
from typing import Callable, Optional
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
from app.models.database import (
    MediaAsset,
    MediaConsentRecord,
    MediaRightsRecord,
    User,
)
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
from app.services.media.contracts import (
    AssetConsentStatus,
    AssetRightsStatus,
    MediaAssetKind,
)
from app.services.media.review import (
    ConsentEvidenceCommand,
    MediaReviewService,
    RightsEvidenceCommand,
)
from app.tasks.media_tasks import inspect_media_asset_task


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


class InspectionQueueRequest(BaseModel):
    """Intentionally empty: inspection facts are owned by the worker."""

    model_config = ConfigDict(extra="forbid")


class InspectionQueueResponse(BaseModel):
    asset_id: UUID
    task_id: str
    status: str


class RightsReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AssetRightsStatus
    basis: str = Field(min_length=1, max_length=100)
    territories: list[str] = Field(min_length=1, max_length=100)
    channels: list[str] = Field(min_length=1, max_length=100)
    source_ref: str = Field(min_length=1, max_length=500)
    valid_from: datetime
    valid_until: Optional[datetime] = None


class RightsReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    asset_id: UUID
    status: str
    basis: str
    territories: list[str]
    channels: list[str]
    source_ref: str
    valid_from: datetime
    valid_until: Optional[datetime]
    created_at: datetime


class ConsentReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_ref: str = Field(min_length=1, max_length=255)
    purpose: str = Field(min_length=1, max_length=500)
    regions: list[str] = Field(min_length=1, max_length=100)
    media_types: list[str] = Field(min_length=1, max_length=20)
    status: AssetConsentStatus
    valid_from: datetime
    valid_until: Optional[datetime] = None
    evidence_asset_id: UUID


class ConsentReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    asset_id: UUID
    subject_ref: str
    purpose: str
    regions: list[str]
    media_types: list[str]
    status: str
    valid_from: datetime
    valid_until: Optional[datetime]
    evidence_asset_id: UUID
    created_at: datetime


class PromoteAssetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scan_report_id: UUID
    rights_record_id: UUID
    consent_record_id: Optional[UUID] = None


def get_media_asset_service(db: Session = Depends(get_db)) -> MediaAssetService:
    return MediaAssetService(db, upload_enabled=settings.MEDIA_UPLOAD_ENABLED)


def get_media_review_service(db: Session = Depends(get_db)) -> MediaReviewService:
    return MediaReviewService(db)


class MediaInspectionDispatchUnavailable(RuntimeError):
    pass


def dispatch_media_inspection(asset_id: UUID, requested_by_user_id: int) -> str:
    if not settings.MEDIA_INSPECTION_ENABLED:
        raise MediaInspectionDispatchUnavailable("Media inspection is disabled")
    result = inspect_media_asset_task.delay(str(asset_id), requested_by_user_id)
    return str(result.id)


def get_media_inspection_dispatcher() -> Callable[[UUID, int], str]:
    return dispatch_media_inspection


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


@router.post(
    "/assets/{asset_id}/inspection",
    response_model=InspectionQueueResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_media_inspection(
    asset_id: UUID,
    request: InspectionQueueRequest,
    current_user: User = Depends(get_current_active_user),
    asset_service: MediaAssetService = Depends(get_media_asset_service),
    dispatcher: Callable[[UUID, int], str] = Depends(
        get_media_inspection_dispatcher
    ),
):
    try:
        principal = _principal(current_user)
        if "admin" not in principal.roles:
            raise MediaAssetForbidden(
                "Media inspection dispatch requires an administrator"
            )
        asset_service.policy_snapshot(asset_id, principal)
        task_id = dispatcher(asset_id, current_user.id)
        return InspectionQueueResponse(
            asset_id=asset_id,
            task_id=task_id,
            status="queued",
        )
    except MediaInspectionDispatchUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Media inspection is unavailable",
        ) from exc
    except Exception as exc:
        _raise_review_http_error(exc)


@router.post(
    "/assets/{asset_id}/reviews/rights",
    response_model=RightsReviewResponse,
    status_code=status.HTTP_201_CREATED,
)
async def record_rights_review(
    asset_id: UUID,
    request: RightsReviewRequest,
    current_user: User = Depends(get_current_active_user),
    review_service: MediaReviewService = Depends(get_media_review_service),
):
    try:
        record: MediaRightsRecord = review_service.record_rights(
            asset_id,
            RightsEvidenceCommand(**request.model_dump()),
            _principal(current_user),
        )
        return RightsReviewResponse.model_validate(record)
    except Exception as exc:
        _raise_review_http_error(exc)


@router.post(
    "/assets/{asset_id}/reviews/consent",
    response_model=ConsentReviewResponse,
    status_code=status.HTTP_201_CREATED,
)
async def record_consent_review(
    asset_id: UUID,
    request: ConsentReviewRequest,
    current_user: User = Depends(get_current_active_user),
    review_service: MediaReviewService = Depends(get_media_review_service),
):
    try:
        record: MediaConsentRecord = review_service.record_consent(
            asset_id,
            ConsentEvidenceCommand(**request.model_dump()),
            _principal(current_user),
        )
        return ConsentReviewResponse.model_validate(record)
    except Exception as exc:
        _raise_review_http_error(exc)


@router.post(
    "/assets/{asset_id}/promote",
    response_model=MediaAssetResponse,
)
async def promote_asset(
    asset_id: UUID,
    request: PromoteAssetRequest,
    current_user: User = Depends(get_current_active_user),
    review_service: MediaReviewService = Depends(get_media_review_service),
    object_store: Optional[MediaObjectStore] = Depends(get_media_object_store),
):
    if object_store is None:
        raise HTTPException(
            status_code=503,
            detail="Media object store is unavailable",
        )
    try:
        asset = review_service.promote(
            asset_id,
            scan_report_id=request.scan_report_id,
            rights_record_id=request.rights_record_id,
            consent_record_id=request.consent_record_id,
            principal=_principal(current_user),
            object_store=object_store,
        )
        return MediaAssetResponse.model_validate(asset)
    except Exception as exc:
        _raise_review_http_error(exc)


def _raise_review_http_error(exc: Exception) -> None:
    if isinstance(exc, MediaAssetNotFound):
        raise HTTPException(status_code=404, detail="Media review resource not found") from exc
    if isinstance(exc, MediaAssetForbidden):
        raise HTTPException(status_code=403, detail="Media review forbidden") from exc
    if isinstance(exc, MediaAssetConflict):
        raise HTTPException(status_code=409, detail="Media review evidence is invalid") from exc
    raise exc
