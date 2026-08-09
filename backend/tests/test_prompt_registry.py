import pytest

from app.services.agent_runtime.prompts import (
    PromptRegistry,
    PromptStatus,
    PromptTemplateVersion,
)


def prompt(version=1, status=PromptStatus.DRAFT):
    return PromptTemplateVersion(
        prompt_key="live_reply",
        version=version,
        content=(
            "You are B-agent. Reply in {locale}. "
            "Never invent company facts. Use case: {use_case}."
        ),
        required_variables={"locale", "use_case"},
        use_cases={"live_reply"},
        status=status,
    )


def test_prompt_versions_are_immutable_and_require_evaluation_before_activation():
    registry = PromptRegistry()
    registry.register(prompt())

    with pytest.raises(ValueError, match="evaluated"):
        registry.activate("live_reply", 1)

    registry.mark_evaluated("live_reply", 1)
    active = registry.activate("live_reply", 1)

    assert active.status == PromptStatus.ACTIVE
    assert registry.active("live_reply").content_hash == active.content_hash
    with pytest.raises(ValueError, match="already exists"):
        registry.register(prompt())


def test_prompt_render_rejects_missing_and_unexpected_variables():
    template = prompt(status=PromptStatus.ACTIVE)

    with pytest.raises(ValueError, match="Missing prompt variables"):
        template.render(locale="zh-CN")
    with pytest.raises(ValueError, match="Unexpected prompt variables"):
        template.render(
            locale="zh-CN",
            use_case="live_reply",
            customer_json="untrusted",
        )

    rendered = template.render(locale="zh-CN", use_case="live_reply")
    assert rendered.startswith("You are B-agent")
    assert "untrusted" not in rendered


def test_activating_new_prompt_retires_previous_version_without_mutating_it():
    registry = PromptRegistry()
    registry.register(prompt(version=1, status=PromptStatus.EVALUATED))
    registry.register(prompt(version=2, status=PromptStatus.EVALUATED))

    first = registry.activate("live_reply", 1)
    second = registry.activate("live_reply", 2)

    assert first.status == PromptStatus.ACTIVE
    assert second.status == PromptStatus.ACTIVE
    assert registry.get("live_reply", 1).status == PromptStatus.RETIRED
    assert registry.active("live_reply").version == 2
    assert registry.get("live_reply", 1).content_hash == first.content_hash
