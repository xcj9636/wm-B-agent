from datetime import datetime, timedelta
from uuid import uuid4

from app.main import app
from app.core.workflow_engine import WorkflowDefinition, WorkflowExecution
from app.models.database import User
from app.api.v1.agent import get_agent_knowledge_service
from app.services.agent_runs import AgentRunCommand, AgentRunService
from app.services.agent_runtime.contracts import ExecutionPrincipal, Sensitivity
from app.services.knowledge import RetrievalResult


def test_agent_overview_exposes_real_business_pipelines(api_context):
    client, _, _ = api_context

    response = client.get("/api/v1/agent/overview")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["agent"]["name"] == "B-agent"
    assert body["runtime"]["registered_skill_count"] >= 13
    assert body["runtime"]["mode"] == "full"
    assert {pipeline["id"] for pipeline in body["pipelines"]} == {
        "lead_acquisition",
        "intelligent_outreach",
        "conversation_conversion",
    }
    registered = {skill["name"] for skill in body["capabilities"]}
    for pipeline in body["pipelines"]:
        assert pipeline["stages"]
        assert {stage["skill"] for stage in pipeline["stages"]} <= registered


def test_agent_runs_returns_live_execution_summaries(api_context):
    client, _, _ = api_context

    response = client.get("/api/v1/agent/runs")

    assert response.status_code == 200, response.text
    assert response.json() == []


def test_agent_knowledge_search_derives_identity_and_policy_server_side(api_context):
    client, _, user = api_context
    captured = {}

    class FakeKnowledgeService:
        async def retrieve(self, *, principal, query, sensitivity, limit):
            captured.update(
                principal=principal,
                query=query,
                sensitivity=sensitivity,
                limit=limit,
            )
            return RetrievalResult(
                snapshot_id="snapshot-1",
                principal_id=str(principal.user_id),
                entitlements_hash=principal.entitlements_hash,
                authorized_at=datetime.now().astimezone(),
                namespace=f"org-{principal.org_id}",
                evidence=[],
            )

    app.dependency_overrides[get_agent_knowledge_service] = (
        lambda: FakeKnowledgeService()
    )
    try:
        response = client.post(
            "/api/v1/agent/knowledge/search",
            json={"query": "verified MOQ", "limit": 5},
        )
    finally:
        app.dependency_overrides.pop(get_agent_knowledge_service, None)

    assert response.status_code == 200, response.text
    assert response.json()["evidence"] == []
    assert captured["query"] == "verified MOQ"
    assert captured["sensitivity"] == Sensitivity.INTERNAL
    assert captured["limit"] == 5
    principal = captured["principal"]
    assert isinstance(principal, ExecutionPrincipal)
    assert principal.user_id == user.id
    assert principal.roles == {"user"}
    assert len(principal.entitlements_hash) == 64


def test_agent_runs_returns_user_owned_durable_safe_metadata(api_context):
    client, db, user = api_context
    run, _ = AgentRunService(db).create(
        AgentRunCommand(
            idempotency_key="agent-api:durable-run:1",
            org_id=uuid4(),
            user_id=user.id,
            session_id=uuid4(),
            turn_id=uuid4(),
            use_case="live_reply",
            input={"message": "private buyer request"},
            sensitivity=Sensitivity.CONFIDENTIAL,
            generation_epoch=2,
            deadline_at=datetime.utcnow() + timedelta(minutes=5),
        )
    )

    response = client.get("/api/v1/agent/runs")
    detail = client.get(f"/api/v1/agent/runs/{run.id}")

    assert response.status_code == 200, response.text
    assert detail.status_code == 200, detail.text
    body = response.json()
    assert body == [detail.json()]
    assert body[0]["id"] == str(run.id)
    assert body[0]["use_case"] == "live_reply"
    assert body[0]["status"] == "queued"
    assert body[0]["effect_state"] == "none"
    assert body[0]["generation_epoch"] == 2
    serialized = response.text
    assert "private buyer request" not in serialized
    assert "input_hash" not in serialized
    assert "idempotency_key" not in serialized
    assert "leased_by" not in serialized
    assert "state_json" not in serialized


def test_agent_run_detail_does_not_cross_user_boundary(api_context):
    client, db, _ = api_context
    other = User(
        username="other-agent-user",
        email="other-agent-user@example.com",
        hashed_password="unused",
        is_active=True,
    )
    db.add(other)
    db.commit()
    run, _ = AgentRunService(db).create(
        AgentRunCommand(
            idempotency_key="agent-api:foreign-run:1",
            org_id=uuid4(),
            user_id=other.id,
            use_case="research",
            input={"objective": "sensitive research"},
            sensitivity=Sensitivity.INTERNAL,
            generation_epoch=1,
            deadline_at=datetime.utcnow() + timedelta(minutes=5),
        )
    )

    response = client.get(f"/api/v1/agent/runs/{run.id}")

    assert response.status_code == 404


def test_agent_overview_counts_durable_active_runs(api_context):
    client, db, user = api_context
    AgentRunService(db).create(
        AgentRunCommand(
            idempotency_key="agent-api:active-count:1",
            org_id=uuid4(),
            user_id=user.id,
            use_case="live_reply",
            input={"message": "hello"},
            sensitivity=Sensitivity.INTERNAL,
            generation_epoch=1,
            deadline_at=datetime.utcnow() + timedelta(minutes=5),
        )
    )

    response = client.get("/api/v1/agent/overview")

    assert response.status_code == 200, response.text
    assert response.json()["runtime"]["active_run_count"] == 1


def test_execution_status_uses_the_public_api_contract():
    definition = WorkflowDefinition(name="Contract run", description="")
    execution = WorkflowExecution("run-1", definition, {"input_data": {}})
    execution.start()

    status = execution.to_dict()

    assert status["id"] == "run-1"
    assert status["workflow_id"] == "Contract run"
    assert status["finished_at"] is None
    assert status["error_msg"] is None
    assert status["metrics"]["progress"] == 0
    assert "execution_id" not in status


def test_every_registered_skill_satisfies_the_metadata_contract():
    import app.skills  # noqa: F401
    from app.core.skill_base import SkillRegistry

    skills = SkillRegistry.list_all()
    assert len(skills) >= 13
    for skill in skills.values():
        assert skill.name
        assert skill.display_name
        assert skill.description
        assert skill.category
