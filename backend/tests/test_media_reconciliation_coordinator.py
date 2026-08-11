from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.integrations.fal_media import (
    MediaOutput,
    MediaProviderError,
    MediaProviderResult,
    MediaQueueState,
    MediaQueueStatus,
)
from app.models.database import MediaGenerationJob
from app.services.media.reconcile_runtime import (
    MediaQuarantineReceipt,
    MediaReconciliationCoordinator,
)


NOW = datetime(2026, 8, 11, 13, 0, tzinfo=timezone.utc)


def claimed_job(db_session):
    job = MediaGenerationJob(
        org_id=uuid4(),
        owner_user_id=7,
        project_id=uuid4(),
        storyboard_version_id=uuid4(),
        shot_id=uuid4(),
        runtime_revision_id=uuid4(),
        idempotency_key=f"reconcile-runtime:{uuid4()}",
        input_hash="a" * 64,
        intent_hash="b" * 64,
        payload_ref="vault://media-intents/reconcile/runtime",
        mode="text_to_video",
        provider="fal",
        model_id="fal-ai/veo3/fast",
        sensitivity="internal",
        status="submitted",
        effect_state="confirmed",
        provider_request_id="fal-request-1",
        provider_state="queued",
        reserved_cost_microusd=2_500_000,
        estimate_hash="c" * 64,
        budget_period_start=NOW.date().replace(day=1),
        deadline_at=NOW.replace(tzinfo=None),
        next_reconcile_at=NOW.replace(tzinfo=None),
        reconciliation_fencing_token=4,
        reconciliation_leased_by="reconciler-a",
        reconciliation_lease_until=NOW.replace(tzinfo=None, hour=14),
    )
    db_session.add(job)
    db_session.commit()
    return job


class FakeAdapter:
    def __init__(self, calls, *, status, result=None):
        self.calls = calls
        self.status_value = status
        self.result_value = result

    async def status(self, *, model_id, request_id):
        self.calls.append(("provider.status", model_id, request_id))
        if isinstance(self.status_value, Exception):
            raise self.status_value
        return self.status_value

    async def result(self, *, model_id, request_id):
        self.calls.append(("provider.result", model_id, request_id))
        if isinstance(self.result_value, Exception):
            raise self.result_value
        return self.result_value


class FakeReconciliation:
    def __init__(self, db_session, calls):
        self.db = db_session
        self.calls = calls

    def record_pending(self, job_id, **kwargs):
        self.calls.append(("jobs.pending", kwargs["provider_state"]))
        job = self.db.get(MediaGenerationJob, job_id)
        job.provider_state = kwargs["provider_state"]
        self.db.commit()
        return job

    def record_retry(self, job_id, **kwargs):
        self.calls.append(("jobs.retry", kwargs["error_code"]))
        job = self.db.get(MediaGenerationJob, job_id)
        job.provider_state = "reconcile_retry"
        job.error_code = kwargs["error_code"]
        self.db.commit()
        return job

    def record_succeeded(self, job_id, **kwargs):
        self.calls.append(
            (
                "jobs.succeeded",
                kwargs["result_ref"],
                kwargs["actual_cost_microusd"],
            )
        )
        job = self.db.get(MediaGenerationJob, job_id)
        job.status = "succeeded"
        job.result_ref = kwargs["result_ref"]
        self.db.commit()
        return job

    def record_failed(self, job_id, **kwargs):
        self.calls.append(("jobs.failed", kwargs["error_code"]))
        job = self.db.get(MediaGenerationJob, job_id)
        job.status = "failed"
        job.error_code = kwargs["error_code"]
        self.db.commit()
        return job


class FakeIngestor:
    def __init__(self, calls, *, failure=None):
        self.calls = calls
        self.failure = failure

    async def ingest(self, *, job, outputs):
        self.calls.append(("quarantine.ingest", str(job.id), outputs))
        if self.failure is not None:
            raise self.failure
        return MediaQuarantineReceipt(
            result_ref=f"quarantine://generated/{job.id}",
            content_hash="d" * 64,
        )


class FakeCostResolver:
    def __init__(self, calls):
        self.calls = calls

    def actual_cost_microusd(self, job):
        self.calls.append(("cost.resolve", str(job.id)))
        return 2_000_000


def coordinator(db_session, calls, *, status, result=None, ingest_failure=None):
    return MediaReconciliationCoordinator(
        db_session,
        reconciliation=FakeReconciliation(db_session, calls),
        adapter=FakeAdapter(calls, status=status, result=result),
        ingestor=FakeIngestor(calls, failure=ingest_failure),
        cost_resolver=FakeCostResolver(calls),
        poll_after_seconds=15,
        retry_after_seconds=30,
    )


@pytest.mark.asyncio
async def test_pending_provider_state_only_schedules_another_safe_read(db_session):
    calls = []
    job = claimed_job(db_session)

    result = await coordinator(
        db_session,
        calls,
        status=MediaQueueStatus(state=MediaQueueState.RUNNING),
    ).reconcile_claimed(
        job.id,
        worker_id="reconciler-a",
        fencing_token=4,
        now=NOW,
    )

    assert result.status == "submitted"
    assert [call[0] for call in calls] == ["provider.status", "jobs.pending"]


@pytest.mark.asyncio
async def test_completed_result_is_ingested_before_terminal_settlement(db_session):
    calls = []
    job = claimed_job(db_session)
    provider_result = MediaProviderResult(
        outputs=[
            MediaOutput(
                url="https://v3.fal.media/files/output.mp4",
                content_type="video/mp4",
            )
        ]
    )

    result = await coordinator(
        db_session,
        calls,
        status=MediaQueueStatus(state=MediaQueueState.COMPLETED),
        result=provider_result,
    ).reconcile_claimed(
        job.id,
        worker_id="reconciler-a",
        fencing_token=4,
        now=NOW,
    )

    assert result.status == "succeeded"
    assert [call[0] for call in calls] == [
        "provider.status",
        "provider.result",
        "quarantine.ingest",
        "cost.resolve",
        "jobs.succeeded",
    ]
    assert calls[-1][1].startswith("quarantine://")
    assert "fal.media" not in result.result_ref


@pytest.mark.asyncio
async def test_read_or_ingestion_failure_is_retried_without_terminal_regression(
    db_session,
):
    calls = []
    job = claimed_job(db_session)
    failure = MediaProviderError(
        error_code="provider_timeout",
        retryable=True,
    )

    result = await coordinator(
        db_session,
        calls,
        status=failure,
    ).reconcile_claimed(
        job.id,
        worker_id="reconciler-a",
        fencing_token=4,
        now=NOW,
    )

    assert result.status == "submitted"
    assert calls[-1] == ("jobs.retry", "provider_timeout")


@pytest.mark.asyncio
async def test_provider_failure_becomes_monotonic_terminal_failure(db_session):
    calls = []
    job = claimed_job(db_session)

    result = await coordinator(
        db_session,
        calls,
        status=MediaQueueStatus(
            state=MediaQueueState.FAILED,
            error_code="runner_server_error",
        ),
    ).reconcile_claimed(
        job.id,
        worker_id="reconciler-a",
        fencing_token=4,
        now=NOW,
    )

    assert result.status == "failed"
    assert calls[-1] == ("jobs.failed", "runner_server_error")
