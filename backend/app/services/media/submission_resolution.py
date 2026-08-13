"""Evidence-backed two-person resolution for ambiguous media submissions."""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.models.database import (
    MediaGenerationAttempt,
    MediaGenerationEvent,
    MediaGenerationJob,
    MediaSubmissionResolutionAction,
    MediaSubmissionResolutionApproval,
    MediaSubmissionResolutionRequest,
    MediaSubmissionResolutionStatus,
)
from app.services.media.jobs import MediaGenerationJobService


REFERENCE_PATTERN = (
    r"^(provider-audit|provider-support|billing-audit)/"
    r"[A-Za-z0-9][A-Za-z0-9._:/-]{1,110}$"
)
REQUEST_ID_PATTERN = r"^[A-Za-z0-9_-]{1,128}$"
REQUIRED_APPROVALS = 2


class MediaSubmissionResolutionConflict(RuntimeError):
    """Requested resolution conflicts with durable job state."""


class MediaSubmissionResolutionNotFound(RuntimeError):
    """Job is absent from the caller's organization boundary."""


class MediaSubmissionResolutionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: MediaSubmissionResolutionAction
    evidence_reference: str = Field(pattern=REFERENCE_PATTERN)
    provider_request_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=REQUEST_ID_PATTERN,
    )

    @model_validator(mode="after")
    def validate_provider_request(self) -> "MediaSubmissionResolutionCommand":
        if (
            self.action == MediaSubmissionResolutionAction.CONFIRMED_SUBMITTED
            and self.provider_request_id is None
        ):
            raise ValueError("confirmed_submitted requires provider_request_id")
        if (
            self.action
            == MediaSubmissionResolutionAction.CONFIRMED_NOT_SUBMITTED
            and self.provider_request_id is not None
        ):
            raise ValueError(
                "confirmed_not_submitted cannot include provider_request_id"
            )
        return self


class MediaSubmissionResolutionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    job_id: str
    action: MediaSubmissionResolutionAction
    status: MediaSubmissionResolutionStatus
    approvals: int
    required_approvals: int = REQUIRED_APPROVALS


class MediaSubmissionResolutionService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def approve(
        self,
        *,
        job_id: UUID,
        org_id: UUID,
        admin_user_id: int,
        command: MediaSubmissionResolutionCommand,
        now: Optional[datetime] = None,
    ) -> MediaSubmissionResolutionResponse:
        now = now or datetime.utcnow()
        job = (
            self._session.query(MediaGenerationJob)
            .filter(
                MediaGenerationJob.id == job_id,
                MediaGenerationJob.org_id == org_id,
            )
            .with_for_update()
            .one_or_none()
        )
        if job is None:
            raise MediaSubmissionResolutionNotFound("Media job not found")
        if job.status != "submission_unknown" or job.effect_state != "unknown":
            raise MediaSubmissionResolutionConflict(
                "Only submission-unknown jobs can be resolved"
            )
        unknown_version = job.updated_at or job.created_at
        resolution = (
            self._session.query(MediaSubmissionResolutionRequest)
            .filter(
                MediaSubmissionResolutionRequest.job_id == job.id,
                MediaSubmissionResolutionRequest.submission_unknown_version
                == unknown_version,
            )
            .with_for_update()
            .one_or_none()
        )
        if resolution is None:
            resolution = MediaSubmissionResolutionRequest(
                job_id=job.id,
                submission_unknown_version=unknown_version,
                action=command.action,
                evidence_reference=command.evidence_reference,
                provider_request_id=command.provider_request_id,
                status=MediaSubmissionResolutionStatus.PENDING,
                requested_by_user_id=admin_user_id,
                created_at=now,
                updated_at=now,
            )
            self._session.add(resolution)
            self._session.flush()
        elif (
            resolution.action != command.action
            or resolution.evidence_reference != command.evidence_reference
            or resolution.provider_request_id != command.provider_request_id
        ):
            raise MediaSubmissionResolutionConflict(
                "A different resolution is already pending for this job"
            )
        prior = (
            self._session.query(MediaSubmissionResolutionApproval)
            .filter(
                MediaSubmissionResolutionApproval.request_id == resolution.id,
                MediaSubmissionResolutionApproval.approved_by_user_id
                == admin_user_id,
            )
            .one_or_none()
        )
        if prior is not None:
            raise MediaSubmissionResolutionConflict(
                "Second approval must come from a different administrator"
            )
        self._session.add(
            MediaSubmissionResolutionApproval(
                request_id=resolution.id,
                approved_by_user_id=admin_user_id,
                created_at=now,
            )
        )
        self._session.flush()
        approvals = (
            self._session.query(MediaSubmissionResolutionApproval)
            .filter(
                MediaSubmissionResolutionApproval.request_id == resolution.id
            )
            .count()
        )
        if approvals >= REQUIRED_APPROVALS:
            self._execute(job=job, resolution=resolution, now=now)
        return MediaSubmissionResolutionResponse(
            request_id=str(resolution.id),
            job_id=str(job.id),
            action=resolution.action,
            status=resolution.status,
            approvals=approvals,
        )

    def _execute(
        self,
        *,
        job: MediaGenerationJob,
        resolution: MediaSubmissionResolutionRequest,
        now: datetime,
    ) -> None:
        attempt = self._current_attempt(job)
        if (
            resolution.action
            == MediaSubmissionResolutionAction.CONFIRMED_SUBMITTED
        ):
            request_id = resolution.provider_request_id
            if request_id is None:
                raise MediaSubmissionResolutionConflict(
                    "Confirmed submission has no provider request"
                )
            duplicate = (
                self._session.query(MediaGenerationAttempt)
                .filter(
                    MediaGenerationAttempt.provider == job.provider,
                    MediaGenerationAttempt.provider_request_id == request_id,
                    MediaGenerationAttempt.id != attempt.id,
                )
                .one_or_none()
            )
            if duplicate is not None:
                raise MediaSubmissionResolutionConflict(
                    "Provider request is already assigned to another job"
                )
            attempt.status = "submitted"
            attempt.effect_state = "confirmed"
            attempt.provider_request_id = request_id
            attempt.error_code = None
            attempt.submitted_at = now
            job.status = "submitted"
            job.effect_state = "confirmed"
            job.provider_request_id = request_id
            job.provider_state = "queued"
            job.next_reconcile_at = now
            job.error_code = None
            event_type = "submission.manually_confirmed"
        else:
            attempt.status = "not_submitted"
            attempt.effect_state = "confirmed_absent"
            attempt.error_code = "manual_confirmed_not_submitted"
            attempt.completed_at = now
            job.status = "cancelled"
            job.effect_state = "confirmed_absent"
            job.error_code = "manual_confirmed_not_submitted"
            job.cancelled_at = now
            job.completed_at = now
            MediaGenerationJobService(self._session)._release_budget(job, now)
            event_type = "submission.not_created_confirmed"
        job.updated_at = now
        self._clear_leases(job)
        self._append_event(job, event_type, now)
        resolution.status = MediaSubmissionResolutionStatus.EXECUTED
        resolution.executed_at = now
        resolution.updated_at = now
        self._session.flush()

    @staticmethod
    def _current_attempt(job: MediaGenerationJob) -> MediaGenerationAttempt:
        if not job.attempts:
            raise MediaSubmissionResolutionConflict(
                "Submission-unknown job has no attempt"
            )
        attempt = job.attempts[-1]
        if attempt.status != "submission_unknown" or attempt.effect_state != "unknown":
            raise MediaSubmissionResolutionConflict(
                "Submission attempt is not unknown"
            )
        return attempt

    def _append_event(
        self,
        job: MediaGenerationJob,
        event_type: str,
        now: datetime,
    ) -> None:
        job.event_sequence += 1
        self._session.add(
            MediaGenerationEvent(
                job_id=job.id,
                sequence=job.event_sequence,
                event_type=event_type,
                data_json={},
                created_at=now,
            )
        )

    @staticmethod
    def _clear_leases(job: MediaGenerationJob) -> None:
        job.leased_by = None
        job.lease_until = None
        job.heartbeat_at = None
        job.reconciliation_leased_by = None
        job.reconciliation_lease_until = None
