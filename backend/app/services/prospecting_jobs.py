"""Durable, budgeted orchestration for multi-domain Hunter searches."""
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.integrations.hunter import HunterClient, HunterConnectorError
from app.models.database import (
    ConnectorConfiguration,
    ProspectingContact,
    ProspectingJob,
    ProspectingJobItem,
    ProspectingSearch,
)
from app.services.prospecting import (
    DEPARTMENTS,
    SEARCH_VERIFICATION_STATUSES,
    SENIORITIES,
    ProspectingService,
)


class ProspectingJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    QUOTA_BLOCKED = "quota_blocked"
    BUDGET_EXHAUSTED = "budget_exhausted"
    PAUSED = "paused"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"


class ProspectingItemStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    QUOTA_BLOCKED = "quota_blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    LEGAL_RESTRICTED = "legal_restricted"


TERMINAL_JOB_STATUSES = {
    ProspectingJobStatus.COMPLETED.value,
    ProspectingJobStatus.COMPLETED_WITH_ERRORS.value,
}
TERMINAL_ITEM_STATUSES = {
    ProspectingItemStatus.COMPLETED.value,
    ProspectingItemStatus.FAILED.value,
    ProspectingItemStatus.LEGAL_RESTRICTED.value,
}


class ProspectingJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domains: List[str] = Field(min_length=1, max_length=100)
    page_size: int = Field(default=10, ge=1, le=100)
    max_pages_per_domain: int = Field(default=1, ge=1, le=10)
    request_budget: int = Field(default=10, ge=1, le=500)
    contact_type: Optional[str] = None
    seniorities: List[str] = Field(default_factory=list, max_length=3)
    departments: List[str] = Field(default_factory=list, max_length=15)
    decision_maker: Optional[bool] = None
    verification_statuses: List[str] = Field(
        default_factory=lambda: ["valid"],
        max_length=3,
    )

    @model_validator(mode="after")
    def validate_filters(self) -> "ProspectingJobCreate":
        if self.contact_type not in {None, "personal", "generic"}:
            raise ValueError("unsupported contact type")
        if not set(self.seniorities).issubset(SENIORITIES):
            raise ValueError("unsupported seniority")
        if not set(self.departments).issubset(DEPARTMENTS):
            raise ValueError("unsupported department")
        if not set(self.verification_statuses).issubset(
            SEARCH_VERIFICATION_STATUSES
        ):
            raise ValueError("unsupported verification status")
        return self


class ProspectingJobResume(BaseModel):
    model_config = ConfigDict(extra="forbid")

    additional_requests: int = Field(default=0, ge=0, le=500)


class ProspectingJobItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    search_id: UUID
    domain: str
    status: str
    next_offset: int
    pages_completed: int
    requests_used: int
    contacts_found: int
    attempt_count: int
    max_attempts: int
    truncated: bool
    error_code: Optional[str] = None
    next_attempt_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class ProspectingJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    provider: str
    status: ProspectingJobStatus
    connector_version: int
    page_size: int
    max_pages_per_domain: int
    request_budget: int
    requests_used: int
    provider_remaining: Optional[float] = None
    provider_usage_unit: Optional[str] = None
    total_items: int
    completed_items: int
    failed_items: int
    contacts_found: int
    error_code: Optional[str] = None
    next_attempt_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    items: List[ProspectingJobItemResponse]


class ProspectingJobNotFound(LookupError):
    pass


class ProspectingConnectorUnavailable(RuntimeError):
    pass


class ProspectingJobConflict(RuntimeError):
    pass


class ProspectingJobService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create_job(
        self,
        command: ProspectingJobCreate,
        *,
        user_id: int,
    ) -> ProspectingJobResponse:
        connector = self._enabled_connector()
        domains = []
        seen = set()
        for raw_domain in command.domains:
            domain = ProspectingService._normalize_domain(raw_domain)
            if domain not in seen:
                domains.append(domain)
                seen.add(domain)

        config = {
            "contact_type": command.contact_type,
            "seniorities": command.seniorities,
            "departments": command.departments,
            "decision_maker": command.decision_maker,
            "verification_statuses": command.verification_statuses,
        }
        config = {key: value for key, value in config.items() if value is not None}
        job = ProspectingJob(
            user_id=user_id,
            provider="hunter",
            status=ProspectingJobStatus.QUEUED.value,
            config_json=config,
            connector_version=connector.version,
            page_size=command.page_size,
            max_pages_per_domain=command.max_pages_per_domain,
            request_budget=command.request_budget,
            requests_used=0,
        )
        self._db.add(job)
        self._db.flush()
        for domain in domains:
            search = ProspectingSearch(
                user_id=user_id,
                provider="hunter",
                mode="batch_domain_search",
                query_json={
                    "domain": domain,
                    "page_size": command.page_size,
                    "max_pages": command.max_pages_per_domain,
                    **config,
                },
                status="queued",
                connector_version=connector.version,
            )
            self._db.add(search)
            self._db.flush()
            self._db.add(
                ProspectingJobItem(
                    job_id=job.id,
                    search_id=search.id,
                    domain=domain,
                    status=ProspectingItemStatus.PENDING.value,
                )
            )
        self._db.commit()
        self._db.refresh(job)
        return self._response(job)

    def list_jobs(
        self,
        *,
        user_id: int,
        limit: int = 20,
    ) -> List[ProspectingJobResponse]:
        rows = (
            self._db.query(ProspectingJob)
            .filter(ProspectingJob.user_id == user_id)
            .order_by(ProspectingJob.created_at.desc())
            .limit(limit)
            .all()
        )
        return [self._response(row) for row in rows]

    def list_due_job_ids(
        self,
        *,
        now: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[UUID]:
        """Find recoverable work; duplicate enqueue is safe because claims are leased."""
        now = now or datetime.utcnow()
        rows = (
            self._db.query(ProspectingJob.id)
            .filter(
                or_(
                    ProspectingJob.status == ProspectingJobStatus.QUEUED.value,
                    and_(
                        ProspectingJob.status
                        == ProspectingJobStatus.RETRY_WAIT.value,
                        ProspectingJob.next_attempt_at <= now,
                    ),
                    and_(
                        ProspectingJob.status
                        == ProspectingJobStatus.RUNNING.value,
                        or_(
                            ProspectingJob.lease_until.is_(None),
                            ProspectingJob.lease_until <= now,
                        ),
                    ),
                )
            )
            .order_by(ProspectingJob.created_at.asc())
            .limit(max(1, min(limit, 500)))
            .all()
        )
        return [row.id for row in rows]

    def get_job(self, job_id: UUID, *, user_id: int) -> ProspectingJobResponse:
        return self._response(self._owned_job(job_id, user_id=user_id))

    def pause_job(self, job_id: UUID, *, user_id: int) -> ProspectingJobResponse:
        job = self._owned_job(job_id, user_id=user_id)
        if job.status in TERMINAL_JOB_STATUSES:
            raise ProspectingJobConflict("Completed jobs cannot be paused")
        job.status = ProspectingJobStatus.PAUSED.value
        self._clear_lease(job)
        self._db.commit()
        self._db.refresh(job)
        return self._response(job)

    def resume_job(
        self,
        job_id: UUID,
        *,
        user_id: int,
        additional_requests: int = 0,
    ) -> ProspectingJobResponse:
        job = self._owned_job(job_id, user_id=user_id)
        if job.status in TERMINAL_JOB_STATUSES:
            raise ProspectingJobConflict("Completed jobs cannot be resumed")
        job.request_budget += additional_requests
        job.status = ProspectingJobStatus.QUEUED.value
        job.error_code = None
        job.next_attempt_at = None
        self._clear_lease(job)
        for item in job.items:
            if item.status not in TERMINAL_ITEM_STATUSES:
                item.status = ProspectingItemStatus.PENDING.value
                item.next_attempt_at = None
        self._db.commit()
        self._db.refresh(job)
        return self._response(job)

    def record_dispatch_failure(
        self,
        job_id: UUID,
        exc: HunterConnectorError,
        *,
        now: Optional[datetime] = None,
    ) -> ProspectingJobResponse:
        """Persist worker setup failures so a queued job never disappears."""
        job = (
            self._db.query(ProspectingJob)
            .filter(ProspectingJob.id == job_id)
            .with_for_update()
            .one_or_none()
        )
        if job is None:
            raise ProspectingJobNotFound("Prospecting job not found")
        if job.status in TERMINAL_JOB_STATUSES or job.status == (
            ProspectingJobStatus.PAUSED.value
        ):
            return self._response(job)

        now = now or datetime.utcnow()
        job.error_code = exc.error_code
        if exc.retryable:
            job.status = ProspectingJobStatus.RETRY_WAIT.value
            job.next_attempt_at = now + timedelta(seconds=30)
        else:
            job.status = ProspectingJobStatus.FAILED.value
            job.completed_at = now
            job.next_attempt_at = None
        self._clear_lease(job)
        self._db.commit()
        self._db.refresh(job)
        return self._response(job)

    def _owned_job(self, job_id: UUID, *, user_id: int) -> ProspectingJob:
        row = (
            self._db.query(ProspectingJob)
            .filter(
                ProspectingJob.id == job_id,
                ProspectingJob.user_id == user_id,
            )
            .one_or_none()
        )
        if row is None:
            raise ProspectingJobNotFound("Prospecting job not found")
        return row

    def _enabled_connector(self) -> ConnectorConfiguration:
        connector = (
            self._db.query(ConnectorConfiguration)
            .filter(
                ConnectorConfiguration.provider == "hunter",
                ConnectorConfiguration.enabled.is_(True),
            )
            .order_by(ConnectorConfiguration.updated_at.desc())
            .first()
        )
        if connector is None:
            raise ProspectingConnectorUnavailable(
                "Hunter connector is not enabled"
            )
        return connector

    @staticmethod
    def _clear_lease(job: ProspectingJob) -> None:
        job.leased_by = None
        job.lease_until = None

    @staticmethod
    def _response(job: ProspectingJob) -> ProspectingJobResponse:
        completed_items = sum(
            item.status == ProspectingItemStatus.COMPLETED.value
            for item in job.items
        )
        failed_items = sum(
            item.status
            in {
                ProspectingItemStatus.FAILED.value,
                ProspectingItemStatus.LEGAL_RESTRICTED.value,
            }
            for item in job.items
        )
        return ProspectingJobResponse(
            id=job.id,
            provider=job.provider,
            status=ProspectingJobStatus(job.status),
            connector_version=job.connector_version,
            page_size=job.page_size,
            max_pages_per_domain=job.max_pages_per_domain,
            request_budget=job.request_budget,
            requests_used=job.requests_used,
            provider_remaining=job.provider_remaining,
            provider_usage_unit=job.provider_usage_unit,
            total_items=len(job.items),
            completed_items=completed_items,
            failed_items=failed_items,
            contacts_found=sum(item.contacts_found for item in job.items),
            error_code=job.error_code,
            next_attempt_at=job.next_attempt_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            created_at=job.created_at,
            updated_at=job.updated_at,
            items=[
                ProspectingJobItemResponse(
                    id=item.id,
                    search_id=item.search_id,
                    domain=item.domain,
                    status=item.status,
                    next_offset=item.next_offset,
                    pages_completed=item.pages_completed,
                    requests_used=item.requests_used,
                    contacts_found=item.contacts_found,
                    attempt_count=item.attempt_count,
                    max_attempts=item.max_attempts,
                    truncated=item.truncated,
                    error_code=item.error_code,
                    next_attempt_at=item.next_attempt_at,
                    completed_at=item.completed_at,
                )
                for item in job.items
            ],
        )


class ProspectingJobRunner:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._prospecting = ProspectingService(db)

    async def run_slice(
        self,
        job_id: UUID,
        *,
        hunter: HunterClient,
        worker_id: str,
        max_requests: int = 5,
        lease_seconds: int = 180,
        now: Optional[datetime] = None,
    ) -> ProspectingJobResponse:
        now = now or datetime.utcnow()
        job = self._claim(
            job_id,
            worker_id=worker_id,
            now=now,
            lease_seconds=lease_seconds,
        )
        if job.status != ProspectingJobStatus.RUNNING.value:
            return ProspectingJobService._response(job)
        lease_version = job.lease_version

        try:
            usage = await hunter.usage()
        except HunterConnectorError as exc:
            if not self._owns_lease(job, worker_id, lease_version):
                return ProspectingJobService._response(job)
            self._mark_job_provider_failure(job, exc, now=now)
            self._db.commit()
            return ProspectingJobService._response(job)

        if not self._owns_lease(job, worker_id, lease_version):
            return ProspectingJobService._response(job)
        job.provider_remaining = usage.remaining
        job.provider_usage_unit = usage.unit
        if usage.remaining <= 0:
            job.status = ProspectingJobStatus.QUOTA_BLOCKED.value
            job.error_code = "provider_quota_unavailable"
            self._clear_lease(job)
            self._db.commit()
            return ProspectingJobService._response(job)
        self._db.commit()

        processed = 0
        while processed < max(1, max_requests):
            self._db.refresh(job)
            active_items = [
                item
                for item in job.items
                if item.status not in TERMINAL_ITEM_STATUSES
            ]
            if not active_items:
                self._finalize(job, now=now)
                break
            if job.requests_used >= job.request_budget:
                job.status = ProspectingJobStatus.BUDGET_EXHAUSTED.value
                job.error_code = "request_budget_exhausted"
                self._clear_lease(job)
                break

            item = next(
                (
                    candidate
                    for candidate in active_items
                    if candidate.status == ProspectingItemStatus.PENDING.value
                ),
                None,
            )
            if item is None:
                self._finalize(job, now=now)
                break

            item.status = ProspectingItemStatus.RUNNING.value
            search = item.search
            search.status = "running"
            self._db.commit()
            try:
                page = await hunter.domain_search_page(
                    domain=item.domain,
                    limit=job.page_size,
                    offset=item.next_offset,
                    **self._hunter_filters(job.config_json or {}),
                )
            except HunterConnectorError as exc:
                if not self._owns_lease(job, worker_id, lease_version):
                    return ProspectingJobService._response(job)
                processed += 1
                self._record_attempt(job, item)
                self._handle_item_failure(job, item, exc, now=now)
                self._db.commit()
                if job.status in {
                    ProspectingJobStatus.RETRY_WAIT.value,
                    ProspectingJobStatus.QUOTA_BLOCKED.value,
                }:
                    break
                continue

            if not self._owns_lease(job, worker_id, lease_version):
                return ProspectingJobService._response(job)
            processed += 1
            self._record_attempt(job, item)
            self._record_page(job, item, search, page, now=now)
            self._db.commit()

        if (
            job.status == ProspectingJobStatus.RUNNING.value
            and self._owns_lease(job, worker_id, lease_version)
        ):
            self._finalize_or_queue(job, now=now)
        self._db.commit()
        self._db.refresh(job)
        return ProspectingJobService._response(job)

    def _claim(
        self,
        job_id: UUID,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> ProspectingJob:
        job = (
            self._db.query(ProspectingJob)
            .filter(ProspectingJob.id == job_id)
            .with_for_update()
            .one_or_none()
        )
        if job is None:
            raise ProspectingJobNotFound("Prospecting job not found")
        if job.status in TERMINAL_JOB_STATUSES or job.status in {
            ProspectingJobStatus.PAUSED.value,
            ProspectingJobStatus.BUDGET_EXHAUSTED.value,
            ProspectingJobStatus.QUOTA_BLOCKED.value,
        }:
            return job
        if (
            job.status == ProspectingJobStatus.RETRY_WAIT.value
            and job.next_attempt_at is not None
            and job.next_attempt_at > now
        ):
            return job
        if job.status == ProspectingJobStatus.RETRY_WAIT.value:
            for item in job.items:
                if (
                    item.status == ProspectingItemStatus.RETRY_WAIT.value
                    and (
                        item.next_attempt_at is None
                        or item.next_attempt_at <= now
                    )
                ):
                    item.status = ProspectingItemStatus.PENDING.value
                    item.next_attempt_at = None
            job.next_attempt_at = None
        if job.status == ProspectingJobStatus.RUNNING.value:
            if job.lease_until is not None and job.lease_until > now:
                return job
            for item in job.items:
                if item.status == ProspectingItemStatus.RUNNING.value:
                    item.status = ProspectingItemStatus.PENDING.value

        job.status = ProspectingJobStatus.RUNNING.value
        job.leased_by = worker_id
        job.lease_until = now + timedelta(seconds=lease_seconds)
        job.lease_version += 1
        job.started_at = job.started_at or now
        job.error_code = None
        self._db.commit()
        self._db.refresh(job)
        return job

    def _owns_lease(
        self,
        job: ProspectingJob,
        worker_id: str,
        lease_version: int,
    ) -> bool:
        self._db.refresh(job)
        return (
            job.status == ProspectingJobStatus.RUNNING.value
            and job.leased_by == worker_id
            and job.lease_version == lease_version
        )

    @staticmethod
    def _hunter_filters(config: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "contact_type": config.get("contact_type"),
            "seniorities": list(config.get("seniorities") or []),
            "departments": list(config.get("departments") or []),
            "decision_maker": config.get("decision_maker"),
            "verification_statuses": list(
                config.get("verification_statuses") or []
            ),
        }

    @staticmethod
    def _record_attempt(job: ProspectingJob, item: ProspectingJobItem) -> None:
        job.requests_used += 1
        item.requests_used += 1

    def _record_page(
        self,
        job: ProspectingJob,
        item: ProspectingJobItem,
        search: ProspectingSearch,
        page,
        *,
        now: datetime,
    ) -> None:
        candidates = list(page.data.get("emails") or [])
        self._prospecting._persist_candidates(
            search,
            candidates,
            defaults={
                "company": page.data.get("organization"),
                "domain": page.data.get("domain") or item.domain,
            },
        )
        item.next_offset += job.page_size
        item.pages_completed += 1
        item.attempt_count = 0
        item.error_code = None
        item.next_attempt_at = None
        item.contacts_found = (
            self._db.query(ProspectingContact)
            .filter(ProspectingContact.search_id == search.id)
            .count()
        )
        search.result_count = item.contacts_found
        reached_provider_end = item.next_offset >= page.total_results
        reached_page_cap = item.pages_completed >= job.max_pages_per_domain
        if reached_provider_end or reached_page_cap:
            item.status = ProspectingItemStatus.COMPLETED.value
            item.truncated = reached_page_cap and not reached_provider_end
            item.completed_at = now
            search.status = "completed"
            search.completed_at = now
        else:
            item.status = ProspectingItemStatus.PENDING.value

    def _handle_item_failure(
        self,
        job: ProspectingJob,
        item: ProspectingJobItem,
        exc: HunterConnectorError,
        *,
        now: datetime,
    ) -> None:
        item.attempt_count += 1
        item.error_code = exc.error_code
        item.search.error_code = exc.error_code
        if exc.legal_restriction:
            item.status = ProspectingItemStatus.LEGAL_RESTRICTED.value
            item.completed_at = now
            item.search.status = "failed"
            item.search.completed_at = now
            return
        if exc.error_code == "quota_exhausted":
            item.status = ProspectingItemStatus.QUOTA_BLOCKED.value
            job.status = ProspectingJobStatus.QUOTA_BLOCKED.value
            job.error_code = exc.error_code
            self._clear_lease(job)
            return
        if exc.retryable and item.attempt_count < item.max_attempts:
            backoff = min(30 * (2 ** (item.attempt_count - 1)), 3600)
            item.status = ProspectingItemStatus.RETRY_WAIT.value
            item.next_attempt_at = now + timedelta(seconds=backoff)
            job.status = ProspectingJobStatus.RETRY_WAIT.value
            job.next_attempt_at = item.next_attempt_at
            job.error_code = exc.error_code
            self._clear_lease(job)
            return
        item.status = ProspectingItemStatus.FAILED.value
        item.completed_at = now
        item.search.status = "failed"
        item.search.completed_at = now

    def _mark_job_provider_failure(
        self,
        job: ProspectingJob,
        exc: HunterConnectorError,
        *,
        now: datetime,
    ) -> None:
        if exc.error_code == "quota_exhausted":
            job.status = ProspectingJobStatus.QUOTA_BLOCKED.value
        elif exc.retryable:
            job.status = ProspectingJobStatus.RETRY_WAIT.value
            job.next_attempt_at = now + timedelta(seconds=30)
        else:
            job.status = ProspectingJobStatus.FAILED.value
            job.completed_at = now
        job.error_code = exc.error_code
        self._clear_lease(job)

    def _finalize_or_queue(self, job: ProspectingJob, *, now: datetime) -> None:
        if all(item.status in TERMINAL_ITEM_STATUSES for item in job.items):
            self._finalize(job, now=now)
        elif job.requests_used >= job.request_budget:
            job.status = ProspectingJobStatus.BUDGET_EXHAUSTED.value
            job.error_code = "request_budget_exhausted"
            self._clear_lease(job)
        else:
            job.status = ProspectingJobStatus.QUEUED.value
            self._clear_lease(job)

    def _finalize(self, job: ProspectingJob, *, now: datetime) -> None:
        has_errors = any(
            item.status
            in {
                ProspectingItemStatus.FAILED.value,
                ProspectingItemStatus.LEGAL_RESTRICTED.value,
            }
            for item in job.items
        )
        job.status = (
            ProspectingJobStatus.COMPLETED_WITH_ERRORS.value
            if has_errors
            else ProspectingJobStatus.COMPLETED.value
        )
        job.completed_at = now
        job.error_code = None
        job.next_attempt_at = None
        self._clear_lease(job)

    @staticmethod
    def _clear_lease(job: ProspectingJob) -> None:
        job.leased_by = None
        job.lease_until = None
