"""Evidence-bound video projects and immutable storyboard revisions."""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.models.database import (
    KnowledgeDocument,
    MediaAsset,
    VideoPersonaVersion,
    VideoProject,
    VideoProjectEvidence,
    VideoStoryboardVersion,
)
from app.services.agent_runtime.contracts import (
    ExecutionPrincipal,
    Sensitivity,
    derive_sensitivity,
)
from app.services.idempotency import IdempotencyConflict, canonical_hash
from app.services.knowledge import SQLKnowledgeACL
from app.services.media.contracts import (
    PersonaStatus,
    Storyboard,
    VideoPersonaSpec,
    VideoProjectBrief,
)


class VideoPlanningForbidden(RuntimeError):
    pass


class VideoPlanningConflict(RuntimeError):
    pass


class VideoPlanningNotFound(LookupError):
    pass


class VideoProjectCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    idempotency_key: str = Field(min_length=8, max_length=255)
    persona_version_id: UUID
    brief: VideoProjectBrief
    evidence_record_ids: list[UUID] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def unique_evidence(self) -> "VideoProjectCommand":
        if len(set(self.evidence_record_ids)) != len(self.evidence_record_ids):
            raise ValueError("Project evidence records must be unique")
        return self


class StoryboardRevisionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    idempotency_key: str = Field(min_length=8, max_length=255)
    storyboard: Storyboard


class VideoPlanningService:
    """Pins approved inputs so later revisions cannot drift running projects."""

    def __init__(self, db: Session, *, planning_enabled: bool) -> None:
        self._db = db
        self._planning_enabled = planning_enabled
        self._knowledge_acl = SQLKnowledgeACL(db)

    def create_project(
        self,
        command: VideoProjectCommand,
        principal: ExecutionPrincipal,
    ) -> tuple[VideoProject, list[VideoProjectEvidence]]:
        self._require_enabled()
        input_hash = canonical_hash(command.model_dump(mode="json"))
        existing = (
            self._db.query(VideoProject)
            .filter(
                VideoProject.org_id == principal.org_id,
                VideoProject.owner_user_id == principal.user_id,
                VideoProject.idempotency_key == command.idempotency_key,
            )
            .one_or_none()
        )
        if existing is not None:
            if existing.input_hash != input_hash:
                raise IdempotencyConflict(
                    "Project idempotency key was reused with different input"
                )
            return existing, self._project_evidence(existing.id)

        persona = self._db.get(VideoPersonaVersion, command.persona_version_id)
        if persona is None:
            raise VideoPlanningNotFound("Persona revision was not found")
        if persona.org_id != principal.org_id:
            raise VideoPlanningForbidden("Persona is outside the organization")
        if persona.status != PersonaStatus.APPROVED.value:
            raise VideoPlanningConflict("Project persona revision is not approved")
        persona_spec = VideoPersonaSpec.model_validate(persona.spec_json)
        evidence_documents = [
            self._authorize_document(record_id, principal)
            for record_id in command.evidence_record_ids
        ]
        sensitivities = [Sensitivity.INTERNAL]
        sensitivities.extend(
            Sensitivity(document.sensitivity) for document in evidence_documents
        )
        for asset_id in persona_spec.reference_asset_ids:
            asset = self._db.get(MediaAsset, asset_id)
            if asset is None or asset.org_id != principal.org_id:
                raise VideoPlanningConflict("Persona reference asset is unavailable")
            sensitivities.append(Sensitivity(asset.sensitivity))
        brief_payload = command.brief.model_dump(mode="json")
        project = VideoProject(
            org_id=principal.org_id,
            owner_user_id=principal.user_id,
            idempotency_key=command.idempotency_key,
            input_hash=input_hash,
            brief_json=brief_payload,
            brief_hash=canonical_hash(brief_payload),
            persona_version_id=persona.id,
            persona_snapshot_json=persona.spec_json,
            persona_spec_hash=persona.spec_hash,
            sensitivity=derive_sensitivity(*sensitivities).value,
            status="draft",
        )
        self._db.add(project)
        self._db.flush()
        rows = [
            VideoProjectEvidence(
                project_id=project.id,
                org_id=principal.org_id,
                knowledge_record_id=document.record_id,
                document_id=document.document_id,
                document_version=document.version,
                source_ref=document.source_ref,
                title=document.title,
                authority=document.authority,
                sensitivity=document.sensitivity,
                acl_policy_version=document.acl_policy_version,
                content_hash=document.content_hash,
                added_by_user_id=principal.user_id,
            )
            for document in evidence_documents
        ]
        self._db.add_all(rows)
        self._db.commit()
        self._db.refresh(project)
        for row in rows:
            self._db.refresh(row)
        return project, rows

    def revise_storyboard(
        self,
        project_id: UUID,
        command: StoryboardRevisionCommand,
        principal: ExecutionPrincipal,
    ) -> tuple[VideoProject, VideoStoryboardVersion]:
        self._require_enabled()
        input_hash = canonical_hash(
            {
                "project_id": str(project_id),
                "storyboard": command.storyboard.model_dump(mode="json"),
            }
        )
        existing = (
            self._db.query(VideoStoryboardVersion)
            .filter(
                VideoStoryboardVersion.org_id == principal.org_id,
                VideoStoryboardVersion.created_by_user_id == principal.user_id,
                VideoStoryboardVersion.idempotency_key == command.idempotency_key,
            )
            .one_or_none()
        )
        if existing is not None:
            if existing.input_hash != input_hash:
                raise IdempotencyConflict(
                    "Storyboard idempotency key was reused with different input"
                )
            return self._project(existing.project_id), existing
        project = self._project(project_id, lock=True)
        self._authorize_owner(project, principal)
        latest = (
            self._db.query(VideoStoryboardVersion.revision)
            .filter(VideoStoryboardVersion.project_id == project.id)
            .order_by(VideoStoryboardVersion.revision.desc())
            .first()
        )
        payload = command.storyboard.model_dump(mode="json")
        version = VideoStoryboardVersion(
            project_id=project.id,
            org_id=principal.org_id,
            revision=(int(latest[0]) + 1 if latest else 1),
            idempotency_key=command.idempotency_key,
            input_hash=input_hash,
            storyboard_json=payload,
            storyboard_hash=canonical_hash(payload),
            status="draft",
            created_by_user_id=principal.user_id,
        )
        self._db.add(version)
        self._db.commit()
        self._db.refresh(version)
        return project, version

    def approve_storyboard(
        self,
        version_id: UUID,
        principal: ExecutionPrincipal,
        *,
        now: Optional[datetime] = None,
    ) -> VideoStoryboardVersion:
        self._require_enabled()
        roles = {role.strip().lower() for role in principal.roles}
        if not roles.intersection({"media_reviewer", "admin"}):
            raise VideoPlanningForbidden("Storyboard approval requires reviewer role")
        version = self._db.get(VideoStoryboardVersion, version_id)
        if version is None:
            raise VideoPlanningNotFound("Storyboard revision was not found")
        if version.org_id != principal.org_id:
            raise VideoPlanningForbidden("Storyboard is outside the organization")
        if version.status == "approved":
            return version
        if version.status != "draft":
            raise VideoPlanningConflict("Only draft storyboards can be approved")
        storyboard = Storyboard.model_validate(version.storyboard_json)
        allowed_evidence_ids = {
            row.id for row in self._project_evidence(version.project_id)
        }
        claimed_evidence_ids = {
            evidence_id
            for shot in storyboard.shots
            for evidence_id in shot.claim_evidence_ids
        }
        if not claimed_evidence_ids.issubset(allowed_evidence_ids):
            raise VideoPlanningConflict(
                "Storyboard claim evidence is outside the project snapshot"
            )
        self._validate_storyboard_assets(storyboard, principal)
        version.status = "approved"
        version.approved_by_user_id = principal.user_id
        version.approved_at = _naive_utc(now)
        self._db.commit()
        self._db.refresh(version)
        return version

    def list_projects(
        self,
        principal: ExecutionPrincipal,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[tuple[VideoProject, list[VideoProjectEvidence]]], int]:
        self._require_enabled()
        query = self._db.query(VideoProject).filter(
            VideoProject.org_id == principal.org_id
        )
        if "admin" not in {role.strip().lower() for role in principal.roles}:
            query = query.filter(VideoProject.owner_user_id == principal.user_id)
        total = query.count()
        projects = (
            query.order_by(VideoProject.created_at.desc(), VideoProject.id)
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [
            (project, self._project_evidence(project.id))
            for project in projects
        ], total

    def project_detail(
        self,
        project_id: UUID,
        principal: ExecutionPrincipal,
    ) -> tuple[
        VideoProject,
        list[VideoProjectEvidence],
        list[VideoStoryboardVersion],
    ]:
        self._require_enabled()
        project = self._project(project_id)
        self._authorize_owner(project, principal)
        storyboards = (
            self._db.query(VideoStoryboardVersion)
            .filter(
                VideoStoryboardVersion.project_id == project.id,
                VideoStoryboardVersion.org_id == principal.org_id,
            )
            .order_by(VideoStoryboardVersion.revision.desc())
            .all()
        )
        return project, self._project_evidence(project.id), storyboards

    def _authorize_document(
        self,
        record_id: UUID,
        principal: ExecutionPrincipal,
    ) -> KnowledgeDocument:
        document = self._db.get(KnowledgeDocument, record_id)
        if document is None:
            raise VideoPlanningNotFound("Knowledge evidence was not found")
        if document.org_id != principal.org_id:
            raise VideoPlanningForbidden("Knowledge evidence is outside the organization")
        allowed = self._knowledge_acl.authorize(
            principal=principal,
            document_id=document.document_id,
            document_version=document.version,
            acl_policy_version=document.acl_policy_version,
        )
        if not allowed:
            raise VideoPlanningForbidden("Knowledge evidence is not authorized")
        return document

    def _project(
        self,
        project_id: UUID,
        *,
        lock: bool = False,
    ) -> VideoProject:
        query = self._db.query(VideoProject).filter(VideoProject.id == project_id)
        if lock:
            query = query.with_for_update()
        project = query.one_or_none()
        if project is None:
            raise VideoPlanningNotFound("Video project was not found")
        return project

    def _project_evidence(self, project_id: UUID) -> list[VideoProjectEvidence]:
        return (
            self._db.query(VideoProjectEvidence)
            .filter(VideoProjectEvidence.project_id == project_id)
            .order_by(VideoProjectEvidence.created_at, VideoProjectEvidence.id)
            .all()
        )

    @staticmethod
    def _authorize_owner(
        project: VideoProject,
        principal: ExecutionPrincipal,
    ) -> None:
        roles = {role.strip().lower() for role in principal.roles}
        if project.org_id != principal.org_id:
            raise VideoPlanningForbidden("Project is outside the organization")
        if project.owner_user_id != principal.user_id and "admin" not in roles:
            raise VideoPlanningForbidden("Project revision requires ownership")

    def _validate_storyboard_assets(
        self,
        storyboard: Storyboard,
        principal: ExecutionPrincipal,
    ) -> None:
        for asset_id in {
            asset_id
            for shot in storyboard.shots
            for asset_id in shot.reference_asset_ids
        }:
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
                raise VideoPlanningConflict(
                    "Storyboard reference asset is missing or unapproved"
                )

    def _require_enabled(self) -> None:
        if not self._planning_enabled:
            raise VideoPlanningForbidden("Media planning is disabled")


def _naive_utc(value: Optional[datetime]) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("Planning timestamps must be timezone-aware")
    return current.astimezone(timezone.utc).replace(tzinfo=None)
