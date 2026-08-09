"""Authenticated prospect search and selective customer import API."""
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.v1.auth import get_current_active_user
from app.db import get_db
from app.integrations.hunter import HunterClient, get_hunter_client
from app.models.database import User
from app.services.prospecting import (
    ProspectingImportCommand,
    ProspectingImportResponse,
    ProspectingProviderFailure,
    ProspectingRecordNotFound,
    ProspectingSearchCreate,
    ProspectingSearchResponse,
    ProspectingService,
)


router = APIRouter()


@router.post(
    "/searches",
    response_model=ProspectingSearchResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_search(
    command: ProspectingSearchCreate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    hunter: HunterClient = Depends(get_hunter_client),
):
    try:
        return await ProspectingService(db).create_search(
            command,
            user_id=current_user.id,
            hunter=hunter,
        )
    except ProspectingProviderFailure as exc:
        if exc.legal_restriction:
            raise HTTPException(
                status_code=451,
                detail="Provider declined this request for legal reasons",
            ) from exc
        raise HTTPException(
            status_code=503 if exc.retryable else 422,
            detail=exc.error_code,
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/searches", response_model=List[ProspectingSearchResponse])
async def list_searches(
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    return ProspectingService(db).list_searches(
        user_id=current_user.id,
        limit=limit,
    )


@router.get("/searches/{search_id}", response_model=ProspectingSearchResponse)
async def get_search(
    search_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    try:
        return ProspectingService(db).get_search(
            search_id,
            user_id=current_user.id,
        )
    except ProspectingRecordNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/contacts/import", response_model=ProspectingImportResponse)
async def import_contacts(
    command: ProspectingImportCommand,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    try:
        return ProspectingService(db).import_contacts(
            command,
            user_id=current_user.id,
        )
    except ProspectingRecordNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
