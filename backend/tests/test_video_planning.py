from uuid import uuid4

import pytest

from app.services.agent_runtime.contracts import ExecutionPrincipal, Sensitivity
from app.services.knowledge import KnowledgeIngestionService
from app.services.media.contracts import (
    PersonaIdentity,
    PersonaNarrative,
    PersonaVisualBible,
    Storyboard,
    StoryboardShot,
    VideoPersonaSpec,
    VideoProjectBrief,
    VideoWorkflowMode,
)
from app.services.media.personas import PersonaRevisionCommand, VideoPersonaService
from app.services.media.planning import (
    StoryboardRevisionCommand,
    VideoPlanningConflict,
    VideoPlanningForbidden,
    VideoPlanningService,
    VideoProjectCommand,
)


def principal(org_id, *, user_id=7, roles=None):
    return ExecutionPrincipal(
        org_id=org_id,
        user_id=user_id,
        roles=set(roles or {"sales", "media_operator"}),
        entitlements_hash="b" * 64,
        authn_context="jwt:mfa",
    )


def persona_spec(name="EU launch"):
    return VideoPersonaSpec(
        identity=PersonaIdentity(
            name=name,
            brand_name="Acme Industrial",
            markets=["DE"],
            languages=["de-DE"],
        ),
        audience_segments=["industrial distributors"],
        narrative=PersonaNarrative(
            tone=["credible"],
            value_propositions=["documented quality control"],
            calls_to_action=["Request the technical datasheet"],
            prohibited_claims=["guaranteed delivery"],
        ),
        visual_bible=PersonaVisualBible(
            style=["industrial documentary"],
            palette=["#0B1F33"],
            camera_language=["slow dolly"],
            forbidden_visuals=["competitor logos"],
        ),
        default_workflow=VideoWorkflowMode.TEXT_TO_VIDEO,
    )


def approved_persona(db, actor, *, name="EU launch", key="persona:create:project"):
    service = VideoPersonaService(db, planning_enabled=True)
    persona, version = service.create(
        PersonaRevisionCommand(idempotency_key=key, spec=persona_spec(name)),
        actor,
    )
    service.approve(
        version.id,
        principal(actor.org_id, user_id=99, roles={"media_reviewer"}),
    )
    return persona, version


def brief():
    return VideoProjectBrief(
        title="Distributor proof campaign",
        objective="Generate qualified distributor enquiries",
        product_summary="AX-7 industrial controller",
        target_audience="German industrial distributors",
        markets=["DE"],
        channels=["linkedin", "website"],
        language="de-DE",
        target_duration_seconds=10,
    )


def ingest_evidence(db, org_id, *, roles=None, source="catalog.pdf"):
    return KnowledgeIngestionService(db).ingest(
        org_id=org_id,
        source_ref=source,
        title="Approved product catalog",
        chunks=["AX-7 has documented IP65 ingress protection."],
        authority="approved_document",
        sensitivity=Sensitivity.INTERNAL,
        allowed_roles=set(roles or {"sales"}),
    )


def storyboard(evidence_id):
    return Storyboard(
        title="Factory proof",
        total_duration_seconds=10,
        shots=[
            StoryboardShot(
                sequence=1,
                duration_seconds=10,
                purpose="proof",
                workflow_mode=VideoWorkflowMode.TEXT_TO_VIDEO,
                visual_prompt="Show the AX-7 enclosure in a clean factory",
                business_claims=["Documented IP65 ingress protection"],
                claim_evidence_ids=[evidence_id],
            )
        ],
    )


def test_project_pins_approved_persona_snapshot_across_future_revisions(db_session):
    org_id = uuid4()
    actor = principal(org_id)
    persona, first = approved_persona(db_session, actor)
    planning = VideoPlanningService(db_session, planning_enabled=True)

    project, _ = planning.create_project(
        VideoProjectCommand(
            idempotency_key="video-project:create:eu-proof",
            persona_version_id=first.id,
            brief=brief(),
            evidence_record_ids=[],
        ),
        actor,
    )
    _, second = VideoPersonaService(db_session, planning_enabled=True).revise(
        persona.id,
        PersonaRevisionCommand(
            idempotency_key="persona:revise:project:v2",
            spec=persona_spec("EU launch v2"),
        ),
        actor,
    )
    VideoPersonaService(db_session, planning_enabled=True).approve(
        second.id,
        principal(org_id, user_id=99, roles={"admin"}),
    )

    db_session.refresh(project)
    assert project.persona_version_id == first.id
    assert project.persona_spec_hash == first.spec_hash
    assert project.persona_snapshot_json["identity"]["name"] == "EU launch"
    assert second.spec_hash != project.persona_spec_hash


def test_project_evidence_is_active_same_org_and_acl_authorized(db_session):
    org_id = uuid4()
    actor = principal(org_id)
    _, version = approved_persona(db_session, actor, key="persona:create:evidence")
    allowed = ingest_evidence(db_session, org_id)
    denied = ingest_evidence(
        db_session,
        org_id,
        roles={"finance"},
        source="finance-only.pdf",
    )
    planning = VideoPlanningService(db_session, planning_enabled=True)

    project, evidence = planning.create_project(
        VideoProjectCommand(
            idempotency_key="video-project:create:evidence",
            persona_version_id=version.id,
            brief=brief(),
            evidence_record_ids=[allowed.record_id],
        ),
        actor,
    )

    assert evidence[0].project_id == project.id
    assert evidence[0].knowledge_record_id == allowed.record_id
    assert evidence[0].content_hash == allowed.content_hash

    with pytest.raises(VideoPlanningForbidden):
        planning.create_project(
            VideoProjectCommand(
                idempotency_key="video-project:create:denied-evidence",
                persona_version_id=version.id,
                brief=brief(),
                evidence_record_ids=[denied.record_id],
            ),
            actor,
        )


def test_storyboard_approval_rejects_claim_evidence_outside_project_snapshot(
    db_session,
):
    org_id = uuid4()
    actor = principal(org_id)
    _, persona_version = approved_persona(
        db_session,
        actor,
        key="persona:create:storyboard",
    )
    allowed = ingest_evidence(db_session, org_id)
    outside = ingest_evidence(db_session, org_id, source="outside.pdf")
    planning = VideoPlanningService(db_session, planning_enabled=True)
    project, evidence = planning.create_project(
        VideoProjectCommand(
            idempotency_key="video-project:create:storyboard",
            persona_version_id=persona_version.id,
            brief=brief(),
            evidence_record_ids=[allowed.record_id],
        ),
        actor,
    )
    _, draft = planning.revise_storyboard(
        project.id,
        StoryboardRevisionCommand(
            idempotency_key="storyboard:create:outside-evidence",
            storyboard=storyboard(outside.record_id),
        ),
        actor,
    )

    with pytest.raises(VideoPlanningConflict, match="evidence"):
        planning.approve_storyboard(
            draft.id,
            principal(org_id, user_id=99, roles={"media_reviewer"}),
        )

    _, valid = planning.revise_storyboard(
        project.id,
        StoryboardRevisionCommand(
            idempotency_key="storyboard:create:allowed-evidence",
            storyboard=storyboard(evidence[0].id),
        ),
        actor,
    )
    approved = planning.approve_storyboard(
        valid.id,
        principal(org_id, user_id=99, roles={"media_reviewer"}),
    )
    assert approved.status == "approved"


def test_project_creation_rejects_draft_or_cross_tenant_persona(db_session):
    org_id = uuid4()
    actor = principal(org_id)
    _, draft = VideoPersonaService(db_session, planning_enabled=True).create(
        PersonaRevisionCommand(
            idempotency_key="persona:create:draft-project",
            spec=persona_spec(),
        ),
        actor,
    )
    planning = VideoPlanningService(db_session, planning_enabled=True)

    with pytest.raises(VideoPlanningConflict):
        planning.create_project(
            VideoProjectCommand(
                idempotency_key="video-project:create:draft-persona",
                persona_version_id=draft.id,
                brief=brief(),
            ),
            actor,
        )
    with pytest.raises(VideoPlanningForbidden):
        planning.create_project(
            VideoProjectCommand(
                idempotency_key="video-project:create:cross-persona",
                persona_version_id=draft.id,
                brief=brief(),
            ),
            principal(uuid4()),
        )
