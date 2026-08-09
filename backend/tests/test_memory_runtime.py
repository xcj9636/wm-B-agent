from uuid import uuid4

import pytest

from app.services.agent_runtime.contracts import Sensitivity
from app.services.memory import (
    MemoryAdmissionPolicy,
    MemoryEpochChanged,
    MemoryScope,
    MemoryStore,
    MemoryTier,
)


def scope(org_id=None, user_id=7, session_id=None):
    return MemoryScope(
        org_id=org_id or uuid4(),
        user_id=user_id,
        session_id=session_id or uuid4(),
    )


def test_memory_correction_immediately_invalidates_old_version_and_epoch():
    store = MemoryStore()
    owner = scope()
    original = store.write(
        scope=owner,
        tier=MemoryTier.SESSION,
        kind="preference",
        content={"language": "English"},
        source_type="user_explicit",
        source_ref="message-1",
        sensitivity=Sensitivity.INTERNAL,
    )
    read_epoch = store.epoch(owner)

    corrected = store.correct(
        original.id,
        scope=owner,
        content={"language": "German"},
        source_ref="message-2",
    )

    assert corrected.version == 2
    assert corrected.correction_of == original.id
    assert store.epoch(owner) == read_epoch + 1
    assert [item.content for item in store.read(scope=owner)] == [
        {"language": "German"}
    ]
    with pytest.raises(MemoryEpochChanged):
        store.assert_epoch(owner, read_epoch)


def test_memory_delete_is_logically_immediate_and_creates_purge_job():
    store = MemoryStore()
    owner = scope()
    item = store.write(
        scope=owner,
        tier=MemoryTier.LONG_TERM,
        kind="approved_policy",
        content={"moq_source": "erp-policy-7"},
        source_type="approved_document",
        source_ref="policy-7",
        sensitivity=Sensitivity.INTERNAL,
    )

    purge = store.delete(item.id, scope=owner)

    assert store.read(scope=owner) == []
    assert purge.status == "pending"
    assert set(purge.targets) == {"vector", "bm25", "cache", "summary"}
    assert purge.tombstone_epoch == store.epoch(owner)


def test_memory_scope_prevents_cross_organization_reads():
    store = MemoryStore()
    first = scope()
    second = scope()
    store.write(
        scope=first,
        tier=MemoryTier.SESSION,
        kind="goal",
        content={"market": "DE"},
        source_type="user_explicit",
        source_ref="message-1",
        sensitivity=Sensitivity.INTERNAL,
    )

    assert store.read(scope=second) == []


@pytest.mark.parametrize(
    ("source_type", "sensitivity"),
    [
        ("model_output", Sensitivity.INTERNAL),
        ("tool_error", Sensitivity.INTERNAL),
        ("user_explicit", Sensitivity.RESTRICTED),
    ],
)
def test_long_term_memory_admission_rejects_untrusted_or_restricted_sources(
    source_type,
    sensitivity,
):
    policy = MemoryAdmissionPolicy()

    assert policy.allow_long_term(
        source_type=source_type,
        sensitivity=sensitivity,
        approved=False,
    ) is False


def test_long_term_memory_admission_accepts_approved_authoritative_fact():
    assert MemoryAdmissionPolicy().allow_long_term(
        source_type="authoritative_connector",
        sensitivity=Sensitivity.CONFIDENTIAL,
        approved=True,
    ) is True
