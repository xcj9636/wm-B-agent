"""Transactional audit records for provider-neutral LLM invocations."""
from datetime import datetime
from typing import Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.database import (
    LLMAttempt,
    LLMAttemptStatus,
    LLMInvocation,
    LLMInvocationStatus,
)
from app.services.idempotency import IdempotencyConflict, canonical_hash
from app.services.llm.contracts import LLMRequest, LLMResponse


class InvocationAuditService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def start(
        self,
        *,
        idempotency_key: str,
        request: LLMRequest,
        backend: str,
        run_id: Optional[UUID] = None,
        fencing_token: Optional[int] = None,
    ) -> Tuple[LLMInvocation, bool]:
        self._validate_fence(run_id, fencing_token)
        input_hash = canonical_hash(
            request.model_dump(mode="json", exclude={"request_id"})
        )
        existing = (
            self._session.query(LLMInvocation)
            .filter(LLMInvocation.idempotency_key == idempotency_key)
            .with_for_update()
            .one_or_none()
        )
        if existing is not None:
            if existing.input_hash != input_hash:
                raise IdempotencyConflict(
                    "LLM idempotency key was reused for different input"
                )
            if existing.agent_run_id is not None:
                if run_id != existing.agent_run_id:
                    raise StaleInvocationFence(
                        "LLM invocation belongs to a different agent run fence"
                    )
                if (
                    existing.status == LLMInvocationStatus.PENDING
                    and fencing_token is not None
                    and fencing_token > existing.fencing_token
                ):
                    existing.fencing_token = fencing_token
                    self._session.flush()
                    return existing, True
            elif run_id is not None:
                raise StaleInvocationFence(
                    "Unfenced LLM invocation cannot be claimed by an agent run"
                )
            return existing, False

        invocation = LLMInvocation(
            request_id=request.request_id,
            idempotency_key=idempotency_key,
            use_case=request.use_case.value,
            backend=backend,
            agent_run_id=run_id,
            fencing_token=fencing_token,
            status=LLMInvocationStatus.PENDING,
            input_hash=input_hash,
        )
        self._session.add(invocation)
        self._session.flush()
        return invocation, True

    def succeed(
        self,
        invocation: LLMInvocation,
        response: LLMResponse,
        *,
        run_id: Optional[UUID] = None,
        fencing_token: Optional[int] = None,
        latency_ms: Optional[int] = None,
        ttft_ms: Optional[int] = None,
        e2e_latency_ms: Optional[int] = None,
        consumer_backpressure_ms: Optional[int] = None,
    ) -> LLMAttempt:
        self._assert_current_fence(invocation, run_id, fencing_token)
        if response.request_id != invocation.request_id:
            raise ValueError("LLM response request ID does not match invocation")

        response_json = response.model_dump(mode="json")
        output_hash = canonical_hash(response_json)
        existing = self._session.query(LLMAttempt).filter(
            LLMAttempt.invocation_id == invocation.id,
            LLMAttempt.attempt_number == 1,
        ).one_or_none()
        if existing is not None:
            if invocation.output_hash != output_hash:
                raise IdempotencyConflict(
                    "LLM invocation completion changed after it was recorded"
                )
            return existing

        invocation.status = LLMInvocationStatus.SUCCEEDED
        invocation.output_hash = output_hash
        invocation.response_json = response_json
        invocation.completed_at = datetime.utcnow()
        attempt = LLMAttempt(
            invocation=invocation,
            attempt_number=1,
            status=LLMAttemptStatus.SUCCEEDED,
            gateway_request_id=response.gateway_request_id,
            provider=response.resolved_provider,
            model=response.resolved_model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            total_tokens=response.usage.total_tokens,
            cost=response.usage.cost,
            cost_status=response.usage.cost_status,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
            e2e_latency_ms=e2e_latency_ms,
            consumer_backpressure_ms=consumer_backpressure_ms,
            completed_at=datetime.utcnow(),
        )
        self._session.add(attempt)
        self._session.flush()
        return attempt

    def fail(
        self,
        invocation: LLMInvocation,
        *,
        error_kind: str,
        retryable: bool,
        run_id: Optional[UUID] = None,
        fencing_token: Optional[int] = None,
        latency_ms: Optional[int] = None,
        e2e_latency_ms: Optional[int] = None,
        consumer_backpressure_ms: Optional[int] = None,
    ) -> LLMAttempt:
        """Record a normalized failure without persisting exception text."""
        self._assert_current_fence(invocation, run_id, fencing_token)
        existing = self._session.query(LLMAttempt).filter(
            LLMAttempt.invocation_id == invocation.id,
            LLMAttempt.attempt_number == 1,
        ).one_or_none()
        if existing is not None:
            return existing

        invocation.status = LLMInvocationStatus.FAILED
        invocation.error_kind = error_kind
        invocation.retryable = retryable
        invocation.completed_at = datetime.utcnow()
        attempt = LLMAttempt(
            invocation=invocation,
            attempt_number=1,
            status=LLMAttemptStatus.FAILED,
            error_kind=error_kind,
            retryable=retryable,
            latency_ms=latency_ms,
            e2e_latency_ms=e2e_latency_ms,
            consumer_backpressure_ms=consumer_backpressure_ms,
            completed_at=datetime.utcnow(),
        )
        self._session.add(attempt)
        self._session.flush()
        return attempt

    @staticmethod
    def _validate_fence(
        run_id: Optional[UUID],
        fencing_token: Optional[int],
    ) -> None:
        if (run_id is None) != (fencing_token is None):
            raise ValueError("run_id and fencing_token must be provided together")
        if fencing_token is not None and fencing_token <= 0:
            raise ValueError("fencing_token must be positive")

    @classmethod
    def _assert_current_fence(
        cls,
        invocation: LLMInvocation,
        run_id: Optional[UUID],
        fencing_token: Optional[int],
    ) -> None:
        cls._validate_fence(run_id, fencing_token)
        if invocation.agent_run_id is None:
            if run_id is not None:
                raise StaleInvocationFence(
                    "LLM invocation does not have an agent run fence"
                )
            return
        if (
            run_id != invocation.agent_run_id
            or fencing_token != invocation.fencing_token
        ):
            raise StaleInvocationFence(
                "LLM invocation completion lost its agent run fence"
            )


class StaleInvocationFence(RuntimeError):
    """A superseded worker attempted to mutate an LLM invocation."""
