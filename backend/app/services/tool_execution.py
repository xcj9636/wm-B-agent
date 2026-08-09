"""Durable tool state machine with fencing, approval, and outbox handoff."""

from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, Tuple

from sqlalchemy.orm import Session

from app.models.database import AgentToolExecution, OutboxEvent
from app.services.agent_runtime.contracts import ExecutionPrincipal
from app.services.idempotency import IdempotencyConflict, canonical_hash
from app.services.outbox import OutboxCommand, OutboxService
from app.services.tool_runtime import (
    ApprovalEnvelope,
    ToolCall,
    ToolCallFactory,
    ToolPolicyDenied,
    ToolRegistry,
    ToolRisk,
)


class StaleToolCall(RuntimeError):
    pass


class SideEffectToolsDisabled(RuntimeError):
    pass


class ToolExecutionBusy(RuntimeError):
    pass


ReadToolHandler = Callable[[ToolCall], Awaitable[Dict[str, object]]]


class DurableToolExecutionService:
    """Persist every transition before running code or emitting a side effect."""

    def __init__(
        self,
        session: Session,
        registry: ToolRegistry,
        *,
        allow_side_effects: bool = False,
    ) -> None:
        self._db = session
        self._registry = registry
        self._allow_side_effects = allow_side_effects

    def record(self, call: ToolCall) -> Tuple[AgentToolExecution, bool]:
        self._validate_registered_call(call)
        input_hash = self._input_hash(call)
        existing = (
            self._db.query(AgentToolExecution)
            .filter(AgentToolExecution.idempotency_key == call.idempotency_key)
            .one_or_none()
        )
        if existing is not None:
            if existing.input_hash != input_hash:
                raise IdempotencyConflict(
                    "Tool idempotency key was reused for a different call"
                )
            return existing, False

        row = AgentToolExecution(
            id=call.call_id,
            idempotency_key=call.idempotency_key,
            input_hash=input_hash,
            run_id=call.run_id,
            turn_id=call.turn_id,
            generation_epoch=call.generation_epoch,
            org_id=call.org_id,
            actor_user_id=call.actor_user_id,
            tool_name=call.tool_name,
            tool_version=call.tool_version,
            risk=call.risk.value,
            arguments=call.arguments,
            provenance=sorted(value.value for value in call.provenance),
            purpose=call.purpose,
            approval_required=call.approval_required,
            status=("awaiting_approval" if call.approval_required else "ready"),
        )
        try:
            self._db.add(row)
            self._db.commit()
            self._db.refresh(row)
        except Exception:
            self._db.rollback()
            raise
        return row, True

    def approve(
        self,
        call: ToolCall,
        approval: ApprovalEnvelope,
        approver: ExecutionPrincipal,
    ) -> AgentToolExecution:
        row = self._row_for_call(call, lock=True)
        ToolCallFactory(self._registry).validate_approval(call, approval)
        if approval.approver_user_id != approver.user_id:
            raise ToolPolicyDenied("Approval identity does not match the approver")
        if approver.org_id != row.org_id:
            raise ToolPolicyDenied("Approver must belong to the same organization")
        if approver.user_id == row.actor_user_id:
            raise ToolPolicyDenied("Approval requires a separate user")
        if not row.approval_required:
            return row
        if row.status == "ready" and row.approved_by_user_id == approver.user_id:
            return row
        if row.status != "awaiting_approval":
            raise ToolPolicyDenied("Tool call is not awaiting approval")

        row.status = "ready"
        row.approved_by_user_id = approver.user_id
        row.approval_entitlements_hash = approver.entitlements_hash
        row.approved_at = self._naive_utc(approval.approved_at)
        self._db.commit()
        self._db.refresh(row)
        return row

    async def execute_read(
        self,
        call: ToolCall,
        *,
        current_generation_epoch: int,
        handler: ReadToolHandler,
    ) -> Dict[str, object]:
        row = self._row_for_call(call, lock=True)
        self._assert_fence(row, current_generation_epoch)
        if ToolRisk(row.risk) != ToolRisk.READ:
            raise ToolPolicyDenied("Side-effect tools cannot execute in the read path")
        if row.status == "succeeded":
            return dict(row.result_json or {})
        if row.status != "ready":
            raise ToolExecutionBusy("Tool call is not ready for execution")

        row.status = "running"
        self._db.commit()
        try:
            result = await handler(call)
        except BaseException:
            self._db.rollback()
            failed = self._db.get(AgentToolExecution, call.call_id)
            if failed is not None and failed.status == "running":
                failed.status = "failed"
                failed.error_code = "tool_execution_failed"
                failed.completed_at = datetime.utcnow()
                self._db.commit()
            raise

        completed = self._db.get(AgentToolExecution, call.call_id)
        if completed is None or completed.status != "running":
            raise ToolExecutionBusy("Tool execution state changed before completion")
        completed.result_json = result
        completed.result_hash = canonical_hash(result)
        completed.status = "succeeded"
        completed.completed_at = datetime.utcnow()
        self._db.commit()
        return dict(result)

    def enqueue_side_effect(
        self,
        call: ToolCall,
        *,
        current_generation_epoch: int,
    ) -> OutboxEvent:
        if not self._allow_side_effects:
            raise SideEffectToolsDisabled(
                "Side-effect tools remain disabled until the canary gate is enabled"
            )
        row = self._row_for_call(call, lock=True)
        self._assert_fence(row, current_generation_epoch)
        if ToolRisk(row.risk) == ToolRisk.READ:
            raise ToolPolicyDenied("Read tools do not use the side-effect outbox")
        if row.status == "enqueued" and row.outbox_event_id is not None:
            event = self._db.get(OutboxEvent, row.outbox_event_id)
            if event is None:
                raise ToolExecutionBusy("Tool outbox event is unavailable")
            return event
        if row.status != "ready":
            raise ToolPolicyDenied("Tool call is not approved and ready")

        event, _ = OutboxService(self._db).enqueue(
            OutboxCommand(
                aggregate_type="agent_tool_call",
                aggregate_id=str(row.id),
                event_type="tool.execute",
                business_key=row.idempotency_key,
                channel="agent_tool",
                payload={
                    "call_id": str(row.id),
                    "run_id": str(row.run_id),
                    "turn_id": str(row.turn_id),
                    "generation_epoch": row.generation_epoch,
                    "org_id": str(row.org_id),
                    "actor_user_id": row.actor_user_id,
                    "tool_name": row.tool_name,
                    "tool_version": row.tool_version,
                    "arguments": dict(row.arguments or {}),
                    "purpose": row.purpose,
                },
            )
        )
        row.outbox_event_id = event.id
        row.status = "enqueued"
        self._db.commit()
        self._db.refresh(event)
        return event

    def _row_for_call(
        self,
        call: ToolCall,
        *,
        lock: bool,
    ) -> AgentToolExecution:
        self._validate_registered_call(call)
        query = self._db.query(AgentToolExecution).filter(
            AgentToolExecution.id == call.call_id,
            AgentToolExecution.idempotency_key == call.idempotency_key,
        )
        if lock:
            query = query.with_for_update()
        row = query.one_or_none()
        if row is None:
            raise LookupError("Durable tool call was not found")
        if row.input_hash != self._input_hash(call):
            raise IdempotencyConflict("Durable tool call payload does not match")
        return row

    def _validate_registered_call(self, call: ToolCall) -> None:
        spec = self._registry.resolve(call.tool_name)
        if spec.version != call.tool_version or spec.risk != call.risk:
            raise ToolPolicyDenied("Tool implementation contract changed")

    @staticmethod
    def _assert_fence(row: AgentToolExecution, current_epoch: int) -> None:
        if row.generation_epoch != current_epoch:
            raise StaleToolCall("Tool call lost its generation fence")

    @staticmethod
    def _input_hash(call: ToolCall) -> str:
        return canonical_hash(
            {
                "run_id": str(call.run_id),
                "turn_id": str(call.turn_id),
                "generation_epoch": call.generation_epoch,
                "org_id": str(call.org_id),
                "actor_user_id": call.actor_user_id,
                "tool_name": call.tool_name,
                "tool_version": call.tool_version,
                "risk": call.risk.value,
                "arguments": call.arguments,
                "provenance": sorted(value.value for value in call.provenance),
                "purpose": call.purpose,
                "approval_required": call.approval_required,
            }
        )

    @staticmethod
    def _naive_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
