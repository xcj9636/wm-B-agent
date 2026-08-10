import json

import pytest

from app.services.agent_path_router import (
    AgentExecutionProfile,
    AgentPathRouter,
)
from app.services.agent_runtime.contracts import Sensitivity


@pytest.fixture
def router() -> AgentPathRouter:
    return AgentPathRouter(
        enabled=True,
        max_input_chars=240,
        max_history_messages=6,
        fast_max_output_tokens=800,
        deep_max_output_tokens=1600,
    )


def test_short_simple_request_uses_bounded_fast_path(router):
    profile = router.route(
        content="Hello, can you improve this sentence?",
        sensitivity=Sensitivity.INTERNAL,
        prior_message_count=2,
    )

    assert profile.path == "fast"
    assert profile.reason_code == "short_simple_request"
    assert profile.max_output_tokens == 800
    assert profile.history_message_limit == 6


@pytest.mark.parametrize(
    ("content", "sensitivity", "history_count", "reason"),
    [
        ("Summarize this", Sensitivity.CONFIDENTIAL, 0, "sensitive_input"),
        ("Summarize this", Sensitivity.RESTRICTED, 0, "sensitive_input"),
        ("A" * 241, Sensitivity.INTERNAL, 0, "long_input"),
        ("Summarize this", Sensitivity.INTERNAL, 7, "long_conversation"),
        (
            "Compare the quotation, MOQ and payment terms",
            Sensitivity.INTERNAL,
            0,
            "business_evidence_required",
        ),
        (
            "请核对报价、交期和付款条款",
            Sensitivity.INTERNAL,
            0,
            "business_evidence_required",
        ),
        (
            "Send this email to the buyer",
            Sensitivity.INTERNAL,
            0,
            "tool_or_action_intent",
        ),
    ],
)
def test_uncertain_or_risky_requests_fail_closed_to_deep_path(
    router,
    content,
    sensitivity,
    history_count,
    reason,
):
    profile = router.route(
        content=content,
        sensitivity=sensitivity,
        prior_message_count=history_count,
    )

    assert profile.path == "deep"
    assert profile.reason_code == reason
    assert profile.max_output_tokens == 1600
    assert profile.history_message_limit is None


def test_disabled_fast_path_is_an_immediate_rollback_switch():
    router = AgentPathRouter(enabled=False)

    profile = router.route(
        content="Hello",
        sensitivity=Sensitivity.INTERNAL,
        prior_message_count=0,
    )

    assert profile.path == "deep"
    assert profile.reason_code == "fast_path_disabled"


def test_execution_profile_never_persists_raw_content(router):
    secret = "private-customer-value-42"

    profile = router.route(
        content=f"Please rephrase {secret}",
        sensitivity=Sensitivity.INTERNAL,
        prior_message_count=0,
    )

    assert secret not in json.dumps(profile.model_dump())


def test_invalid_or_unknown_profile_state_fails_closed_to_deep_path():
    profile = AgentExecutionProfile.from_state(
        {
            "path": "fast",
            "reason_code": "short_simple_request",
            "route_version": "future-version",
            "max_output_tokens": 1,
            "history_message_limit": 999,
        }
    )

    assert profile.path == "deep"
    assert profile.reason_code == "invalid_persisted_profile"
    assert profile.max_output_tokens == 1600
    assert profile.history_message_limit is None
