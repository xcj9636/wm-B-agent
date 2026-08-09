from types import SimpleNamespace

import pytest

from app.core.context import ExecutionContext
from app.skills.skill_ai_reply import AIReplySkill
from app.skills.skill_rag import RagSkill


@pytest.mark.asyncio
async def test_ai_reply_rag_failure_does_not_fabricate_business_policy(
    monkeypatch,
):
    async def fail_query(*args, **kwargs):
        raise RuntimeError("embedding provider unavailable")

    monkeypatch.setattr(RagSkill, "execute", fail_query)

    context, sources = await AIReplySkill()._search_knowledge_base(
        "What are your payment terms?",
        "payment_inquiry",
        {},
        {},
    )

    assert context == ""
    assert sources == []


@pytest.mark.asyncio
async def test_ai_reply_prompt_requires_verified_evidence(monkeypatch):
    captured = {}

    class FakeLLMService:
        async def complete(self, use_case, messages):
            captured["messages"] = messages
            return SimpleNamespace(content="I need a verified source before answering.")

    monkeypatch.setattr(
        "app.services.llm.factory.get_llm_service",
        lambda: FakeLLMService(),
    )

    await AIReplySkill()._generate_reply(
        "What is the MOQ?",
        "moq_inquiry",
        "medium",
        "",
        {},
        [],
    )

    system_prompt = captured["messages"][0]["content"]
    assert "Use general knowledge for the company's products and services" not in system_prompt
    assert "verified company knowledge" in system_prompt.lower()
    assert "do not invent" in system_prompt.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["add", "clear"])
async def test_rag_skill_rejects_model_visible_mutating_actions(
    monkeypatch,
    action,
):
    skill = RagSkill()

    def fail_if_called(*args, **kwargs):
        raise AssertionError("vector store must not be touched")

    monkeypatch.setattr(skill, "_get_vectorstore", fail_if_called)

    result = await skill.execute(
        ExecutionContext(workflow_id="test", execution_id="run-1"),
        action=action,
        text="untrusted content",
        collection_name="../../another-tenant",
    )

    assert result == {
        "success": False,
        "code": "RAG_READ_ONLY",
        "message": "Knowledge mutation is not available to agent tools",
    }


@pytest.mark.asyncio
async def test_rag_skill_uses_server_configured_scope(monkeypatch):
    skill = RagSkill({"collection_name": "approved-org-scope"})
    observed = {}

    async def fake_query(query, collection_name, top_k):
        observed.update(
            query=query,
            collection_name=collection_name,
            top_k=top_k,
        )
        return {"success": True, "documents": []}

    monkeypatch.setattr(skill, "_query_documents", fake_query)

    result = await skill.execute(
        ExecutionContext(workflow_id="test", execution_id="run-2"),
        action="query",
        text="approved product facts",
        collection_name="../../another-tenant",
        top_k=999,
    )

    assert result["success"] is True
    assert observed == {
        "query": "approved product facts",
        "collection_name": "approved-org-scope",
        "top_k": 10,
    }
    assert "collection_name" not in RagSkill.input_schema["properties"]


@pytest.mark.asyncio
async def test_rag_skill_returns_sanitized_query_errors(monkeypatch):
    skill = RagSkill({"collection_name": "approved-org-scope"})

    async def fail_query(*args, **kwargs):
        raise RuntimeError("secret token and /internal/vector/path")

    monkeypatch.setattr(skill, "_query_documents", fail_query)

    result = await skill.execute(
        ExecutionContext(workflow_id="test", execution_id="run-3"),
        action="query",
        text="product",
    )

    assert result == {
        "success": False,
        "code": "RAG_QUERY_FAILED",
        "message": "Knowledge retrieval is temporarily unavailable",
    }
