import pytest

from app.models.database import AIChatSession, AgentTurn
from app.services.agent_runtime.turns import (
    AgentTurnCoordinator,
    StaleTurn,
    TurnBusy,
)


def chat_session(db_session, user_id=1):
    row = AIChatSession(user_id=user_id, title="Turn test", use_case="live_reply")
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def test_turn_start_is_idempotent_and_has_monotonic_sequence_and_fence(db_session):
    session = chat_session(db_session)
    coordinator = AgentTurnCoordinator(db_session)

    first, created = coordinator.start(
        session_id=session.id,
        user_id=session.user_id,
        idempotency_key="turn-idempotency-1",
    )
    repeated, repeated_created = coordinator.start(
        session_id=session.id,
        user_id=session.user_id,
        idempotency_key="turn-idempotency-1",
    )

    assert created is True
    assert repeated_created is False
    assert repeated.id == first.id
    assert first.sequence == 1
    assert first.generation_epoch == 1
    assert first.status == "running"


def test_default_turn_policy_rejects_a_second_active_generation(db_session):
    session = chat_session(db_session)
    coordinator = AgentTurnCoordinator(db_session)
    coordinator.start(
        session_id=session.id,
        user_id=session.user_id,
        idempotency_key="turn-active-1",
    )

    with pytest.raises(TurnBusy):
        coordinator.start(
            session_id=session.id,
            user_id=session.user_id,
            idempotency_key="turn-active-2",
        )

    assert db_session.query(AgentTurn).count() == 1


def test_cancel_previous_fences_old_worker_before_new_turn_can_commit(db_session):
    session = chat_session(db_session)
    coordinator = AgentTurnCoordinator(db_session)
    first, _ = coordinator.start(
        session_id=session.id,
        user_id=session.user_id,
        idempotency_key="turn-cancel-1",
    )
    second, _ = coordinator.start(
        session_id=session.id,
        user_id=session.user_id,
        idempotency_key="turn-cancel-2",
        policy="cancel_previous",
    )

    assert first.status == "superseded"
    assert second.sequence == 2
    assert second.generation_epoch == 2
    with pytest.raises(StaleTurn):
        coordinator.complete(first.id, generation_epoch=first.generation_epoch)

    completed = coordinator.complete(
        second.id,
        generation_epoch=second.generation_epoch,
    )
    assert completed.status == "completed"
