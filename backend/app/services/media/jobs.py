"""Durable, fenced media generation jobs and append-only budget accounting."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import re
from typing import List, Tuple
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.models.database import (
    MediaBudgetAccount,
    MediaBudgetLedgerEntry,
    MediaGenerationAttempt,
    MediaGenerationEvent,
    MediaGenerationJob,
    MediaRuntimeActivation,
    MediaRuntimeRevision,
)
from app.services.agent_runtime.contracts import Sensitivity
from app.services.idempotency import IdempotencyConflict, canonical_hash
from app.services.media.runtime import MediaWorkflowMode


TERMINAL_JOB_STATUSES = {"succeeded", "failed", "cancelled"}


class MediaBudgetExceeded(RuntimeError):
    """The tenant has no server-approved budget capacity for this job."""


class MediaJobLeaseConflict(RuntimeError):
    """A stale or different worker attempted to mutate a generation job."""


class MediaGenerationJobCommand(BaseModel):
    """Internal command; browser ingress must never choose tenant or estimate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    idempotency_key: str = Field(min_length=8, max_length=255)
    org_id: UUID
    owner_user_id: int = Field(gt=0)
    project_id: UUID
    storyboard_version_id: UUID
    shot_id: UUID
    runtime_revision_id: UUID
    mode: MediaWorkflowMode
    model_id: str = Field(min_length=1, max_length=255)
    intent_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    payload_ref: str = Field(min_length=1, max_length=1000)
    sensitivity: Sensitivity
    estimated_cost_microusd: int = Field(ge=0)
    estimate_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    deadline_at: datetime

    @field_validator("payload_ref")
    @classmethod
    def validate_payload_ref(cls, value: str) -> str:
        if not value.startswith("vault://"):
            raise ValueError("payload_ref must use the approved vault scheme")
        return value


class MediaGenerationJobService:
    """Coordinates durable state without ever storing the generation prompt."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def create(
        self,
        command: MediaGenerationJobCommand,
        *,
        now: datetime,
    ) -> Tuple[MediaGenerationJob, bool]:
        now = self._naive_utc(now)
        input_hash = canonical_hash(command.model_dump(mode="json"))
        existing = (
            self._db.query(MediaGenerationJob)
            .filter(
                MediaGenerationJob.org_id == command.org_id,
                MediaGenerationJob.owner_user_id == command.owner_user_id,
                MediaGenerationJob.idempotency_key == command.idempotency_key,
            )
            .one_or_none()
        )
        if existing is not None:
            if existing.input_hash != input_hash:
                raise IdempotencyConflict(
                    "Media job idempotency key was reused for different input"
                )
            return existing, False
        if command.deadline_at <= now:
            raise ValueError("media job deadline must be in the future")

        runtime = self._active_runtime(command)
        aliases = dict(runtime.model_aliases or {})
        if aliases.get(command.mode.value) != command.model_id:
            raise ValueError("model alias is not approved by the active runtime")

        period_start = date(now.year, now.month, 1)
        account = (
            self._db.query(MediaBudgetAccount)
            .filter(
                MediaBudgetAccount.org_id == command.org_id,
                MediaBudgetAccount.period_start == period_start,
            )
            .with_for_update()
            .one_or_none()
        )
        if account is None:
            raise MediaBudgetExceeded("media budget is not configured")
        available = (
            account.limit_microusd
            - account.reserved_microusd
            - account.spent_microusd
        )
        if command.estimated_cost_microusd > available:
            raise MediaBudgetExceeded("media budget capacity exceeded")

        job = MediaGenerationJob(
            org_id=command.org_id,
            owner_user_id=command.owner_user_id,
            project_id=command.project_id,
            storyboard_version_id=command.storyboard_version_id,
            shot_id=command.shot_id,
            runtime_revision_id=runtime.id,
            idempotency_key=command.idempotency_key,
            input_hash=input_hash,
            intent_hash=command.intent_hash,
            payload_ref=command.payload_ref,
            mode=command.mode.value,
            provider=runtime.provider,
            model_id=command.model_id,
            sensitivity=command.sensitivity.value,
            status="queued",
            effect_state="none",
            reserved_cost_microusd=command.estimated_cost_microusd,
            estimate_hash=command.estimate_hash,
            budget_period_start=period_start,
            deadline_at=self._naive_utc(command.deadline_at),
            created_at=now,
            updated_at=now,
        )
        self._db.add(job)
        self._db.flush()
        account.reserved_microusd += command.estimated_cost_microusd
        self._db.add(
            MediaBudgetLedgerEntry(
                org_id=job.org_id,
                job_id=job.id,
                period_start=period_start,
                entry_type="reservation",
                amount_microusd=command.estimated_cost_microusd,
                idempotency_key=f"media-budget:{job.id}:reservation",
                estimate_hash=command.estimate_hash,
                created_at=now,
            )
        )
        self._append_event(job, "job.created", {}, now)
        self._db.commit()
        self._db.refresh(job)
        return job, True

    def claim_batch(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
    ) -> List[MediaGenerationJob]:
        self._validate_lease_request(worker_id, limit, lease_seconds)
        now = self._naive_utc(now)
        self.recover_expired(now=now)
        rows = (
            self._db.query(MediaGenerationJob)
            .filter(
                MediaGenerationJob.status == "queued",
                MediaGenerationJob.deadline_at > now,
            )
            .order_by(MediaGenerationJob.created_at, MediaGenerationJob.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
            .all()
        )
        for job in rows:
            self._claim(job, worker_id, now, lease_seconds)
        self._db.commit()
        return rows

    def claim_one(
        self,
        job_id: UUID,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> MediaGenerationJob:
        self._validate_lease_request(worker_id, 1, lease_seconds)
        now = self._naive_utc(now)
        self.recover_expired(now=now)
        job = (
            self._db.query(MediaGenerationJob)
            .filter(
                MediaGenerationJob.id == job_id,
                MediaGenerationJob.status == "queued",
                MediaGenerationJob.deadline_at > now,
            )
            .with_for_update()
            .one_or_none()
        )
        if job is None:
            raise MediaJobLeaseConflict("media job is not claimable")
        self._claim(job, worker_id, now, lease_seconds)
        self._db.commit()
        return job

    def begin_submission(
        self,
        job_id: UUID,
        *,
        worker_id: str,
        fencing_token: int,
        now: datetime,
    ) -> MediaGenerationAttempt:
        now = self._naive_utc(now)
        job = self._leased_job(job_id, worker_id, fencing_token, now)
        if job.effect_state != "none":
            raise MediaJobLeaseConflict("media submission effect already started")
        attempt_number = len(job.attempts) + 1
        attempt = MediaGenerationAttempt(
            job_id=job.id,
            attempt_number=attempt_number,
            fencing_token=fencing_token,
            provider=job.provider,
            model_id=job.model_id,
            status="submitting",
            effect_state="started",
            started_at=now,
        )
        self._db.add(attempt)
        job.effect_state = "started"
        job.updated_at = now
        self._append_event(
            job,
            "submission.started",
            {"attempt_number": attempt_number},
            now,
        )
        self._db.commit()
        self._db.refresh(attempt)
        return attempt

    def record_submitted(
        self,
        job_id: UUID,
        *,
        worker_id: str,
        fencing_token: int,
        provider_request_id: str,
        now: datetime,
    ) -> MediaGenerationJob:
        now = self._naive_utc(now)
        request_id = self._safe_external_ref(provider_request_id, "provider request")
        job = self._leased_job(job_id, worker_id, fencing_token, now)
        attempt = self._current_attempt(job)
        if job.effect_state != "started" or attempt.status != "submitting":
            raise MediaJobLeaseConflict("media job is not awaiting a submit receipt")
        attempt.status = "submitted"
        attempt.effect_state = "confirmed"
        attempt.provider_request_id = request_id
        attempt.submitted_at = now
        job.status = "submitted"
        job.effect_state = "confirmed"
        job.provider_request_id = request_id
        job.updated_at = now
        self._clear_lease(job)
        self._append_event(
            job,
            "submission.accepted",
            {"provider_request_id": request_id},
            now,
        )
        self._db.commit()
        return job

    def mark_submission_unknown(
        self,
        job_id: UUID,
        *,
        worker_id: str,
        fencing_token: int,
        error_code: str,
        now: datetime,
    ) -> MediaGenerationJob:
        """Freeze an ambiguous external effect so no worker can resubmit it."""
        now = self._naive_utc(now)
        job = self._leased_job(job_id, worker_id, fencing_token, now)
        attempt = self._current_attempt(job)
        if job.effect_state != "started" or attempt.status != "submitting":
            raise MediaJobLeaseConflict("media job has no ambiguous submission")
        safe_code = self._safe_error_code(error_code)
        job.status = "submission_unknown"
        job.effect_state = "unknown"
        job.error_code = safe_code
        job.updated_at = now
        attempt.status = "submission_unknown"
        attempt.effect_state = "unknown"
        attempt.error_code = safe_code
        self._clear_lease(job)
        self._append_event(
            job,
            "submission.unknown",
            {"error_code": safe_code},
            now,
        )
        self._db.commit()
        return job

    def record_succeeded(
        self,
        job_id: UUID,
        *,
        provider_request_id: str,
        result_ref: str,
        actual_cost_microusd: int,
        now: datetime,
    ) -> MediaGenerationJob:
        now = self._naive_utc(now)
        if actual_cost_microusd < 0:
            raise ValueError("actual media cost cannot be negative")
        request_id = self._safe_external_ref(provider_request_id, "provider request")
        if not result_ref.startswith("quarantine://") or len(result_ref) > 1000:
            raise ValueError("result_ref must use the quarantine scheme")
        job = (
            self._db.query(MediaGenerationJob)
            .filter(MediaGenerationJob.id == job_id)
            .with_for_update()
            .one_or_none()
        )
        if job is None:
            raise KeyError("media job not found")
        if job.status == "succeeded":
            if (
                job.provider_request_id != request_id
                or job.result_ref != result_ref
                or job.actual_cost_microusd != actual_cost_microusd
            ):
                raise IdempotencyConflict("terminal media result does not match")
            return job
        if job.status in TERMINAL_JOB_STATUSES or job.status != "submitted":
            raise ValueError("media job cannot transition to succeeded")
        if job.provider_request_id != request_id:
            raise ValueError("provider request does not belong to media job")

        attempt = self._current_attempt(job)
        attempt.status = "succeeded"
        attempt.completed_at = now
        job.status = "succeeded"
        job.result_ref = result_ref
        job.actual_cost_microusd = actual_cost_microusd
        job.completed_at = now
        job.updated_at = now
        self._settle_budget(job, actual_cost_microusd, now)
        self._append_event(
            job,
            "job.succeeded",
            {"result_ref": result_ref},
            now,
        )
        self._db.commit()
        return job

    def cancel(
        self,
        job_id: UUID,
        *,
        requested_by_user_id: int,
        now: datetime,
    ) -> MediaGenerationJob:
        now = self._naive_utc(now)
        job = (
            self._db.query(MediaGenerationJob)
            .filter(MediaGenerationJob.id == job_id)
            .with_for_update()
            .one_or_none()
        )
        if job is None or job.owner_user_id != requested_by_user_id:
            raise KeyError("media job not found")
        if job.status == "cancelled":
            return job
        if job.status in {"succeeded", "failed"}:
            return job
        if job.effect_state == "none":
            job.status = "cancelled"
            job.cancelled_at = now
            job.completed_at = now
            self._release_budget(job, now)
            event_type = "job.cancelled"
        else:
            job.status = "cancel_requested"
            event_type = "job.cancel_requested"
        job.updated_at = now
        self._clear_lease(job)
        self._append_event(job, event_type, {}, now)
        self._db.commit()
        return job

    def recover_expired(self, *, now: datetime) -> List[MediaGenerationJob]:
        now = self._naive_utc(now)
        rows = (
            self._db.query(MediaGenerationJob)
            .filter(
                MediaGenerationJob.status.in_(["queued", "running"]),
                (
                    (MediaGenerationJob.deadline_at <= now)
                    | (
                        (MediaGenerationJob.status == "running")
                        & (MediaGenerationJob.lease_until <= now)
                    )
                ),
            )
            .with_for_update(skip_locked=True)
            .all()
        )
        for job in rows:
            if job.effect_state == "started":
                job.status = "submission_unknown"
                job.effect_state = "unknown"
                job.error_code = "lease_expired_after_submission_started"
                attempt = self._current_attempt(job)
                attempt.status = "submission_unknown"
                attempt.effect_state = "unknown"
                attempt.error_code = job.error_code
                event_type = "submission.unknown"
            elif job.deadline_at <= now:
                job.status = "cancelled"
                job.error_code = "deadline_exceeded"
                job.cancelled_at = now
                job.completed_at = now
                self._release_budget(job, now)
                event_type = "job.cancelled"
            else:
                job.status = "queued"
                job.error_code = "lease_expired_before_submission"
                event_type = "job.requeued"
            job.updated_at = now
            self._clear_lease(job)
            self._append_event(job, event_type, {}, now)
        self._db.commit()
        return rows

    def _active_runtime(
        self,
        command: MediaGenerationJobCommand,
    ) -> MediaRuntimeRevision:
        return_value = (
            self._db.query(MediaRuntimeRevision)
            .join(
                MediaRuntimeActivation,
                MediaRuntimeActivation.active_revision_id
                == MediaRuntimeRevision.id,
            )
            .filter(
                MediaRuntimeActivation.org_id == command.org_id,
                MediaRuntimeRevision.org_id == command.org_id,
                MediaRuntimeRevision.id == command.runtime_revision_id,
            )
            .one_or_none()
        )
        if return_value is None:
            raise ValueError("active runtime revision does not match the media job")
        if command.mode.value not in (return_value.enabled_modes or []):
            raise ValueError("media mode is not enabled by the active runtime")
        return return_value

    def _claim(
        self,
        job: MediaGenerationJob,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> None:
        job.status = "running"
        job.fencing_token += 1
        job.leased_by = worker_id
        job.lease_until = now + timedelta(seconds=lease_seconds)
        job.heartbeat_at = now
        job.updated_at = now
        self._append_event(
            job,
            "job.claimed",
            {"fencing_token": job.fencing_token},
            now,
        )

    def _leased_job(
        self,
        job_id: UUID,
        worker_id: str,
        fencing_token: int,
        now: datetime,
    ) -> MediaGenerationJob:
        job = (
            self._db.query(MediaGenerationJob)
            .filter(MediaGenerationJob.id == job_id)
            .with_for_update()
            .one_or_none()
        )
        if (
            job is None
            or job.status != "running"
            or job.leased_by != worker_id
            or job.fencing_token != fencing_token
            or job.lease_until is None
            or job.lease_until <= now
            or job.deadline_at <= now
        ):
            raise MediaJobLeaseConflict("media job lease is no longer current")
        return job

    def _settle_budget(
        self,
        job: MediaGenerationJob,
        actual_cost_microusd: int,
        now: datetime,
    ) -> None:
        if job.budget_finalized_at is not None:
            return
        account = self._budget_account(job)
        if account.reserved_microusd < job.reserved_cost_microusd:
            raise RuntimeError("media budget reservation invariant violated")
        account.reserved_microusd -= job.reserved_cost_microusd
        account.spent_microusd += actual_cost_microusd
        self._db.add(
            MediaBudgetLedgerEntry(
                org_id=job.org_id,
                job_id=job.id,
                period_start=job.budget_period_start,
                entry_type="settlement",
                amount_microusd=actual_cost_microusd,
                idempotency_key=f"media-budget:{job.id}:settlement",
                estimate_hash=job.estimate_hash,
                created_at=now,
            )
        )
        job.budget_finalized_at = now

    def _release_budget(self, job: MediaGenerationJob, now: datetime) -> None:
        if job.budget_finalized_at is not None:
            return
        account = self._budget_account(job)
        if account.reserved_microusd < job.reserved_cost_microusd:
            raise RuntimeError("media budget reservation invariant violated")
        account.reserved_microusd -= job.reserved_cost_microusd
        self._db.add(
            MediaBudgetLedgerEntry(
                org_id=job.org_id,
                job_id=job.id,
                period_start=job.budget_period_start,
                entry_type="release",
                amount_microusd=job.reserved_cost_microusd,
                idempotency_key=f"media-budget:{job.id}:release",
                estimate_hash=job.estimate_hash,
                created_at=now,
            )
        )
        job.budget_finalized_at = now

    def _budget_account(self, job: MediaGenerationJob) -> MediaBudgetAccount:
        account = (
            self._db.query(MediaBudgetAccount)
            .filter(
                MediaBudgetAccount.org_id == job.org_id,
                MediaBudgetAccount.period_start == job.budget_period_start,
            )
            .with_for_update()
            .one_or_none()
        )
        if account is None:
            raise RuntimeError("media budget account disappeared")
        return account

    def _append_event(
        self,
        job: MediaGenerationJob,
        event_type: str,
        data: dict,
        now: datetime,
    ) -> None:
        job.event_sequence += 1
        self._db.add(
            MediaGenerationEvent(
                job_id=job.id,
                sequence=job.event_sequence,
                event_type=event_type,
                data_json=data,
                created_at=now,
            )
        )

    @staticmethod
    def _current_attempt(job: MediaGenerationJob) -> MediaGenerationAttempt:
        if not job.attempts:
            raise MediaJobLeaseConflict("media job has no active submission attempt")
        return job.attempts[-1]

    @staticmethod
    def _clear_lease(job: MediaGenerationJob) -> None:
        job.leased_by = None
        job.lease_until = None
        job.heartbeat_at = None

    @staticmethod
    def _validate_lease_request(
        worker_id: str,
        limit: int,
        lease_seconds: int,
    ) -> None:
        if not worker_id or len(worker_id) > 100:
            raise ValueError("worker_id is invalid")
        if not 1 <= limit <= 100:
            raise ValueError("claim limit must be between 1 and 100")
        if not 1 <= lease_seconds <= 900:
            raise ValueError("lease_seconds must be between 1 and 900")

    @staticmethod
    def _safe_external_ref(value: str, name: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 255:
            raise ValueError(f"{name} is invalid")
        return normalized

    @staticmethod
    def _safe_error_code(value: str) -> str:
        normalized = value.strip().lower()
        if re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,99}", normalized):
            return normalized
        return "provider_submission_failed"

    @staticmethod
    def _naive_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
