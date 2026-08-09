from app.core.workflow_engine import WorkflowDefinition, WorkflowExecution


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

