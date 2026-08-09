"""Minimal fail-closed runtime shell used while legacy flows migrate."""

import logging
from typing import AsyncIterator, Protocol
from uuid import uuid4

from app.services.agent_runtime.contracts import (
    AgentEvent,
    AgentRequest,
    AgentResult,
)


logger = logging.getLogger(__name__)


class AgentExecutor(Protocol):
    async def execute(self, request: AgentRequest) -> AgentResult:
        ...


class AgentRuntime:
    """Emit a stable event envelope around a use-case executor."""

    def __init__(self, executor: AgentExecutor) -> None:
        self._executor = executor

    async def run(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        run_id = uuid4()
        trace_id = uuid4()
        yield AgentEvent(
            run_id=run_id,
            turn_id=request.turn_id,
            sequence=1,
            type="run.started",
            trace_id=trace_id,
            payload={"use_case": request.use_case.value},
        )
        try:
            result = await self._executor.execute(request)
        except Exception as exc:
            logger.warning(
                "Agent executor failed without exposing internal details",
                extra={"error_type": type(exc).__name__, "run_id": str(run_id)},
            )
            yield AgentEvent(
                run_id=run_id,
                turn_id=request.turn_id,
                sequence=2,
                type="run.failed",
                trace_id=trace_id,
                payload={"code": "AGENT_EXECUTION_FAILED"},
            )
            return

        yield AgentEvent(
            run_id=run_id,
            turn_id=request.turn_id,
            sequence=2,
            type="run.completed",
            trace_id=trace_id,
            payload={"content": result.content, "metadata": result.metadata},
        )
