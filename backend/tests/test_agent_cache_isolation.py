from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.services.agent_runtime.contracts import ExecutionPrincipal, Sensitivity
from app.services.knowledge import (
    KnowledgeRetrievalService,
    RawKnowledgeCandidate,
)
from app.services.knowledge_cache import KnowledgeCacheScope


class VersionedBackend:
    def __init__(self, candidates):
        self.candidates = candidates
        self.search_calls = 0
        self.scope = KnowledgeCacheScope(
            acl_policy_version="acl-snapshot-v1",
            index_version="index-snapshot-v1",
        )

    async def cache_scope(self, *, org_id):
        return self.scope

    async def search(self, *, namespace, query, filters, limit):
        self.search_calls += 1
        return self.candidates


class CountingACL:
    def __init__(self, allowed):
        self.allowed = allowed
        self.calls = 0

    def authorize(self, *, principal, document_id, document_version, acl_policy_version):
        self.calls += 1
        return document_id in self.allowed


class MemoryCache:
    def __init__(self):
        self.values = {}
        self.get_keys = []
        self.set_keys = []

    async def get(self, key):
        self.get_keys.append(key)
        return self.values.get(key.digest())

    async def set(self, key, candidates):
        self.set_keys.append(key)
        self.values[key.digest()] = candidates


def principal(org_id, *, user_id=7, entitlements="a" * 64):
    return ExecutionPrincipal(
        org_id=org_id,
        user_id=user_id,
        roles={"sales"},
        entitlements_hash=entitlements,
        authn_context="jwt:mfa",
    )


def candidate(org_id, document_id):
    return RawKnowledgeCandidate(
        candidate_id=f"chunk:{document_id}",
        org_id=org_id,
        document_id=document_id,
        document_version=1,
        acl_policy_version="document-acl-v1",
        index_version="document-index-v1",
        chunk_id="chunk-1",
        content="Verified MOQ is 500 units.",
        source_ref="catalog.pdf#page=1",
        authority="approved_document",
        sensitivity=Sensitivity.INTERNAL,
        valid_at=datetime.now(timezone.utc),
        score=0.9,
    )


@pytest.mark.asyncio
async def test_retrieval_cache_reauthorizes_every_hit_and_avoids_backend_repeat():
    org_id = uuid4()
    document_id = uuid4()
    backend = VersionedBackend([candidate(org_id, document_id)])
    acl = CountingACL({document_id})
    cache = MemoryCache()
    service = KnowledgeRetrievalService(backend, acl, cache=cache)

    first = await service.retrieve(
        principal=principal(org_id),
        query="MOQ",
        sensitivity=Sensitivity.INTERNAL,
    )
    second = await service.retrieve(
        principal=principal(org_id),
        query="MOQ",
        sensitivity=Sensitivity.INTERNAL,
    )

    assert first.evidence == second.evidence
    assert backend.search_calls == 1
    assert acl.calls == 2
    key = cache.set_keys[0]
    assert key.org_id == org_id
    assert key.principal_id == "7"
    assert key.entitlements_hash == "a" * 64
    assert key.sensitivity == Sensitivity.INTERNAL
    assert key.acl_policy_version == "acl-snapshot-v1"
    assert key.index_version == "index-snapshot-v1"
    assert key.query_hash != "MOQ"


@pytest.mark.asyncio
async def test_retrieval_cache_key_separates_identity_policy_and_index_versions():
    org_id = uuid4()
    document_id = uuid4()
    backend = VersionedBackend([candidate(org_id, document_id)])
    cache = MemoryCache()
    service = KnowledgeRetrievalService(
        backend,
        CountingACL({document_id}),
        cache=cache,
    )

    await service.retrieve(
        principal=principal(org_id),
        query="MOQ",
        sensitivity=Sensitivity.INTERNAL,
    )
    await service.retrieve(
        principal=principal(org_id, user_id=8),
        query="MOQ",
        sensitivity=Sensitivity.INTERNAL,
    )
    await service.retrieve(
        principal=principal(org_id, entitlements="b" * 64),
        query="MOQ",
        sensitivity=Sensitivity.INTERNAL,
    )
    backend.scope = KnowledgeCacheScope(
        acl_policy_version="acl-snapshot-v2",
        index_version="index-snapshot-v2",
    )
    await service.retrieve(
        principal=principal(org_id),
        query="MOQ",
        sensitivity=Sensitivity.INTERNAL,
    )

    assert backend.search_calls == 4
    assert len({key.digest() for key in cache.set_keys}) == 4


@pytest.mark.asyncio
async def test_confidential_retrieval_bypasses_candidate_cache():
    org_id = uuid4()
    backend = VersionedBackend([])
    cache = MemoryCache()
    service = KnowledgeRetrievalService(backend, CountingACL(set()), cache=cache)

    await service.retrieve(
        principal=principal(org_id),
        query="contract pricing",
        sensitivity=Sensitivity.CONFIDENTIAL,
    )
    await service.retrieve(
        principal=principal(org_id),
        query="contract pricing",
        sensitivity=Sensitivity.CONFIDENTIAL,
    )

    assert backend.search_calls == 2
    assert cache.get_keys == []
    assert cache.set_keys == []


@pytest.mark.asyncio
async def test_cached_cross_tenant_candidate_is_dropped_by_post_authorization():
    org_id = uuid4()
    foreign_org_id = uuid4()
    backend = VersionedBackend([])
    cache = MemoryCache()
    service = KnowledgeRetrievalService(backend, CountingACL(set()), cache=cache)

    await service.retrieve(
        principal=principal(org_id),
        query="MOQ",
        sensitivity=Sensitivity.INTERNAL,
    )
    key = cache.set_keys[0]
    cache.values[key.digest()] = [
        candidate(foreign_org_id, uuid4()).model_dump(mode="json")
    ]

    result = await service.retrieve(
        principal=principal(org_id),
        query="MOQ",
        sensitivity=Sensitivity.INTERNAL,
    )

    assert result.evidence == []
