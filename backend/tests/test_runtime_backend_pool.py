import asyncio

import pytest

from app.services.llm.runtime_pool import RuntimeBackendPool


class Backend:
    def __init__(self, name):
        self.name = name
        self.close_count = 0

    async def complete(self, request):
        return self.name, request

    async def aclose(self):
        self.close_count += 1


@pytest.mark.asyncio
async def test_pool_reuses_backend_for_same_runtime_snapshot():
    pool = RuntimeBackendPool()
    created = []

    def build():
        backend = Backend("v1")
        created.append(backend)
        return backend

    first = pool.acquire("snapshot-v1", build)
    second = pool.acquire("snapshot-v1", build)

    assert first.backend is second.backend
    assert len(created) == 1
    await first.aclose()
    await second.aclose()
    assert created[0].close_count == 0

    await pool.aclose()
    assert created[0].close_count == 1


@pytest.mark.asyncio
async def test_hot_reload_defers_old_backend_close_until_inflight_release():
    pool = RuntimeBackendPool()
    old = Backend("v1")
    new = Backend("v2")
    old_lease = pool.acquire("snapshot-v1", lambda: old)

    new_lease = pool.acquire("snapshot-v2", lambda: new)
    await asyncio.sleep(0)

    assert new_lease.backend is new
    assert old.close_count == 0
    await old_lease.aclose()
    assert old.close_count == 1
    assert new.close_count == 0

    await new_lease.aclose()
    await pool.aclose()
    assert new.close_count == 1


@pytest.mark.asyncio
async def test_lease_release_is_idempotent_and_delegates_backend_methods():
    pool = RuntimeBackendPool()
    backend = Backend("shared")
    lease = pool.acquire("snapshot", lambda: backend)

    assert await lease.complete("request") == ("shared", "request")
    await lease.aclose()
    await lease.aclose()
    await pool.aclose()

    assert backend.close_count == 1
