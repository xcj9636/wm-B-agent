"""Transactional audit records for provider-neutral LLM invocations."""
from datetime import datetime
from typing import Tuple

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
    ) -> Tuple[LLMInvocation, bool]:
        input_hash = canonical_hash(
            request.model_dump(mode="json", exclude={"request_id"})
        )
        existing = self._session.query(LLMInvocation).filter(
            LLMInvocation.idempotency_key == idempotency_key
        ).one_or_none()
        if existing is not None:
            if existing.input_hash != input_hash:
                raise IdempotencyConflict(
                    "LLM idempotency key was reused for different input"
                )
            return existing, False

        invocation = LLMInvocation(
            request_id=request.request_id,
            idempotency_key=idempotency_key,
            use_case=request.use_case.value,
            backend=backend,
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
    ) -> LLMAttempt:
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
    ) -> LLMAttempt:
        """Record a normalized failure without persisting exception text."""
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
            completed_at=datetime.utcnow(),
        )
        self._session.add(attempt)
        self._session.flush()
        return attempt
