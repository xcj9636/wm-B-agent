"""Durable provider usage evidence, deliberately separate from pricing."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.integrations.fal_media import MediaProviderResult
from app.models.database import MediaGenerationJob, MediaProviderUsageReceipt
from app.services.idempotency import canonical_hash


class MediaUsageReceiptConflict(RuntimeError):
    """Usage evidence could not be bound uniquely to the submitted job."""


@dataclass(frozen=True)
class MediaUsageReceiptResult:
    receipt_id: UUID
    created: bool


class MediaUsageReceiptService:
    """Persist exact billable units without pretending they are priced cost."""

    def __init__(self, db: Session):
        self._db = db

    def record(
        self,
        *,
        job: MediaGenerationJob,
        provider_result: MediaProviderResult,
        now: datetime,
    ) -> MediaUsageReceiptResult:
        self._validate_binding(job, provider_result)
        units = provider_result.billable_units.quantize(Decimal("0.000000001"))
        receipt_hash = canonical_hash(
            {
                "provider": job.provider,
                "provider_request_id": job.provider_request_id,
                "model_id": job.model_id,
                "runtime_revision_id": str(job.runtime_revision_id),
                "billable_units": format(units, "f"),
            }
        )
        existing = self._find(job)
        if existing is not None:
            return self._replay(existing, receipt_hash)
        receipt = MediaProviderUsageReceipt(
            job_id=job.id,
            runtime_revision_id=job.runtime_revision_id,
            provider=job.provider,
            provider_request_id=job.provider_request_id,
            model_id=job.model_id,
            billable_units=units,
            pricing_status="unpriced",
            unit_price_microusd=None,
            cost_microusd=None,
            receipt_hash=receipt_hash,
            observed_at=self._naive_utc(now),
        )
        try:
            with self._db.begin_nested():
                self._db.add(receipt)
                self._db.flush()
        except IntegrityError:
            existing = self._find(job)
            if existing is None:
                raise
            return self._replay(existing, receipt_hash)
        self._db.commit()
        return MediaUsageReceiptResult(receipt_id=receipt.id, created=True)

    def _find(self, job: MediaGenerationJob) -> MediaProviderUsageReceipt | None:
        return (
            self._db.query(MediaProviderUsageReceipt)
            .filter(
                MediaProviderUsageReceipt.provider == job.provider,
                MediaProviderUsageReceipt.provider_request_id
                == job.provider_request_id,
            )
            .one_or_none()
        )

    @staticmethod
    def _validate_binding(
        job: MediaGenerationJob,
        provider_result: MediaProviderResult,
    ) -> None:
        if (
            job.provider != "fal"
            or job.status != "submitted"
            or not job.provider_request_id
            or provider_result.provider_request_id != job.provider_request_id
            or provider_result.model_id != job.model_id
        ):
            raise MediaUsageReceiptConflict("provider usage receipt is unbound")

    @staticmethod
    def _replay(
        receipt: MediaProviderUsageReceipt,
        receipt_hash: str,
    ) -> MediaUsageReceiptResult:
        if receipt.receipt_hash != receipt_hash:
            raise MediaUsageReceiptConflict("provider usage receipt changed")
        return MediaUsageReceiptResult(receipt_id=receipt.id, created=False)

    @staticmethod
    def _naive_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
