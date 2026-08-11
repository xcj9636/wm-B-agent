"""Provider read, result quarantine, and terminal reconciliation coordinator."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.integrations.fal_media import (
    MediaOutput,
    MediaProviderError,
    MediaProviderResult,
    MediaQueueState,
    MediaQueueStatus,
)
from app.models.database import MediaGenerationJob
from app.services.media.reconciliation import (
    MediaReconciliationLeaseConflict,
    MediaReconciliationService,
)


class MediaQuarantineReceipt(BaseModel):
    """Opaque receipt proving provider output crossed the quarantine boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    result_ref: str = Field(
        min_length=1,
        max_length=1000,
        pattern=r"^quarantine://[A-Za-z0-9._/-]+$",
    )
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class MediaStatusAdapter(Protocol):
    async def status(
        self,
        *,
        model_id: str,
        request_id: str,
    ) -> MediaQueueStatus: ...

    async def result(
        self,
        *,
        model_id: str,
        request_id: str,
    ) -> MediaProviderResult: ...


class MediaResultIngestor(Protocol):
    async def ingest(
        self,
        *,
        job: MediaGenerationJob,
        outputs: List[MediaOutput],
    ) -> MediaQuarantineReceipt: ...


class MediaCostResolver(Protocol):
    def actual_cost_microusd(self, job: MediaGenerationJob) -> int: ...


class MediaReconciliationCoordinator:
    """Treat provider callbacks as hints and derive truth through safe reads."""

    def __init__(
        self,
        db: Session,
        *,
        reconciliation: MediaReconciliationService,
        adapter: MediaStatusAdapter,
        ingestor: MediaResultIngestor,
        cost_resolver: MediaCostResolver,
        poll_after_seconds: int = 15,
        retry_after_seconds: int = 30,
    ) -> None:
        if not 1 <= poll_after_seconds <= 900:
            raise ValueError("poll interval must be between 1 and 900 seconds")
        if not 1 <= retry_after_seconds <= 3600:
            raise ValueError("retry interval must be between 1 and 3600 seconds")
        self._db = db
        self._reconciliation = reconciliation
        self._adapter = adapter
        self._ingestor = ingestor
        self._cost_resolver = cost_resolver
        self._poll_after_seconds = poll_after_seconds
        self._retry_after_seconds = retry_after_seconds

    async def reconcile_claimed(
        self,
        job_id: UUID,
        *,
        worker_id: str,
        fencing_token: int,
        now: datetime,
    ) -> MediaGenerationJob:
        checked_at = self._aware_utc(now)
        job = self._current_claim(job_id, worker_id, fencing_token, checked_at)
        try:
            status = await self._adapter.status(
                model_id=job.model_id,
                request_id=job.provider_request_id,
            )
        except MediaProviderError as exc:
            return self._retry(
                job,
                worker_id,
                fencing_token,
                exc.error_code,
                checked_at,
            )
        except Exception:
            return self._retry(
                job,
                worker_id,
                fencing_token,
                "provider_status_failed",
                checked_at,
            )

        if status.state in {MediaQueueState.QUEUED, MediaQueueState.RUNNING}:
            return self._reconciliation.record_pending(
                job.id,
                worker_id=worker_id,
                fencing_token=fencing_token,
                provider_state=status.state.value,
                now=checked_at,
                poll_after_seconds=self._poll_after_seconds,
            )
        if status.state == MediaQueueState.FAILED:
            try:
                actual_cost = self._validated_cost(job)
            except Exception:
                return self._retry(
                    job,
                    worker_id,
                    fencing_token,
                    "cost_resolution_failed",
                    checked_at,
                )
            return self._reconciliation.record_failed(
                job.id,
                worker_id=worker_id,
                fencing_token=fencing_token,
                error_code=status.error_code or "provider_request_failed",
                actual_cost_microusd=actual_cost,
                now=checked_at,
            )

        try:
            provider_result = await self._adapter.result(
                model_id=job.model_id,
                request_id=job.provider_request_id,
            )
            receipt = await self._ingestor.ingest(
                job=job,
                outputs=provider_result.outputs,
            )
            actual_cost = self._validated_cost(job)
        except MediaProviderError as exc:
            return self._retry(
                job,
                worker_id,
                fencing_token,
                exc.error_code,
                checked_at,
            )
        except Exception:
            return self._retry(
                job,
                worker_id,
                fencing_token,
                "result_ingestion_failed",
                checked_at,
            )
        return self._reconciliation.record_succeeded(
            job.id,
            worker_id=worker_id,
            fencing_token=fencing_token,
            result_ref=receipt.result_ref,
            actual_cost_microusd=actual_cost,
            now=checked_at,
        )

    def _retry(
        self,
        job: MediaGenerationJob,
        worker_id: str,
        fencing_token: int,
        error_code: str,
        now: datetime,
    ) -> MediaGenerationJob:
        return self._reconciliation.record_retry(
            job.id,
            worker_id=worker_id,
            fencing_token=fencing_token,
            error_code=error_code,
            now=now,
            retry_after_seconds=self._retry_after_seconds,
        )

    def _current_claim(
        self,
        job_id: UUID,
        worker_id: str,
        fencing_token: int,
        now: datetime,
    ) -> MediaGenerationJob:
        job = self._db.get(MediaGenerationJob, job_id)
        naive_now = now.replace(tzinfo=None)
        if (
            job is None
            or job.status != "submitted"
            or not job.provider_request_id
            or job.reconciliation_leased_by != worker_id
            or job.reconciliation_fencing_token != fencing_token
            or job.reconciliation_lease_until is None
            or job.reconciliation_lease_until <= naive_now
        ):
            raise MediaReconciliationLeaseConflict(
                "media reconciliation lease is no longer current"
            )
        return job

    def _validated_cost(self, job: MediaGenerationJob) -> int:
        value = self._cost_resolver.actual_cost_microusd(job)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("media cost resolver returned an invalid amount")
        return value

    @staticmethod
    def _aware_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
