"""Tenant-scoped, secret-free read model for media generation jobs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.database import MediaGenerationEvent, MediaGenerationJob


_SAFE_EVENT_FIELDS: dict[str, frozenset[str]] = {
    "job.created": frozenset(),
    "job.claimed": frozenset(),
    "job.requeued": frozenset(),
    "job.cancelled": frozenset(),
    "job.cancel_requested": frozenset(),
    "job.succeeded": frozenset(),
    "job.failed": frozenset({"error_code"}),
    "submission.started": frozenset({"attempt_number"}),
    "submission.accepted": frozenset(),
    "submission.unknown": frozenset({"error_code"}),
    "submission.manually_confirmed": frozenset(),
    "submission.not_created_confirmed": frozenset(),
}


@dataclass(frozen=True)
class SafeMediaGenerationEvent:
    sequence: int
    event_type: str
    data: dict[str, Any]
    created_at: datetime


class MediaGenerationJobAccessService:
    """Read one user's jobs while dropping all unapproved event metadata."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def get(
        self,
        job_id: UUID,
        *,
        org_id: UUID,
        user_id: int,
        is_admin: bool,
    ) -> MediaGenerationJob:
        query = self._db.query(MediaGenerationJob).filter(
            MediaGenerationJob.id == job_id,
            MediaGenerationJob.org_id == org_id,
        )
        if not is_admin:
            query = query.filter(MediaGenerationJob.owner_user_id == user_id)
        job = query.one_or_none()
        if job is None:
            raise KeyError("media generation job not found")
        return job

    def list_events(
        self,
        job_id: UUID,
        *,
        org_id: UUID,
        user_id: int,
        is_admin: bool,
        after_sequence: int,
        limit: int,
    ) -> tuple[list[SafeMediaGenerationEvent], int]:
        if after_sequence < 0:
            raise ValueError("after_sequence cannot be negative")
        if not 1 <= limit <= 100:
            raise ValueError("event limit must be between 1 and 100")
        self.get(
            job_id,
            org_id=org_id,
            user_id=user_id,
            is_admin=is_admin,
        )
        rows = (
            self._db.query(MediaGenerationEvent)
            .filter(
                MediaGenerationEvent.job_id == job_id,
                MediaGenerationEvent.sequence > after_sequence,
            )
            .order_by(MediaGenerationEvent.sequence)
            .limit(limit)
            .all()
        )
        events = [self._safe_event(row) for row in rows]
        next_sequence = events[-1].sequence if events else after_sequence
        return events, next_sequence

    @staticmethod
    def _safe_event(row: MediaGenerationEvent) -> SafeMediaGenerationEvent:
        allowed = _SAFE_EVENT_FIELDS.get(row.event_type, frozenset())
        source = row.data_json if isinstance(row.data_json, dict) else {}
        data = {
            key: source[key]
            for key in allowed
            if key in source
            and isinstance(source[key], (str, int, float, bool))
        }
        return SafeMediaGenerationEvent(
            sequence=row.sequence,
            event_type=row.event_type,
            data=data,
            created_at=row.created_at,
        )
