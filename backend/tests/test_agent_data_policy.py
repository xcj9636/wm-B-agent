import pytest

from app.services.agent_runtime.contracts import Sensitivity
from app.services.data_policy import (
    DataPolicyUnavailable,
    ProviderRoutePolicy,
    RedactionVault,
    SensitiveDataClassifier,
)


def test_classifier_combines_intrinsic_requested_and_detected_sensitivity():
    classifier = SensitiveDataClassifier()

    classified = classifier.classify(
        "Contact buyer@example.com using token sk-abcdefghijklmnopqrstuvwxyz123456",
        intrinsic=Sensitivity.INTERNAL,
        requested_floor=Sensitivity.PUBLIC,
    )

    assert classified.sensitivity == Sensitivity.RESTRICTED
    assert {match.kind for match in classified.matches} >= {"email", "api_key"}


def test_redaction_vault_uses_run_scoped_placeholders_and_controlled_rehydration():
    vault = RedactionVault()
    redacted = vault.redact(
        "Email buyer@example.com or call +86 13800138000",
        run_id="run-1",
    )

    assert "buyer@example.com" not in redacted.text
    assert "13800138000" not in redacted.text
    assert "[[EMAIL_1]]" in redacted.text
    assert "[[PHONE_1]]" in redacted.text
    assert vault.rehydrate(redacted.text, run_id="run-1") == (
        "Email buyer@example.com or call +86 13800138000"
    )
    with pytest.raises(PermissionError):
        vault.rehydrate(redacted.text, run_id="another-run")


def test_provider_policy_fails_closed_when_route_or_dependency_is_missing():
    policy = ProviderRoutePolicy(
        {
            ("live_reply", Sensitivity.INTERNAL): "reply-internal-v1",
            ("live_reply", Sensitivity.CONFIDENTIAL): "reply-private-v1",
        }
    )

    assert policy.resolve("live_reply", Sensitivity.CONFIDENTIAL) == "reply-private-v1"
    with pytest.raises(DataPolicyUnavailable):
        policy.resolve("live_reply", Sensitivity.RESTRICTED)

    policy.disable()
    with pytest.raises(DataPolicyUnavailable):
        policy.resolve("live_reply", Sensitivity.INTERNAL)
