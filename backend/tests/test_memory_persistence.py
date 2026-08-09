from uuid import uuid4

import pytest

from app.models.database import AgentMemory, AgentMemoryPurgeJob
from app.services.agent_runtime.contracts import Sensitivity
from app.services.memory import (
    MemoryAdmissionDenied,
    MemoryEpochChanged,
    MemoryScope,
    MemoryTier,
    PersistentMemoryStore,
)


def scope(*, org_id=None, user_id=17, session_id=None):
    return MemoryScope(
        org_id=org_id or uuid4(),
        user_id=user_id,
        session_id=session_id or uuid4(),
    )


def test_memory_survives_identity_map_expiry_and_is_tenant_isolated(db_session):
    owner = scope()
    other_org = scope()
    store = PersistentMemoryStore(db_session)

    written = store.write(
        scope=owner,
        tier=MemoryTier.SESSION,
        kind="buyer_preference",
        content={"language": "German"},
        source_type="user_explicit",
        source_ref="message-17",
        sensitivity=Sensitivity.CONFIDENTIAL,
        approved=True,
    )
    db_session.expire_all()

    assert [item.id for item in PersistentMemoryStore(db_session).read(scope=owner)] == [
        written.id
    ]
    assert PersistentMemoryStore(db_session).read(scope=other_org) == []
    assert db_session.query(AgentMemory).count() == 1


def test_persistent_correction_is_atomic_and_fences_stale_context(db_session):
    owner = scope()
    store = PersistentMemoryStore(db_session)
    original = store.write(
        scope=owner,
        tier=MemoryTier.SESSION,
        kind="target_market",
        content={"country": "FR"},
        source_type="user_explicit",
        source_ref="message-1",
        sensitivity=Sensitivity.INTERNAL,
        approved=True,
    )
    previous_epoch = store.epoch(owner)

    corrected = store.correct(
        original.id,
        scope=owner,
        content={"country": "DE"},
        source_ref="message-2",
    )

    assert corrected.version == 2
    assert corrected.correction_of == original.id
    assert [item.content for item in store.read(scope=owner)] == [{"country": "DE"}]
    with pytest.raises(MemoryEpochChanged):
        store.assert_epoch(owner, previous_epoch)


def test_persistent_delete_immediately_hides_memory_and_queues_purge(db_session):
    owner = scope()
    store = PersistentMemoryStore(db_session)
    item = store.write(
        scope=owner,
        tier=MemoryTier.LONG_TERM,
        kind="approved_catalog_fact",
        content={"catalog_ref": "catalog-v7"},
        source_type="approved_document",
        source_ref="catalog-v7#page=4",
        sensitivity=Sensitivity.INTERNAL,
        approved=True,
    )

    purge = store.delete(item.id, scope=owner)

    assert store.read(scope=owner) == []
    row = db_session.query(AgentMemoryPurgeJob).one()
    assert row.id == purge.id
    assert row.memory_id == item.id
    assert row.tombstone_epoch == store.epoch(owner)
    assert set(row.targets) == {"vector", "bm25", "cache", "summary"}


@pytest.mark.parametrize(
    ("source_type", "sensitivity", "approved"),
    [
        ("model_output", Sensitivity.INTERNAL, True),
        ("tool_error", Sensitivity.INTERNAL, True),
        ("user_explicit", Sensitivity.RESTRICTED, True),
        ("approved_document", Sensitivity.INTERNAL, False),
    ],
)
def test_persistent_long_term_memory_fails_closed_before_database_write(
    db_session,
    source_type,
    sensitivity,
    approved,
):
    with pytest.raises(MemoryAdmissionDenied):
        PersistentMemoryStore(db_session).write(
            scope=scope(),
            tier=MemoryTier.LONG_TERM,
            kind="unsafe_fact",
            content={"value": "must not persist"},
            source_type=source_type,
            source_ref="unsafe-source",
            sensitivity=sensitivity,
            approved=approved,
        )

    assert db_session.query(AgentMemory).count() == 0
