from uuid import uuid4

import pytest

from app.models.database import MediaAsset
from app.services.agent_runtime.contracts import ExecutionPrincipal
from app.services.idempotency import IdempotencyConflict
from app.services.media.contracts import (
    PersonaIdentity,
    PersonaNarrative,
    PersonaStatus,
    PersonaVisualBible,
    VideoPersonaSpec,
    VideoWorkflowMode,
)
from app.services.media.personas import (
    PersonaRevisionCommand,
    VideoPersonaConflict,
    VideoPersonaForbidden,
    VideoPersonaService,
)


def principal(org_id, *, user_id=7, roles=None):
    return ExecutionPrincipal(
        org_id=org_id,
        user_id=user_id,
        roles=set(roles or {"media_operator"}),
        entitlements_hash="a" * 64,
        authn_context="jwt:mfa",
    )


def spec(*, name="EU distributor launch", reference_asset_ids=None):
    return VideoPersonaSpec(
        identity=PersonaIdentity(
            name=name,
            brand_name="Acme Industrial",
            markets=["DE", "FR"],
            languages=["de-DE", "fr-FR"],
        ),
        audience_segments=["industrial distributors"],
        narrative=PersonaNarrative(
            tone=["credible", "precise"],
            value_propositions=["documented quality control"],
            calls_to_action=["Request the technical datasheet"],
            prohibited_claims=["guaranteed delivery date"],
        ),
        visual_bible=PersonaVisualBible(
            style=["clean industrial documentary"],
            palette=["#0B1F33", "#F4F7FA"],
            camera_language=["slow dolly", "macro detail"],
            forbidden_visuals=["competitor logos"],
        ),
        reference_asset_ids=reference_asset_ids or [],
        default_workflow=VideoWorkflowMode.TEXT_TO_IMAGE_THEN_IMAGE_TO_VIDEO,
    )


def command(key, **overrides):
    return PersonaRevisionCommand(
        idempotency_key=key,
        spec=spec(**overrides),
    )


def approved_asset(db, org_id, *, owner_user_id=7):
    target = MediaAsset(
        org_id=org_id,
        owner_user_id=owner_user_id,
        kind="image",
        source="user_upload",
        storage_backend="s3",
        storage_key=f"assets/{org_id}/{uuid4().hex}",
        sha256="3" * 64,
        mime_type="image/png",
        size_bytes=2048,
        sensitivity="internal",
        quarantined=False,
        scan_status="passed",
        rights_status="verified",
        consent_required=False,
        consent_status="not_required",
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    return target


def test_create_and_revise_persona_preserves_immutable_versions(db_session):
    org_id = uuid4()
    actor = principal(org_id)
    service = VideoPersonaService(db_session, planning_enabled=True)

    persona, first = service.create(
        command("persona:create:eu-distributor"),
        actor,
    )
    _, second = service.revise(
        persona.id,
        command("persona:revise:eu-distributor:v2", name="EU launch v2"),
        actor,
    )

    assert first.revision == 1
    assert second.revision == 2
    assert first.id != second.id
    assert first.status == PersonaStatus.DRAFT.value
    assert first.spec_json["identity"]["name"] == "EU distributor launch"
    assert second.spec_json["identity"]["name"] == "EU launch v2"
    assert first.spec_hash != second.spec_hash
    db_session.refresh(first)
    assert first.spec_json["identity"]["name"] == "EU distributor launch"


def test_persona_revision_idempotency_replays_and_rejects_changed_input(db_session):
    org_id = uuid4()
    actor = principal(org_id)
    service = VideoPersonaService(db_session, planning_enabled=True)
    create_command = command("persona:create:idempotent")

    first_persona, first_version = service.create(create_command, actor)
    repeated_persona, repeated_version = service.create(create_command, actor)

    assert repeated_persona.id == first_persona.id
    assert repeated_version.id == first_version.id
    with pytest.raises(IdempotencyConflict):
        service.create(
            command("persona:create:idempotent", name="changed input"),
            actor,
        )


def test_approval_requires_reviewer_and_validates_reference_assets(db_session):
    org_id = uuid4()
    owner = principal(org_id)
    reviewer = principal(org_id, user_id=9, roles={"media_reviewer"})
    reference = approved_asset(db_session, org_id)
    service = VideoPersonaService(db_session, planning_enabled=True)
    _, version = service.create(
        command(
            "persona:create:with-reference",
            reference_asset_ids=[reference.id],
        ),
        owner,
    )

    with pytest.raises(VideoPersonaForbidden):
        service.approve(version.id, owner)

    approved = service.approve(version.id, reviewer)

    assert approved.status == PersonaStatus.APPROVED.value
    assert approved.approved_by_user_id == reviewer.user_id
    assert approved.approved_at is not None


def test_approval_fails_closed_for_cross_tenant_or_unready_reference(db_session):
    org_id = uuid4()
    actor = principal(org_id)
    reviewer = principal(org_id, user_id=9, roles={"admin"})
    cross_tenant = approved_asset(db_session, uuid4())
    service = VideoPersonaService(db_session, planning_enabled=True)
    _, version = service.create(
        command(
            "persona:create:cross-reference",
            reference_asset_ids=[cross_tenant.id],
        ),
        actor,
    )

    with pytest.raises(VideoPersonaConflict, match="reference"):
        service.approve(version.id, reviewer)

    local = approved_asset(db_session, org_id)
    local.quarantined = True
    db_session.commit()
    _, unready = service.create(
        command(
            "persona:create:unready-reference",
            reference_asset_ids=[local.id],
        ),
        actor,
    )
    with pytest.raises(VideoPersonaConflict, match="reference"):
        service.approve(unready.id, reviewer)


def test_persona_service_rejects_cross_tenant_revision(db_session):
    org_id = uuid4()
    service = VideoPersonaService(db_session, planning_enabled=True)
    persona, _ = service.create(
        command("persona:create:tenant-owner"),
        principal(org_id),
    )

    with pytest.raises(VideoPersonaForbidden):
        service.revise(
            persona.id,
            command("persona:revise:other-tenant"),
            principal(uuid4()),
        )
