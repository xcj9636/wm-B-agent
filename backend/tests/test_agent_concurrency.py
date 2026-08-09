import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.services.agent_concurrency import (
    ConcurrencyLeaseLost,
    ConcurrencyLimitExceeded,
    ConcurrencyLimits,
    ConcurrencyRequest,
    ConcurrencyUnavailable,
    DistributedConcurrencyLimiter,
)
from app.services.agent_execution import (
    DeadlineExceeded,
    ExecutionBudget,
    FanoutTask,
    ReadOnlyFanout,
    UnsafeParallelism,
)
from app.services.tool_runtime import ToolRisk


@pytest.mark.asyncio
async def test_read_fanout_enforces_parallelism_limit_and_preserves_order():
    active = 0
    observed_max = 0
    lock = asyncio.Lock()

    async def operation(value: int) -> int:
        nonlocal active, observed_max
        async with lock:
            active += 1
            observed_max = max(observed_max, active)
        await asyncio.sleep(0.01)
        async with lock:
            active -= 1
        return value

    tasks = [
        FanoutTask(
            name=f"lookup-{index}",
            risk=ToolRisk.READ,
            operation=lambda index=index: operation(index),
        )
        for index in range(8)
    ]
    results = await ReadOnlyFanout().run(
        tasks,
        ExecutionBudget(
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=2),
            max_parallel=3,
        ),
    )

    assert results == list(range(8))
    assert observed_max == 3


@pytest.mark.asyncio
async def test_write_and_destructive_tasks_are_never_parallelized():
    async def operation():
        return "should not run"

    for risk in (ToolRisk.WRITE, ToolRisk.DESTRUCTIVE):
        with pytest.raises(UnsafeParallelism):
            await ReadOnlyFanout().run(
                [FanoutTask(name="unsafe", risk=risk, operation=operation)],
                ExecutionBudget(
                    deadline_at=datetime.now(timezone.utc)
                    + timedelta(seconds=1)
                ),
            )


@pytest.mark.asyncio
async def test_deadline_cancels_outstanding_tasks_quickly():
    cancelled = asyncio.Event()

    async def slow_operation():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    started = asyncio.get_running_loop().time()
    with pytest.raises(DeadlineExceeded):
        await ReadOnlyFanout().run(
            [
                FanoutTask(
                    name="slow",
                    risk=ToolRisk.READ,
                    operation=slow_operation,
                )
            ],
            ExecutionBudget(
                deadline_at=datetime.now(timezone.utc)
                + timedelta(milliseconds=30)
            ),
        )

    assert cancelled.is_set()
    assert asyncio.get_running_loop().time() - started < 0.5


@pytest.mark.asyncio
async def test_failure_cancels_sibling_reads():
    sibling_cancelled = asyncio.Event()

    async def fail():
        await asyncio.sleep(0)
        raise RuntimeError("lookup failed")

    async def sibling():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            sibling_cancelled.set()
            raise

    with pytest.raises(RuntimeError, match="lookup failed"):
        await ReadOnlyFanout().run(
            [
                FanoutTask(name="fail", risk=ToolRisk.READ, operation=fail),
                FanoutTask(
                    name="sibling",
                    risk=ToolRisk.READ,
                    operation=sibling,
                ),
            ],
            ExecutionBudget(
                deadline_at=datetime.now(timezone.utc) + timedelta(seconds=1)
            ),
        )

    assert sibling_cancelled.is_set()


class ScriptRedis:
    """Small Redis script harness; production Lua remains the system under test."""

    def __init__(self):
        self.values = {}
        self.calls = []
        self.fail = False

    async def eval(self, script, numkeys, *values):
        if self.fail:
            raise RedisConnectionError("redis unavailable")
        keys = list(values[:numkeys])
        args = list(values[numkeys:])
        self.calls.append((script, keys, args))
        if "AGENT_CONCURRENCY_ACQUIRE" in script:
            return self._acquire(keys, args)
        if "AGENT_CONCURRENCY_RENEW" in script:
            return self._renew(keys, args)
        if "AGENT_CONCURRENCY_RELEASE" in script:
            return self._release(keys, args)
        raise AssertionError("Unknown Lua script")

    def _acquire(self, keys, args):
        lease_id, now_ms, expires_ms, _, *limits = args
        now_ms = int(now_ms)
        for key in keys:
            self._purge(key, now_ms)
        for index, (key, limit) in enumerate(zip(keys, limits), start=1):
            if len(self.values.get(key, {})) >= int(limit):
                return -index
        for key in keys:
            self.values.setdefault(key, {})[lease_id] = int(expires_ms)
        return 1

    def _renew(self, keys, args):
        lease_id, now_ms, expires_ms, _ = args
        now_ms = int(now_ms)
        for key in keys:
            self._purge(key, now_ms)
            if lease_id not in self.values.get(key, {}):
                return 0
        for key in keys:
            self.values[key][lease_id] = int(expires_ms)
        return 1

    def _release(self, keys, args):
        lease_id = args[0]
        removed = 0
        for key in keys:
            removed += self.values.get(key, {}).pop(lease_id, None) is not None
        return removed

    def _purge(self, key, now_ms):
        current = self.values.get(key, {})
        self.values[key] = {
            member: score for member, score in current.items() if score > now_ms
        }


def request(**overrides):
    values = {
        "org_id": uuid4(),
        "user_id": 7,
        "provider_id": "approved-provider/private",
        "tool_name": "crm.lookup-sensitive",
    }
    values.update(overrides)
    return ConcurrencyRequest(**values)


def limiter(redis):
    return DistributedConcurrencyLimiter(
        redis,
        limits=ConcurrencyLimits(
            global_limit=2,
            org_limit=1,
            user_limit=1,
            provider_limit=1,
            tool_limit=1,
        ),
        key_prefix="test:agent:concurrency",
    )


@pytest.mark.asyncio
async def test_acquire_is_atomic_across_all_concurrency_scopes():
    redis = ScriptRedis()
    service = limiter(redis)
    now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    first_request = request()
    first = await service.acquire(first_request, now=now, lease_seconds=30)
    blocked_request = request(
        org_id=uuid4(),
        user_id=9,
        provider_id=first_request.provider_id,
        tool_name="different-tool",
    )

    with pytest.raises(ConcurrencyLimitExceeded) as blocked:
        await service.acquire(blocked_request, now=now, lease_seconds=30)

    assert blocked.value.scope == "provider"
    await service.release(first)
    replacement = await service.acquire(
        blocked_request,
        now=now,
        lease_seconds=30,
    )
    assert replacement.org_id == blocked_request.org_id


@pytest.mark.asyncio
async def test_expired_leases_release_capacity_without_process_cleanup():
    redis = ScriptRedis()
    service = limiter(redis)
    now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    first_request = request()
    await service.acquire(first_request, now=now, lease_seconds=10)

    replacement = await service.acquire(
        request(
            org_id=first_request.org_id,
            user_id=first_request.user_id,
            provider_id=first_request.provider_id,
            tool_name=first_request.tool_name,
        ),
        now=now + timedelta(seconds=11),
        lease_seconds=10,
    )

    assert replacement.expires_at == now + timedelta(seconds=21)


@pytest.mark.asyncio
async def test_renew_requires_the_lease_to_exist_in_every_scope():
    redis = ScriptRedis()
    service = limiter(redis)
    now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    lease = await service.acquire(request(), now=now, lease_seconds=30)
    _, keys, _ = redis.calls[-1]
    redis.values[keys[-1]].pop(str(lease.lease_id))

    with pytest.raises(ConcurrencyLeaseLost):
        await service.renew(
            lease,
            now=now + timedelta(seconds=5),
            lease_seconds=30,
        )


@pytest.mark.asyncio
async def test_release_is_idempotent():
    redis = ScriptRedis()
    service = limiter(redis)
    lease = await service.acquire(
        request(),
        now=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
        lease_seconds=30,
    )

    assert await service.release(lease) is True
    assert await service.release(lease) is False


@pytest.mark.asyncio
async def test_redis_failure_is_fail_closed_for_acquire_renew_and_release():
    redis = ScriptRedis()
    service = limiter(redis)
    now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    lease = await service.acquire(request(), now=now, lease_seconds=30)
    redis.fail = True

    with pytest.raises(ConcurrencyUnavailable):
        await service.acquire(request(), now=now, lease_seconds=30)
    with pytest.raises(ConcurrencyUnavailable):
        await service.renew(lease, now=now, lease_seconds=30)
    with pytest.raises(ConcurrencyUnavailable):
        await service.release(lease)


@pytest.mark.asyncio
async def test_redis_keys_hash_provider_and_tool_identifiers():
    redis = ScriptRedis()
    service = limiter(redis)
    requested = request()

    await service.acquire(
        requested,
        now=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
        lease_seconds=30,
    )

    _, keys, _ = redis.calls[-1]
    serialized_keys = " ".join(keys)
    assert requested.provider_id not in serialized_keys
    assert requested.tool_name not in serialized_keys
    assert all(key.startswith("test:agent:concurrency:") for key in keys)
    assert all("{agent-concurrency}" in key for key in keys)
