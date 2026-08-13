"""Authenticated video studio asset APIs."""

from datetime import datetime, timezone
from functools import lru_cache
from hashlib import sha256
import json
from typing import Callable, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.v1.auth import get_current_active_user
from app.config import settings
from app.db import get_db
from app.integrations.object_store import (
    MediaObjectStore,
    ObjectStoreConfigurationError,
    ObjectStoreIntegrityError,
    PresignedDownload,
    PresignedUpload,
    S3CompatibleMediaObjectStore,
)
from app.models.database import (
    MediaAsset,
    MediaConsentRecord,
    MediaRightsRecord,
    User,
    VideoPersonaVersion,
    VideoProject,
    VideoProjectEvidence,
    VideoStoryboardVersion,
    MediaGenerationJob,
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
from app.services.media.access import MediaAssetAccessService
from app.services.media.contracts import (
    AssetConsentStatus,
    AssetRightsStatus,
    GenerationMode,
    MediaAssetKind,
    Storyboard,
    VideoPersonaSpec,
    VideoProjectBrief,
)
from app.services.media.personas import (
    PersonaRevisionCommand,
    VideoPersonaConflict,
    VideoPersonaForbidden,
    VideoPersonaNotFound,
    VideoPersonaService,
)
from app.services.media.planning import (
    StoryboardRevisionCommand,
    VideoPlanningConflict,
    VideoPlanningForbidden,
    VideoPlanningNotFound,
    VideoPlanningService,
    VideoProjectCommand,
)
from app.services.media.prompts import (
    VideoPromptCompiler,
    VideoPromptConflict,
    VideoPromptForbidden,
    VideoPromptNotFound,
)
from app.services.media.review import (
    ConsentEvidenceCommand,
    MediaReviewService,
    RightsEvidenceCommand,
)
from app.services.media.thumbnail import MediaThumbnailService
from app.services.media.lifecycle import MediaAssetLifecycleService
from app.services.media.intent_vault import EncryptedMediaIntentVault
from app.services.media.job_creator import (
    MediaGenerationJobCreateRequest,
    MediaGenerationJobCreator,
    MediaGenerationJobUnavailable,
)
from app.tasks.media_tasks import (
    generate_media_thumbnail_task,
    inspect_media_asset_task,
)


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


class ThumbnailQueueRequest(BaseModel):
    """Intentionally empty: transform parameters are server-owned."""

    model_config = ConfigDict(extra="forbid")


class ThumbnailQueueResponse(BaseModel):
    asset_id: UUID
    task_id: str
    status: str


class MediaDeletionResponse(BaseModel):
    id: UUID
    status: str
    deleted_at: datetime


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


class PersonaRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=8, max_length=255)
    spec: VideoPersonaSpec


class EmptyApprovalRequest(BaseModel):
    """Approval identity and status are always derived on the server."""

    model_config = ConfigDict(extra="forbid")


class PersonaRevisionResponse(BaseModel):
    persona_id: UUID
    version_id: UUID
    revision: int
    status: str
    spec_hash: str
    spec: VideoPersonaSpec
    approved_by_user_id: Optional[int]
    approved_at: Optional[datetime]
    created_at: datetime


class PersonaListResponse(BaseModel):
    items: list[PersonaRevisionResponse]
    total: int
    limit: int
    offset: int


class VideoProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=8, max_length=255)
    persona_version_id: UUID
    brief: VideoProjectBrief
    evidence_record_ids: list[UUID] = Field(default_factory=list, max_length=50)


class ProjectEvidenceResponse(BaseModel):
    id: UUID
    knowledge_record_id: UUID
    document_id: UUID
    document_version: int
    source_ref: str
    title: str
    authority: str
    sensitivity: str
    content_hash: str


class VideoProjectResponse(BaseModel):
    id: UUID
    persona_version_id: UUID
    persona_spec_hash: str
    brief: VideoProjectBrief
    brief_hash: str
    sensitivity: str
    status: str
    evidence: list[ProjectEvidenceResponse]
    created_at: datetime
    updated_at: datetime


class ProjectListResponse(BaseModel):
    items: list[VideoProjectResponse]
    total: int
    limit: int
    offset: int


class StoryboardRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=8, max_length=255)
    storyboard: Storyboard


class StoryboardRevisionResponse(BaseModel):
    project_id: UUID
    version_id: UUID
    revision: int
    status: str
    storyboard_hash: str
    storyboard: Storyboard
    approved_by_user_id: Optional[int]
    approved_at: Optional[datetime]
    created_at: datetime


class VideoProjectDetailResponse(VideoProjectResponse):
    storyboards: list[StoryboardRevisionResponse]


class CompileShotRequest(BaseModel):
    """Provider, routing, identity, and policy inputs are server-owned."""

    model_config = ConfigDict(extra="forbid")


class CompileShotResponse(BaseModel):
    shot_id: UUID
    persona_version_id: UUID
    mode: str
    sensitivity: str
    reference_asset_ids: list[UUID]
    prompt_hash: str
    evidence_snapshot_hash: str


class CreateGenerationJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=8, max_length=255)
    storyboard_version_id: UUID


class MediaGenerationJobResponse(BaseModel):
    id: UUID
    project_id: UUID
    storyboard_version_id: UUID
    shot_id: UUID
    mode: str
    provider: str
    model_id: str
    sensitivity: str
    status: str
    effect_state: str
    reservation_ceiling_microusd: int
    provider_state: Optional[str]
    error_code: Optional[str]
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime]


def get_media_asset_service(db: Session = Depends(get_db)) -> MediaAssetService:
    return MediaAssetService(db, upload_enabled=settings.MEDIA_UPLOAD_ENABLED)


def get_media_review_service(db: Session = Depends(get_db)) -> MediaReviewService:
    return MediaReviewService(db)


def get_media_access_service(
    db: Session = Depends(get_db),
) -> MediaAssetAccessService:
    return MediaAssetAccessService(db)


def get_media_thumbnail_service(
    db: Session = Depends(get_db),
) -> MediaThumbnailService:
    return MediaThumbnailService(db)


def get_media_lifecycle_service(
    db: Session = Depends(get_db),
) -> MediaAssetLifecycleService:
    return MediaAssetLifecycleService(db)


def get_video_persona_service(
    db: Session = Depends(get_db),
) -> VideoPersonaService:
    return VideoPersonaService(
        db,
        planning_enabled=settings.MEDIA_PLANNING_ENABLED,
    )


def get_video_planning_service(
    db: Session = Depends(get_db),
) -> VideoPlanningService:
    return VideoPlanningService(
        db,
        planning_enabled=settings.MEDIA_PLANNING_ENABLED,
    )


def get_video_prompt_compiler(
    db: Session = Depends(get_db),
) -> VideoPromptCompiler:
    return VideoPromptCompiler(
        db,
        planning_enabled=settings.MEDIA_PLANNING_ENABLED,
    )


def get_media_generation_job_creator(
    db: Session = Depends(get_db),
) -> MediaGenerationJobCreator:
    if not settings.MEDIA_SUBMIT_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Media generation is unavailable",
        )
    compiler = VideoPromptCompiler(
        db,
        planning_enabled=settings.MEDIA_PLANNING_ENABLED,
    )
    vault = EncryptedMediaIntentVault(
        root=settings.MEDIA_INTENT_VAULT_DIR,
        key_file=settings.MEDIA_INTENT_VAULT_KEY_FILE,
    )
    return MediaGenerationJobCreator(
        db,
        compiler=compiler,
        vault=vault,
        reservation_ceilings={
            GenerationMode.TEXT_TO_VIDEO: (
                settings.MEDIA_T2V_RESERVATION_CEILING_MICROUSD
            )
        },
        deadline_seconds=settings.MEDIA_JOB_DEADLINE_SECONDS,
    )


class MediaInspectionDispatchUnavailable(RuntimeError):
    pass


def dispatch_media_inspection(asset_id: UUID, requested_by_user_id: int) -> str:
    if not settings.MEDIA_INSPECTION_ENABLED:
        raise MediaInspectionDispatchUnavailable("Media inspection is disabled")
    result = inspect_media_asset_task.delay(str(asset_id), requested_by_user_id)
    return str(result.id)


def get_media_inspection_dispatcher() -> Callable[[UUID, int], str]:
    return dispatch_media_inspection


def dispatch_media_thumbnail(asset_id: UUID, requested_by_user_id: int) -> str:
    if not settings.MEDIA_THUMBNAIL_ENABLED:
        raise MediaInspectionDispatchUnavailable("Media thumbnails are disabled")
    result = generate_media_thumbnail_task.delay(
        str(asset_id),
        requested_by_user_id,
    )
    return str(result.id)


def get_media_thumbnail_dispatcher() -> Callable[[UUID, int], str]:
    return dispatch_media_thumbnail


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


def _persona_response(
    persona_id: UUID,
    version: VideoPersonaVersion,
) -> PersonaRevisionResponse:
    return PersonaRevisionResponse(
        persona_id=persona_id,
        version_id=version.id,
        revision=version.revision,
        status=version.status,
        spec_hash=version.spec_hash,
        spec=VideoPersonaSpec.model_validate(version.spec_json),
        approved_by_user_id=version.approved_by_user_id,
        approved_at=version.approved_at,
        created_at=version.created_at,
    )


def _storyboard_response(
    version: VideoStoryboardVersion,
) -> StoryboardRevisionResponse:
    return StoryboardRevisionResponse(
        project_id=version.project_id,
        version_id=version.id,
        revision=version.revision,
        status=version.status,
        storyboard_hash=version.storyboard_hash,
        storyboard=Storyboard.model_validate(version.storyboard_json),
        approved_by_user_id=version.approved_by_user_id,
        approved_at=version.approved_at,
        created_at=version.created_at,
    )


def _project_response(
    project: VideoProject,
    evidence: list[VideoProjectEvidence],
) -> VideoProjectResponse:
    return VideoProjectResponse(
        id=project.id,
        persona_version_id=project.persona_version_id,
        persona_spec_hash=project.persona_spec_hash,
        brief=VideoProjectBrief.model_validate(project.brief_json),
        brief_hash=project.brief_hash,
        sensitivity=project.sensitivity,
        status=project.status,
        evidence=[
            ProjectEvidenceResponse(
                id=row.id,
                knowledge_record_id=row.knowledge_record_id,
                document_id=row.document_id,
                document_version=row.document_version,
                source_ref=row.source_ref,
                title=row.title,
                authority=row.authority,
                sensitivity=row.sensitivity,
                content_hash=row.content_hash,
            )
            for row in evidence
        ],
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _media_generation_job_response(
    job: MediaGenerationJob,
) -> MediaGenerationJobResponse:
    return MediaGenerationJobResponse(
        id=job.id,
        project_id=job.project_id,
        storyboard_version_id=job.storyboard_version_id,
        shot_id=job.shot_id,
        mode=job.mode,
        provider=job.provider,
        model_id=job.model_id,
        sensitivity=job.sensitivity,
        status=job.status,
        effect_state=job.effect_state,
        reservation_ceiling_microusd=job.reserved_cost_microusd,
        provider_state=job.provider_state,
        error_code=job.error_code,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
    )


def _require_media_reviewer(principal: ExecutionPrincipal) -> None:
    roles = {role.strip().lower() for role in principal.roles}
    if not roles.intersection({"media_reviewer", "admin"}):
        raise HTTPException(
            status_code=403,
            detail="Media approval requires reviewer role",
        )


@router.get("/personas", response_model=PersonaListResponse)
async def list_video_personas(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_active_user),
    persona_service: VideoPersonaService = Depends(get_video_persona_service),
):
    try:
        items, total = persona_service.list_latest(
            _principal(current_user),
            limit=limit,
            offset=offset,
        )
        return PersonaListResponse(
            items=[
                _persona_response(persona.id, version)
                for persona, version in items
            ],
            total=total,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        _raise_video_planning_http_error(exc, "persona")


@router.get(
    "/personas/{persona_id}/versions",
    response_model=PersonaListResponse,
)
async def list_video_persona_versions(
    persona_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_active_user),
    persona_service: VideoPersonaService = Depends(get_video_persona_service),
):
    try:
        persona, versions, total = persona_service.list_versions(
            persona_id,
            _principal(current_user),
            limit=limit,
            offset=offset,
        )
        return PersonaListResponse(
            items=[_persona_response(persona.id, version) for version in versions],
            total=total,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        _raise_video_planning_http_error(exc, "persona")


@router.get("/projects", response_model=ProjectListResponse)
async def list_video_projects(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_active_user),
    planning_service: VideoPlanningService = Depends(get_video_planning_service),
):
    try:
        items, total = planning_service.list_projects(
            _principal(current_user),
            limit=limit,
            offset=offset,
        )
        return ProjectListResponse(
            items=[
                _project_response(project, evidence)
                for project, evidence in items
            ],
            total=total,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        _raise_video_planning_http_error(exc, "project")


@router.get(
    "/projects/{project_id}",
    response_model=VideoProjectDetailResponse,
)
async def get_video_project(
    project_id: UUID,
    current_user: User = Depends(get_current_active_user),
    planning_service: VideoPlanningService = Depends(get_video_planning_service),
):
    try:
        project, evidence, storyboards = planning_service.project_detail(
            project_id,
            _principal(current_user),
        )
        base = _project_response(project, evidence)
        return VideoProjectDetailResponse(
            **base.model_dump(),
            storyboards=[_storyboard_response(version) for version in storyboards],
        )
    except Exception as exc:
        _raise_video_planning_http_error(exc, "project")


@router.post(
    "/personas",
    response_model=PersonaRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_video_persona(
    request: PersonaRevisionRequest,
    current_user: User = Depends(get_current_active_user),
    persona_service: VideoPersonaService = Depends(get_video_persona_service),
):
    try:
        persona, version = persona_service.create(
            PersonaRevisionCommand(**request.model_dump()),
            _principal(current_user),
        )
        return _persona_response(persona.id, version)
    except Exception as exc:
        _raise_video_planning_http_error(exc, "persona")


@router.post(
    "/personas/{persona_id}/versions",
    response_model=PersonaRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def revise_video_persona(
    persona_id: UUID,
    request: PersonaRevisionRequest,
    current_user: User = Depends(get_current_active_user),
    persona_service: VideoPersonaService = Depends(get_video_persona_service),
):
    try:
        persona, version = persona_service.revise(
            persona_id,
            PersonaRevisionCommand(**request.model_dump()),
            _principal(current_user),
        )
        return _persona_response(persona.id, version)
    except Exception as exc:
        _raise_video_planning_http_error(exc, "persona")


@router.post(
    "/persona-versions/{version_id}/approve",
    response_model=PersonaRevisionResponse,
)
async def approve_video_persona(
    version_id: UUID,
    request: EmptyApprovalRequest,
    current_user: User = Depends(get_current_active_user),
    persona_service: VideoPersonaService = Depends(get_video_persona_service),
):
    principal = _principal(current_user)
    _require_media_reviewer(principal)
    try:
        version = persona_service.approve(version_id, principal)
        return _persona_response(version.persona_id, version)
    except Exception as exc:
        _raise_video_planning_http_error(exc, "persona")


@router.post(
    "/projects",
    response_model=VideoProjectResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_video_project(
    request: VideoProjectRequest,
    current_user: User = Depends(get_current_active_user),
    planning_service: VideoPlanningService = Depends(get_video_planning_service),
):
    try:
        project, evidence = planning_service.create_project(
            VideoProjectCommand(**request.model_dump()),
            _principal(current_user),
        )
        return _project_response(project, evidence)
    except Exception as exc:
        _raise_video_planning_http_error(exc, "project")


@router.post(
    "/projects/{project_id}/storyboards",
    response_model=StoryboardRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def revise_video_storyboard(
    project_id: UUID,
    request: StoryboardRevisionRequest,
    current_user: User = Depends(get_current_active_user),
    planning_service: VideoPlanningService = Depends(get_video_planning_service),
):
    try:
        _, version = planning_service.revise_storyboard(
            project_id,
            StoryboardRevisionCommand(**request.model_dump()),
            _principal(current_user),
        )
        return _storyboard_response(version)
    except Exception as exc:
        _raise_video_planning_http_error(exc, "storyboard")


@router.post(
    "/storyboard-versions/{version_id}/approve",
    response_model=StoryboardRevisionResponse,
)
async def approve_video_storyboard(
    version_id: UUID,
    request: EmptyApprovalRequest,
    current_user: User = Depends(get_current_active_user),
    planning_service: VideoPlanningService = Depends(get_video_planning_service),
):
    principal = _principal(current_user)
    _require_media_reviewer(principal)
    try:
        version = planning_service.approve_storyboard(version_id, principal)
        return _storyboard_response(version)
    except Exception as exc:
        _raise_video_planning_http_error(exc, "storyboard")


@router.post(
    (
        "/projects/{project_id}/storyboards/{storyboard_version_id}"
        "/shots/{shot_id}/compile"
    ),
    response_model=CompileShotResponse,
)
async def compile_video_shot(
    project_id: UUID,
    storyboard_version_id: UUID,
    shot_id: UUID,
    request: CompileShotRequest,
    current_user: User = Depends(get_current_active_user),
    compiler: VideoPromptCompiler = Depends(get_video_prompt_compiler),
):
    try:
        compiled = compiler.compile(
            project_id,
            storyboard_version_id,
            shot_id,
            _principal(current_user),
        )
        return CompileShotResponse(
            shot_id=compiled.intent.shot_id,
            persona_version_id=compiled.intent.persona_version_id,
            mode=compiled.intent.mode.value,
            sensitivity=compiled.intent.sensitivity.value,
            reference_asset_ids=compiled.intent.reference_asset_ids,
            prompt_hash=compiled.prompt_hash,
            evidence_snapshot_hash=compiled.evidence_snapshot_hash,
        )
    except Exception as exc:
        _raise_video_planning_http_error(exc, "generation")


@router.post(
    "/projects/{project_id}/shots/{shot_id}/generation-jobs",
    response_model=MediaGenerationJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_media_generation_job(
    project_id: UUID,
    shot_id: UUID,
    request: CreateGenerationJobRequest,
    response: Response,
    current_user: User = Depends(get_current_active_user),
    creator: MediaGenerationJobCreator = Depends(
        get_media_generation_job_creator
    ),
):
    try:
        job, created = creator.create(
            MediaGenerationJobCreateRequest(
                idempotency_key=request.idempotency_key,
                project_id=project_id,
                storyboard_version_id=request.storyboard_version_id,
                shot_id=shot_id,
            ),
            _principal(current_user),
            now=datetime.now(timezone.utc),
        )
        response.status_code = (
            status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK
        )
        return _media_generation_job_response(job)
    except Exception as exc:
        _raise_media_generation_http_error(exc)


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
    "/assets/{asset_id}/thumbnail",
    response_model=ThumbnailQueueResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_media_thumbnail(
    asset_id: UUID,
    request: ThumbnailQueueRequest,
    current_user: User = Depends(get_current_active_user),
    thumbnail_service: MediaThumbnailService = Depends(
        get_media_thumbnail_service
    ),
    dispatcher: Callable[[UUID, int], str] = Depends(
        get_media_thumbnail_dispatcher
    ),
):
    try:
        thumbnail_service.authorize_request(
            asset_id,
            _principal(current_user),
        )
        task_id = dispatcher(asset_id, current_user.id)
        return ThumbnailQueueResponse(
            asset_id=asset_id,
            task_id=task_id,
            status="queued",
        )
    except MediaInspectionDispatchUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Media thumbnail service is unavailable",
        ) from exc
    except (MediaAssetNotFound, MediaAssetForbidden) as exc:
        raise HTTPException(status_code=404, detail="Media asset not found") from exc
    except MediaAssetConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="Media asset cannot produce a thumbnail",
        ) from exc


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


@router.post(
    "/assets/{asset_id}/download",
    response_model=PresignedDownload,
)
async def create_asset_download(
    asset_id: UUID,
    current_user: User = Depends(get_current_active_user),
    access_service: MediaAssetAccessService = Depends(get_media_access_service),
    object_store: Optional[MediaObjectStore] = Depends(get_media_object_store),
):
    if object_store is None:
        raise HTTPException(
            status_code=503,
            detail="Media object store is unavailable",
        )
    try:
        return access_service.create_download(
            asset_id,
            _principal(current_user),
            object_store,
            expires_seconds=settings.MEDIA_DOWNLOAD_TTL_SECONDS,
        )
    except MediaAssetNotFound as exc:
        raise HTTPException(status_code=404, detail="Media asset not found") from exc
    except MediaAssetForbidden as exc:
        # Do not reveal whether an asset exists across authorization boundaries.
        raise HTTPException(status_code=404, detail="Media asset not found") from exc
    except MediaAssetConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="Media asset is not downloadable",
        ) from exc


@router.delete(
    "/assets/{asset_id}",
    response_model=MediaDeletionResponse,
)
async def soft_delete_media_asset(
    asset_id: UUID,
    current_user: User = Depends(get_current_active_user),
    lifecycle_service: MediaAssetLifecycleService = Depends(
        get_media_lifecycle_service
    ),
):
    try:
        deleted = lifecycle_service.soft_delete(
            asset_id,
            _principal(current_user),
        )
        return MediaDeletionResponse(
            id=deleted.id,
            status="deleted",
            deleted_at=deleted.deleted_at,
        )
    except (MediaAssetNotFound, MediaAssetForbidden) as exc:
        raise HTTPException(status_code=404, detail="Media asset not found") from exc
    except MediaAssetConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="Media asset is still referenced",
        ) from exc


def _raise_review_http_error(exc: Exception) -> None:
    if isinstance(exc, MediaAssetNotFound):
        raise HTTPException(status_code=404, detail="Media review resource not found") from exc
    if isinstance(exc, MediaAssetForbidden):
        raise HTTPException(status_code=403, detail="Media review forbidden") from exc
    if isinstance(exc, MediaAssetConflict):
        raise HTTPException(status_code=409, detail="Media review evidence is invalid") from exc
    raise exc


def _raise_video_planning_http_error(exc: Exception, resource: str) -> None:
    if isinstance(
        exc,
        (VideoPersonaNotFound, VideoPlanningNotFound, VideoPromptNotFound),
    ):
        raise HTTPException(
            status_code=404,
            detail=f"Video {resource} not found",
        ) from exc
    if isinstance(
        exc,
        (VideoPersonaForbidden, VideoPlanningForbidden, VideoPromptForbidden),
    ):
        if "disabled" in str(exc).lower():
            raise HTTPException(
                status_code=503,
                detail="Video planning is unavailable",
            ) from exc
        # Cross-tenant and ownership failures intentionally match missing resources.
        raise HTTPException(
            status_code=404,
            detail=f"Video {resource} not found",
        ) from exc
    if isinstance(
        exc,
        (
            IdempotencyConflict,
            VideoPersonaConflict,
            VideoPlanningConflict,
            VideoPromptConflict,
        ),
    ):
        raise HTTPException(
            status_code=409,
            detail=f"Video {resource} request conflicts with current state",
        ) from exc
    raise exc


def _raise_media_generation_http_error(exc: Exception) -> None:
    if isinstance(exc, (PermissionError, LookupError)):
        raise HTTPException(
            status_code=404,
            detail="Media generation resource not found",
        ) from exc
    if isinstance(exc, IdempotencyConflict):
        raise HTTPException(
            status_code=409,
            detail="Media generation request conflicts with current state",
        ) from exc
    if isinstance(exc, MediaGenerationJobUnavailable):
        raise HTTPException(
            status_code=503,
            detail="Media generation is unavailable",
        ) from exc
    _raise_video_planning_http_error(exc, "generation")
