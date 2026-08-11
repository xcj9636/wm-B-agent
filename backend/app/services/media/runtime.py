"""Immutable, secret-safe runtime configuration for media providers."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Literal, Optional, Protocol
from uuid import UUID
import os

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.db import get_db
from app.models.database import (
    MediaRuntimeActivation,
    MediaRuntimeProbeRecord,
    MediaRuntimeRevision,
)
from app.services.idempotency import canonical_hash


class MediaWorkflowMode(str, Enum):
    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_TO_VIDEO = "image_to_video"
    TEXT_TO_VIDEO = "text_to_video"


class MediaModelCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=255, pattern=r"^[a-zA-Z0-9._/-]+$")
    display_name: str = Field(min_length=1, max_length=160)
    modes: List[MediaWorkflowMode] = Field(min_length=1, max_length=3)

    @field_validator("modes")
    @classmethod
    def unique_modes(cls, values: List[MediaWorkflowMode]) -> List[MediaWorkflowMode]:
        if len(set(values)) != len(values):
            raise ValueError("model capability modes must be unique")
        return values


class MediaCapabilityCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["fal"]
    schema_version: str = Field(min_length=1, max_length=100)
    models: List[MediaModelCapability] = Field(min_length=1, max_length=200)

    @field_validator("models")
    @classmethod
    def unique_model_ids(
        cls,
        values: List[MediaModelCapability],
    ) -> List[MediaModelCapability]:
        ids = [model.id for model in values]
        if len(set(ids)) != len(ids):
            raise ValueError("media capability model IDs must be unique")
        return values


class MediaProviderProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ready: bool
    reachable: bool
    issues: List[str] = Field(default_factory=list, max_length=20)


class MediaProviderControl(Protocol):
    def get_capabilities(self) -> MediaCapabilityCatalog: ...

    async def discover_capabilities(self, api_key: str) -> MediaCapabilityCatalog: ...

    async def probe(
        self,
        *,
        api_key: str,
        model_ids: List[str],
    ) -> MediaProviderProbe: ...


class UnavailableMediaProviderControl:
    """Fail-closed default until the concrete provider adapter is wired."""

    def get_capabilities(self) -> MediaCapabilityCatalog:
        raise RuntimeError("media_provider_adapter_unavailable")

    async def discover_capabilities(self, api_key: str) -> MediaCapabilityCatalog:
        raise RuntimeError("media_provider_adapter_unavailable")

    async def probe(
        self,
        *,
        api_key: str,
        model_ids: List[str],
    ) -> MediaProviderProbe:
        return MediaProviderProbe(
            ready=False,
            reachable=False,
            issues=["media_provider_adapter_unavailable"],
        )


class MediaRuntimeRevisionCreate(BaseModel):
    """Write-only key plus exact, server-verified aliases."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["fal"]
    enabled_modes: List[MediaWorkflowMode] = Field(min_length=1, max_length=3)
    model_aliases: Dict[MediaWorkflowMode, str]
    api_key: Optional[SecretStr] = Field(default=None, min_length=1)

    @field_validator("enabled_modes")
    @classmethod
    def unique_enabled_modes(
        cls,
        values: List[MediaWorkflowMode],
    ) -> List[MediaWorkflowMode]:
        if len(set(values)) != len(values):
            raise ValueError("enabled media modes must be unique")
        return values


class MediaRuntimeProbeResponse(MediaProviderProbe):
    id: UUID
    revision_id: UUID
    capability_snapshot_hash: str
    created_at: datetime


class MediaRuntimeRevisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    org_id: UUID
    revision: int
    provider: Literal["fal"]
    enabled_modes: List[MediaWorkflowMode]
    model_aliases: Dict[MediaWorkflowMode, str]
    capability_snapshot: MediaCapabilityCatalog
    capability_snapshot_hash: str
    api_key_configured: bool
    latest_probe: Optional[MediaRuntimeProbeResponse] = None
    created_at: datetime


class MediaRuntimeState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_revision: Optional[MediaRuntimeRevisionResponse]
    submission_enabled: bool
    api_key_configured: bool


class MediaRuntimeService:
    """Create immutable revisions and atomically select the one used by new jobs."""

    def __init__(
        self,
        db: Session,
        config: Settings = settings,
        *,
        provider_control: Optional[MediaProviderControl] = None,
    ) -> None:
        self._db = db
        self._settings = config
        self._provider_control = provider_control or UnavailableMediaProviderControl()

    def get_state(self) -> MediaRuntimeState:
        active = self._active_row()
        revision = self._to_response(active) if active is not None else None
        configured = bool(active and self._secret_path(active.id).is_file())
        return MediaRuntimeState(
            active_revision=revision,
            submission_enabled=bool(
                self._settings.MEDIA_SUBMIT_ENABLED and revision is not None
            ),
            api_key_configured=configured,
        )

    def get_capabilities(self) -> MediaCapabilityCatalog:
        return self._provider_control.get_capabilities()

    def list_revisions(self) -> List[MediaRuntimeRevisionResponse]:
        rows = (
            self._db.query(MediaRuntimeRevision)
            .filter(MediaRuntimeRevision.org_id == self._settings.AGENT_ORG_ID)
            .order_by(MediaRuntimeRevision.revision.desc())
            .all()
        )
        return [self._to_response(row) for row in rows]

    def get_revision(self, revision_id: UUID) -> MediaRuntimeRevisionResponse:
        return self._to_response(self._revision_row(revision_id))

    async def create_revision(
        self,
        command: MediaRuntimeRevisionCreate,
        *,
        created_by_user_id: int,
    ) -> MediaRuntimeRevisionResponse:
        api_key = self._resolve_key_for_new_revision(command.api_key)
        catalog = await self._provider_control.discover_capabilities(api_key)
        if catalog.provider != command.provider:
            raise ValueError("provider capability catalog does not match revision")
        aliases = self._validate_aliases(
            command.enabled_modes,
            command.model_aliases,
            catalog,
        )
        catalog_json = catalog.model_dump(mode="json")
        snapshot_hash = canonical_hash(catalog_json)
        next_revision = (
            self._db.query(func.max(MediaRuntimeRevision.revision))
            .filter(MediaRuntimeRevision.org_id == self._settings.AGENT_ORG_ID)
            .scalar()
            or 0
        ) + 1
        row = MediaRuntimeRevision(
            org_id=self._settings.AGENT_ORG_ID,
            revision=next_revision,
            provider=command.provider,
            enabled_modes=[mode.value for mode in command.enabled_modes],
            model_aliases=aliases,
            capability_snapshot=catalog_json,
            capability_snapshot_hash=snapshot_hash,
            created_by_user_id=created_by_user_id,
        )
        self._db.add(row)
        self._db.flush()
        secret_path = self._secret_path(row.id)
        try:
            self._write_secret(secret_path, api_key)
            self._db.commit()
        except Exception:
            self._db.rollback()
            secret_path.unlink(missing_ok=True)
            raise
        self._db.refresh(row)
        return self._to_response(row)

    async def probe_revision(
        self,
        revision_id: UUID,
        *,
        probed_by_user_id: int,
    ) -> MediaRuntimeProbeResponse:
        row = self._revision_row(revision_id)
        api_key = self._read_secret(row.id)
        model_ids = sorted(set((row.model_aliases or {}).values()))
        try:
            result = await self._provider_control.probe(
                api_key=api_key,
                model_ids=model_ids,
            )
        except Exception:
            result = MediaProviderProbe(
                ready=False,
                reachable=False,
                issues=["media_provider_probe_failed"],
            )
        record = MediaRuntimeProbeRecord(
            org_id=self._settings.AGENT_ORG_ID,
            revision_id=row.id,
            ready=result.ready,
            reachable=result.reachable,
            issues=list(result.issues),
            capability_snapshot_hash=row.capability_snapshot_hash,
            probed_by_user_id=probed_by_user_id,
        )
        self._db.add(record)
        self._db.commit()
        self._db.refresh(record)
        return self._probe_response(record)

    def activate_revision(
        self,
        revision_id: UUID,
        *,
        activated_by_user_id: int,
    ) -> MediaRuntimeState:
        row = self._revision_row(revision_id)
        probe = self._latest_probe(row.id)
        if (
            probe is None
            or not probe.ready
            or probe.capability_snapshot_hash != row.capability_snapshot_hash
        ):
            raise ValueError("media runtime revision requires a healthy probe")
        activation = self._db.get(
            MediaRuntimeActivation,
            self._settings.AGENT_ORG_ID,
        )
        if activation is None:
            activation = MediaRuntimeActivation(org_id=self._settings.AGENT_ORG_ID)
            self._db.add(activation)
        activation.active_revision_id = row.id
        activation.activated_by_user_id = activated_by_user_id
        activation.activated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        self._db.commit()
        return self.get_state()

    def _active_row(self) -> Optional[MediaRuntimeRevision]:
        activation = self._db.get(
            MediaRuntimeActivation,
            self._settings.AGENT_ORG_ID,
        )
        if activation is None:
            return None
        return (
            self._db.query(MediaRuntimeRevision)
            .filter(
                MediaRuntimeRevision.id == activation.active_revision_id,
                MediaRuntimeRevision.org_id == self._settings.AGENT_ORG_ID,
            )
            .one_or_none()
        )

    def _revision_row(self, revision_id: UUID) -> MediaRuntimeRevision:
        row = (
            self._db.query(MediaRuntimeRevision)
            .filter(
                MediaRuntimeRevision.id == revision_id,
                MediaRuntimeRevision.org_id == self._settings.AGENT_ORG_ID,
            )
            .one_or_none()
        )
        if row is None:
            raise KeyError("media runtime revision not found")
        return row

    def _latest_probe(self, revision_id: UUID) -> Optional[MediaRuntimeProbeRecord]:
        return (
            self._db.query(MediaRuntimeProbeRecord)
            .filter(
                MediaRuntimeProbeRecord.revision_id == revision_id,
                MediaRuntimeProbeRecord.org_id == self._settings.AGENT_ORG_ID,
            )
            .order_by(MediaRuntimeProbeRecord.created_at.desc())
            .first()
        )

    def _to_response(
        self,
        row: MediaRuntimeRevision,
    ) -> MediaRuntimeRevisionResponse:
        probe = self._latest_probe(row.id)
        return MediaRuntimeRevisionResponse(
            id=row.id,
            org_id=row.org_id,
            revision=row.revision,
            provider=row.provider,
            enabled_modes=list(row.enabled_modes or []),
            model_aliases=dict(row.model_aliases or {}),
            capability_snapshot=MediaCapabilityCatalog.model_validate(
                row.capability_snapshot
            ),
            capability_snapshot_hash=row.capability_snapshot_hash,
            api_key_configured=self._secret_path(row.id).is_file(),
            latest_probe=self._probe_response(probe) if probe else None,
            created_at=self._as_utc(row.created_at),
        )

    def _probe_response(
        self,
        row: MediaRuntimeProbeRecord,
    ) -> MediaRuntimeProbeResponse:
        return MediaRuntimeProbeResponse(
            id=row.id,
            revision_id=row.revision_id,
            ready=row.ready,
            reachable=row.reachable,
            issues=list(row.issues or []),
            capability_snapshot_hash=row.capability_snapshot_hash,
            created_at=self._as_utc(row.created_at),
        )

    def _resolve_key_for_new_revision(
        self,
        supplied: Optional[SecretStr],
    ) -> str:
        if supplied is not None:
            value = supplied.get_secret_value().strip()
            if not value:
                raise ValueError("media provider API key cannot be empty")
            return value
        active = self._active_row()
        if active is None:
            raise ValueError("media provider API key is required")
        return self._read_secret(active.id)

    @staticmethod
    def _validate_aliases(
        enabled_modes: List[MediaWorkflowMode],
        aliases: Dict[MediaWorkflowMode, str],
        catalog: MediaCapabilityCatalog,
    ) -> Dict[str, str]:
        if set(enabled_modes) != set(aliases):
            raise ValueError("every enabled media mode requires exactly one model alias")
        models = {model.id: model for model in catalog.models}
        normalized: Dict[str, str] = {}
        for mode, raw_model_id in aliases.items():
            model_id = raw_model_id.strip()
            if model_id.lower().startswith("auto/"):
                raise ValueError("dynamic auto/* media routes are not approved")
            model = models.get(model_id)
            if model is None:
                raise ValueError("media model is absent from the capability catalog")
            if mode not in model.modes:
                raise ValueError("media model does not support the configured mode")
            normalized[mode.value] = model_id
        return normalized

    def _secret_path(self, revision_id: UUID) -> Path:
        return Path(self._settings.MEDIA_RUNTIME_SECRET_DIR) / f"{revision_id}.key"

    @staticmethod
    def _write_secret(path: Path, value: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        temporary = path.with_suffix(".key.tmp")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        finally:
            temporary.unlink(missing_ok=True)

    def _read_secret(self, revision_id: UUID) -> str:
        path = self._secret_path(revision_id)
        if not path.is_file():
            raise RuntimeError("media provider API key is not configured")
        value = path.read_text(encoding="utf-8").strip()
        if not value:
            raise RuntimeError("media provider API key is not configured")
        return value

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def get_media_runtime_service(
    db: Session = Depends(get_db),
) -> MediaRuntimeService:
    from app.integrations.fal_media import FalMediaProviderControl

    return MediaRuntimeService(db, provider_control=FalMediaProviderControl())
    def get_capabilities(self) -> MediaCapabilityCatalog:
        raise RuntimeError("media_provider_adapter_unavailable")
