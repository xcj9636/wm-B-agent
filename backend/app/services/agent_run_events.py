"""Fenced, user-isolated durable event log for resumable agent streams."""

from datetime import datetime, timedelta, timezone
import json
import re
from typing import Dict, List
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.models.database import AgentRun, AgentRunEvent
from app.services.agent_runs import RunLeaseConflict, RunNotFound


class AgentRunEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    sequence: int = Field(ge=1)
    event_type: str
    data: Dict[str, object]
    occurred_at: datetime


class AgentRunEventService:
    MAX_EVENT_BYTES = 64 * 1024
    MAX_EVENTS_PER_RUN = 4000
    MAX_BYTES_PER_RUN = 2 * 1024 * 1024
    MAX_REPLAY_EVENTS = 2000
    MAX_REPLAY_BYTES = 256 * 1024
    RETENTION = timedelta(days=7)

    def __init__(self, session: Session) -> None:
        self._db = session

    def append(
        self,
        run_id: UUID,
        *,
        worker_id: str,
        fencing_token: int,
        event_type: str,
        data: Dict[str, object],
        now: datetime,
        commit: bool = True,
    ) -> AgentRunEventResponse:
        event_type = event_type.strip()
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,49}", event_type):
            raise ValueError("event_type is invalid")
        try:
            encoded = json.dumps(
                data,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("event payload must be JSON serializable") from exc
        if len(encoded) > self.MAX_EVENT_BYTES:
            raise ValueError("event payload exceeds the size limit")

        now = self._naive_utc(now)
        run = (
            self._db.query(AgentRun)
            .filter(AgentRun.id == run_id)
            .with_for_update()
            .one_or_none()
        )
        if (
            run is None
            or run.status != "running"
            or run.leased_by != worker_id
            or run.fencing_token != fencing_token
            or run.lease_until is None
            or run.lease_until <= now
            or run.deadline_at <= now
        ):
            raise RunLeaseConflict("Agent run lease is no longer current")
        if (
            run.event_sequence >= self.MAX_EVENTS_PER_RUN
            or run.event_bytes + len(encoded) > self.MAX_BYTES_PER_RUN
        ):
            raise ValueError("agent run event quota exceeded")

        run.event_sequence += 1
        run.event_bytes += len(encoded)
        row = AgentRunEvent(
            run_id=run.id,
            sequence=run.event_sequence,
            event_type=event_type,
            data_json=data,
            created_at=now,
            expires_at=now + self.RETENTION,
        )
        self._db.add(row)
        self._db.flush()
        response = self._response(row)
        if commit:
            self._db.commit()
        return response

    def list_for_user(
        self,
        run_id: UUID,
        *,
        user_id: int,
        after_sequence: int = 0,
        limit: int = MAX_REPLAY_EVENTS,
    ) -> List[AgentRunEventResponse]:
        if after_sequence < 0:
            raise ValueError("after_sequence cannot be negative")
        if not 1 <= limit <= self.MAX_REPLAY_EVENTS:
            raise ValueError("limit is outside the replay boundary")
        run = (
            self._db.query(AgentRun)
            .filter(AgentRun.id == run_id, AgentRun.user_id == user_id)
            .one_or_none()
        )
        if run is None:
            raise RunNotFound("Agent run not found")
        rows = (
            self._db.query(AgentRunEvent)
            .filter(
                AgentRunEvent.run_id == run_id,
                AgentRunEvent.sequence > after_sequence,
                AgentRunEvent.expires_at > datetime.utcnow(),
            )
            .order_by(AgentRunEvent.sequence)
            .limit(limit)
            .all()
        )
        responses: List[AgentRunEventResponse] = []
        replay_bytes = 0
        for row in rows:
            response = self._response(row)
            event_bytes = len(
                json.dumps(
                    response.data,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            if responses and replay_bytes + event_bytes > self.MAX_REPLAY_BYTES:
                break
            responses.append(response)
            replay_bytes += event_bytes
        return responses

    def purge_expired(self, *, now: datetime, limit: int = 1000) -> int:
        if not 1 <= limit <= 10000:
            raise ValueError("limit is outside the purge boundary")
        now = self._naive_utc(now)
        rows = (
            self._db.query(AgentRunEvent)
            .filter(AgentRunEvent.expires_at <= now)
            .order_by(AgentRunEvent.expires_at, AgentRunEvent.id)
            .limit(limit)
            .all()
        )
        for row in rows:
            self._db.delete(row)
        self._db.commit()
        return len(rows)

    @staticmethod
    def _response(row: AgentRunEvent) -> AgentRunEventResponse:
        occurred_at = row.created_at
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        return AgentRunEventResponse(
            run_id=row.run_id,
            sequence=row.sequence,
            event_type=row.event_type,
            data=dict(row.data_json or {}),
            occurred_at=occurred_at,
        )

    @staticmethod
    def _naive_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
