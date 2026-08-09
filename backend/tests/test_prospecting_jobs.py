from datetime import datetime, timedelta

import pytest

from app.api.v1.auth import get_current_active_user
from app.main import app
from app.models.database import (
    ConnectorConfiguration,
    ProspectingContact,
    ProspectingJob,
    ProspectingJobItem,
    User,
)
from app.services.prospecting_jobs import (
    ProspectingJobCreate,
    ProspectingJobRunner,
    ProspectingJobService,
    ProspectingJobStatus,
)
from app.integrations.hunter import (
    build_hunter_client,
    HunterConnectorError,
    HunterDomainSearchPage,
    HunterUsage,
)


class FakeBatchHunter:
    def __init__(self, pages=None, *, remaining=100):
        self.pages = pages or {}
        self.remaining = remaining
        self.calls = []
        self.usage_calls = 0

    async def usage(self):
        self.usage_calls += 1
        return HunterUsage(remaining=self.remaining, unit="searches")

    async def domain_search_page(self, **kwargs):
        self.calls.append(kwargs)
        key = (kwargs["domain"], kwargs["offset"])
        value = self.pages.get(key)
        if isinstance(value, Exception):
            raise value
        return value or HunterDomainSearchPage(
            data={
                "domain": kwargs["domain"],
                "organization": kwargs["domain"].split(".")[0].title(),
                "emails": [],
            },
            total_results=0,
        )


def page(domain, emails, total):
    return HunterDomainSearchPage(
        data={
            "domain": domain,
            "organization": domain.split(".")[0].title(),
            "emails": [
                {
                    "value": email,
                    "verification": {"status": "valid"},
                    "sources": [],
                }
                for email in emails
            ],
        },
        total_results=total,
    )


@pytest.fixture
def configured_api(api_context, tmp_path):
    client, db, user = api_context
    secret = tmp_path / "hunter.key"
    secret.write_text("not-used-by-contract-tests", encoding="utf-8")
    connector = ConnectorConfiguration(
        provider="hunter",
        name="primary",
        enabled=True,
        config_json={"timeout_seconds": 15},
        secret_ref=str(secret),
        version=7,
        last_status="healthy",
        updated_by_user_id=user.id,
    )
    db.add(connector)
    db.commit()
    return client, db, user


def job_command(**overrides):
    values = {
        "domains": ["https://ACME.com/about", "acme.com", "contoso.com"],
        "page_size": 2,
        "max_pages_per_domain": 2,
        "request_budget": 4,
        "verification_statuses": ["valid"],
    }
    values.update(overrides)
    return ProspectingJobCreate(**values)


def test_job_api_normalizes_domains_persists_budget_and_enqueues(
    configured_api,
    monkeypatch,
):
    client, db, _ = configured_api
    queued = []
    monkeypatch.setattr(
        "app.api.v1.prospecting.enqueue_prospecting_job",
        lambda job_id: queued.append(job_id),
    )

    response = client.post(
        "/api/v1/prospecting/jobs",
        json=job_command().model_dump(),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["total_items"] == 2
    assert body["request_budget"] == 4
    assert body["requests_used"] == 0
    assert body["connector_version"] == 7
    assert [item["domain"] for item in body["items"]] == [
        "acme.com",
        "contoso.com",
    ]
    assert queued == [body["id"]]
    assert db.query(ProspectingJob).count() == 1
    assert db.query(ProspectingJobItem).count() == 2
    assert "linkedin" not in response.text.lower()


@pytest.mark.asyncio
async def test_runner_commits_each_page_and_resumes_from_persisted_offset(
    configured_api,
):
    _, db, user = configured_api
    job = ProspectingJobService(db).create_job(
        job_command(domains=["acme.com"], request_budget=2),
        user_id=user.id,
    )
    hunter = FakeBatchHunter(
        {
            ("acme.com", 0): page(
                "acme.com",
                ["one@acme.com", "two@acme.com"],
                3,
            ),
            ("acme.com", 2): page(
                "acme.com",
                ["two@acme.com", "three@acme.com"],
                3,
            ),
        }
    )
    runner = ProspectingJobRunner(db)

    first = await runner.run_slice(
        job.id,
        hunter=hunter,
        worker_id="worker-a",
        max_requests=1,
    )
    db.expire_all()
    item = db.query(ProspectingJobItem).one()
    assert first.status == ProspectingJobStatus.QUEUED
    assert item.next_offset == 2
    assert item.pages_completed == 1
    assert db.query(ProspectingContact).count() == 2

    second = await runner.run_slice(
        job.id,
        hunter=hunter,
        worker_id="worker-b",
        max_requests=1,
    )
    db.expire_all()
    item = db.query(ProspectingJobItem).one()
    assert second.status == ProspectingJobStatus.COMPLETED
    assert item.next_offset == 4
    assert item.pages_completed == 2
    assert db.query(ProspectingContact).count() == 3
    assert [call["offset"] for call in hunter.calls] == [0, 2]


@pytest.mark.asyncio
async def test_request_budget_pauses_before_an_unbudgeted_provider_call(
    configured_api,
):
    _, db, user = configured_api
    service = ProspectingJobService(db)
    job = service.create_job(
        job_command(domains=["acme.com", "contoso.com"], request_budget=1),
        user_id=user.id,
    )
    hunter = FakeBatchHunter()

    result = await ProspectingJobRunner(db).run_slice(
        job.id,
        hunter=hunter,
        worker_id="budget-worker",
        max_requests=10,
    )

    assert result.status == ProspectingJobStatus.BUDGET_EXHAUSTED
    assert result.requests_used == 1
    assert len(hunter.calls) == 1
    resumed = service.resume_job(
        job.id,
        user_id=user.id,
        additional_requests=1,
    )
    assert resumed.status == ProspectingJobStatus.QUEUED
    assert resumed.request_budget == 2


@pytest.mark.asyncio
async def test_provider_usage_gate_blocks_without_spending_job_budget(
    configured_api,
):
    _, db, user = configured_api
    job = ProspectingJobService(db).create_job(
        job_command(domains=["acme.com"], request_budget=1),
        user_id=user.id,
    )
    hunter = FakeBatchHunter(remaining=0)

    result = await ProspectingJobRunner(db).run_slice(
        job.id,
        hunter=hunter,
        worker_id="quota-worker",
        max_requests=5,
    )

    assert result.status == ProspectingJobStatus.QUOTA_BLOCKED
    assert result.provider_remaining == 0
    assert result.requests_used == 0
    assert hunter.calls == []


@pytest.mark.asyncio
async def test_retryable_failure_backoffs_and_manual_resume_keeps_offset(
    configured_api,
):
    _, db, user = configured_api
    service = ProspectingJobService(db)
    created = service.create_job(
        job_command(domains=["acme.com"], request_budget=2),
        user_id=user.id,
    )
    job = db.get(ProspectingJob, created.id)
    retryable = HunterConnectorError(
        error_code="rate_limited",
        retryable=True,
    )
    hunter = FakeBatchHunter({("acme.com", 0): retryable})
    started = datetime(2026, 8, 9, 12, 0, 0)

    result = await ProspectingJobRunner(db).run_slice(
        job.id,
        hunter=hunter,
        worker_id="retry-worker",
        max_requests=5,
        now=started,
    )

    item = db.query(ProspectingJobItem).one()
    assert result.status == ProspectingJobStatus.RETRY_WAIT
    assert result.requests_used == 1
    assert item.next_offset == 0
    assert item.next_attempt_at == started + timedelta(seconds=30)
    assert item.error_code == "rate_limited"

    service.resume_job(job.id, user_id=user.id)
    hunter.pages[("acme.com", 0)] = page("acme.com", [], 0)
    completed = await ProspectingJobRunner(db).run_slice(
        job.id,
        hunter=hunter,
        worker_id="retry-worker-2",
        max_requests=5,
        now=started,
    )
    assert completed.status == ProspectingJobStatus.COMPLETED
    assert [call["offset"] for call in hunter.calls] == [0, 0]


@pytest.mark.asyncio
async def test_expired_read_lease_is_recovered_without_skipping_page(
    configured_api,
):
    _, db, user = configured_api
    service = ProspectingJobService(db)
    created = service.create_job(
        job_command(domains=["acme.com"], request_budget=2),
        user_id=user.id,
    )
    job = db.get(ProspectingJob, created.id)
    item = db.query(ProspectingJobItem).one()
    job.status = ProspectingJobStatus.RUNNING.value
    job.leased_by = "lost-worker"
    job.lease_until = datetime(2026, 8, 9, 11, 59, 0)
    item.status = "running"
    db.commit()
    hunter = FakeBatchHunter(
        {("acme.com", 0): page("acme.com", ["one@acme.com"], 1)}
    )

    result = await ProspectingJobRunner(db).run_slice(
        job.id,
        hunter=hunter,
        worker_id="replacement-worker",
        max_requests=1,
        now=datetime(2026, 8, 9, 12, 0, 0),
    )

    assert result.status == ProspectingJobStatus.COMPLETED
    assert hunter.calls[0]["offset"] == 0
    assert db.query(ProspectingContact).count() == 1


@pytest.mark.asyncio
async def test_late_worker_cannot_advance_cursor_after_lease_is_replaced(
    configured_api,
):
    _, db, user = configured_api
    created = ProspectingJobService(db).create_job(
        job_command(domains=["acme.com"], request_budget=2),
        user_id=user.id,
    )

    class LeaseStealingHunter(FakeBatchHunter):
        async def domain_search_page(self, **kwargs):
            self.calls.append(kwargs)
            row = db.get(ProspectingJob, created.id)
            row.lease_version += 1
            row.leased_by = "replacement-worker"
            row.lease_until = datetime(2026, 8, 9, 12, 5, 0)
            db.commit()
            return page("acme.com", ["late@acme.com"], 1)

    result = await ProspectingJobRunner(db).run_slice(
        created.id,
        hunter=LeaseStealingHunter(),
        worker_id="late-worker",
        max_requests=1,
        now=datetime(2026, 8, 9, 12, 0, 0),
    )

    db.expire_all()
    job = db.get(ProspectingJob, created.id)
    item = db.query(ProspectingJobItem).one()
    assert result.status == ProspectingJobStatus.RUNNING
    assert job.leased_by == "replacement-worker"
    assert item.next_offset == 0
    assert db.query(ProspectingContact).count() == 0


def test_other_user_cannot_view_or_resume_job(configured_api, monkeypatch):
    client, db, user = configured_api
    job = ProspectingJobService(db).create_job(
        job_command(domains=["acme.com"]),
        user_id=user.id,
    )
    other = User(
        username="job-other",
        email="job-other@example.com",
        hashed_password="unused",
        is_active=True,
    )
    db.add(other)
    db.commit()
    app.dependency_overrides[get_current_active_user] = lambda: other
    monkeypatch.setattr(
        "app.api.v1.prospecting.enqueue_prospecting_job",
        lambda _: None,
    )

    assert client.get(f"/api/v1/prospecting/jobs/{job.id}").status_code == 404
    assert client.post(
        f"/api/v1/prospecting/jobs/{job.id}/resume",
        json={"additional_requests": 1},
    ).status_code == 404


def test_worker_connector_resolution_fails_closed_after_version_change(
    configured_api,
):
    _, db, user = configured_api
    created = ProspectingJobService(db).create_job(
        job_command(domains=["acme.com"]),
        user_id=user.id,
    )
    connector = db.query(ConnectorConfiguration).one()
    connector.version += 1
    db.commit()

    with pytest.raises(HunterConnectorError) as caught:
        build_hunter_client(db, expected_version=created.connector_version)

    assert caught.value.error_code == "connector_version_changed"
    assert caught.value.retryable is False


def test_dispatch_setup_failure_is_visible_in_durable_job(configured_api):
    _, db, user = configured_api
    service = ProspectingJobService(db)
    created = service.create_job(
        job_command(domains=["acme.com"]),
        user_id=user.id,
    )

    result = service.record_dispatch_failure(
        created.id,
        HunterConnectorError(
            error_code="connector_secret_unavailable",
            retryable=False,
        ),
    )

    assert result.status == ProspectingJobStatus.FAILED
    assert result.error_code == "connector_secret_unavailable"
    assert result.completed_at is not None
