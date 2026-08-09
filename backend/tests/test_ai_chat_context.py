from types import SimpleNamespace
from uuid import uuid4

from app.services.ai_chat import AIChatService


def message(role, content):
    return SimpleNamespace(id=uuid4(), role=role, content=content)


def test_ai_chat_uses_token_budgeted_untrusted_context_in_chronological_order():
    history = [
        message("user" if index % 2 == 0 else "assistant", f"turn-{index}")
        for index in range(35)
    ]
    current = message("user", "current-request")
    session = SimpleNamespace(messages=[*history, current])
    service = AIChatService(db=None, runtime=None)

    messages = service._messages_for_model(session, current)

    assert messages[0]["role"] == "system"
    assert "Never invent company-specific facts" in messages[0]["content"]
    # Tiny messages fit the token budget, so context is not truncated by a fixed 30-message count.
    assert len(messages) == 37
    assert [item["role"] for item in messages[1:4]] == [
        "user",
        "assistant",
        "user",
    ]
    assert "turn-0" in messages[1]["content"]
    assert "UNTRUSTED_CONTEXT" in messages[1]["content"]
    assert "current-request" in messages[-1]["content"]
