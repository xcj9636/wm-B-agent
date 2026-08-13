from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.media.intent_vault import MediaIntentVaultUnavailable
from app.services.media.policy import MediaPolicyDenied
from app.services.media.submission_authorizer import (
    MediaSubmissionAuthorizationDenied,
)
from app.services.media.submission import MediaIntentMismatch
from app.services.media.submission_worker import run_media_submission_batch
from app.services.media.worker_runtime import MediaRuntimeUnavailable


NOW = datetime(2026, 8, 13, 10, 0, 0)


class FakeJobs:
    def __init__(self, jobs):
        self.jobs = list(jobs)
        self.claims = []
        self.failures = []

    def claim_batch(self, **kwargs):
        self.claims.append(kwargs)
        claimed = self.jobs[: kwargs["limit"]]
        self.jobs = self.jobs[kwargs["limit"] :]
        return claimed

    def fail_before_submission(self, job_id, **kwargs):
        self.failures.append((job_id, kwargs))
        return SimpleNamespace(status="failed")


class FakeVault:
    def __init__(self, failures=None):
        self.failures = failures or {}
        self.calls = []

    def load(self, payload_ref):
        self.calls.append(payload_ref)
        failure = self.failures.get(payload_ref)
        if failure is not None:
            raise failure
        return SimpleNamespace(prompt="must-not-enter-task-result")


class FakeAuthorizer:
    def __init__(self, failures=None):
        self.failures = failures or {}
        self.calls = []

    def authorize(self, job, intent, *, now):
        self.calls.append((job.id, intent, now))
        failure = self.failures.get(job.id)
        if failure is not None:
            raise failure
        return SimpleNamespace(
            principal=SimpleNamespace(user_id=job.owner_user_id),
            decision=SimpleNamespace(decision_id=uuid4()),
        )


class FakeAdapter:
    def __init__(self, job_id, closed):
        self.job_id = job_id
        self.closed = closed

    async def aclose(self):
        self.closed.append(self.job_id)


class FakeRuntimeFactory:
    def __init__(self, closed, failures=None):
        self.closed = closed
        self.failures = failures or {}
        self.calls = []

    def build(self, job):
        self.calls.append(job.id)
        failure = self.failures.get(job.id)
        if failure is not None:
            raise failure
        return FakeAdapter(job.id, self.closed)


class FakeCoordinator:
    def __init__(self, adapter, results, calls, *, failure=None):
        self.adapter = adapter
        self.results = results
        self.calls = calls
        self.failure = failure

    async def submit_claimed(self, job_id, **kwargs):
        self.calls.append((job_id, kwargs, self.adapter.job_id))
        if self.failure is not None:
            raise self.failure
        return self.results.get(
            job_id,
            SimpleNamespace(status="submitted"),
        )


def job(index):
    return SimpleNamespace(
        id=uuid4(),
        owner_user_id=index,
        payload_ref=f"vault://media-intents/{uuid4()}",
        fencing_token=index,
    )


@pytest.mark.asyncio
async def test_batch_claims_one_at_a_time_authorizes_and_closes_pinned_runtime():
    jobs = [job(4), job(8)]
    service = FakeJobs(jobs)
    vault = FakeVault()
    authorizer = FakeAuthorizer()
    closed = []
    runtime_factory = FakeRuntimeFactory(closed)
    calls = []

    result = await run_media_submission_batch(
        jobs=service,
        vault=vault,
        authorizer=authorizer,
        runtime_factory=runtime_factory,
        coordinator_builder=lambda adapter: FakeCoordinator(adapter, {}, calls),
        worker_id="media-submit-a",
        now=NOW,
        batch_size=10,
        lease_seconds=300,
    )

    assert service.claims[0] == {
        "worker_id": "media-submit-a",
        "now": NOW,
        "limit": 1,
        "lease_seconds": 300,
    }
    assert all(claim["limit"] == 1 for claim in service.claims)
    assert [call[0] for call in authorizer.calls] == [item.id for item in jobs]
    assert runtime_factory.calls == [item.id for item in jobs]
    assert closed == [item.id for item in jobs]
    assert [call[1]["fencing_token"] for call in calls] == [4, 8]
    assert result == {
        "claimed": 2,
        "submitted": 2,
        "submission_unknown": 0,
        "failed_before_submission": 0,
        "deferred": 0,
    }
    assert "must-not-enter-task-result" not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "error_code"),
    [
        (
            MediaIntentVaultUnavailable("secret path must not leak"),
            "media_intent_unavailable",
        ),
        (
            MediaSubmissionAuthorizationDenied("private identity detail"),
            "media_authorization_denied",
        ),
        (
            MediaPolicyDenied(["asset_rights_unverified"]),
            "media_policy_denied",
        ),
    ],
)
async def test_pre_effect_failures_are_terminal_sanitized_and_releaseable(
    failure,
    error_code,
):
    target = job(3)
    vault_failures = (
        {target.payload_ref: failure}
        if isinstance(failure, MediaIntentVaultUnavailable)
        else {}
    )
    authorizer_failures = {} if vault_failures else {target.id: failure}
    service = FakeJobs([target])

    result = await run_media_submission_batch(
        jobs=service,
        vault=FakeVault(vault_failures),
        authorizer=FakeAuthorizer(authorizer_failures),
        runtime_factory=FakeRuntimeFactory([]),
        coordinator_builder=lambda adapter: pytest.fail(
            "provider coordinator must not be constructed"
        ),
        worker_id="media-submit-a",
        now=NOW,
        batch_size=1,
        lease_seconds=300,
    )

    assert len(service.failures) == 1
    failed_job_id, values = service.failures[0]
    assert failed_job_id == target.id
    assert values["error_code"] == error_code
    assert "secret" not in repr(values)
    assert "private" not in repr(values)
    assert result["failed_before_submission"] == 1


@pytest.mark.asyncio
async def test_runtime_unavailable_is_deferred_without_false_terminal_failure():
    target = job(6)
    service = FakeJobs([target])
    runtime_factory = FakeRuntimeFactory(
        [],
        failures={
            target.id: MediaRuntimeUnavailable("runtime secret is unavailable")
        },
    )

    result = await run_media_submission_batch(
        jobs=service,
        vault=FakeVault(),
        authorizer=FakeAuthorizer(),
        runtime_factory=runtime_factory,
        coordinator_builder=lambda adapter: pytest.fail(
            "unavailable runtime must not create a coordinator"
        ),
        worker_id="media-submit-a",
        now=NOW,
        batch_size=10,
        lease_seconds=300,
    )

    assert service.failures == []
    assert result["deferred"] == 1
    assert result["claimed"] == 1
    assert "secret" not in repr(result)


@pytest.mark.asyncio
async def test_unsupported_or_mismatched_intent_fails_before_effect():
    target = job(7)
    service = FakeJobs([target])
    closed = []

    result = await run_media_submission_batch(
        jobs=service,
        vault=FakeVault(),
        authorizer=FakeAuthorizer(),
        runtime_factory=FakeRuntimeFactory(closed),
        coordinator_builder=lambda adapter: FakeCoordinator(
            adapter,
            {},
            [],
            failure=MediaIntentMismatch("prompt must not leak"),
        ),
        worker_id="media-submit-a",
        now=NOW,
        batch_size=1,
        lease_seconds=300,
    )

    assert service.failures[0][1]["error_code"] == "media_intent_mismatch"
    assert result["failed_before_submission"] == 1
    assert closed == [target.id]
    assert "prompt" not in repr(result)


@pytest.mark.asyncio
async def test_ambiguous_provider_outcome_is_counted_but_never_resubmitted():
    target = job(9)
    calls = []
    closed = []
    result = await run_media_submission_batch(
        jobs=FakeJobs([target]),
        vault=FakeVault(),
        authorizer=FakeAuthorizer(),
        runtime_factory=FakeRuntimeFactory(closed),
        coordinator_builder=lambda adapter: FakeCoordinator(
            adapter,
            {target.id: SimpleNamespace(status="submission_unknown")},
            calls,
        ),
        worker_id="media-submit-a",
        now=NOW,
        batch_size=10,
        lease_seconds=300,
    )

    assert len(calls) == 1
    assert result["submission_unknown"] == 1
    assert result["submitted"] == 0
    assert closed == [target.id]


@pytest.mark.asyncio
async def test_each_claim_uses_fresh_time_and_operational_bounds():
    jobs = [job(1), job(2)]
    moments = iter(
        [
            datetime(2026, 8, 13, 10, 0, 1),
            datetime(2026, 8, 13, 10, 0, 2),
        ]
    )
    service = FakeJobs(jobs)
    await run_media_submission_batch(
        jobs=service,
        vault=FakeVault(),
        authorizer=FakeAuthorizer(),
        runtime_factory=FakeRuntimeFactory([]),
        coordinator_builder=lambda adapter: FakeCoordinator(adapter, {}, []),
        worker_id="media-submit-a",
        now=NOW,
        batch_size=10,
        lease_seconds=300,
        clock=lambda: next(moments),
    )
    assert [claim["now"] for claim in service.claims[:2]] == [
        NOW,
        datetime(2026, 8, 13, 10, 0, 1),
    ]

    for batch_size, lease_seconds in [(0, 300), (101, 300), (1, 299), (1, 901)]:
        with pytest.raises(ValueError):
            await run_media_submission_batch(
                jobs=FakeJobs([]),
                vault=FakeVault(),
                authorizer=FakeAuthorizer(),
                runtime_factory=FakeRuntimeFactory([]),
                coordinator_builder=lambda adapter: None,
                worker_id="media-submit-a",
                now=NOW,
                batch_size=batch_size,
                lease_seconds=lease_seconds,
            )
