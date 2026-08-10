from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.models.database import AgentRun
from app.services.agent_runs import (
    AgentRunCommand,
    AgentRunService,
    RunLeaseConflict,
)
from app.services.agent_runtime.contracts import Sensitivity
from app.services.idempotency import IdempotencyConflict
from app.services.idempotency import canonical_hash


def command(**overrides):
    values = {
        "idempotency_key": "agent-run:org-1:request-1",
        "org_id": uuid4(),
        "user_id": 7,
        "session_id": uuid4(),
        "turn_id": uuid4(),
        "use_case": "live_reply",
        "input": {"message": "Prepare a verified response"},
        "sensitivity": Sensitivity.INTERNAL,
        "generation_epoch": 3,
        "deadline_at": datetime(2026, 8, 10, 12, 5, 0),
    }
    values.update(overrides)
    return AgentRunCommand(**values)


def test_create_is_idempotent_without_persisting_raw_input(db_session):
    service = AgentRunService(db_session)
    requested = command(input={"message": "private buyer request"})

    first, created = service.create(requested)
    replay, replay_created = service.create(requested)

    assert created is True
    assert replay_created is False
    assert replay.id == first.id
    assert db_session.query(AgentRun).count() == 1
    assert "private buyer request" not in repr(first.__dict__)

    with pytest.raises(IdempotencyConflict):
        service.create(requested.model_copy(update={"input": {"message": "changed"}}))


def test_command_without_profile_preserves_pre_routing_idempotency_hash():
    requested = command(execution_profile=None)
    legacy_hash = canonical_hash(
        {
            "org_id": str(requested.org_id),
            "user_id": requested.user_id,
            "session_id": str(requested.session_id),
            "turn_id": str(requested.turn_id),
            "use_case": requested.use_case,
            "input": requested.input,
            "sensitivity": requested.sensitivity.value,
            "generation_epoch": requested.generation_epoch,
            "deadline_at": requested.deadline_at.isoformat(),
        }
    )

    assert AgentRunService._command_hash(requested) == legacy_hash


def test_only_one_worker_claims_and_heartbeat_requires_current_fence(db_session):
    service = AgentRunService(db_session)
    run, _ = service.create(command())
    now = datetime(2026, 8, 10, 12, 0, 0)

    claimed = service.claim_batch(
        worker_id="worker-a",
        now=now,
        limit=5,
        lease_seconds=60,
    )
    second = service.claim_batch(
        worker_id="worker-b",
        now=now,
        limit=5,
        lease_seconds=60,
    )

    assert [item.id for item in claimed] == [run.id]
    assert second == []
    assert claimed[0].fencing_token == 1
    service.heartbeat(
        run.id,
        worker_id="worker-a",
        fencing_token=1,
        now=now + timedelta(seconds=20),
        lease_seconds=60,
    )
    with pytest.raises(RunLeaseConflict):
        service.heartbeat(
            run.id,
            worker_id="worker-b",
            fencing_token=1,
            now=now + timedelta(seconds=21),
            lease_seconds=60,
        )


def test_claim_one_leases_only_the_requested_run(db_session):
    service = AgentRunService(db_session)
    first, _ = service.create(command(idempotency_key="agent-run:claim-one:first"))
    second, _ = service.create(command(idempotency_key="agent-run:claim-one:second"))
    now = datetime(2026, 8, 10, 12, 0, 0)

    claimed = service.claim_one(
        second.id,
        worker_id="inline-api-worker",
        now=now,
        lease_seconds=60,
    )

    assert claimed.id == second.id
    assert claimed.status == "running"
    assert claimed.fencing_token == 1
    assert db_session.get(AgentRun, first.id).status == "queued"


def test_expired_safe_run_is_reclaimed_and_stale_worker_cannot_commit(db_session):
    service = AgentRunService(db_session)
    run, _ = service.create(command())
    first_claim = service.claim_batch(
        worker_id="worker-a",
        now=datetime(2026, 8, 10, 12, 0, 0),
        limit=1,
        lease_seconds=30,
    )[0]
    old_fence = first_claim.fencing_token

    reclaimed = service.claim_batch(
        worker_id="worker-b",
        now=datetime(2026, 8, 10, 12, 0, 31),
        limit=1,
        lease_seconds=30,
    )[0]

    assert reclaimed.id == run.id
    assert reclaimed.fencing_token == old_fence + 1
    with pytest.raises(RunLeaseConflict):
        service.complete(
            run.id,
            worker_id="worker-a",
            fencing_token=old_fence,
            now=datetime(2026, 8, 10, 12, 0, 32),
        )
    completed = service.complete(
        run.id,
        worker_id="worker-b",
        fencing_token=reclaimed.fencing_token,
        now=datetime(2026, 8, 10, 12, 0, 33),
    )
    assert completed.status == "completed"


def test_current_worker_can_requeue_safe_run_for_retry(db_session):
    service = AgentRunService(db_session)
    run, _ = service.create(command())
    claimed = service.claim_one(
        run.id,
        worker_id="worker-a",
        now=datetime(2026, 8, 10, 12, 0, 0),
        lease_seconds=60,
    )
    first_fencing_token = claimed.fencing_token

    requeued = service.requeue(
        run.id,
        worker_id="worker-a",
        fencing_token=first_fencing_token,
        now=datetime(2026, 8, 10, 12, 0, 10),
        error_code="agent_capacity_exhausted",
    )

    assert requeued.status == "queued"
    assert requeued.error_code == "agent_capacity_exhausted"
    assert requeued.leased_by is None
    with pytest.raises(RunLeaseConflict):
        service.complete(
            run.id,
            worker_id="worker-a",
            fencing_token=first_fencing_token,
            now=datetime(2026, 8, 10, 12, 0, 11),
        )
    reclaimed = service.claim_one(
        run.id,
        worker_id="worker-b",
        now=datetime(2026, 8, 10, 12, 0, 12),
        lease_seconds=60,
    )
    assert reclaimed.fencing_token == first_fencing_token + 1


def test_expired_run_after_effect_started_becomes_unknown_not_retried(db_session):
    service = AgentRunService(db_session)
    run, _ = service.create(command())
    claimed = service.claim_batch(
        worker_id="worker-a",
        now=datetime(2026, 8, 10, 12, 0, 0),
        limit=1,
        lease_seconds=30,
    )[0]
    service.mark_effect_started(
        run.id,
        worker_id="worker-a",
        fencing_token=claimed.fencing_token,
        now=datetime(2026, 8, 10, 12, 0, 10),
    )

    recovered = service.recover_expired(
        now=datetime(2026, 8, 10, 12, 0, 31)
    )

    assert [item.id for item in recovered] == [run.id]
    assert recovered[0].status == "unknown"
    assert recovered[0].error_code == "lease_expired_after_effect_started"
    assert service.claim_batch(
        worker_id="worker-b",
        now=datetime(2026, 8, 10, 12, 0, 32),
        limit=1,
        lease_seconds=30,
    ) == []


def test_deadline_cancels_safe_run_but_preserves_unknown_effect_state(db_session):
    service = AgentRunService(db_session)
    safe, _ = service.create(
        command(
            idempotency_key="agent-run:deadline:safe",
            deadline_at=datetime(2026, 8, 10, 12, 0, 5),
        )
    )
    effect, _ = service.create(
        command(
            idempotency_key="agent-run:deadline:effect",
            deadline_at=datetime(2026, 8, 10, 12, 0, 5),
        )
    )
    claimed = service.claim_batch(
        worker_id="worker-a",
        now=datetime(2026, 8, 10, 12, 0, 0),
        limit=2,
        lease_seconds=60,
    )
    effect_claim = next(item for item in claimed if item.id == effect.id)
    service.mark_effect_started(
        effect.id,
        worker_id="worker-a",
        fencing_token=effect_claim.fencing_token,
        now=datetime(2026, 8, 10, 12, 0, 2),
    )

    service.recover_expired(now=datetime(2026, 8, 10, 12, 0, 6))

    assert db_session.get(AgentRun, safe.id).status == "cancelled"
    assert db_session.get(AgentRun, effect.id).status == "unknown"
