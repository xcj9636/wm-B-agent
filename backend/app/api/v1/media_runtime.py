"""Administrator-only media provider runtime control plane."""
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.v1.auth import get_current_active_user
from app.models.database import User
from app.services.media.runtime import (
    MediaCapabilityCatalog,
    MediaRuntimeProbeResponse,
    MediaRuntimeRevisionCreate,
    MediaRuntimeRevisionResponse,
    MediaRuntimeService,
    MediaRuntimeState,
    get_media_runtime_service,
)


router = APIRouter()


def _require_admin(user: User) -> None:
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin access required")


@router.get("", response_model=MediaRuntimeState)
def get_media_runtime_state(
    current_user: User = Depends(get_current_active_user),
    runtime: MediaRuntimeService = Depends(get_media_runtime_service),
):
    _require_admin(current_user)
    return runtime.get_state()


@router.get("/capabilities", response_model=MediaCapabilityCatalog)
def get_media_runtime_capabilities(
    current_user: User = Depends(get_current_active_user),
    runtime: MediaRuntimeService = Depends(get_media_runtime_service),
):
    _require_admin(current_user)
    return runtime.get_capabilities()


@router.get("/revisions", response_model=List[MediaRuntimeRevisionResponse])
def list_media_runtime_revisions(
    current_user: User = Depends(get_current_active_user),
    runtime: MediaRuntimeService = Depends(get_media_runtime_service),
):
    _require_admin(current_user)
    return runtime.list_revisions()


@router.post(
    "/revisions",
    response_model=MediaRuntimeRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_media_runtime_revision(
    command: MediaRuntimeRevisionCreate,
    current_user: User = Depends(get_current_active_user),
    runtime: MediaRuntimeService = Depends(get_media_runtime_service),
):
    _require_admin(current_user)
    try:
        return await runtime.create_revision(
            command,
            created_by_user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Media provider capability discovery failed",
        ) from exc


@router.post(
    "/revisions/{revision_id}/probe",
    response_model=MediaRuntimeProbeResponse,
)
async def probe_media_runtime_revision(
    revision_id: UUID,
    current_user: User = Depends(get_current_active_user),
    runtime: MediaRuntimeService = Depends(get_media_runtime_service),
):
    _require_admin(current_user)
    try:
        return await runtime.probe_revision(
            revision_id,
            probed_by_user_id=current_user.id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/revisions/{revision_id}/activate",
    response_model=MediaRuntimeState,
)
def activate_media_runtime_revision(
    revision_id: UUID,
    current_user: User = Depends(get_current_active_user),
    runtime: MediaRuntimeService = Depends(get_media_runtime_service),
):
    _require_admin(current_user)
    try:
        return runtime.activate_revision(
            revision_id,
            activated_by_user_id=current_user.id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
