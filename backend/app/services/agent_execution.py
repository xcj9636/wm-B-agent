"""Deadline-aware execution primitives for safe read-only fan-out."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, List, Sequence

from app.services.tool_runtime import ToolRisk


class DeadlineExceeded(TimeoutError):
    pass


class UnsafeParallelism(RuntimeError):
    pass


@dataclass(frozen=True)
class ExecutionBudget:
    deadline_at: datetime
    max_parallel: int = 3

    def __post_init__(self) -> None:
        if self.deadline_at.tzinfo is None:
            raise ValueError("deadline_at must be timezone-aware")
        if not 1 <= self.max_parallel <= 3:
            raise ValueError("max_parallel must be between 1 and 3")

    def remaining_seconds(self) -> float:
        return max(
            0.0,
            (self.deadline_at - datetime.now(timezone.utc)).total_seconds(),
        )

    def ensure_remaining(self) -> None:
        if self.remaining_seconds() <= 0:
            raise DeadlineExceeded("Agent execution deadline has elapsed")


@dataclass(frozen=True)
class FanoutTask:
    name: str
    risk: ToolRisk
    operation: Callable[[], Awaitable[Any]]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Fan-out task name is required")


class ReadOnlyFanout:
    """Run independent reads with bounded concurrency and shared cancellation."""

    async def run(
        self,
        tasks: Sequence[FanoutTask],
        budget: ExecutionBudget,
    ) -> List[Any]:
        unsafe = [task.name for task in tasks if task.risk != ToolRisk.READ]
        if unsafe:
            raise UnsafeParallelism(
                "Only read-only tasks may enter fan-out execution"
            )
        if not tasks:
            return []
        budget.ensure_remaining()

        semaphore = asyncio.Semaphore(budget.max_parallel)

        async def execute(task: FanoutTask) -> Any:
            async with semaphore:
                budget.ensure_remaining()
                return await task.operation()

        scheduled = [asyncio.create_task(execute(task)) for task in tasks]
        try:
            async with asyncio.timeout(budget.remaining_seconds()):
                return list(await asyncio.gather(*scheduled))
        except TimeoutError as exc:
            await self._cancel(scheduled)
            raise DeadlineExceeded("Agent execution deadline has elapsed") from exc
        except BaseException:
            await self._cancel(scheduled)
            raise

    @staticmethod
    async def _cancel(tasks: Sequence[asyncio.Task[Any]]) -> None:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
