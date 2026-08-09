"""Central, raw-prompt-free audit sinks for every LLM service call."""

from dataclasses import dataclass
from typing import Callable, Optional, Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.database import LLMInvocation, LLMInvocationStatus
from app.services.llm.audit import InvocationAuditService
from app.services.llm.contracts import GatewayError, LLMRequest, LLMResponse


@dataclass(frozen=True)
class InvocationAuditStart:
    invocation_id: UUID
    created: bool
    cached_response: Optional[LLMResponse] = None


class InvocationInProgress(RuntimeError):
    """The same business idempotency key is already executing."""


class InvocationAuditSink(Protocol):
    def start(
        self,
        *,
        idempotency_key: str,
        request: LLMRequest,
        backend: str,
    ) -> InvocationAuditStart:
        ...

    def succeed(self, invocation_id: UUID, response: LLMResponse) -> None:
        ...

    def fail(self, invocation_id: UUID, error: Exception) -> None:
        ...


def _start_with_session(
    session: Session,
    *,
    idempotency_key: str,
    request: LLMRequest,
    backend: str,
) -> InvocationAuditStart:
    invocation, created = InvocationAuditService(session).start(
        idempotency_key=idempotency_key,
        request=request,
        backend=backend,
    )
    cached = None
    if not created and invocation.status == LLMInvocationStatus.SUCCEEDED:
        cached = LLMResponse.model_validate(invocation.response_json)
    session.commit()
    return InvocationAuditStart(
        invocation_id=invocation.id,
        created=created,
        cached_response=cached,
    )


def _succeed_with_session(
    session: Session,
    invocation_id: UUID,
    response: LLMResponse,
) -> None:
    invocation = session.get(LLMInvocation, invocation_id)
    if invocation is None:
        raise LookupError("LLM invocation audit record not found")
    InvocationAuditService(session).succeed(invocation, response)
    session.commit()


def _fail_with_session(
    session: Session,
    invocation_id: UUID,
    error: Exception,
) -> None:
    invocation = session.get(LLMInvocation, invocation_id)
    if invocation is None:
        raise LookupError("LLM invocation audit record not found")
    if isinstance(error, GatewayError):
        error_kind = error.kind.value
        retryable = error.retryable
    else:
        error_kind = "internal_error"
        retryable = False
    InvocationAuditService(session).fail(
        invocation,
        error_kind=error_kind,
        retryable=retryable,
    )
    session.commit()


class SessionInvocationAuditSink:
    """Audit using the request-owned SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def start(self, **kwargs) -> InvocationAuditStart:
        return _start_with_session(self._session, **kwargs)

    def succeed(self, invocation_id: UUID, response: LLMResponse) -> None:
        _succeed_with_session(self._session, invocation_id, response)

    def fail(self, invocation_id: UUID, error: Exception) -> None:
        _fail_with_session(self._session, invocation_id, error)


class SessionFactoryInvocationAuditSink:
    """Audit global/legacy calls with a short-lived independent session."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def start(self, **kwargs) -> InvocationAuditStart:
        with self._session_factory() as session:
            return _start_with_session(session, **kwargs)

    def succeed(self, invocation_id: UUID, response: LLMResponse) -> None:
        with self._session_factory() as session:
            _succeed_with_session(session, invocation_id, response)

    def fail(self, invocation_id: UUID, error: Exception) -> None:
        with self._session_factory() as session:
            _fail_with_session(session, invocation_id, error)
