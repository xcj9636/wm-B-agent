"""Short-lived, namespace-safe cache primitives for knowledge retrieval."""

from hashlib import sha256
import json
from typing import Dict, List, Optional, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import settings
from app.services.agent_runtime.contracts import Sensitivity


class KnowledgeCacheScope(BaseModel):
    """Authoritative versions that invalidate stale retrieval candidates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    acl_policy_version: str = Field(min_length=1, max_length=255)
    index_version: str = Field(min_length=1, max_length=255)


class KnowledgeRetrievalCacheKey(BaseModel):
    """Every identity, policy and index boundary required for cache isolation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    org_id: UUID
    principal_id: str = Field(min_length=1, max_length=255)
    entitlements_hash: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    acl_policy_version: str = Field(min_length=1, max_length=255)
    sensitivity: Sensitivity
    query_hash: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    index_version: str = Field(min_length=1, max_length=255)
    limit: int = Field(ge=1, le=20)

    def digest(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(payload.encode("utf-8")).hexdigest()


class KnowledgeRetrievalCache(Protocol):
    async def get(
        self,
        key: KnowledgeRetrievalCacheKey,
    ) -> Optional[List[Dict[str, object]]]:
        ...

    async def set(
        self,
        key: KnowledgeRetrievalCacheKey,
        candidates: List[Dict[str, object]],
    ) -> None:
        ...


class KnowledgeCacheUnavailable(RuntimeError):
    """The performance cache failed; callers may safely use source retrieval."""


class RedisKnowledgeRetrievalCache:
    """Store validated candidate envelopes under opaque, short-lived keys."""

    MAX_PAYLOAD_BYTES = 1024 * 1024
    MAX_CANDIDATES = 20

    def __init__(
        self,
        redis: Redis,
        *,
        ttl_seconds: int,
        key_prefix: str = "b-agent:retrieval:v1",
    ) -> None:
        if not 1 <= ttl_seconds <= 3600:
            raise ValueError("retrieval cache TTL is outside the safe boundary")
        self._redis = redis
        self._ttl_seconds = ttl_seconds
        self._key_prefix = key_prefix.rstrip(":")

    async def get(
        self,
        key: KnowledgeRetrievalCacheKey,
    ) -> Optional[List[Dict[str, object]]]:
        try:
            encoded = await self._redis.get(self._redis_key(key))
        except RedisError as exc:
            raise KnowledgeCacheUnavailable(
                "Knowledge retrieval cache is unavailable"
            ) from exc
        if encoded is None:
            return None
        if len(encoded) > self.MAX_PAYLOAD_BYTES:
            return None
        try:
            payload = json.loads(encoded)
        except (TypeError, ValueError, UnicodeDecodeError):
            return None
        if not isinstance(payload, list) or len(payload) > self.MAX_CANDIDATES:
            return None
        if not all(isinstance(item, dict) for item in payload):
            return None
        return payload

    async def set(
        self,
        key: KnowledgeRetrievalCacheKey,
        candidates: List[Dict[str, object]],
    ) -> None:
        if len(candidates) > self.MAX_CANDIDATES:
            return
        encoded = json.dumps(
            candidates,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > self.MAX_PAYLOAD_BYTES:
            return
        try:
            await self._redis.set(
                self._redis_key(key),
                encoded,
                ex=self._ttl_seconds,
            )
        except RedisError as exc:
            raise KnowledgeCacheUnavailable(
                "Knowledge retrieval cache is unavailable"
            ) from exc

    def _redis_key(self, key: KnowledgeRetrievalCacheKey) -> str:
        return f"{self._key_prefix}:{key.digest()}"


_redis_client: Optional[Redis] = None
_default_cache: Optional[RedisKnowledgeRetrievalCache] = None


def get_knowledge_retrieval_cache() -> RedisKnowledgeRetrievalCache:
    global _redis_client, _default_cache
    if _default_cache is None:
        _redis_client = Redis.from_url(
            settings.REDIS_CACHE_URL,
            decode_responses=False,
        )
        _default_cache = RedisKnowledgeRetrievalCache(
            _redis_client,
            ttl_seconds=settings.AGENT_RETRIEVAL_CACHE_TTL_SECONDS,
        )
    return _default_cache


async def close_knowledge_retrieval_cache() -> None:
    global _redis_client, _default_cache
    if _redis_client is not None:
        await _redis_client.aclose()
    _redis_client = None
    _default_cache = None
