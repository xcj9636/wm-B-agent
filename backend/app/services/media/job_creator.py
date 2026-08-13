"""Server-owned creation boundary for durable media generation jobs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Mapping, Protocol
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.models.database import MediaRuntimeActivation, MediaRuntimeRevision
from app.services.agent_runtime.contracts import ExecutionPrincipal
from app.services.idempotency import canonical_hash
from app.services.media.contracts import GenerationIntent, GenerationMode
from app.services.media.intent_vault import MediaIntentVaultUnavailable
from app.services.media.jobs import (
    MediaGenerationJobCommand,
    MediaGenerationJobService,
)
from app.services.media.prompts import VideoPromptCompiler
from app.services.media.runtime import MediaCapabilityCatalog, MediaWorkflowMode


_ATTEMPT_NAMESPACE = UUID("ba6e0000-0000-0000-0000-000000000005")


class MediaGenerationJobUnavailable(RuntimeError):
    """Trusted creation dependencies are missing or no longer consistent."""


class MediaIntentStore(Protocol):
    def store(self, intent: GenerationIntent) -> str: ...


class MediaGenerationJobCreateRequest(BaseModel):
    """Browser input; every provider, identity and budget field is forbidden."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    idempotency_key: str = Field(min_length=8, max_length=255)
    project_id: UUID
    storyboard_version_id: UUID
    shot_id: UUID


class MediaGenerationJobCreator:
    """Compile, pin, encrypt and reserve without trusting browser-owned routing."""

    def __init__(
        self,
        db: Session,
        *,
        compiler: VideoPromptCompiler,
        vault: MediaIntentStore,
        reservation_ceilings: Mapping[GenerationMode, int],
        deadline_seconds: int,
    ) -> None:
        if not 60 <= deadline_seconds <= 86_400:
            raise ValueError("media job deadline must be between 60 and 86400 seconds")
        self._db = db
        self._compiler = compiler
        self._vault = vault
        self._ceilings = dict(reservation_ceilings)
        self._deadline_seconds = deadline_seconds

    def create(
        self,
        request: MediaGenerationJobCreateRequest,
        principal: ExecutionPrincipal,
        *,
        now: datetime,
    ):
        created_at = self._aware_utc(now)
        compiled = self._compiler.compile(
            request.project_id,
            request.storyboard_version_id,
            request.shot_id,
            principal,
        )
        intent = compiled.intent.model_copy(
            update={
                "attempt_id": self._stable_attempt_id(request, principal),
            }
        )
        self._validate_compiled_envelope(request, principal, intent)
        runtime, model_id = self._active_runtime(intent)
        ceiling = self._reservation_ceiling(intent.mode)
        estimate_hash = canonical_hash(
            {
                "basis": "configured_reservation_ceiling",
                "runtime_revision_id": str(runtime.id),
                "capability_snapshot_hash": runtime.capability_snapshot_hash,
                "model_id": model_id,
                "mode": intent.mode.value,
                "reservation_ceiling_microusd": ceiling,
            }
        )
        try:
            payload_ref = self._vault.store(intent)
        except MediaIntentVaultUnavailable as exc:
            raise MediaGenerationJobUnavailable(
                "Media generation intent storage is unavailable"
            ) from exc
        command = MediaGenerationJobCommand(
            idempotency_key=request.idempotency_key,
            org_id=principal.org_id,
            owner_user_id=principal.user_id,
            project_id=request.project_id,
            storyboard_version_id=request.storyboard_version_id,
            shot_id=request.shot_id,
            runtime_revision_id=runtime.id,
            mode=MediaWorkflowMode(intent.mode.value),
            model_id=model_id,
            intent_hash=intent.input_hash(),
            payload_ref=payload_ref,
            sensitivity=intent.sensitivity,
            estimated_cost_microusd=ceiling,
            estimate_hash=estimate_hash,
            deadline_at=created_at + timedelta(seconds=self._deadline_seconds),
        )
        return MediaGenerationJobService(self._db).create(command, now=created_at)

    def _active_runtime(
        self,
        intent: GenerationIntent,
    ) -> tuple[MediaRuntimeRevision, str]:
        activation = self._db.get(MediaRuntimeActivation, intent.org_id)
        runtime = (
            self._db.get(MediaRuntimeRevision, activation.active_revision_id)
            if activation is not None
            else None
        )
        if runtime is None or runtime.org_id != intent.org_id:
            raise MediaGenerationJobUnavailable(
                "Media generation runtime is unavailable"
            )
        snapshot = dict(runtime.capability_snapshot or {})
        if canonical_hash(snapshot) != runtime.capability_snapshot_hash:
            raise MediaGenerationJobUnavailable(
                "Media generation runtime is unavailable"
            )
        try:
            catalog = MediaCapabilityCatalog.model_validate(snapshot)
        except Exception as exc:
            raise MediaGenerationJobUnavailable(
                "Media generation runtime is unavailable"
            ) from exc
        model_id = dict(runtime.model_aliases or {}).get(intent.mode.value)
        models = {model.id: model for model in catalog.models}
        model = models.get(model_id)
        try:
            mode = MediaWorkflowMode(intent.mode.value)
        except ValueError as exc:
            raise MediaGenerationJobUnavailable(
                "Media generation mode is unavailable"
            ) from exc
        if (
            intent.mode.value not in (runtime.enabled_modes or [])
            or model is None
            or mode not in model.modes
        ):
            raise MediaGenerationJobUnavailable(
                "Media generation runtime is unavailable"
            )
        return runtime, model_id

    def _reservation_ceiling(self, mode: GenerationMode) -> int:
        value = self._ceilings.get(mode)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value <= 0
        ):
            raise MediaGenerationJobUnavailable(
                "Media reservation ceiling is unavailable"
            )
        return value

    @staticmethod
    def _validate_compiled_envelope(
        request: MediaGenerationJobCreateRequest,
        principal: ExecutionPrincipal,
        intent: GenerationIntent,
    ) -> None:
        if (
            intent.org_id != principal.org_id
            or intent.actor_user_id != principal.user_id
            or intent.project_id != request.project_id
            or intent.shot_id != request.shot_id
        ):
            raise MediaGenerationJobUnavailable(
                "Media generation request is unavailable"
            )

    @staticmethod
    def _stable_attempt_id(
        request: MediaGenerationJobCreateRequest,
        principal: ExecutionPrincipal,
    ) -> UUID:
        name = canonical_hash(
            {
                "org_id": str(principal.org_id),
                "user_id": principal.user_id,
                "idempotency_key": request.idempotency_key,
            }
        )
        return uuid5(_ATTEMPT_NAMESPACE, name)

    @staticmethod
    def _aware_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
