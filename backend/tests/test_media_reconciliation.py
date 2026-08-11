from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest

from app.models.database import MediaGenerationJob
from app.models.database import (
    MediaBudgetAccount,
    MediaBudgetLedgerEntry,
    MediaGenerationAttempt,
)
from app.services.media.reconciliation import (
    MediaReconciliationLeaseConflict,
    MediaReconciliationService,
)


NOW = datetime(2026, 8, 11, 12, 0, 0)


def submitted_job(db_session, **overrides):
    values = {
        "org_id": uuid4(),
        "owner_user_id": 7,
        "project_id": uuid4(),
        "storyboard_version_id": uuid4(),
        "shot_id": uuid4(),
        "runtime_revision_id": uuid4(),
        "idempotency_key": f"media-reconcile:{uuid4()}",
        "input_hash": "a" * 64,
        "intent_hash": "b" * 64,
        "payload_ref": "vault://media-intents/reconcile/test",
        "mode": "text_to_video",
        "provider": "fal",
        "model_id": "fal-ai/veo3/fast",
        "sensitivity": "internal",
        "status": "submitted",
        "effect_state": "confirmed",
        "provider_request_id": f"fal-{uuid4().hex}",
        "reserved_cost_microusd": 1_000_000,
        "estimate_hash": "c" * 64,
        "budget_period_start": date(2026, 8, 1),
        "deadline_at": NOW - timedelta(minutes=1),
        "next_reconcile_at": NOW,
    }
    values.update(overrides)
    job = MediaGenerationJob(**values)
    db_session.add(job)
    db_session.commit()
    return job


def billable_submitted_job(db_session):
    job = submitted_job(db_session)
    db_session.add(
        MediaBudgetAccount(
            org_id=job.org_id,
            period_start=job.budget_period_start,
            limit_microusd=10_000_000,
            reserved_microusd=job.reserved_cost_microusd,
            spent_microusd=0,
        )
    )
    db_session.add(
        MediaGenerationAttempt(
            job_id=job.id,
            attempt_number=1,
            fencing_token=1,
            provider=job.provider,
            model_id=job.model_id,
            status="submitted",
            effect_state="confirmed",
            provider_request_id=job.provider_request_id,
            started_at=NOW - timedelta(minutes=2),
            submitted_at=NOW - timedelta(minutes=1),
        )
    )
    db_session.commit()
    return job


def test_only_one_reconciler_claims_due_submitted_job(db_session):
    job = submitted_job(db_session)
    service = MediaReconciliationService(db_session)

    first = service.claim_batch(
        worker_id="reconciler-a",
        now=NOW,
        limit=10,
        lease_seconds=30,
    )
    second = service.claim_batch(
        worker_id="reconciler-b",
        now=NOW,
        limit=10,
        lease_seconds=30,
    )

    assert [item.id for item in first] == [job.id]
    assert second == []
    assert first[0].reconciliation_fencing_token == 1
    assert first[0].reconciliation_leased_by == "reconciler-a"


def test_observed_queue_state_schedules_bounded_poll_and_blocks_stale_fence(
    db_session,
):
    job = submitted_job(db_session)
    service = MediaReconciliationService(db_session)
    claimed = service.claim_batch(
        worker_id="reconciler-a",
        now=NOW,
        limit=1,
        lease_seconds=30,
    )[0]
    pending = service.record_pending(
        job.id,
        worker_id="reconciler-a",
        fencing_token=claimed.reconciliation_fencing_token,
        provider_state="running",
        now=NOW + timedelta(seconds=2),
        poll_after_seconds=15,
    )

    assert pending.status == "submitted"
    assert pending.provider_state == "running"
    assert pending.reconcile_count == 1
    assert pending.last_reconciled_at == NOW + timedelta(seconds=2)
    assert pending.next_reconcile_at == NOW + timedelta(seconds=17)
    assert pending.reconciliation_leased_by is None
    assert service.claim_batch(
        worker_id="reconciler-b",
        now=NOW + timedelta(seconds=16),
        limit=1,
        lease_seconds=30,
    ) == []
    with pytest.raises(MediaReconciliationLeaseConflict):
        service.record_pending(
            job.id,
            worker_id="reconciler-a",
            fencing_token=claimed.reconciliation_fencing_token,
            provider_state="queued",
            now=NOW + timedelta(seconds=18),
            poll_after_seconds=15,
        )


def test_expired_reconcile_lease_is_reclaimed_with_new_fence(db_session):
    job = submitted_job(db_session)
    service = MediaReconciliationService(db_session)
    first = service.claim_batch(
        worker_id="reconciler-a",
        now=NOW,
        limit=1,
        lease_seconds=30,
    )[0]
    first_fencing_token = first.reconciliation_fencing_token

    reclaimed = service.claim_batch(
        worker_id="reconciler-b",
        now=NOW + timedelta(seconds=31),
        limit=1,
        lease_seconds=30,
    )[0]

    assert reclaimed.id == job.id
    assert reclaimed.status == "submitted"
    assert reclaimed.reconciliation_fencing_token == (
        first_fencing_token + 1
    )
    assert reclaimed.reconciliation_leased_by == "reconciler-b"


def test_terminal_or_not_yet_due_jobs_are_never_claimed(db_session):
    submitted_job(
        db_session,
        status="succeeded",
        completed_at=NOW,
    )
    submitted_job(
        db_session,
        next_reconcile_at=NOW + timedelta(minutes=1),
    )

    assert MediaReconciliationService(db_session).claim_batch(
        worker_id="reconciler-a",
        now=NOW,
        limit=10,
        lease_seconds=30,
    ) == []


def test_reconcile_retry_is_sanitized_and_releases_lease(db_session):
    job = submitted_job(db_session)
    service = MediaReconciliationService(db_session)
    claimed = service.claim_batch(
        worker_id="reconciler-a",
        now=NOW,
        limit=1,
        lease_seconds=30,
    )[0]

    retried = service.record_retry(
        job.id,
        worker_id="reconciler-a",
        fencing_token=claimed.reconciliation_fencing_token,
        error_code="secret response body\napi-key",
        now=NOW + timedelta(seconds=2),
        retry_after_seconds=30,
    )

    assert retried.status == "submitted"
    assert retried.provider_state == "reconcile_retry"
    assert retried.error_code == "provider_reconciliation_failed"
    assert retried.next_reconcile_at == NOW + timedelta(seconds=32)
    assert retried.reconciliation_leased_by is None


def test_failed_reconciliation_settles_once_and_blocks_stale_worker(db_session):
    job = billable_submitted_job(db_session)
    service = MediaReconciliationService(db_session)
    claimed = service.claim_batch(
        worker_id="reconciler-a",
        now=NOW,
        limit=1,
        lease_seconds=30,
    )[0]
    fence = claimed.reconciliation_fencing_token

    failed = service.record_failed(
        job.id,
        worker_id="reconciler-a",
        fencing_token=fence,
        error_code="runner_server_error",
        actual_cost_microusd=250_000,
        now=NOW + timedelta(seconds=2),
    )

    assert failed.status == "failed"
    assert failed.actual_cost_microusd == 250_000
    assert failed.attempts[-1].status == "failed"
    account = db_session.query(MediaBudgetAccount).one()
    assert account.reserved_microusd == 0
    assert account.spent_microusd == 250_000
    assert db_session.query(MediaBudgetLedgerEntry).count() == 1
    with pytest.raises(MediaReconciliationLeaseConflict):
        service.record_failed(
            job.id,
            worker_id="reconciler-a",
            fencing_token=fence,
            error_code="runner_server_error",
            actual_cost_microusd=250_000,
            now=NOW + timedelta(seconds=3),
        )


def test_successful_reconciliation_requires_quarantine_receipt_and_settles(
    db_session,
):
    job = billable_submitted_job(db_session)
    service = MediaReconciliationService(db_session)
    claimed = service.claim_batch(
        worker_id="reconciler-a",
        now=NOW,
        limit=1,
        lease_seconds=30,
    )[0]

    succeeded = service.record_succeeded(
        job.id,
        worker_id="reconciler-a",
        fencing_token=claimed.reconciliation_fencing_token,
        result_ref=f"quarantine://generated/{job.id}",
        actual_cost_microusd=2_000_000,
        now=NOW + timedelta(seconds=2),
    )

    assert succeeded.status == "succeeded"
    assert succeeded.provider_state == "completed"
    assert succeeded.result_ref.startswith("quarantine://")
    account = db_session.query(MediaBudgetAccount).one()
    assert account.reserved_microusd == 0
    assert account.spent_microusd == 2_000_000
