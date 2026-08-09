"""Shared deterministic hashing and idempotency errors."""
import hashlib
import json
from typing import Any


class IdempotencyConflict(RuntimeError):
    """A stable business key was reused for different immutable input."""


def canonical_hash(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
