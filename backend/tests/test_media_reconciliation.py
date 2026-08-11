from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest

from app.models.database import MediaGenerationJob
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
