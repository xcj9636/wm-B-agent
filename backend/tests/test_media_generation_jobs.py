from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest

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
from app.services.idempotency import IdempotencyConflict
from app.services.media.jobs import (
    MediaBudgetExceeded,
    MediaGenerationJobCommand,
    MediaGenerationJobService,
    MediaJobLeaseConflict,
)
from app.services.media.runtime import MediaWorkflowMode


NOW = datetime(2026, 8, 11, 10, 0, 0)


def install_runtime(db_session, *, org_id, user_id=7):
    revision = MediaRuntimeRevision(
        org_id=org_id,
        revision=1,
        provider="fal",
        enabled_modes=[MediaWorkflowMode.TEXT_TO_VIDEO.value],
        model_aliases={
            MediaWorkflowMode.TEXT_TO_VIDEO.value: "fal-ai/veo3/fast"
        },
        capability_snapshot={"provider": "fal", "schema_version": "test"},
        capability_snapshot_hash="a" * 64,
        created_by_user_id=user_id,
    )
    db_session.add(revision)
    db_session.flush()
    db_session.add(
        MediaRuntimeActivation(
            org_id=org_id,
            active_revision_id=revision.id,
            activated_by_user_id=user_id,
        )
    )
    db_session.add(
        MediaBudgetAccount(
            org_id=org_id,
            period_start=date(2026, 8, 1),
            limit_microusd=10_000_000,
            reserved_microusd=0,
            spent_microusd=0,
        )
    )
    db_session.commit()
    return revision


def command(*, org_id, runtime_revision_id, **overrides):
    values = {
        "idempotency_key": "media-job:project-1:shot-1:v1",
        "org_id": org_id,
        "owner_user_id": 7,
        "project_id": uuid4(),
        "storyboard_version_id": uuid4(),
        "shot_id": uuid4(),
        "runtime_revision_id": runtime_revision_id,
        "mode": MediaWorkflowMode.TEXT_TO_VIDEO,
        "model_id": "fal-ai/veo3/fast",
        "intent_hash": "b" * 64,
        "payload_ref": "vault://media-intents/project-1/shot-1/v1",
        "sensitivity": Sensitivity.INTERNAL,
        "estimated_cost_microusd": 2_500_000,
        "estimate_hash": "c" * 64,
        "deadline_at": NOW + timedelta(hours=1),
    }
    values.update(overrides)
    return MediaGenerationJobCommand(**values)


def test_create_pins_runtime_reserves_budget_and_is_idempotent(db_session):
    org_id = uuid4()
    runtime = install_runtime(db_session, org_id=org_id)
    service = MediaGenerationJobService(db_session)
    requested = command(org_id=org_id, runtime_revision_id=runtime.id)

    first, created = service.create(requested, now=NOW)
    replay, replay_created = service.create(requested, now=NOW)

    assert created is True
    assert replay_created is False
    assert replay.id == first.id
    assert first.runtime_revision_id == runtime.id
    assert first.model_id == "fal-ai/veo3/fast"
    assert first.status == "queued"
    assert first.reserved_cost_microusd == 2_500_000
    assert "private prompt" not in repr(first.__dict__)
    account = db_session.query(MediaBudgetAccount).one()
    assert account.reserved_microusd == 2_500_000
    assert db_session.query(MediaBudgetLedgerEntry).count() == 1
    assert [event.event_type for event in first.events] == ["job.created"]

    with pytest.raises(IdempotencyConflict):
        service.create(
            requested.model_copy(update={"intent_hash": "d" * 64}),
            now=NOW,
        )


def test_create_fails_closed_for_budget_runtime_and_model_mismatch(db_session):
    org_id = uuid4()
    runtime = install_runtime(db_session, org_id=org_id)
    service = MediaGenerationJobService(db_session)

    with pytest.raises(MediaBudgetExceeded):
        service.create(
            command(
                org_id=org_id,
                runtime_revision_id=runtime.id,
                estimated_cost_microusd=10_000_001,
            ),
            now=NOW,
        )

    with pytest.raises(ValueError, match="active runtime"):
        service.create(
            command(org_id=org_id, runtime_revision_id=uuid4()),
            now=NOW,
        )

    with pytest.raises(ValueError, match="model alias"):
        service.create(
            command(
                org_id=org_id,
                runtime_revision_id=runtime.id,
                model_id="fal-ai/unapproved-model",
            ),
            now=NOW,
        )

    assert db_session.query(MediaGenerationJob).count() == 0
    assert db_session.query(MediaBudgetLedgerEntry).count() == 0


def test_fenced_submission_state_never_retries_an_unknown_effect(db_session):
    org_id = uuid4()
    runtime = install_runtime(db_session, org_id=org_id)
    service = MediaGenerationJobService(db_session)
    job, _ = service.create(
        command(org_id=org_id, runtime_revision_id=runtime.id),
        now=NOW,
    )

    claimed = service.claim_one(
        job.id,
        worker_id="media-worker-a",
        now=NOW,
        lease_seconds=30,
    )
    attempt = service.begin_submission(
        job.id,
        worker_id="media-worker-a",
        fencing_token=claimed.fencing_token,
        now=NOW + timedelta(seconds=5),
    )
    assert attempt.attempt_number == 1
    assert attempt.status == "submitting"
    recovered = service.recover_expired(now=NOW + timedelta(seconds=31))

    assert [item.id for item in recovered] == [job.id]
    assert recovered[0].status == "submission_unknown"
    assert recovered[0].effect_state == "unknown"
    assert service.claim_batch(
        worker_id="media-worker-b",
        now=NOW + timedelta(seconds=32),
        limit=10,
        lease_seconds=30,
    ) == []
    with pytest.raises(MediaJobLeaseConflict):
        service.record_submitted(
            job.id,
            worker_id="media-worker-a",
            fencing_token=claimed.fencing_token,
            provider_request_id="req-too-late",
            now=NOW + timedelta(seconds=32),
        )


def test_provider_receipt_is_unique_and_terminal_result_is_monotonic(db_session):
    org_id = uuid4()
    runtime = install_runtime(db_session, org_id=org_id)
    service = MediaGenerationJobService(db_session)
    first, _ = service.create(
        command(org_id=org_id, runtime_revision_id=runtime.id),
        now=NOW,
    )
    claim = service.claim_one(
        first.id,
        worker_id="worker-a",
        now=NOW,
        lease_seconds=60,
    )
    service.begin_submission(
        first.id,
        worker_id="worker-a",
        fencing_token=claim.fencing_token,
        now=NOW + timedelta(seconds=1),
    )
    submitted = service.record_submitted(
        first.id,
        worker_id="worker-a",
        fencing_token=claim.fencing_token,
        provider_request_id="fal-request-1",
        now=NOW + timedelta(seconds=2),
    )

    assert submitted.status == "submitted"
    assert submitted.effect_state == "confirmed"
    finished = service.record_succeeded(
        first.id,
        provider_request_id="fal-request-1",
        result_ref="quarantine://fal-request-1/result.json",
        actual_cost_microusd=2_000_000,
        now=NOW + timedelta(minutes=3),
    )
    duplicate = service.record_succeeded(
        first.id,
        provider_request_id="fal-request-1",
        result_ref="quarantine://fal-request-1/result.json",
        actual_cost_microusd=2_000_000,
        now=NOW + timedelta(minutes=4),
    )

    assert finished.status == "succeeded"
    assert duplicate.status == "succeeded"
    assert finished.result_ref.startswith("quarantine://")
    account = db_session.query(MediaBudgetAccount).one()
    assert account.reserved_microusd == 0
    assert account.spent_microusd == 2_000_000
    assert db_session.query(MediaBudgetLedgerEntry).count() == 2
    assert db_session.query(MediaGenerationAttempt).count() == 1
    assert [
        event.sequence
        for event in db_session.query(MediaGenerationEvent)
        .filter(MediaGenerationEvent.job_id == first.id)
        .order_by(MediaGenerationEvent.sequence)
    ] == [1, 2, 3, 4, 5]


def test_cancelling_before_submission_releases_budget_exactly_once(db_session):
    org_id = uuid4()
    runtime = install_runtime(db_session, org_id=org_id)
    service = MediaGenerationJobService(db_session)
    job, _ = service.create(
        command(org_id=org_id, runtime_revision_id=runtime.id),
        now=NOW,
    )

    cancelled = service.cancel(
        job.id,
        requested_by_user_id=7,
        now=NOW + timedelta(seconds=5),
    )
    replay = service.cancel(
        job.id,
        requested_by_user_id=7,
        now=NOW + timedelta(seconds=6),
    )

    assert cancelled.status == "cancelled"
    assert replay.status == "cancelled"
    account = db_session.query(MediaBudgetAccount).one()
    assert account.reserved_microusd == 0
    entries = db_session.query(MediaBudgetLedgerEntry).all()
    assert [entry.entry_type for entry in entries] == ["reservation", "release"]


def test_fenced_pre_submission_failure_releases_budget_without_attempt(db_session):
    org_id = uuid4()
    runtime = install_runtime(db_session, org_id=org_id)
    service = MediaGenerationJobService(db_session)
    job, _ = service.create(
        command(org_id=org_id, runtime_revision_id=runtime.id),
        now=NOW,
    )
    claim = service.claim_one(
        job.id,
        worker_id="worker-a",
        now=NOW,
        lease_seconds=60,
    )

    failed = service.fail_before_submission(
        job.id,
        worker_id="worker-a",
        fencing_token=claim.fencing_token,
        error_code="media_policy_denied",
        now=NOW + timedelta(seconds=1),
    )

    assert failed.status == "failed"
    assert failed.effect_state == "none"
    assert failed.error_code == "media_policy_denied"
    assert failed.completed_at == NOW + timedelta(seconds=1)
    assert failed.leased_by is None
    assert db_session.query(MediaGenerationAttempt).count() == 0
    account = db_session.query(MediaBudgetAccount).one()
    assert account.reserved_microusd == 0
    assert [
        entry.entry_type
        for entry in db_session.query(MediaBudgetLedgerEntry).order_by(
            MediaBudgetLedgerEntry.created_at
        )
    ] == ["reservation", "release"]
    assert failed.events[-1].event_type == "job.failed"
    assert failed.events[-1].data_json == {"error_code": "media_policy_denied"}


def test_pre_submission_failure_rejects_stale_or_started_effect(db_session):
    org_id = uuid4()
    runtime = install_runtime(db_session, org_id=org_id)
    service = MediaGenerationJobService(db_session)
    job, _ = service.create(
        command(org_id=org_id, runtime_revision_id=runtime.id),
        now=NOW,
    )
    claim = service.claim_one(
        job.id,
        worker_id="worker-a",
        now=NOW,
        lease_seconds=60,
    )

    with pytest.raises(MediaJobLeaseConflict):
        service.fail_before_submission(
            job.id,
            worker_id="worker-b",
            fencing_token=claim.fencing_token,
            error_code="media_policy_denied",
            now=NOW + timedelta(seconds=1),
        )

    service.begin_submission(
        job.id,
        worker_id="worker-a",
        fencing_token=claim.fencing_token,
        now=NOW + timedelta(seconds=2),
    )
    with pytest.raises(MediaJobLeaseConflict):
        service.fail_before_submission(
            job.id,
            worker_id="worker-a",
            fencing_token=claim.fencing_token,
            error_code="media_policy_denied",
            now=NOW + timedelta(seconds=3),
        )
