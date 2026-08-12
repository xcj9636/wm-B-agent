"""Bounded worker loop for fenced provider reconciliation reads."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from app.services.media.reconciliation import MediaReconciliationService
from app.services.media.worker_runtime import (
    MediaRuntimeUnavailable,
    PinnedMediaRuntimeFactory,
)


async def run_media_reconciliation_batch(
    *,
    reconciliation: MediaReconciliationService,
    runtime_factory: PinnedMediaRuntimeFactory,
    coordinator_builder: Callable,
    worker_id: str,
    now: datetime,
    batch_size: int,
    lease_seconds: int,
    retry_after_seconds: int = 30,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, int]:
    """Poll a bounded batch; every network adapter is scoped to one pinned job."""
    if not worker_id or len(worker_id) > 100:
        raise ValueError("worker_id is invalid")
    if not 1 <= batch_size <= 100:
        raise ValueError("batch_size must be between 1 and 100")
    if not 1 <= lease_seconds <= 900:
        raise ValueError("lease_seconds must be between 1 and 900")
    if not 1 <= retry_after_seconds <= 3600:
        raise ValueError("retry_after_seconds must be between 1 and 3600")
    counters = {
        "claimed": 0,
        "pending": 0,
        "succeeded": 0,
        "failed": 0,
        "retry_scheduled": 0,
    }
    for index in range(batch_size):
        checked_at = now if index == 0 else clock()
        jobs = reconciliation.claim_batch(
            worker_id=worker_id,
            now=checked_at,
            limit=1,
            lease_seconds=lease_seconds,
        )
        if not jobs:
            break
        job = jobs[0]
        counters["claimed"] += 1
        adapter = None
        try:
            adapter = runtime_factory.build(job)
            result = await coordinator_builder(adapter).reconcile_claimed(
                job.id,
                worker_id=worker_id,
                fencing_token=job.reconciliation_fencing_token,
                now=checked_at,
            )
        except MediaRuntimeUnavailable:
            reconciliation.record_retry(
                job.id,
                worker_id=worker_id,
                fencing_token=job.reconciliation_fencing_token,
                error_code="media_runtime_unavailable",
                now=checked_at,
                retry_after_seconds=retry_after_seconds,
            )
            counters["retry_scheduled"] += 1
            continue
        finally:
            if adapter is not None:
                await adapter.aclose()

        if result.status == "succeeded":
            counters["succeeded"] += 1
        elif result.status == "failed":
            counters["failed"] += 1
        elif result.provider_state == "reconcile_retry":
            counters["retry_scheduled"] += 1
        else:
            counters["pending"] += 1
    return counters
