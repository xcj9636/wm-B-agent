"""Reauthorize a media submission from live durable identity and evidence."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.database import (
    MediaAsset,
    MediaConsentRecord,
    MediaGenerationJob,
    MediaRightsRecord,
    MediaScanReport,
    User,
    VideoPersonaVersion,
    VideoProject,
    VideoStoryboardVersion,
)
from app.services.agent_runtime.contracts import ExecutionPrincipal, Sensitivity
from app.services.idempotency import canonical_hash
from app.services.media.contracts import (
    AssetConsentStatus,
    AssetRightsStatus,
    AssetScanStatus,
    GenerationIntent,
    MediaAssetPolicySnapshot,
    MediaPolicyDecision,
)
from app.services.media.policy import MediaSubmissionPolicy


class MediaSubmissionAuthorizationDenied(RuntimeError):
    """Live durable state no longer authorizes the external effect."""


@dataclass(frozen=True)
class AuthorizedMediaSubmission:
    principal: ExecutionPrincipal
    decision: MediaPolicyDecision


class MediaSubmissionAuthorizer:
    """Mint a short-lived decision only after re-reading every trusted input."""

    def __init__(
        self,
        db: Session,
        *,
        policy: MediaSubmissionPolicy,
        deployment_org_id: UUID,
    ) -> None:
        self._db = db
        self._policy = policy
        self._deployment_org_id = deployment_org_id

    def authorize(
        self,
        job: MediaGenerationJob,
        intent: GenerationIntent,
        *,
        now: datetime,
    ) -> AuthorizedMediaSubmission:
        checked_at = self._aware_utc(now)
        self._validate_job_envelope(job, intent)
        principal = self._principal(job, intent)
        self._validate_approved_snapshots(job, intent)
        assets = [
            self._asset_snapshot(asset_id, intent.org_id, checked_at)
            for asset_id in intent.reference_asset_ids
        ]
        available_assets = [asset for asset in assets if asset is not None]
        decision = self._policy.authorize(
            principal,
            intent,
            assets=available_assets,
            now=checked_at,
        )
        return AuthorizedMediaSubmission(principal=principal, decision=decision)

    def _validate_job_envelope(
        self,
        job: MediaGenerationJob,
        intent: GenerationIntent,
    ) -> None:
        expected = (
            job.org_id == self._deployment_org_id == intent.org_id
            and job.owner_user_id == intent.actor_user_id
            and job.project_id == intent.project_id
            and job.shot_id == intent.shot_id
            and job.mode == intent.mode.value
            and job.sensitivity == intent.sensitivity.value
            and job.intent_hash == intent.input_hash()
        )
        if not expected:
            raise MediaSubmissionAuthorizationDenied(
                "Media submission envelope is no longer authorized"
            )

    def _principal(
        self,
        job: MediaGenerationJob,
        intent: GenerationIntent,
    ) -> ExecutionPrincipal:
        user = self._db.get(User, job.owner_user_id)
        if user is None or not user.is_active or user.id != intent.actor_user_id:
            raise MediaSubmissionAuthorizationDenied(
                "Media submission identity is no longer authorized"
            )
        role = str(user.role or "user").strip().lower() or "user"
        roles = {role}
        if user.is_superuser:
            roles.add("admin")
        entitlements = {
            "org_id": str(job.org_id),
            "user_id": user.id,
            "roles": sorted(roles),
            "is_active": bool(user.is_active),
            "is_superuser": bool(user.is_superuser),
        }
        return ExecutionPrincipal(
            org_id=job.org_id,
            user_id=user.id,
            roles=roles,
            entitlements_hash=canonical_hash(entitlements),
            authn_context="worker:celery",
        )

    def _validate_approved_snapshots(
        self,
        job: MediaGenerationJob,
        intent: GenerationIntent,
    ) -> None:
        project = self._db.get(VideoProject, job.project_id)
        persona = self._db.get(VideoPersonaVersion, intent.persona_version_id)
        storyboard = self._db.get(
            VideoStoryboardVersion,
            job.storyboard_version_id,
        )
        valid_project = (
            project is not None
            and project.org_id == job.org_id
            and project.owner_user_id == job.owner_user_id
            and project.persona_version_id == intent.persona_version_id
            and project.persona_spec_hash
            == canonical_hash(project.persona_snapshot_json)
        )
        valid_persona = (
            persona is not None
            and persona.org_id == job.org_id
            and persona.status == "approved"
            and persona.approved_by_user_id is not None
            and persona.approved_at is not None
            and persona.spec_hash == canonical_hash(persona.spec_json)
            and project is not None
            and project.persona_spec_hash == persona.spec_hash
        )
        valid_storyboard = (
            storyboard is not None
            and storyboard.org_id == job.org_id
            and storyboard.project_id == job.project_id
            and storyboard.status == "approved"
            and storyboard.approved_by_user_id is not None
            and storyboard.approved_at is not None
            and storyboard.storyboard_hash
            == canonical_hash(storyboard.storyboard_json)
        )
        if not (valid_project and valid_persona and valid_storyboard):
            raise MediaSubmissionAuthorizationDenied(
                "Media approval evidence is no longer valid"
            )

    def _asset_snapshot(
        self,
        asset_id: UUID,
        org_id: UUID,
        now: datetime,
    ) -> MediaAssetPolicySnapshot | None:
        asset = self._db.get(MediaAsset, asset_id)
        if asset is None:
            return None
        scan_status = self._scan_status(asset, org_id)
        rights_status = self._rights_status(asset, org_id, now)
        consent_status = self._consent_status(asset, org_id, now)
        try:
            sensitivity = Sensitivity(asset.sensitivity)
        except ValueError as exc:
            raise MediaSubmissionAuthorizationDenied(
                "Media asset sensitivity is invalid"
            ) from exc
        if asset.deleted_at is not None or asset.quarantined:
            scan_status = AssetScanStatus.PENDING
        return MediaAssetPolicySnapshot(
            asset_id=asset.id,
            org_id=asset.org_id,
            scan_status=scan_status,
            rights_status=rights_status,
            consent_required=bool(asset.consent_required),
            consent_status=consent_status,
            sensitivity=sensitivity,
        )

    def _scan_status(self, asset: MediaAsset, org_id: UUID) -> AssetScanStatus:
        report = (
            self._db.get(MediaScanReport, asset.scan_report_id)
            if asset.scan_report_id is not None
            else None
        )
        if (
            asset.org_id == org_id
            and asset.scan_status == "passed"
            and report is not None
            and report.org_id == org_id
            and report.asset_id == asset.id
            and report.status == "passed"
            and report.asset_sha256 == asset.sha256
        ):
            return AssetScanStatus.PASSED
        return AssetScanStatus.PENDING

    def _rights_status(
        self,
        asset: MediaAsset,
        org_id: UUID,
        now: datetime,
    ) -> AssetRightsStatus:
        record = (
            self._db.get(MediaRightsRecord, asset.rights_record_id)
            if asset.rights_record_id is not None
            else None
        )
        if record is None:
            return AssetRightsStatus.UNKNOWN
        checked_at = now.replace(tzinfo=None)
        active = (
            asset.org_id == org_id
            and asset.rights_status == "verified"
            and record.org_id == org_id
            and record.asset_id == asset.id
            and record.status == "verified"
            and record.revoked_at is None
            and record.valid_from <= checked_at
            and (record.valid_until is None or record.valid_until > checked_at)
        )
        if active:
            return AssetRightsStatus.VERIFIED
        if record.valid_until is not None and record.valid_until <= checked_at:
            return AssetRightsStatus.EXPIRED
        if record.revoked_at is not None or record.status == "revoked":
            return AssetRightsStatus.REVOKED
        return AssetRightsStatus.UNKNOWN

    def _consent_status(
        self,
        asset: MediaAsset,
        org_id: UUID,
        now: datetime,
    ) -> AssetConsentStatus:
        if not asset.consent_required:
            return AssetConsentStatus.NOT_REQUIRED
        record = (
            self._db.get(MediaConsentRecord, asset.consent_record_id)
            if asset.consent_record_id is not None
            else None
        )
        if record is None:
            return AssetConsentStatus.UNKNOWN
        checked_at = now.replace(tzinfo=None)
        evidence = self._db.get(MediaAsset, record.evidence_asset_id)
        evidence_live = (
            evidence is not None
            and evidence.org_id == org_id
            and evidence.deleted_at is None
            and not evidence.quarantined
            and evidence.scan_status == "passed"
        )
        active = (
            asset.org_id == org_id
            and asset.consent_status == "valid"
            and record.org_id == org_id
            and record.asset_id == asset.id
            and record.status == "valid"
            and record.revoked_at is None
            and evidence_live
            and record.valid_from <= checked_at
            and (record.valid_until is None or record.valid_until > checked_at)
        )
        if active:
            return AssetConsentStatus.VALID
        if record.valid_until is not None and record.valid_until <= checked_at:
            return AssetConsentStatus.EXPIRED
        if record.revoked_at is not None or record.status == "revoked":
            return AssetConsentStatus.REVOKED
        return AssetConsentStatus.UNKNOWN

    @staticmethod
    def _aware_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
