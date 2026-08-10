from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.models.database import AgentRunEvent
from app.models.database import AIChatSession, User
from app.services.ai_chat import AIChatService
from app.services.agent_run_events import AgentRunEventService
from app.services.agent_runs import (
    AgentRunCommand,
    AgentRunService,
    RunLeaseConflict,
    RunNotFound,
)
from app.services.agent_runtime.contracts import Sensitivity


def _claimed_run(db_session, *, user_id=7):
    now = datetime.utcnow()
    run, _ = AgentRunService(db_session).create(
        AgentRunCommand(
            idempotency_key=f"agent-run:event-test:{uuid4()}",
            org_id=uuid4(),
            user_id=user_id,
            session_id=uuid4(),
            turn_id=uuid4(),
            use_case="live_reply",
            input={"content": "private input must not enter the event envelope"},
            sensitivity=Sensitivity.INTERNAL,
            generation_epoch=1,
            deadline_at=now + timedelta(minutes=5),
        )
    )
    claimed = AgentRunService(db_session).claim_one(
        run.id,
        worker_id="event-worker",
        now=now,
        lease_seconds=60,
    )
    return claimed, now


def test_events_get_monotonic_sequences_and_replay_after_cursor(db_session):
    run, now = _claimed_run(db_session)
    events = AgentRunEventService(db_session)

    started = events.append(
        run.id,
        worker_id="event-worker",
        fencing_token=run.fencing_token,
        event_type="run.started",
        data={"run_id": str(run.id)},
        now=now,
    )
    delta = events.append(
        run.id,
        worker_id="event-worker",
        fencing_token=run.fencing_token,
        event_type="message.delta",
        data={"delta": "verified"},
        now=now + timedelta(seconds=1),
    )

    assert (started.sequence, delta.sequence) == (1, 2)
    assert db_session.query(AgentRunEvent).count() == 2
    replay = events.list_for_user(run.id, user_id=run.user_id, after_sequence=1)
    assert [item.sequence for item in replay] == [2]
    assert replay[0].event_type == "message.delta"
    assert replay[0].data == {"delta": "verified"}


def test_event_append_requires_the_current_run_fence(db_session):
    run, now = _claimed_run(db_session)

    with pytest.raises(RunLeaseConflict):
        AgentRunEventService(db_session).append(
            run.id,
            worker_id="stale-worker",
            fencing_token=run.fencing_token,
            event_type="message.delta",
            data={"delta": "must not commit"},
            now=now,
        )

    assert db_session.query(AgentRunEvent).count() == 0


def test_event_replay_is_user_isolated_and_payload_bounded(db_session):
    run, now = _claimed_run(db_session)
    events = AgentRunEventService(db_session)
    events.append(
        run.id,
        worker_id="event-worker",
        fencing_token=run.fencing_token,
        event_type="run.started",
        data={"run_id": str(run.id)},
        now=now,
    )

    with pytest.raises(RunNotFound):
        events.list_for_user(run.id, user_id=run.user_id + 1)
    with pytest.raises(ValueError, match="payload"):
        events.append(
            run.id,
            worker_id="event-worker",
            fencing_token=run.fencing_token,
            event_type="message.delta",
            data={"delta": "x" * 70_000},
            now=now + timedelta(seconds=1),
        )


def test_deleting_chat_session_purges_derived_run_event_content(db_session):
    user = User(
        username="event-delete-user",
        email="event-delete@example.com",
        hashed_password="unused",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    chat = AIChatSession(
        user_id=user.id,
        title="Delete derived events",
        use_case="live_reply",
    )
    db_session.add(chat)
    db_session.commit()
    run, now = _claimed_run(db_session, user_id=user.id)
    run.session_id = chat.id
    db_session.commit()
    AgentRunEventService(db_session).append(
        run.id,
        worker_id="event-worker",
        fencing_token=run.fencing_token,
        event_type="message.delta",
        data={"delta": "buyer@example.com"},
        now=now,
    )

    AIChatService(db_session, None, concurrency=None).delete_session(
        chat.id,
        user.id,
    )

    assert db_session.query(AgentRunEvent).count() == 0
