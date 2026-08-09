from datetime import datetime, timedelta

from app.models.database import AgentRun
from app.services.agent_runs import AgentRunService
from app.tasks import task_functions
from app.tasks.celery_worker import celery

from test_agent_run_recovery import command


def test_agent_run_recovery_is_registered_with_celery_beat():
    schedule = celery.conf.beat_schedule["recover-agent-runs"]

    assert schedule["task"] == (
        "app.tasks.task_functions.sweep_agent_runs_task"
    )
    assert 0 < schedule["schedule"] <= 30


def test_agent_run_sweeper_requeues_expired_safe_work(
    db_session,
    monkeypatch,
):
    service = AgentRunService(db_session)
    now = datetime.utcnow()
    run, _ = service.create(command(deadline_at=now + timedelta(hours=1)))
    service.claim_batch(
        worker_id="terminated-worker",
        now=now - timedelta(minutes=2),
        limit=1,
        lease_seconds=30,
    )
    monkeypatch.setattr(task_functions, "SessionLocal", lambda: db_session)

    result = task_functions.sweep_agent_runs_task.run()

    recovered = db_session.get(AgentRun, run.id)
    assert result == {"requeued": 1, "cancelled": 0, "unknown": 0}
    assert recovered.status == "queued"
    assert recovered.leased_by is None


def test_agent_run_sweeper_reports_unknown_effect_without_retry(
    db_session,
    monkeypatch,
):
    service = AgentRunService(db_session)
    now = datetime.utcnow()
    run, _ = service.create(
        command(
            idempotency_key="agent-run:sweep:effect",
            deadline_at=now + timedelta(hours=1),
        )
    )
    claimed = service.claim_batch(
        worker_id="terminated-worker",
        now=now - timedelta(minutes=2),
        limit=1,
        lease_seconds=30,
    )[0]
    service.mark_effect_started(
        run.id,
        worker_id="terminated-worker",
        fencing_token=claimed.fencing_token,
        now=now - timedelta(seconds=100),
    )
    monkeypatch.setattr(task_functions, "SessionLocal", lambda: db_session)

    result = task_functions.sweep_agent_runs_task.run()

    recovered = db_session.get(AgentRun, run.id)
    assert result == {"requeued": 0, "cancelled": 0, "unknown": 1}
    assert recovered.status == "unknown"
    assert recovered.error_code == "lease_expired_after_effect_started"
