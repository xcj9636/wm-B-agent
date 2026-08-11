"""Fenced polling leases for already-submitted external media requests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.database import MediaGenerationEvent, MediaGenerationJob


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
    def _naive_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
