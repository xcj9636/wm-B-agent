from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.integrations.fal_media import MediaProviderError, MediaSubmissionReceipt
from app.models.database import MediaGenerationAttempt, MediaGenerationJob
from app.services.agent_runtime.contracts import ExecutionPrincipal, Sensitivity
from app.services.media.contracts import GenerationIntent, GenerationMode
from app.services.media.jobs import MediaGenerationJobService, MediaJobLeaseConflict
from app.services.media.submission import (
    MediaIntentMismatch,
    MediaSubmissionCoordinator,
)


NOW = datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc)


def intent(*, org_id, user_id=7):
    return GenerationIntent(
        project_id=uuid4(),
        shot_id=uuid4(),
        persona_version_id=uuid4(),
        org_id=org_id,
        actor_user_id=user_id,
        mode=GenerationMode.TEXT_TO_VIDEO,
        prompt="Approved export product film",
        sensitivity=Sensitivity.INTERNAL,
        persona_approved=True,
        storyboard_approved=True,
    )


def principal(org_id):
    return ExecutionPrincipal(
        org_id=org_id,
        user_id=7,
        roles={"media_operator"},
        entitlements_hash="a" * 64,
        authn_context="worker:celery",
    )


class FakeVault:
    def __init__(self, value, calls):
        self.value = value
        self.calls = calls

    def load(self, payload_ref):
        self.calls.append(("vault.load", payload_ref))
        return self.value


class FakePolicy:
    def __init__(self, calls):
        self.calls = calls

    def verify(self, decision, generation_intent, *, now):
        self.calls.append(("policy.verify", generation_intent.input_hash()))


class FakeAdapter:
    def __init__(self, calls, *, failure=None):
        self.calls = calls
        self.failure = failure

    async def submit(self, *, model_id, arguments):
        self.calls.append(("provider.submit", model_id, arguments))
        if self.failure is not None:
            raise self.failure
        return MediaSubmissionReceipt(request_id="fal-request-1")


class FakeInputResolver:
    def __init__(self, calls, *, arguments=None, failure=None):
        self.calls = calls
        self.arguments = arguments or {"prompt": "resolved"}
        self.failure = failure

    def resolve(self, generation_intent, *, now):
        self.calls.append(("input.resolve", generation_intent.mode.value))
        if self.failure is not None:
            raise self.failure
        return self.arguments


class FakeJobs:
    def __init__(self, db_session, calls):
        self.db = db_session
        self.calls = calls

    def begin_submission(self, job_id, **kwargs):
        self.calls.append(("jobs.begin_submission", str(job_id)))
        job = self.db.get(MediaGenerationJob, job_id)
        attempt = MediaGenerationAttempt(
            job_id=job.id,
            attempt_number=1,
            fencing_token=kwargs["fencing_token"],
            provider=job.provider,
            model_id=job.model_id,
            status="submitting",
            effect_state="started",
            started_at=kwargs["now"].replace(tzinfo=None),
        )
        self.db.add(attempt)
        job.effect_state = "started"
        self.db.commit()
        return attempt

    def record_submitted(self, job_id, **kwargs):
        self.calls.append(("jobs.record_submitted", kwargs["provider_request_id"]))
        job = self.db.get(MediaGenerationJob, job_id)
        job.status = "submitted"
        job.effect_state = "confirmed"
        job.provider_request_id = kwargs["provider_request_id"]
        self.db.commit()
        return job

    def mark_submission_unknown(self, job_id, **kwargs):
        self.calls.append(("jobs.mark_submission_unknown", kwargs["error_code"]))
        job = self.db.get(MediaGenerationJob, job_id)
        job.status = "submission_unknown"
        job.effect_state = "unknown"
        job.error_code = kwargs["error_code"]
        self.db.commit()
        return job


def stored_job(db_session, generation_intent):
    job = MediaGenerationJob(
        org_id=generation_intent.org_id,
        owner_user_id=generation_intent.actor_user_id,
        project_id=generation_intent.project_id,
        storyboard_version_id=uuid4(),
        shot_id=generation_intent.shot_id,
        runtime_revision_id=uuid4(),
        idempotency_key="media-submit:test:job",
        input_hash="b" * 64,
        intent_hash=generation_intent.input_hash(),
        payload_ref="vault://media-intents/test/job",
        mode="text_to_video",
        provider="fal",
        model_id="fal-ai/veo3/fast",
        sensitivity="internal",
        status="running",
        effect_state="none",
        fencing_token=3,
        leased_by="worker-a",
        lease_until=NOW.replace(tzinfo=None).replace(hour=11),
        reserved_cost_microusd=1,
        estimate_hash="c" * 64,
        budget_period_start=NOW.date().replace(day=1),
        deadline_at=NOW.replace(tzinfo=None).replace(hour=12),
    )
    db_session.add(job)
    db_session.commit()
    return job


@pytest.mark.asyncio
async def test_policy_is_verified_before_effect_and_provider_submission(db_session):
    calls = []
    generation_intent = intent(org_id=uuid4())
    job = stored_job(db_session, generation_intent)
    coordinator = MediaSubmissionCoordinator(
        db_session,
        jobs=FakeJobs(db_session, calls),
        vault=FakeVault(generation_intent, calls),
        policy=FakePolicy(calls),
        adapter=FakeAdapter(calls),
    )

    submitted = await coordinator.submit_claimed(
        job.id,
        worker_id="worker-a",
        fencing_token=3,
        principal=principal(generation_intent.org_id),
        decision=SimpleNamespace(decision_id=uuid4()),
        now=NOW,
    )

    assert submitted.status == "submitted"
    assert [call[0] for call in calls] == [
        "vault.load",
        "policy.verify",
        "jobs.begin_submission",
        "provider.submit",
        "jobs.record_submitted",
    ]
    assert calls[3][2] == {"prompt": "Approved export product film"}


@pytest.mark.asyncio
async def test_server_resolves_provider_input_after_policy_before_effect(db_session):
    calls = []
    generation_intent = intent(org_id=uuid4()).model_copy(
        update={
            "mode": GenerationMode.IMAGE_TO_VIDEO,
            "reference_asset_ids": [uuid4()],
        }
    )
    job = stored_job(db_session, generation_intent)
    job.mode = GenerationMode.IMAGE_TO_VIDEO.value
    db_session.commit()
    arguments = {
        "prompt": generation_intent.prompt,
        "image_url": "https://objects.example.test/provider-input",
    }
    coordinator = MediaSubmissionCoordinator(
        db_session,
        jobs=FakeJobs(db_session, calls),
        vault=FakeVault(generation_intent, calls),
        policy=FakePolicy(calls),
        input_resolver=FakeInputResolver(calls, arguments=arguments),
        adapter=FakeAdapter(calls),
    )

    await coordinator.submit_claimed(
        job.id,
        worker_id="worker-a",
        fencing_token=3,
        principal=principal(generation_intent.org_id),
        decision=SimpleNamespace(decision_id=uuid4()),
        now=NOW,
    )

    assert [call[0] for call in calls] == [
        "vault.load",
        "policy.verify",
        "input.resolve",
        "jobs.begin_submission",
        "provider.submit",
        "jobs.record_submitted",
    ]
    assert calls[4][2] == arguments


@pytest.mark.asyncio
async def test_provider_input_failure_happens_before_effect(db_session):
    calls = []
    generation_intent = intent(org_id=uuid4())
    job = stored_job(db_session, generation_intent)
    coordinator = MediaSubmissionCoordinator(
        db_session,
        jobs=FakeJobs(db_session, calls),
        vault=FakeVault(generation_intent, calls),
        policy=FakePolicy(calls),
        input_resolver=FakeInputResolver(
            calls,
            failure=MediaIntentMismatch("provider input denied"),
        ),
        adapter=FakeAdapter(calls),
    )

    with pytest.raises(MediaIntentMismatch):
        await coordinator.submit_claimed(
            job.id,
            worker_id="worker-a",
            fencing_token=3,
            principal=principal(generation_intent.org_id),
            decision=SimpleNamespace(decision_id=uuid4()),
            now=NOW,
        )

    assert [call[0] for call in calls] == [
        "vault.load",
        "policy.verify",
        "input.resolve",
    ]
    assert db_session.query(MediaGenerationAttempt).count() == 0


@pytest.mark.asyncio
async def test_tampered_vault_intent_is_rejected_before_effect(db_session):
    calls = []
    approved = intent(org_id=uuid4())
    job = stored_job(db_session, approved)
    tampered = approved.model_copy(update={"prompt": "Tampered prompt"})
    coordinator = MediaSubmissionCoordinator(
        db_session,
        jobs=FakeJobs(db_session, calls),
        vault=FakeVault(tampered, calls),
        policy=FakePolicy(calls),
        adapter=FakeAdapter(calls),
    )

    with pytest.raises(MediaIntentMismatch):
        await coordinator.submit_claimed(
            job.id,
            worker_id="worker-a",
            fencing_token=3,
            principal=principal(approved.org_id),
            decision=SimpleNamespace(decision_id=uuid4()),
            now=NOW,
        )

    assert [call[0] for call in calls] == ["vault.load"]
    assert db_session.query(MediaGenerationAttempt).count() == 0


@pytest.mark.asyncio
async def test_any_provider_exception_after_effect_start_becomes_unknown(db_session):
    calls = []
    generation_intent = intent(org_id=uuid4())
    job = stored_job(db_session, generation_intent)
    coordinator = MediaSubmissionCoordinator(
        db_session,
        jobs=FakeJobs(db_session, calls),
        vault=FakeVault(generation_intent, calls),
        policy=FakePolicy(calls),
        adapter=FakeAdapter(
            calls,
            failure=MediaProviderError(
                error_code="provider_timeout",
                retryable=True,
            ),
        ),
    )

    result = await coordinator.submit_claimed(
        job.id,
        worker_id="worker-a",
        fencing_token=3,
        principal=principal(generation_intent.org_id),
        decision=SimpleNamespace(decision_id=uuid4()),
        now=NOW,
    )

    assert result.status == "submission_unknown"
    assert result.error_code == "provider_timeout"
    assert calls[-1] == ("jobs.mark_submission_unknown", "provider_timeout")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        None,
        MediaProviderError(error_code="provider_timeout", retryable=True),
    ],
)
async def test_provider_completion_after_lease_expiry_is_not_recorded(
    db_session,
    failure,
):
    calls = []
    generation_intent = intent(org_id=uuid4())
    job = stored_job(db_session, generation_intent)
    completion_time = NOW + timedelta(hours=1, seconds=1)
    coordinator = MediaSubmissionCoordinator(
        db_session,
        jobs=MediaGenerationJobService(db_session),
        vault=FakeVault(generation_intent, calls),
        policy=FakePolicy(calls),
        adapter=FakeAdapter(calls, failure=failure),
        clock=lambda: completion_time,
    )

    with pytest.raises(MediaJobLeaseConflict):
        await coordinator.submit_claimed(
            job.id,
            worker_id="worker-a",
            fencing_token=3,
            principal=principal(generation_intent.org_id),
            decision=SimpleNamespace(decision_id=uuid4()),
            now=NOW,
        )

    db_session.expire_all()
    unresolved = db_session.get(MediaGenerationJob, job.id)
    assert unresolved.status == "running"
    assert unresolved.effect_state == "started"

    recovered = MediaGenerationJobService(db_session).recover_expired(
        now=completion_time
    )
    assert [item.id for item in recovered] == [job.id]
    assert recovered[0].status == "submission_unknown"
    assert recovered[0].error_code == "lease_expired_after_submission_started"
