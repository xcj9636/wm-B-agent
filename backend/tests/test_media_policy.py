from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.services.agent_runtime.contracts import ExecutionPrincipal, Sensitivity
from app.services.media.contracts import (
    AssetConsentStatus,
    AssetRightsStatus,
    AssetScanStatus,
    GenerationIntent,
    GenerationMode,
    MediaAssetPolicySnapshot,
)
from app.services.media.policy import (
    MediaFeatureDisabled,
    MediaPolicyDenied,
    MediaPolicyInvalid,
    MediaSubmissionPolicy,
)


def principal(org_id=None):
    return ExecutionPrincipal(
        org_id=org_id or uuid4(),
        user_id=42,
        roles={"media_operator"},
        entitlements_hash="a" * 64,
        authn_context="jwt:mfa",
    )


def intent(org_id, *, asset_ids=None, sensitivity=Sensitivity.INTERNAL):
    return GenerationIntent(
        attempt_id=uuid4(),
        project_id=uuid4(),
        shot_id=uuid4(),
        persona_version_id=uuid4(),
        org_id=org_id,
        actor_user_id=42,
        mode=GenerationMode.IMAGE_TO_VIDEO,
        prompt="Slow camera orbit around the approved product",
        reference_asset_ids=asset_ids or [],
        sensitivity=sensitivity,
        persona_approved=True,
        storyboard_approved=True,
    )


def ready_asset(asset_id, org_id, **overrides):
    values = {
        "asset_id": asset_id,
        "org_id": org_id,
        "scan_status": AssetScanStatus.PASSED,
        "rights_status": AssetRightsStatus.VERIFIED,
        "consent_required": False,
        "consent_status": AssetConsentStatus.NOT_REQUIRED,
        "sensitivity": Sensitivity.INTERNAL,
    }
    values.update(overrides)
    return MediaAssetPolicySnapshot(**values)


def policy(*, enabled=True):
    return MediaSubmissionPolicy(
        submission_enabled=enabled,
        policy_version="media-policy-v1",
        signing_key=b"test-only-media-policy-signing-key",
        decision_ttl_seconds=120,
    )


def test_media_features_are_default_off():
    config = Settings(_env_file=None)

    assert config.MEDIA_UPLOAD_ENABLED is False
    assert config.MEDIA_PLANNING_ENABLED is False
    assert config.MEDIA_SUBMIT_ENABLED is False


def test_settings_reject_multi_tenant_deployment_claim():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            DEPLOYMENT_TENANCY="multi_tenant",
        )


def test_settings_reject_media_submission_without_prerequisites():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            MEDIA_SUBMIT_ENABLED=True,
        )

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            MEDIA_UPLOAD_ENABLED=True,
            MEDIA_PLANNING_ENABLED=True,
            MEDIA_SUBMIT_ENABLED=True,
            MEDIA_POLICY_SIGNING_KEY="too-short",
        )


def test_settings_allow_explicitly_enabled_media_pipeline_with_strong_key():
    config = Settings(
        _env_file=None,
        MEDIA_UPLOAD_ENABLED=True,
        MEDIA_PLANNING_ENABLED=True,
        MEDIA_SUBMIT_ENABLED=True,
        MEDIA_POLICY_SIGNING_KEY="m" * 32,
    )

    assert config.DEPLOYMENT_TENANCY == "single_organization"
    assert config.MEDIA_SUBMIT_ENABLED is True


def test_submission_policy_fails_closed_when_feature_is_disabled():
    actor = principal()
    command = intent(actor.org_id)

    with pytest.raises(MediaFeatureDisabled):
        policy(enabled=False).authorize(actor, command, assets=[])


@pytest.mark.parametrize(
    ("asset_overrides", "reason_code"),
    [
        ({"scan_status": AssetScanStatus.PENDING}, "asset_scan_not_passed"),
        ({"rights_status": AssetRightsStatus.UNKNOWN}, "asset_rights_unverified"),
        (
            {
                "consent_required": True,
                "consent_status": AssetConsentStatus.REVOKED,
            },
            "asset_consent_invalid",
        ),
    ],
)
def test_submission_policy_rejects_unready_assets(asset_overrides, reason_code):
    actor = principal()
    asset_id = uuid4()
    command = intent(actor.org_id, asset_ids=[asset_id])
    asset = ready_asset(asset_id, actor.org_id, **asset_overrides)

    with pytest.raises(MediaPolicyDenied) as exc:
        policy().authorize(actor, command, assets=[asset])

    assert reason_code in exc.value.reason_codes


def test_submission_policy_rejects_cross_org_or_missing_assets():
    actor = principal()
    asset_id = uuid4()
    command = intent(actor.org_id, asset_ids=[asset_id])

    with pytest.raises(MediaPolicyDenied) as missing:
        policy().authorize(actor, command, assets=[])
    assert "referenced_asset_missing" in missing.value.reason_codes

    with pytest.raises(MediaPolicyDenied) as cross_org:
        policy().authorize(
            actor,
            command,
            assets=[ready_asset(asset_id, uuid4())],
        )
    assert "asset_org_mismatch" in cross_org.value.reason_codes


def test_policy_decision_is_signed_and_bound_to_attempt_and_input_hash():
    actor = principal()
    asset_id = uuid4()
    command = intent(actor.org_id, asset_ids=[asset_id])
    service = policy()
    now = datetime.now(timezone.utc)

    decision = service.authorize(
        actor,
        command,
        assets=[ready_asset(asset_id, actor.org_id)],
        now=now,
    )

    assert decision.allowed is True
    assert decision.attempt_id == command.attempt_id
    assert decision.input_hash == command.input_hash()
    assert decision.expires_at == now + timedelta(seconds=120)
    service.verify(decision, command, now=now + timedelta(seconds=30))

    changed = command.model_copy(update={"prompt": "Tampered prompt"})
    with pytest.raises(MediaPolicyInvalid):
        service.verify(decision, changed, now=now + timedelta(seconds=30))

    with pytest.raises(MediaPolicyInvalid):
        service.verify(decision, command, now=now + timedelta(seconds=121))


def test_policy_kill_switch_invalidates_an_already_issued_decision():
    actor = principal()
    asset_id = uuid4()
    command = intent(actor.org_id, asset_ids=[asset_id])
    asset = ready_asset(asset_id, actor.org_id)
    issued_at = datetime.now(timezone.utc)
    enabled = policy(enabled=True)
    decision = enabled.authorize(
        actor,
        command,
        assets=[asset],
        now=issued_at,
    )

    disabled = policy(enabled=False)
    with pytest.raises(MediaFeatureDisabled):
        disabled.verify(
            decision,
            command,
            now=issued_at + timedelta(seconds=10),
        )


def test_policy_rejects_unapproved_persona_or_storyboard_and_identity_mismatch():
    actor = principal()

    with pytest.raises(MediaPolicyDenied) as unapproved:
        policy().authorize(
            actor,
            intent(actor.org_id).model_copy(update={"persona_approved": False}),
            assets=[],
        )
    assert "persona_not_approved" in unapproved.value.reason_codes

    with pytest.raises(MediaPolicyDenied) as wrong_actor:
        policy().authorize(
            actor,
            intent(actor.org_id).model_copy(update={"actor_user_id": 99}),
            assets=[],
        )
    assert "actor_mismatch" in wrong_actor.value.reason_codes
