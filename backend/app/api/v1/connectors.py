"""Administrator connector control plane."""
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.auth import get_current_active_user
from app.config import settings
from app.db import get_db
from app.models.database import User
from app.services.connectors import (
    ConnectorCatalogItem,
    ConnectorConflict,
    ConnectorCreate,
    ConnectorNotFound,
    ConnectorProbe,
    ConnectorResponse,
    ConnectorService,
    ConnectorUpdate,
)


router = APIRouter()


def require_admin(current_user: User = Depends(get_current_active_user)) -> User:
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


def service(db: Session = Depends(get_db)) -> ConnectorService:
    return ConnectorService(db, settings)


@router.get("/catalog", response_model=List[ConnectorCatalogItem])
async def catalog(_: User = Depends(require_admin)):
    return ConnectorService.CATALOG


@router.get("", response_model=List[ConnectorResponse])
async def list_connectors(
    _: User = Depends(require_admin),
    connector_service: ConnectorService = Depends(service),
):
    return connector_service.list()


@router.post("", response_model=ConnectorResponse, status_code=status.HTTP_201_CREATED)
async def create_connector(
    command: ConnectorCreate,
    current_user: User = Depends(require_admin),
    connector_service: ConnectorService = Depends(service),
):
    try:
        return connector_service.create(command, updated_by_user_id=current_user.id)
    except (ValueError, ConnectorConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/{connector_id}", response_model=ConnectorResponse)
async def update_connector(
    connector_id: UUID,
    command: ConnectorUpdate,
    current_user: User = Depends(require_admin),
    connector_service: ConnectorService = Depends(service),
):
    try:
        return connector_service.update(
            connector_id,
            command,
            updated_by_user_id=current_user.id,
        )
    except ConnectorNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, ConnectorConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{connector_id}/test", response_model=ConnectorProbe)
async def test_connector(
    connector_id: UUID,
    _: User = Depends(require_admin),
    connector_service: ConnectorService = Depends(service),
):
    try:
        return await connector_service.probe(connector_id)
    except ConnectorNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{connector_id}/enable", response_model=ConnectorResponse)
async def enable_connector(
    connector_id: UUID,
    current_user: User = Depends(require_admin),
    connector_service: ConnectorService = Depends(service),
):
    return _set_enabled(connector_service, connector_id, current_user.id, True)


@router.post("/{connector_id}/disable", response_model=ConnectorResponse)
async def disable_connector(
    connector_id: UUID,
    current_user: User = Depends(require_admin),
    connector_service: ConnectorService = Depends(service),
):
    return _set_enabled(connector_service, connector_id, current_user.id, False)


def _set_enabled(
    connector_service: ConnectorService,
    connector_id: UUID,
    user_id: int,
    enabled: bool,
) -> ConnectorResponse:
    try:
        return connector_service.set_enabled(
            connector_id,
            enabled=enabled,
            updated_by_user_id=user_id,
        )
    except ConnectorNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
