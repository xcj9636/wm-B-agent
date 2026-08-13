from datetime import date, datetime, timedelta
from uuid import uuid4

from app.api.v1.auth import get_current_active_user
from app.config import settings
from app.main import app
from app.models.database import (
    MediaBudgetAccount,
    MediaBudgetLedgerEntry,
    MediaGenerationAttempt,
    MediaGenerationEvent,
    MediaGenerationJob,
    MediaSubmissionResolutionApproval,
    MediaSubmissionResolutionRequest,
    User,
)


NOW = datetime(2026, 8, 12, 12, 0, 0)


def create_admin(db, *, username):
    admin = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password="unused",
        is_active=True,
        is_superuser=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


def create_unknown_job(db, *, org_id=None, request_suffix="unknown"):
    org_id = org_id or settings.AGENT_ORG_ID
    account = db.query(MediaBudgetAccount).filter_by(org_id=org_id).one_or_none()
    if account is None:
        account = MediaBudgetAccount(
            org_id=org_id,
            period_start=date(2026, 8, 1),
            limit_microusd=20_000_000,
            reserved_microusd=0,
            spent_microusd=0,
        )
        db.add(account)
        db.flush()
    job = MediaGenerationJob(
        org_id=org_id,
        owner_user_id=7,
        project_id=uuid4(),
        storyboard_version_id=uuid4(),
        shot_id=uuid4(),
        runtime_revision_id=uuid4(),
        idempotency_key=f"unknown:{request_suffix}:{uuid4()}",
        input_hash="a" * 64,
        intent_hash="b" * 64,
        payload_ref="vault://media-intents/unknown",
        mode="text_to_video",
        provider="fal",
        model_id="fal-ai/veo3/fast",
        sensitivity="internal",
        status="submission_unknown",
        effect_state="unknown",
        event_sequence=0,
        reserved_cost_microusd=2_500_000,
        estimate_hash="c" * 64,
        budget_period_start=date(2026, 8, 1),
        error_code="provider_submit_response_lost",
        deadline_at=NOW + timedelta(hours=1),
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(job)
    db.flush()
    attempt = MediaGenerationAttempt(
        job_id=job.id,
        attempt_number=1,
        fencing_token=1,
        provider="fal",
        model_id=job.model_id,
        status="submission_unknown",
        effect_state="unknown",
        error_code=job.error_code,
        started_at=NOW,
    )
    db.add(attempt)
    account.reserved_microusd += job.reserved_cost_microusd
    db.add(
        MediaBudgetLedgerEntry(
            org_id=org_id,
            job_id=job.id,
            period_start=job.budget_period_start,
            entry_type="reservation",
            amount_microusd=job.reserved_cost_microusd,
            idempotency_key=f"media-budget:{job.id}:reservation",
            estimate_hash=job.estimate_hash,
            created_at=NOW,
        )
    )
    db.commit()
    db.refresh(job)
    return job, account, attempt


def approval_url(job):
    return (
        f"/api/v1/admin/media/jobs/{job.id}/submission-unknown/"
        "resolution-approvals"
    )


def test_media_submission_resolution_requires_superuser(api_context):
    client, db, _ = api_context
    job, _, _ = create_unknown_job(db, request_suffix="forbidden")

    response = client.post(
        approval_url(job),
        json={
            "action": "confirmed_not_submitted",
            "evidence_reference": "provider-audit/NOT-CREATED-001",
        },
    )

    assert response.status_code == 403
    db.refresh(job)
    assert job.status == "submission_unknown"


def test_confirmed_submitted_requires_two_admins_and_only_wakes_reconciliation(
    api_context,
):
    client, db, first_admin = api_context
    first_admin.is_superuser = True
    second_admin = create_admin(db, username="second-media-submit-admin")
    job, account, attempt = create_unknown_job(db, request_suffix="submitted")
    body = {
        "action": "confirmed_submitted",
        "evidence_reference": "provider-audit/REQUEST-FOUND-001",
        "provider_request_id": "fal_request_manual_001",
    }

    first = client.post(approval_url(job), json=body)
    duplicate = client.post(approval_url(job), json=body)
    db.refresh(job)

    assert first.status_code == 200
    assert first.json() == {
        "request_id": first.json()["request_id"],
        "job_id": str(job.id),
        "action": "confirmed_submitted",
        "status": "pending",
        "approvals": 1,
        "required_approvals": 2,
    }
    assert duplicate.status_code == 409
    assert job.status == "submission_unknown"

    app.dependency_overrides[get_current_active_user] = lambda: second_admin
    second = client.post(approval_url(job), json=body)
    db.refresh(job)
    db.refresh(attempt)
    db.refresh(account)

    assert second.status_code == 200
    assert second.json()["status"] == "executed"
    assert second.json()["approvals"] == 2
    assert job.status == "submitted"
    assert job.effect_state == "confirmed"
    assert job.provider_request_id == "fal_request_manual_001"
    assert job.provider_state == "queued"
    assert job.next_reconcile_at is not None
    assert attempt.status == "submitted"
    assert attempt.effect_state == "confirmed"
    assert account.reserved_microusd == 2_500_000
    assert account.spent_microusd == 0
    assert "provider-audit/REQUEST-FOUND-001" not in second.text
    assert [event.event_type for event in job.events] == [
        "submission.manually_confirmed"
    ]


def test_confirmed_not_submitted_releases_budget_without_requeue(api_context):
    client, db, first_admin = api_context
    first_admin.is_superuser = True
    second_admin = create_admin(db, username="second-media-absent-admin")
    job, account, attempt = create_unknown_job(db, request_suffix="absent")
    body = {
        "action": "confirmed_not_submitted",
        "evidence_reference": "provider-audit/NOT-CREATED-002",
    }

    first = client.post(approval_url(job), json=body)
    app.dependency_overrides[get_current_active_user] = lambda: second_admin
    second = client.post(approval_url(job), json=body)
    db.refresh(job)
    db.refresh(attempt)
    db.refresh(account)

    assert first.status_code == 200
    assert second.status_code == 200
    assert job.status == "cancelled"
    assert job.effect_state == "confirmed_absent"
    assert job.error_code == "manual_confirmed_not_submitted"
    assert job.cancelled_at is not None
    assert job.completed_at is not None
    assert attempt.status == "not_submitted"
    assert attempt.effect_state == "confirmed_absent"
    assert account.reserved_microusd == 0
    assert account.spent_microusd == 0
    assert [entry.entry_type for entry in db.query(MediaBudgetLedgerEntry)] == [
        "reservation",
        "release",
    ]
    assert [event.event_type for event in job.events] == [
        "submission.not_created_confirmed"
    ]


def test_resolution_hides_cross_org_job_and_rejects_conflicting_evidence(
    api_context,
):
    client, db, first_admin = api_context
    first_admin.is_superuser = True
    second_admin = create_admin(db, username="second-media-conflict-admin")
    foreign, _, _ = create_unknown_job(
        db,
        org_id=uuid4(),
        request_suffix="foreign",
    )

    hidden = client.post(
        approval_url(foreign),
        json={
            "action": "confirmed_not_submitted",
            "evidence_reference": "provider-audit/FOREIGN-001",
        },
    )

    assert hidden.status_code == 404
    assert hidden.json()["detail"] == "Media job not found"

    job, _, _ = create_unknown_job(db, request_suffix="conflict")
    first = client.post(
        approval_url(job),
        json={
            "action": "confirmed_not_submitted",
            "evidence_reference": "provider-audit/NOT-CREATED-003",
        },
    )
    app.dependency_overrides[get_current_active_user] = lambda: second_admin
    conflict = client.post(
        approval_url(job),
        json={
            "action": "confirmed_submitted",
            "evidence_reference": "provider-audit/REQUEST-FOUND-003",
            "provider_request_id": "fal_request_conflict_003",
        },
    )

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert db.query(MediaSubmissionResolutionRequest).count() == 1
    assert db.query(MediaSubmissionResolutionApproval).count() == 1
    db.refresh(job)
    assert job.status == "submission_unknown"


def test_resolution_input_contract_rejects_unbounded_or_inconsistent_evidence(
    api_context,
):
    client, db, admin = api_context
    admin.is_superuser = True
    job, _, _ = create_unknown_job(db, request_suffix="validation")

    missing_request = client.post(
        approval_url(job),
        json={
            "action": "confirmed_submitted",
            "evidence_reference": "provider-audit/REQUEST-MISSING-001",
        },
    )
    unexpected_request = client.post(
        approval_url(job),
        json={
            "action": "confirmed_not_submitted",
            "evidence_reference": "provider-audit/NOT-CREATED-004",
            "provider_request_id": "must_not_be_accepted",
        },
    )
    free_text = client.post(
        approval_url(job),
        json={
            "action": "confirmed_not_submitted",
            "evidence_reference": "contains spaces and private notes",
        },
    )
    unapproved_evidence_source = client.post(
        approval_url(job),
        json={
            "action": "confirmed_not_submitted",
            "evidence_reference": "internal-note/NOT-CREATED-005",
        },
    )

    assert missing_request.status_code == 422
    assert unexpected_request.status_code == 422
    assert free_text.status_code == 422
    assert unapproved_evidence_source.status_code == 422
    assert db.query(MediaSubmissionResolutionRequest).count() == 0


def test_confirmed_submission_rejects_request_id_owned_by_another_job(
    api_context,
):
    client, db, first_admin = api_context
    first_admin.is_superuser = True
    second_admin = create_admin(db, username="second-media-duplicate-admin")
    existing_job, _, existing_attempt = create_unknown_job(
        db,
        request_suffix="existing-request",
    )
    existing_job.status = "submitted"
    existing_job.effect_state = "confirmed"
    existing_job.provider_request_id = "fal_request_already_owned"
    existing_attempt.status = "submitted"
    existing_attempt.effect_state = "confirmed"
    existing_attempt.provider_request_id = "fal_request_already_owned"
    db.commit()
    unknown_job, _, _ = create_unknown_job(
        db,
        request_suffix="duplicate-request",
    )
    body = {
        "action": "confirmed_submitted",
        "evidence_reference": "provider-audit/DUPLICATE-REQUEST-001",
        "provider_request_id": "fal_request_already_owned",
    }

    first = client.post(approval_url(unknown_job), json=body)
    app.dependency_overrides[get_current_active_user] = lambda: second_admin
    second = client.post(approval_url(unknown_job), json=body)
    db.refresh(unknown_job)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"] == (
        "Provider request is already assigned to another job"
    )
    assert unknown_job.status == "submission_unknown"
    resolution = (
        db.query(MediaSubmissionResolutionRequest)
        .filter(MediaSubmissionResolutionRequest.job_id == unknown_job.id)
        .one()
    )
    assert resolution.status.value == "pending"
    assert (
        db.query(MediaSubmissionResolutionApproval)
        .filter(MediaSubmissionResolutionApproval.request_id == resolution.id)
        .count()
        == 1
    )
