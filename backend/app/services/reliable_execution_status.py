"""Secret-free operational summary for reliable execution state."""
from datetime import datetime
from typing import Dict, Optional

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.database import (
    LLMInvocation,
    LLMInvocationStatus,
    OutboxEvent,
    OutboxStatus,
)


class ReliableExecutionStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outbox_counts: Dict[str, int]
    llm_invocation_counts: Dict[str, int]
    expired_outbox_leases: int
    oldest_pending_at: Optional[datetime] = None
    checked_at: datetime = Field(default_factory=datetime.utcnow)


class ReliableExecutionStatusService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_status(self, *, now: Optional[datetime] = None) -> ReliableExecutionStatus:
        now = now or datetime.utcnow()
        outbox_counts = {status.value: 0 for status in OutboxStatus}
        for status, count in (
            self._session.query(OutboxEvent.status, func.count(OutboxEvent.id))
            .group_by(OutboxEvent.status)
            .all()
        ):
            outbox_counts[self._enum_value(status)] = count

        invocation_counts = {status.value: 0 for status in LLMInvocationStatus}
        for status, count in (
            self._session.query(
                LLMInvocation.status,
                func.count(LLMInvocation.id),
            )
            .group_by(LLMInvocation.status)
            .all()
        ):
            invocation_counts[self._enum_value(status)] = count

        expired_leases = (
            self._session.query(OutboxEvent)
            .filter(
                OutboxEvent.status == OutboxStatus.PROCESSING,
                OutboxEvent.lease_until <= now,
            )
            .count()
        )
        oldest_pending_at = (
            self._session.query(func.min(OutboxEvent.available_at))
            .filter(
                OutboxEvent.status.in_(
                    [OutboxStatus.PENDING, OutboxStatus.RETRY]
                )
            )
            .scalar()
        )
        return ReliableExecutionStatus(
            outbox_counts=outbox_counts,
            llm_invocation_counts=invocation_counts,
            expired_outbox_leases=expired_leases,
            oldest_pending_at=oldest_pending_at,
            checked_at=now,
        )

    @staticmethod
    def _enum_value(value) -> str:
        return value.value if hasattr(value, "value") else str(value)


def get_reliable_execution_status_service(
    session: Session = Depends(get_db),
) -> ReliableExecutionStatusService:
    return ReliableExecutionStatusService(session)
