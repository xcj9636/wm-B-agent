from app.api.v1.video import (
    get_video_persona_service,
    get_video_planning_service,
    get_video_prompt_compiler,
)
from app.config import settings
from app.main import app
from app.services.agent_runtime.contracts import Sensitivity
from app.services.knowledge import KnowledgeIngestionService
from app.services.media.personas import VideoPersonaService
from app.services.media.planning import VideoPlanningService
from app.services.media.prompts import VideoPromptCompiler


def persona_payload(**overrides):
    value = {
        "idempotency_key": "persona:create:api-eu-launch",
        "spec": {
            "identity": {
                "name": "EU distributor launch",
                "brand_name": "Acme Industrial",
                "markets": ["DE"],
                "languages": ["de-DE"],
            },
            "audience_segments": ["industrial distributors"],
            "narrative": {
                "tone": ["credible"],
                "value_propositions": ["documented quality control"],
                "calls_to_action": ["Request the technical datasheet"],
                "prohibited_claims": ["guaranteed delivery"],
            },
            "visual_bible": {
                "style": ["industrial documentary"],
                "palette": ["#0B1F33"],
                "camera_language": ["slow dolly"],
                "forbidden_visuals": ["competitor logos"],
            },
            "reference_asset_ids": [],
            "default_workflow": "text_to_video",
        },
    }
    value.update(overrides)
    return value


def enable_planning_services(db):
    app.dependency_overrides[get_video_persona_service] = lambda: VideoPersonaService(
        db,
        planning_enabled=True,
    )
    app.dependency_overrides[get_video_planning_service] = lambda: VideoPlanningService(
        db,
        planning_enabled=True,
    )
    app.dependency_overrides[get_video_prompt_compiler] = lambda: VideoPromptCompiler(
        db,
        planning_enabled=True,
    )


def test_authenticated_api_runs_persona_project_storyboard_compile_flow(api_context):
    client, db, user = api_context
    enable_planning_services(db)
    created = client.post("/api/v1/video/personas", json=persona_payload())

    assert created.status_code == 201
    persona = created.json()
    assert persona["revision"] == 1
    assert persona["status"] == "draft"
    assert persona["spec"]["identity"]["brand_name"] == "Acme Industrial"
    assert "input_hash" not in persona

    forbidden = client.post(
        f"/api/v1/video/persona-versions/{persona['version_id']}/approve",
        json={},
    )
    assert forbidden.status_code == 403

    user.is_superuser = True
    db.commit()
    approved = client.post(
        f"/api/v1/video/persona-versions/{persona['version_id']}/approve",
        json={},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    evidence = KnowledgeIngestionService(db).ingest(
        org_id=settings.AGENT_ORG_ID,
        source_ref="catalog.pdf",
        title="Approved catalog",
        chunks=["AX-7 has documented IP65 ingress protection."],
        authority="approved_document",
        sensitivity=Sensitivity.INTERNAL,
        allowed_roles={"admin"},
    )
    project_response = client.post(
        "/api/v1/video/projects",
        json={
            "idempotency_key": "video-project:create:api-proof",
            "persona_version_id": persona["version_id"],
            "brief": {
                "title": "Distributor proof",
                "objective": "Generate qualified distributor enquiries",
                "product_summary": "AX-7 industrial controller",
                "target_audience": "German industrial distributors",
                "markets": ["DE"],
                "channels": ["linkedin", "website"],
                "language": "de-DE",
                "target_duration_seconds": 10,
            },
            "evidence_record_ids": [str(evidence.record_id)],
        },
    )

    assert project_response.status_code == 201
    project = project_response.json()
    assert project["persona_version_id"] == persona["version_id"]
    assert project["sensitivity"] == "internal"
    assert len(project["evidence"]) == 1

    storyboard_response = client.post(
        f"/api/v1/video/projects/{project['id']}/storyboards",
        json={
            "idempotency_key": "storyboard:create:api-proof",
            "storyboard": {
                "title": "Factory proof",
                "total_duration_seconds": 10,
                "shots": [
                    {
                        "sequence": 1,
                        "duration_seconds": 10,
                        "purpose": "proof",
                        "workflow_mode": "text_to_video",
                        "visual_prompt": "Show the AX-7 in a clean factory",
                        "business_claims": [
                            "Documented IP65 ingress protection"
                        ],
                        "claim_evidence_ids": [project["evidence"][0]["id"]],
                    }
                ],
            },
        },
    )

    assert storyboard_response.status_code == 201
    storyboard = storyboard_response.json()
    shot_id = storyboard["storyboard"]["shots"][0]["shot_id"]
    approved_storyboard = client.post(
        f"/api/v1/video/storyboard-versions/{storyboard['version_id']}/approve",
        json={},
    )
    assert approved_storyboard.status_code == 200

    compiled = client.post(
        (
            f"/api/v1/video/projects/{project['id']}/storyboards/"
            f"{storyboard['version_id']}/shots/{shot_id}/compile"
        ),
        json={},
    )

    assert compiled.status_code == 200
    assert compiled.json()["mode"] == "text_to_video"
    assert compiled.json()["sensitivity"] == "internal"
    assert "prompt_hash" in compiled.json()
    assert "prompt" not in compiled.json()
    assert "SYSTEM_CONSTRAINTS" not in compiled.text


def test_planning_api_rejects_client_provider_and_approval_fields(api_context):
    client, db, _ = api_context
    enable_planning_services(db)

    response = client.post(
        "/api/v1/video/personas",
        json={
            **persona_payload(),
            "provider": "fal",
            "status": "approved",
            "approved_by_user_id": 1,
        },
    )

    assert response.status_code == 422


def test_planning_api_exposes_owner_scoped_paginated_read_models(api_context):
    client, db, user = api_context
    enable_planning_services(db)
    created = client.post("/api/v1/video/personas", json=persona_payload())
    assert created.status_code == 201
    persona = created.json()

    personas = client.get("/api/v1/video/personas?limit=20&offset=0")
    assert personas.status_code == 200
    assert personas.json()["total"] == 1
    assert personas.json()["items"][0]["persona_id"] == persona["persona_id"]

    versions = client.get(
        f"/api/v1/video/personas/{persona['persona_id']}/versions"
    )
    assert versions.status_code == 200
    assert versions.json()["items"][0]["version_id"] == persona["version_id"]

    user.is_superuser = True
    db.commit()
    approved = client.post(
        f"/api/v1/video/persona-versions/{persona['version_id']}/approve",
        json={},
    )
    assert approved.status_code == 200
    project_response = client.post(
        "/api/v1/video/projects",
        json={
            "idempotency_key": "video-project:read-models",
            "persona_version_id": persona["version_id"],
            "brief": {
                "title": "Read model project",
                "objective": "Support the video studio UI",
                "product_summary": "AX-7 industrial controller",
                "target_audience": "Industrial distributors",
                "markets": ["DE"],
                "channels": ["website"],
                "language": "de-DE",
                "target_duration_seconds": 6,
            },
            "evidence_record_ids": [],
        },
    )
    assert project_response.status_code == 201
    project = project_response.json()
    storyboard_response = client.post(
        f"/api/v1/video/projects/{project['id']}/storyboards",
        json={
            "idempotency_key": "storyboard:read-models",
            "storyboard": {
                "title": "Read model storyboard",
                "total_duration_seconds": 6,
                "shots": [
                    {
                        "sequence": 1,
                        "duration_seconds": 6,
                        "purpose": "overview",
                        "workflow_mode": "text_to_video",
                        "visual_prompt": "Show the controller in a factory",
                    }
                ],
            },
        },
    )
    assert storyboard_response.status_code == 201

    projects = client.get("/api/v1/video/projects?limit=20&offset=0")
    assert projects.status_code == 200
    assert projects.json()["total"] == 1
    assert projects.json()["items"][0]["id"] == project["id"]

    detail = client.get(f"/api/v1/video/projects/{project['id']}")
    assert detail.status_code == 200
    assert detail.json()["id"] == project["id"]
    assert detail.json()["storyboards"][0]["version_id"] == (
        storyboard_response.json()["version_id"]
    )
