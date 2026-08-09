"""Reference-counted backend pool for hot-reloadable runtime snapshots."""

import asyncio
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable, Dict, List, Optional, Set


@dataclass
class _Entry:
    key: str
    backend: Any
    references: int = 0
    stale: bool = False
    closed: bool = False


class RuntimeBackendLease:
    def __init__(self, pool: "RuntimeBackendPool", entry: _Entry) -> None:
        self._pool = pool
        self._entry = entry
        self._released = False

    @property
    def backend(self) -> Any:
        return self._entry.backend

    def __getattr__(self, name: str) -> Any:
        return getattr(self._entry.backend, name)

    async def aclose(self) -> None:
        if self._released:
            return
        self._released = True
        await self._pool.release(self._entry)


class RuntimeBackendPool:
    """Keep one backend per active config and drain replaced snapshots safely."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._entries: Dict[str, _Entry] = {}
        self._current_key: Optional[str] = None
        self._closed = False
        self._pending_close: List[_Entry] = []
        self._background_tasks: Set[asyncio.Task[None]] = set()

    def acquire(
        self,
        key: str,
        factory: Callable[[], Any],
    ) -> RuntimeBackendLease:
        close_now: List[_Entry] = []
        with self._lock:
            if self._closed:
                raise RuntimeError("Runtime backend pool is closed")
            entry = self._entries.get(key)
            if entry is None:
                backend = factory()
                entry = _Entry(key=key, backend=backend)
                self._entries[key] = entry
            if self._current_key != key:
                self._current_key = key
                for other in self._entries.values():
                    if other.key != key:
                        other.stale = True
                close_now = self._claim_closable_locked()
            entry.references += 1
        self._schedule_close(close_now)
        return RuntimeBackendLease(self, entry)

    async def release(self, entry: _Entry) -> None:
        close_now: List[_Entry] = []
        with self._lock:
            current = self._entries.get(entry.key)
            if current is None or current is not entry:
                return
            if entry.references > 0:
                entry.references -= 1
            close_now = self._claim_closable_locked()
        await self._close_entries(close_now)

    async def aclose(self) -> None:
        with self._lock:
            if self._closed and not self._entries and not self._pending_close:
                tasks = list(self._background_tasks)
                entries: List[_Entry] = []
            else:
                self._closed = True
                self._current_key = None
                entries = list(self._entries.values()) + self._pending_close
                self._entries.clear()
                self._pending_close = []
                for entry in entries:
                    entry.closed = True
                tasks = list(self._background_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._close_entries(entries)

    def _claim_closable_locked(self) -> List[_Entry]:
        entries: List[_Entry] = []
        for key, entry in list(self._entries.items()):
            if entry.stale and entry.references == 0 and not entry.closed:
                entry.closed = True
                entries.append(entry)
                del self._entries[key]
        return entries

    def _schedule_close(self, entries: List[_Entry]) -> None:
        if not entries:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            with self._lock:
                self._pending_close.extend(entries)
            return
        task = loop.create_task(self._close_entries(entries))
        with self._lock:
            self._background_tasks.add(task)
        task.add_done_callback(self._discard_task)

    def _discard_task(self, task: asyncio.Task[None]) -> None:
        with self._lock:
            self._background_tasks.discard(task)

    @staticmethod
    async def _close_entries(entries: List[_Entry]) -> None:
        for entry in entries:
            close = getattr(entry.backend, "aclose", None)
            if close is not None:
                await close()


runtime_backend_pool = RuntimeBackendPool()


async def close_runtime_backend_pool() -> None:
    await runtime_backend_pool.aclose()
