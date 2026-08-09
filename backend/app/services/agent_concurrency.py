"""Redis-backed atomic concurrency leases for distributed agent execution."""

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Protocol, Tuple
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError


_ACQUIRE_SCRIPT = """
-- AGENT_CONCURRENCY_ACQUIRE
local lease_id = ARGV[1]
local now_ms = tonumber(ARGV[2])
local expires_ms = tonumber(ARGV[3])
local ttl_ms = tonumber(ARGV[4])

for _, key in ipairs(KEYS) do
    redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms)
end

for index, key in ipairs(KEYS) do
    local limit = tonumber(ARGV[4 + index])
    if redis.call('ZCARD', key) >= limit then
        return -index
    end
end

for _, key in ipairs(KEYS) do
    redis.call('ZADD', key, expires_ms, lease_id)
    redis.call('PEXPIRE', key, ttl_ms)
end
return 1
"""


_RENEW_SCRIPT = """
-- AGENT_CONCURRENCY_RENEW
local lease_id = ARGV[1]
local now_ms = tonumber(ARGV[2])
local expires_ms = tonumber(ARGV[3])
local ttl_ms = tonumber(ARGV[4])

for _, key in ipairs(KEYS) do
    redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms)
    if redis.call('ZSCORE', key, lease_id) == false then
        return 0
    end
end

for _, key in ipairs(KEYS) do
    redis.call('ZADD', key, 'XX', expires_ms, lease_id)
    redis.call('PEXPIRE', key, ttl_ms)
end
return 1
"""


_RELEASE_SCRIPT = """
-- AGENT_CONCURRENCY_RELEASE
local removed = 0
for _, key in ipairs(KEYS) do
    removed = removed + redis.call('ZREM', key, ARGV[1])
end
return removed
"""


class AsyncRedisScripts(Protocol):
    async def eval(self, script: str, numkeys: int, *values: object) -> object:
        ...


class ConcurrencyUnavailable(RuntimeError):
    """Redis coordination is unavailable; callers must reject new work."""


class ConcurrencyLeaseLost(RuntimeError):
    """A lease expired or lost at least one required scope."""


class ConcurrencyLimitExceeded(RuntimeError):
    def __init__(self, scope: str) -> None:
        self.scope = scope
        super().__init__(f"Agent concurrency limit reached for scope: {scope}")


class ConcurrencyLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    global_limit: int = Field(default=32, ge=1, le=10000)
    org_limit: int = Field(default=16, ge=1, le=10000)
    user_limit: int = Field(default=4, ge=1, le=10000)
    provider_limit: int = Field(default=16, ge=1, le=10000)
    tool_limit: int = Field(default=8, ge=1, le=10000)


class ConcurrencyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    org_id: UUID
    user_id: int = Field(gt=0)
    provider_id: Optional[str] = Field(default=None, min_length=1, max_length=255)
    tool_name: Optional[str] = Field(default=None, min_length=1, max_length=128)


class ConcurrencyLease(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lease_id: UUID
    org_id: UUID
    user_id: int
    provider_id: Optional[str] = None
    tool_name: Optional[str] = None
    expires_at: datetime


Scope = Tuple[str, str, int]


class DistributedConcurrencyLimiter:
    """Atomically reserve all applicable concurrency scopes in Redis."""

    def __init__(
        self,
        redis: AsyncRedisScripts,
        *,
        limits: Optional[ConcurrencyLimits] = None,
        key_prefix: str = "b-agent:concurrency",
    ) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9:_-]{1,100}", key_prefix):
            raise ValueError("key_prefix contains unsupported characters")
        self._redis = redis
        self._limits = limits or ConcurrencyLimits()
        self._key_prefix = key_prefix.rstrip(":")

    async def acquire(
        self,
        request: ConcurrencyRequest,
        *,
        now: datetime,
        lease_seconds: int,
    ) -> ConcurrencyLease:
        now = self._utc(now)
        self._validate_lease_seconds(lease_seconds)
        lease = ConcurrencyLease(
            lease_id=uuid4(),
            org_id=request.org_id,
            user_id=request.user_id,
            provider_id=request.provider_id,
            tool_name=request.tool_name,
            expires_at=now + timedelta(seconds=lease_seconds),
        )
        scopes = self._scopes(lease)
        try:
            result = int(
                await self._redis.eval(
                    _ACQUIRE_SCRIPT,
                    len(scopes),
                    *(scope[1] for scope in scopes),
                    str(lease.lease_id),
                    self._milliseconds(now),
                    self._milliseconds(lease.expires_at),
                    self._ttl_milliseconds(lease_seconds),
                    *(scope[2] for scope in scopes),
                )
            )
        except RedisError as exc:
            raise ConcurrencyUnavailable(
                "Agent concurrency coordination is unavailable"
            ) from exc
        if result < 0:
            failed_index = abs(result) - 1
            if failed_index >= len(scopes):
                raise ConcurrencyUnavailable(
                    "Agent concurrency coordinator returned an invalid result"
                )
            raise ConcurrencyLimitExceeded(scopes[failed_index][0])
        if result != 1:
            raise ConcurrencyUnavailable(
                "Agent concurrency coordinator returned an invalid result"
            )
        return lease

    async def renew(
        self,
        lease: ConcurrencyLease,
        *,
        now: datetime,
        lease_seconds: int,
    ) -> ConcurrencyLease:
        now = self._utc(now)
        self._validate_lease_seconds(lease_seconds)
        renewed = lease.model_copy(
            update={"expires_at": now + timedelta(seconds=lease_seconds)}
        )
        scopes = self._scopes(lease)
        try:
            result = int(
                await self._redis.eval(
                    _RENEW_SCRIPT,
                    len(scopes),
                    *(scope[1] for scope in scopes),
                    str(lease.lease_id),
                    self._milliseconds(now),
                    self._milliseconds(renewed.expires_at),
                    self._ttl_milliseconds(lease_seconds),
                )
            )
        except RedisError as exc:
            raise ConcurrencyUnavailable(
                "Agent concurrency coordination is unavailable"
            ) from exc
        if result == 0:
            raise ConcurrencyLeaseLost("Agent concurrency lease is no longer active")
        if result != 1:
            raise ConcurrencyUnavailable(
                "Agent concurrency coordinator returned an invalid result"
            )
        return renewed

    async def release(self, lease: ConcurrencyLease) -> bool:
        scopes = self._scopes(lease)
        try:
            result = int(
                await self._redis.eval(
                    _RELEASE_SCRIPT,
                    len(scopes),
                    *(scope[1] for scope in scopes),
                    str(lease.lease_id),
                )
            )
        except RedisError as exc:
            raise ConcurrencyUnavailable(
                "Agent concurrency coordination is unavailable"
            ) from exc
        return result > 0

    def _scopes(self, lease: ConcurrencyLease) -> List[Scope]:
        slot_prefix = f"{self._key_prefix}:{{agent-concurrency}}"
        scopes: List[Scope] = [
            (
                "global",
                f"{slot_prefix}:global",
                self._limits.global_limit,
            ),
            (
                "org",
                f"{slot_prefix}:org:{lease.org_id}",
                self._limits.org_limit,
            ),
            (
                "user",
                f"{slot_prefix}:user:{lease.org_id}:{lease.user_id}",
                self._limits.user_limit,
            ),
        ]
        if lease.provider_id is not None:
            scopes.append(
                (
                    "provider",
                    f"{slot_prefix}:provider:{self._digest(lease.provider_id)}",
                    self._limits.provider_limit,
                )
            )
        if lease.tool_name is not None:
            scopes.append(
                (
                    "tool",
                    (
                        f"{slot_prefix}:tool:{lease.org_id}:"
                        f"{self._digest(lease.tool_name)}"
                    ),
                    self._limits.tool_limit,
                )
            )
        return scopes

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _milliseconds(value: datetime) -> int:
        return int(value.timestamp() * 1000)

    @staticmethod
    def _ttl_milliseconds(lease_seconds: int) -> int:
        return (lease_seconds * 1000) + 5000

    @staticmethod
    def _validate_lease_seconds(value: int) -> None:
        if value <= 0 or value > 3600:
            raise ValueError("lease_seconds must contain 1 to 3600 seconds")

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Concurrency lease timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)
