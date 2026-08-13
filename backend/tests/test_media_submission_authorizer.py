from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.models.database import (
    MediaAsset,
    MediaConsentRecord,
    MediaGenerationJob,
    MediaRightsRecord,
    MediaScanReport,
    User,
    VideoPersona,
    VideoPersonaVersion,
    VideoProject,
    VideoStoryboardVersion,
)
from app.services.agent_runtime.contracts import Sensitivity
from app.services.idempotency import canonical_hash
from app.services.media.contracts import GenerationIntent, GenerationMode
from app.services.media.policy import MediaPolicyDenied, MediaSubmissionPolicy
from app.services.media.submission_authorizer import (
    MediaSubmissionAuthorizationDenied,
    MediaSubmissionAuthorizer,
)


NOW = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)


def install_live_submission(db_session, *, consent_required=False):
    org_id = uuid4()
    user = User(
        username=f"operator-{uuid4()}",
        email=f"operator-{uuid4()}@example.com",
        hashed_password="unused",
        role="media_operator",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    persona = VideoPersona(org_id=org_id, owner_user_id=user.id)
    db_session.add(persona)
    db_session.flush()
    persona_spec = {"identity": {"name": "Approved persona"}}
    persona_version = VideoPersonaVersion(
        persona_id=persona.id,
        org_id=org_id,
        revision=1,
        idempotency_key=f"persona-{uuid4()}",
        input_hash="a" * 64,
        spec_json=persona_spec,
        spec_hash=canonical_hash(persona_spec),
        status="approved",
        created_by_user_id=user.id,
        approved_by_user_id=user.id,
        approved_at=NOW.replace(tzinfo=None),
    )
    db_session.add(persona_version)
    db_session.flush()

    project = VideoProject(
        org_id=org_id,
        owner_user_id=user.id,
        idempotency_key=f"project-{uuid4()}",
        input_hash="b" * 64,
        brief_json={"title": "Approved project"},
        brief_hash=canonical_hash({"title": "Approved project"}),
        persona_version_id=persona_version.id,
        persona_snapshot_json=persona_spec,
        persona_spec_hash=persona_version.spec_hash,
        sensitivity="internal",
        status="active",
    )
    db_session.add(project)
    db_session.flush()

    shot_id = uuid4()
    storyboard_json = {"title": "Approved storyboard", "shots": []}
    storyboard = VideoStoryboardVersion(
        project_id=project.id,
        org_id=org_id,
        revision=1,
        idempotency_key=f"storyboard-{uuid4()}",
        input_hash="c" * 64,
        storyboard_json=storyboard_json,
        storyboard_hash=canonical_hash(storyboard_json),
        status="approved",
        created_by_user_id=user.id,
        approved_by_user_id=user.id,
        approved_at=NOW.replace(tzinfo=None),
    )
    db_session.add(storyboard)

    asset = MediaAsset(
        org_id=org_id,
        owner_user_id=user.id,
        kind="image",
        source="upload",
        storage_backend="s3",
        storage_key=f"assets/{uuid4()}",
        sha256="d" * 64,
        mime_type="image/png",
        size_bytes=100,
        sensitivity="internal",
        quarantined=False,
        scan_status="passed",
        rights_status="verified",
        consent_required=consent_required,
        consent_status="valid" if consent_required else "not_required",
    )
    db_session.add(asset)
    db_session.flush()
    scan = MediaScanReport(
        org_id=org_id,
        asset_id=asset.id,
        scanner="clamav",
        scanner_version="1.4.2",
        status="passed",
        asset_sha256=asset.sha256,
        findings_json={},
        created_by_user_id=user.id,
    )
    rights = MediaRightsRecord(
        org_id=org_id,
        asset_id=asset.id,
        status="verified",
        basis="owned_product_media",
        territories=["GLOBAL"],
        channels=["website"],
        source_ref="contract:masked",
        valid_from=(NOW - timedelta(days=1)).replace(tzinfo=None),
        valid_until=(NOW + timedelta(days=30)).replace(tzinfo=None),
        reviewed_by_user_id=user.id,
    )
    db_session.add_all([scan, rights])
    db_session.flush()
    asset.scan_report_id = scan.id
    asset.rights_record_id = rights.id

    consent = None
    if consent_required:
        evidence = MediaAsset(
            org_id=org_id,
            owner_user_id=user.id,
            kind="project_file",
            source="upload",
            storage_backend="s3",
            storage_key=f"assets/{uuid4()}",
            sha256="e" * 64,
            mime_type="application/pdf",
            size_bytes=100,
            sensitivity="internal",
            quarantined=False,
            scan_status="passed",
            rights_status="verified",
            consent_required=False,
            consent_status="not_required",
        )
        db_session.add(evidence)
        db_session.flush()
        evidence_scan = MediaScanReport(
            org_id=org_id,
            asset_id=evidence.id,
            scanner="clamav",
            scanner_version="1.4.2",
            status="passed",
            asset_sha256=evidence.sha256,
            findings_json={},
            created_by_user_id=user.id,
        )
        db_session.add(evidence_scan)
        db_session.flush()
        evidence.scan_report_id = evidence_scan.id
        consent = MediaConsentRecord(
            org_id=org_id,
            asset_id=asset.id,
            subject_ref="talent:masked",
            purpose="product marketing",
            regions=["GLOBAL"],
            media_types=["video"],
            status="valid",
            valid_from=(NOW - timedelta(days=1)).replace(tzinfo=None),
            valid_until=(NOW + timedelta(days=30)).replace(tzinfo=None),
            evidence_asset_id=evidence.id,
            created_by_user_id=user.id,
        )
        db_session.add(consent)
        db_session.flush()
        asset.consent_record_id = consent.id

    intent = GenerationIntent(
        project_id=project.id,
        shot_id=shot_id,
        persona_version_id=persona_version.id,
        org_id=org_id,
        actor_user_id=user.id,
        mode=GenerationMode.IMAGE_TO_VIDEO,
        prompt="Approved product orbit",
        reference_asset_ids=[asset.id],
        sensitivity=Sensitivity.INTERNAL,
        persona_approved=True,
        storyboard_approved=True,
    )
    job = MediaGenerationJob(
        org_id=org_id,
        owner_user_id=user.id,
        project_id=project.id,
        storyboard_version_id=storyboard.id,
        shot_id=shot_id,
        runtime_revision_id=uuid4(),
        idempotency_key=f"job-{uuid4()}",
        input_hash="f" * 64,
        intent_hash=intent.input_hash(),
        payload_ref=f"vault://media-intents/{intent.attempt_id}",
        mode=intent.mode.value,
        provider="fal",
        model_id="fal-ai/model",
        sensitivity=intent.sensitivity.value,
        status="running",
        effect_state="none",
        fencing_token=1,
        leased_by="worker-a",
        lease_until=(NOW + timedelta(minutes=5)).replace(tzinfo=None),
        reserved_cost_microusd=100,
        estimate_hash="1" * 64,
        budget_period_start=NOW.date().replace(day=1),
        deadline_at=(NOW + timedelta(hours=1)).replace(tzinfo=None),
    )
    db_session.add(job)
    db_session.commit()
    return {
        "org_id": org_id,
        "user": user,
        "persona": persona_version,
        "project": project,
        "storyboard": storyboard,
        "asset": asset,
        "scan": scan,
        "rights": rights,
        "consent": consent,
        "consent_evidence": evidence if consent_required else None,
        "consent_evidence_scan": evidence_scan if consent_required else None,
        "intent": intent,
        "job": job,
    }


def authorizer(db_session, org_id):
    policy = MediaSubmissionPolicy(
        submission_enabled=True,
        policy_version="media-policy-v1",
        signing_key=b"test-only-media-policy-signing-key",
        decision_ttl_seconds=120,
    )
    return MediaSubmissionAuthorizer(
        db_session,
        policy=policy,
        deployment_org_id=org_id,
    ), policy


def test_reauthorizes_from_live_identity_approval_and_asset_evidence(db_session):
    state = install_live_submission(db_session, consent_required=True)
    service, policy = authorizer(db_session, state["org_id"])

    approved = service.authorize(state["job"], state["intent"], now=NOW)

    assert approved.principal.user_id == state["user"].id
    assert approved.principal.roles == {"media_operator"}
    assert approved.principal.authn_context == "worker:celery"
    policy.verify(approved.decision, state["intent"], now=NOW)


@pytest.mark.parametrize("invalid_target", ["user", "persona", "storyboard"])
def test_live_identity_and_approvals_must_still_be_valid(
    db_session,
    invalid_target,
):
    state = install_live_submission(db_session)
    if invalid_target == "user":
        state["user"].is_active = False
    else:
        state[invalid_target].status = "draft"
    db_session.commit()
    service, _ = authorizer(db_session, state["org_id"])

    with pytest.raises(MediaSubmissionAuthorizationDenied):
        service.authorize(state["job"], state["intent"], now=NOW)


@pytest.mark.parametrize(
    ("mutation", "reason_code"),
    [
        ("scan_hash", "asset_scan_not_passed"),
        ("rights_expired", "asset_rights_unverified"),
        ("consent_revoked", "asset_consent_invalid"),
        ("sensitivity_raised", "sensitivity_underclassified"),
    ],
)
def test_live_asset_evidence_revocation_fails_closed(
    db_session,
    mutation,
    reason_code,
):
    state = install_live_submission(db_session, consent_required=True)
    if mutation == "scan_hash":
        state["scan"].asset_sha256 = "0" * 64
    elif mutation == "rights_expired":
        state["rights"].valid_until = (NOW - timedelta(seconds=1)).replace(
            tzinfo=None
        )
    elif mutation == "consent_revoked":
        state["consent"].revoked_at = NOW.replace(tzinfo=None)
    else:
        state["asset"].sensitivity = "confidential"
    db_session.commit()
    service, _ = authorizer(db_session, state["org_id"])

    with pytest.raises(MediaPolicyDenied) as exc:
        service.authorize(state["job"], state["intent"], now=NOW)

    assert reason_code in exc.value.reason_codes


def test_cross_deployment_org_and_tampered_project_snapshot_are_rejected(db_session):
    state = install_live_submission(db_session)
    service, _ = authorizer(db_session, uuid4())
    with pytest.raises(MediaSubmissionAuthorizationDenied):
        service.authorize(state["job"], state["intent"], now=NOW)


def test_consent_evidence_must_remain_live_scanned_and_same_org(db_session):
    state = install_live_submission(db_session, consent_required=True)
    state["consent_evidence"].deleted_at = NOW.replace(tzinfo=None)
    db_session.commit()
    service, _ = authorizer(db_session, state["org_id"])

    with pytest.raises(MediaPolicyDenied) as exc:
        service.authorize(state["job"], state["intent"], now=NOW)

    assert "asset_consent_invalid" in exc.value.reason_codes


def test_consent_evidence_scan_must_match_current_content(db_session):
    state = install_live_submission(db_session, consent_required=True)
    state["consent_evidence_scan"].asset_sha256 = "0" * 64
    db_session.commit()
    service, _ = authorizer(db_session, state["org_id"])

    with pytest.raises(MediaPolicyDenied) as exc:
        service.authorize(state["job"], state["intent"], now=NOW)

    assert "asset_consent_invalid" in exc.value.reason_codes


def test_inactive_project_cannot_start_new_external_effect(db_session):
    state = install_live_submission(db_session)
    state["project"].status = "archived"
    db_session.commit()
    service, _ = authorizer(db_session, state["org_id"])

    with pytest.raises(MediaSubmissionAuthorizationDenied):
        service.authorize(state["job"], state["intent"], now=NOW)

    service, _ = authorizer(db_session, state["org_id"])
    state["project"].persona_spec_hash = "0" * 64
    db_session.commit()
    with pytest.raises(MediaSubmissionAuthorizationDenied):
        service.authorize(state["job"], state["intent"], now=NOW)
