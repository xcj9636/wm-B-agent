"""Immutable video persona revisions and evidence-backed approval."""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.models.database import MediaAsset, VideoPersona, VideoPersonaVersion
from app.services.agent_runtime.contracts import ExecutionPrincipal
from app.services.idempotency import IdempotencyConflict, canonical_hash
from app.services.media.contracts import PersonaStatus, VideoPersonaSpec


class VideoPersonaForbidden(RuntimeError):
    pass


class VideoPersonaConflict(RuntimeError):
    pass


class VideoPersonaNotFound(LookupError):
    pass


class PersonaRevisionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    idempotency_key: str = Field(min_length=8, max_length=255)
    spec: VideoPersonaSpec


class VideoPersonaService:
    """Creates immutable revisions and records separate approval facts."""

    def __init__(self, db: Session, *, planning_enabled: bool) -> None:
        self._db = db
        self._planning_enabled = planning_enabled

    def create(
        self,
        command: PersonaRevisionCommand,
        principal: ExecutionPrincipal,
    ) -> tuple[VideoPersona, VideoPersonaVersion]:
        self._require_enabled()
        input_hash = self._input_hash("create", None, command)
        replay = self._replay(command.idempotency_key, input_hash, principal)
        if replay is not None:
            return self._persona(replay.persona_id), replay

        persona = VideoPersona(
            org_id=principal.org_id,
            owner_user_id=principal.user_id,
        )
        self._db.add(persona)
        self._db.flush()
        version = self._new_version(
            persona,
            revision=1,
            command=command,
            input_hash=input_hash,
            principal=principal,
        )
        self._db.commit()
        self._db.refresh(persona)
        self._db.refresh(version)
        return persona, version

    def revise(
        self,
        persona_id: UUID,
        command: PersonaRevisionCommand,
        principal: ExecutionPrincipal,
    ) -> tuple[VideoPersona, VideoPersonaVersion]:
        self._require_enabled()
        input_hash = self._input_hash("revise", persona_id, command)
        replay = self._replay(command.idempotency_key, input_hash, principal)
        if replay is not None:
            return self._persona(replay.persona_id), replay

        persona = self._persona(persona_id, lock=True)
        self._authorize_owner(persona, principal)
        if persona.retired_at is not None:
            raise VideoPersonaConflict("Retired persona cannot be revised")
        latest = (
            self._db.query(VideoPersonaVersion.revision)
            .filter(VideoPersonaVersion.persona_id == persona.id)
            .order_by(VideoPersonaVersion.revision.desc())
            .first()
        )
        revision = int(latest[0]) + 1 if latest is not None else 1
        version = self._new_version(
            persona,
            revision=revision,
            command=command,
            input_hash=input_hash,
            principal=principal,
        )
        self._db.commit()
        self._db.refresh(version)
        return persona, version

    def approve(
        self,
        version_id: UUID,
        principal: ExecutionPrincipal,
        *,
        now: Optional[datetime] = None,
    ) -> VideoPersonaVersion:
        self._require_enabled()
        roles = {role.strip().lower() for role in principal.roles}
        if not roles.intersection({"media_reviewer", "admin"}):
            raise VideoPersonaForbidden("Persona approval requires reviewer role")
        version = self._version(version_id)
        if version.org_id != principal.org_id:
            raise VideoPersonaForbidden("Persona is outside the current organization")
        if version.status == PersonaStatus.APPROVED.value:
            return version
        if version.status != PersonaStatus.DRAFT.value:
            raise VideoPersonaConflict("Only draft persona revisions can be approved")
        spec = VideoPersonaSpec.model_validate(version.spec_json)
        self._validate_reference_assets(spec, principal)
        version.status = PersonaStatus.APPROVED.value
        version.approved_by_user_id = principal.user_id
        version.approved_at = _naive_utc(now)
        self._db.commit()
        self._db.refresh(version)
        return version

    def list_latest(
        self,
        principal: ExecutionPrincipal,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[tuple[VideoPersona, VideoPersonaVersion]], int]:
        self._require_enabled()
        query = self._db.query(VideoPersona).filter(
            VideoPersona.org_id == principal.org_id,
            VideoPersona.retired_at.is_(None),
        )
        if "admin" not in {role.strip().lower() for role in principal.roles}:
            query = query.filter(VideoPersona.owner_user_id == principal.user_id)
        total = query.count()
        personas = (
            query.order_by(VideoPersona.created_at.desc(), VideoPersona.id)
            .offset(offset)
            .limit(limit)
            .all()
        )
        items: list[tuple[VideoPersona, VideoPersonaVersion]] = []
        for persona in personas:
            version = (
                self._db.query(VideoPersonaVersion)
                .filter(VideoPersonaVersion.persona_id == persona.id)
                .order_by(VideoPersonaVersion.revision.desc())
                .first()
            )
            if version is not None:
                items.append((persona, version))
        return items, total

    def list_versions(
        self,
        persona_id: UUID,
        principal: ExecutionPrincipal,
        *,
        limit: int,
        offset: int,
    ) -> tuple[VideoPersona, list[VideoPersonaVersion], int]:
        self._require_enabled()
        persona = self._persona(persona_id)
        self._authorize_owner(persona, principal)
        query = self._db.query(VideoPersonaVersion).filter(
            VideoPersonaVersion.persona_id == persona.id,
            VideoPersonaVersion.org_id == principal.org_id,
        )
        total = query.count()
        versions = (
            query.order_by(VideoPersonaVersion.revision.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return persona, versions, total

    def _new_version(
        self,
        persona: VideoPersona,
        *,
        revision: int,
        command: PersonaRevisionCommand,
        input_hash: str,
        principal: ExecutionPrincipal,
    ) -> VideoPersonaVersion:
        payload = command.spec.model_dump(mode="json")
        version = VideoPersonaVersion(
            persona_id=persona.id,
            org_id=principal.org_id,
            revision=revision,
            idempotency_key=command.idempotency_key,
            input_hash=input_hash,
            spec_json=payload,
            spec_hash=canonical_hash(payload),
            status=PersonaStatus.DRAFT.value,
            created_by_user_id=principal.user_id,
        )
        self._db.add(version)
        self._db.flush()
        return version

    def _replay(
        self,
        idempotency_key: str,
        input_hash: str,
        principal: ExecutionPrincipal,
    ) -> VideoPersonaVersion | None:
        existing = (
            self._db.query(VideoPersonaVersion)
            .filter(
                VideoPersonaVersion.org_id == principal.org_id,
                VideoPersonaVersion.created_by_user_id == principal.user_id,
                VideoPersonaVersion.idempotency_key == idempotency_key,
            )
            .one_or_none()
        )
        if existing is not None and existing.input_hash != input_hash:
            raise IdempotencyConflict(
                "Persona idempotency key was reused with different input"
            )
        return existing

    def _persona(self, persona_id: UUID, *, lock: bool = False) -> VideoPersona:
        query = self._db.query(VideoPersona).filter(VideoPersona.id == persona_id)
        if lock:
            query = query.with_for_update()
        persona = query.one_or_none()
        if persona is None:
            raise VideoPersonaNotFound("Video persona was not found")
        return persona

    def _version(self, version_id: UUID) -> VideoPersonaVersion:
        version = self._db.get(VideoPersonaVersion, version_id)
        if version is None:
            raise VideoPersonaNotFound("Video persona revision was not found")
        return version

    @staticmethod
    def _authorize_owner(
        persona: VideoPersona,
        principal: ExecutionPrincipal,
    ) -> None:
        roles = {role.strip().lower() for role in principal.roles}
        if persona.org_id != principal.org_id:
            raise VideoPersonaForbidden("Persona is outside the current organization")
        if persona.owner_user_id != principal.user_id and "admin" not in roles:
            raise VideoPersonaForbidden("Persona revision requires ownership")

    def _validate_reference_assets(
        self,
        spec: VideoPersonaSpec,
        principal: ExecutionPrincipal,
    ) -> None:
        for asset_id in spec.reference_asset_ids:
            asset = self._db.get(MediaAsset, asset_id)
            consent_valid = asset is not None and (
                not asset.consent_required or asset.consent_status == "valid"
            )
            if (
                asset is None
                or asset.deleted_at is not None
                or asset.org_id != principal.org_id
                or asset.quarantined
                or asset.scan_status != "passed"
                or asset.rights_status != "verified"
                or not consent_valid
            ):
                raise VideoPersonaConflict(
                    "Persona reference asset is missing or unapproved"
                )

    @staticmethod
    def _input_hash(
        action: str,
        persona_id: UUID | None,
        command: PersonaRevisionCommand,
    ) -> str:
        return canonical_hash(
            {
                "action": action,
                "persona_id": str(persona_id) if persona_id else None,
                "spec": command.spec.model_dump(mode="json"),
            }
        )

    def _require_enabled(self) -> None:
        if not self._planning_enabled:
            raise VideoPersonaForbidden("Media planning is disabled")


def _naive_utc(value: Optional[datetime]) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("Persona timestamps must be timezone-aware")
    return current.astimezone(timezone.utc).replace(tzinfo=None)
