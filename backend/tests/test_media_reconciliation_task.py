from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.media.worker_runtime import MediaRuntimeUnavailable
from app.tasks.celery_worker import celery
from app.tasks.media_tasks import (
    reconcile_media_jobs_task,
)
from app.services.media.reconciliation_worker import (
    run_media_reconciliation_batch,
)


NOW = datetime(2026, 8, 12, 9, 0, 0)


class FakeReconciliation:
    def __init__(self, jobs):
        self.jobs = list(jobs)
        self.all_jobs = list(jobs)
        self.claims = []
        self.retries = []

    def claim_batch(self, **kwargs):
        self.claims.append(kwargs)
        claimed = self.jobs[: kwargs["limit"]]
        self.jobs = self.jobs[kwargs["limit"] :]
        return claimed

    def record_retry(self, job_id, **kwargs):
        self.retries.append((job_id, kwargs))
        return next(job for job in self.all_jobs if job.id == job_id)


class FakeAdapter:
    def __init__(self, job_id, closed):
        self.job_id = job_id
        self.closed = closed

    async def aclose(self):
        self.closed.append(self.job_id)


class FakeRuntimeFactory:
    def __init__(self, closed, *, unavailable_job_id=None):
        self.closed = closed
        self.unavailable_job_id = unavailable_job_id
        self.built = []

    def build(self, job):
        self.built.append(job.id)
        if job.id == self.unavailable_job_id:
            raise MediaRuntimeUnavailable("secret path must never be returned")
        return FakeAdapter(job.id, self.closed)


class FakeCoordinator:
    def __init__(self, calls, adapter):
        self.calls = calls
        self.adapter = adapter

    async def reconcile_claimed(self, job_id, **kwargs):
        self.calls.append((job_id, kwargs, self.adapter.job_id))
        return SimpleNamespace(status="submitted", provider_state="running")


@pytest.mark.asyncio
async def test_batch_claims_bounded_jobs_uses_pinned_adapter_and_closes_each():
    jobs = [
        SimpleNamespace(
            id=uuid4(),
            reconciliation_fencing_token=index,
        )
        for index in (4, 8)
    ]
    reconciliation = FakeReconciliation(jobs)
    closed = []
    runtime_factory = FakeRuntimeFactory(closed)
    calls = []

    result = await run_media_reconciliation_batch(
        reconciliation=reconciliation,
        runtime_factory=runtime_factory,
        coordinator_builder=lambda adapter: FakeCoordinator(calls, adapter),
        worker_id="media-reconciler-a",
        now=NOW,
        batch_size=10,
        lease_seconds=60,
    )

    assert reconciliation.claims[0] == {
        "worker_id": "media-reconciler-a",
        "now": NOW,
        "limit": 1,
        "lease_seconds": 60,
    }
    assert all(claim["limit"] == 1 for claim in reconciliation.claims)
    assert runtime_factory.built == [job.id for job in jobs]
    assert closed == [job.id for job in jobs]
    assert [call[0] for call in calls] == [job.id for job in jobs]
    assert [call[1]["fencing_token"] for call in calls] == [4, 8]
    assert result == {
        "claimed": 2,
        "pending": 2,
        "succeeded": 0,
        "failed": 0,
        "retry_scheduled": 0,
    }


@pytest.mark.asyncio
async def test_batch_uses_a_fresh_reconciliation_time_for_each_job():
    jobs = [
        SimpleNamespace(id=uuid4(), reconciliation_fencing_token=index)
        for index in (1, 2)
    ]
    calls = []
    moments = iter(
        [
            datetime(2026, 8, 12, 9, 0, 1),
            datetime(2026, 8, 12, 9, 0, 2),
        ]
    )

    await run_media_reconciliation_batch(
        reconciliation=FakeReconciliation(jobs),
        runtime_factory=FakeRuntimeFactory([]),
        coordinator_builder=lambda adapter: FakeCoordinator(calls, adapter),
        worker_id="media-reconciler-a",
        now=NOW,
        batch_size=10,
        lease_seconds=60,
        clock=lambda: next(moments),
    )

    assert [call[1]["now"] for call in calls] == [
        datetime(2026, 8, 12, 9, 0, 1),
        datetime(2026, 8, 12, 9, 0, 2),
    ]


@pytest.mark.asyncio
async def test_runtime_failure_schedules_safe_read_retry_without_leaking_details():
    failed = SimpleNamespace(id=uuid4(), reconciliation_fencing_token=2)
    healthy = SimpleNamespace(id=uuid4(), reconciliation_fencing_token=3)
    reconciliation = FakeReconciliation([failed, healthy])
    closed = []
    runtime_factory = FakeRuntimeFactory(
        closed,
        unavailable_job_id=failed.id,
    )

    result = await run_media_reconciliation_batch(
        reconciliation=reconciliation,
        runtime_factory=runtime_factory,
        coordinator_builder=lambda adapter: FakeCoordinator([], adapter),
        worker_id="media-reconciler-a",
        now=NOW,
        batch_size=10,
        lease_seconds=60,
        retry_after_seconds=45,
    )

    assert len(reconciliation.retries) == 1
    retry_job_id, retry = reconciliation.retries[0]
    assert retry_job_id == failed.id
    assert retry["error_code"] == "media_runtime_unavailable"
    assert retry["retry_after_seconds"] == 45
    assert "secret" not in repr(retry)
    assert closed == [healthy.id]
    assert result["retry_scheduled"] == 1
    assert result["pending"] == 1


@pytest.mark.parametrize(
    ("batch_size", "lease_seconds"),
    [(0, 60), (101, 60), (1, 0), (1, 901)],
)
@pytest.mark.asyncio
async def test_batch_rejects_unsafe_operational_bounds(batch_size, lease_seconds):
    with pytest.raises(ValueError):
        await run_media_reconciliation_batch(
            reconciliation=FakeReconciliation([]),
            runtime_factory=FakeRuntimeFactory([]),
            coordinator_builder=lambda adapter: FakeCoordinator([], adapter),
            worker_id="media-reconciler-a",
            now=NOW,
            batch_size=batch_size,
            lease_seconds=lease_seconds,
        )


def test_reconciliation_task_and_beat_schedule_are_fail_closed(monkeypatch):
    monkeypatch.setattr(
        "app.tasks.media_tasks.settings.MEDIA_SUBMIT_ENABLED",
        False,
    )
    monkeypatch.setattr(
        "app.tasks.media_tasks.SessionLocal",
        lambda: pytest.fail("disabled task must not open the database"),
    )

    assert reconcile_media_jobs_task.run() == {
        "claimed": 0,
        "pending": 0,
        "succeeded": 0,
        "failed": 0,
        "retry_scheduled": 0,
        "status": "disabled",
    }
    beat = celery.conf.beat_schedule["reconcile-media-generation-jobs"]
    assert beat["task"] == "app.tasks.media_tasks.reconcile_media_jobs_task"
    assert 5 <= beat["schedule"] <= 60
