import asyncio
from datetime import datetime, timedelta, timezone

import pytest

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
