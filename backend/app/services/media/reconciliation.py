"""Fenced polling leases for already-submitted external media requests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import List
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.database import (
    MediaBudgetAccount,
    MediaBudgetLedgerEntry,
    MediaGenerationEvent,
    MediaGenerationJob,
)
from app.services.media.jobs import MediaGenerationJobService


class MediaReconciliationLeaseConflict(RuntimeError):
    """A stale or different reconciler attempted to mutate provider state."""


class MediaReconciliationService:
    """Coordinates safe-to-repeat provider reads independently of submission."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def claim_batch(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
    ) -> List[MediaGenerationJob]:
        self._validate_claim(worker_id, limit, lease_seconds)
        now = self._naive_utc(now)
        rows = (
            self._db.query(MediaGenerationJob)
            .filter(
                MediaGenerationJob.status == "submitted",
                MediaGenerationJob.next_reconcile_at.is_not(None),
                MediaGenerationJob.next_reconcile_at <= now,
                or_(
                    MediaGenerationJob.reconciliation_leased_by.is_(None),
                    MediaGenerationJob.reconciliation_lease_until <= now,
                ),
            )
            .order_by(
                MediaGenerationJob.next_reconcile_at,
                MediaGenerationJob.created_at,
            )
            .with_for_update(skip_locked=True)
            .limit(limit)
            .all()
        )
        lease_until = now + timedelta(seconds=lease_seconds)
        for job in rows:
            job.reconciliation_fencing_token += 1
            job.reconciliation_leased_by = worker_id
            job.reconciliation_lease_until = lease_until
            job.updated_at = now
        self._db.commit()
        return rows

    def record_pending(
        self,
        job_id: UUID,
        *,
        worker_id: str,
        fencing_token: int,
        provider_state: str,
        now: datetime,
        poll_after_seconds: int,
    ) -> MediaGenerationJob:
        if provider_state not in {"queued", "running"}:
            raise ValueError("provider state is not pending")
        if not 1 <= poll_after_seconds <= 900:
            raise ValueError("poll interval must be between 1 and 900 seconds")
        now = self._naive_utc(now)
        job = self._leased_job(job_id, worker_id, fencing_token, now)
        job.provider_state = provider_state
        job.reconcile_count += 1
        job.last_reconciled_at = now
        job.next_reconcile_at = now + timedelta(seconds=poll_after_seconds)
        job.updated_at = now
        self._clear_lease(job)
        self._append_event(job, f"provider.{provider_state}", now)
        self._db.commit()
        return job

    def record_retry(
        self,
        job_id: UUID,
        *,
        worker_id: str,
        fencing_token: int,
        error_code: str,
        now: datetime,
        retry_after_seconds: int,
    ) -> MediaGenerationJob:
        if not 1 <= retry_after_seconds <= 3600:
            raise ValueError("retry interval must be between 1 and 3600 seconds")
        now = self._naive_utc(now)
        job = self._leased_job(job_id, worker_id, fencing_token, now)
        job.provider_state = "reconcile_retry"
        job.error_code = self._safe_error_code(error_code)
        job.reconcile_count += 1
        job.last_reconciled_at = now
        job.next_reconcile_at = now + timedelta(seconds=retry_after_seconds)
        job.updated_at = now
        self._clear_lease(job)
        self._append_event(job, "provider.retry_scheduled", now)
        self._db.commit()
        return job

    def record_succeeded(
        self,
        job_id: UUID,
        *,
        worker_id: str,
        fencing_token: int,
        result_ref: str,
        actual_cost_microusd: int,
        now: datetime,
    ) -> MediaGenerationJob:
        now = self._naive_utc(now)
        job = self._leased_job(job_id, worker_id, fencing_token, now)
        request_id = job.provider_request_id
        if request_id is None:
            raise MediaReconciliationLeaseConflict(
                "submitted media job has no provider request"
            )
        self._clear_lease(job)
        job.last_reconciled_at = now
        job.provider_state = "completed"
        return MediaGenerationJobService(self._db).record_succeeded(
            job.id,
            provider_request_id=request_id,
            result_ref=result_ref,
            actual_cost_microusd=actual_cost_microusd,
            now=now,
        )

    def record_failed(
        self,
        job_id: UUID,
        *,
        worker_id: str,
        fencing_token: int,
        error_code: str,
        actual_cost_microusd: int,
        now: datetime,
    ) -> MediaGenerationJob:
        if actual_cost_microusd < 0:
            raise ValueError("actual media cost cannot be negative")
        now = self._naive_utc(now)
        job = self._leased_job(job_id, worker_id, fencing_token, now)
        safe_code = self._safe_error_code(error_code)
        if not job.attempts:
            raise MediaReconciliationLeaseConflict(
                "submitted media job has no submission attempt"
            )
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
        attempt = job.attempts[-1]
        attempt.status = "failed"
        attempt.error_code = safe_code
        attempt.completed_at = now
        job.status = "failed"
        job.provider_state = "failed"
        job.error_code = safe_code
        job.actual_cost_microusd = actual_cost_microusd
        job.budget_finalized_at = now
        job.completed_at = now
        job.last_reconciled_at = now
        job.next_reconcile_at = None
        job.updated_at = now
        self._clear_lease(job)
        self._append_event(job, "job.failed", now)
        self._db.commit()
        return job

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
            or job.status != "submitted"
            or job.reconciliation_leased_by != worker_id
            or job.reconciliation_fencing_token != fencing_token
            or job.reconciliation_lease_until is None
            or job.reconciliation_lease_until <= now
        ):
            raise MediaReconciliationLeaseConflict(
                "media reconciliation lease is no longer current"
            )
        return job

    def _append_event(
        self,
        job: MediaGenerationJob,
        event_type: str,
        now: datetime,
    ) -> None:
        job.event_sequence += 1
        self._db.add(
            MediaGenerationEvent(
                job_id=job.id,
                sequence=job.event_sequence,
                event_type=event_type,
                data_json={"reconcile_count": job.reconcile_count},
                created_at=now,
            )
        )

    @staticmethod
    def _clear_lease(job: MediaGenerationJob) -> None:
        job.reconciliation_leased_by = None
        job.reconciliation_lease_until = None

    @staticmethod
    def _validate_claim(worker_id: str, limit: int, lease_seconds: int) -> None:
        if not worker_id or len(worker_id) > 100:
            raise ValueError("worker_id is invalid")
        if not 1 <= limit <= 100:
            raise ValueError("claim limit must be between 1 and 100")
        if not 1 <= lease_seconds <= 900:
            raise ValueError("lease_seconds must be between 1 and 900")

    @staticmethod
    def _safe_error_code(value: str) -> str:
        normalized = value.strip().lower()
        if re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,99}", normalized):
            return normalized
        return "provider_reconciliation_failed"

    @staticmethod
    def _naive_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
