from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.services.media.contracts import (
    GenerationIngressRequest,
    PersonaIdentity,
    PersonaNarrative,
    PersonaVisualBible,
    Storyboard,
    StoryboardShot,
    VideoPersonaSpec,
    VideoWorkflowMode,
)


def test_generation_ingress_cannot_assert_server_routing_or_identity():
    with pytest.raises(ValidationError):
        GenerationIngressRequest.model_validate(
            {
                "idempotency_key": "video:project-1:shot-1",
                "project_id": str(uuid4()),
                "shot_id": str(uuid4()),
                "requested_mode": "text_to_video",
                "creative_direction": "Cinematic product reveal",
                "provider": "unapproved-provider",
                "model": "auto/latest",
                "org_id": str(uuid4()),
                "user_id": 1,
                "sensitivity": "public",
            }
        )


def test_video_persona_is_structured_and_rejects_provider_configuration():
    persona = VideoPersonaSpec(
        identity=PersonaIdentity(
            name="EU distributor launch",
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
        default_workflow=VideoWorkflowMode.TEXT_TO_IMAGE_THEN_IMAGE_TO_VIDEO,
    )

    assert persona.identity.brand_name == "Acme Industrial"
    assert persona.default_workflow == (
        VideoWorkflowMode.TEXT_TO_IMAGE_THEN_IMAGE_TO_VIDEO
    )

    with pytest.raises(ValidationError):
        VideoPersonaSpec.model_validate(
            {
                **persona.model_dump(mode="json"),
                "provider": "fal",
            }
        )


def test_storyboard_requires_contiguous_shots_and_exact_total_duration():
    storyboard = Storyboard(
        title="Factory proof",
        total_duration_seconds=10,
        shots=[
            StoryboardShot(
                sequence=1,
                duration_seconds=4,
                purpose="hook",
                workflow_mode=VideoWorkflowMode.TEXT_TO_VIDEO,
                visual_prompt="Precision machining close-up",
            ),
            StoryboardShot(
                sequence=2,
                duration_seconds=6,
                purpose="proof",
                workflow_mode=VideoWorkflowMode.IMAGE_TO_VIDEO,
                visual_prompt="Animate the approved product photograph",
                reference_asset_ids=[uuid4()],
            ),
        ],
    )

    assert storyboard.total_duration_seconds == 10

    with pytest.raises(ValidationError):
        Storyboard(
            title="Broken sequence",
            total_duration_seconds=10,
            shots=[
                StoryboardShot(
                    sequence=2,
                    duration_seconds=10,
                    purpose="hook",
                    workflow_mode=VideoWorkflowMode.TEXT_TO_VIDEO,
                    visual_prompt="A factory",
                )
            ],
        )

    with pytest.raises(ValidationError):
        Storyboard(
            title="Broken duration",
            total_duration_seconds=9,
            shots=storyboard.shots,
        )


def test_storyboard_business_claims_require_evidence():
    with pytest.raises(ValidationError):
        StoryboardShot(
            sequence=1,
            duration_seconds=5,
            purpose="proof",
            workflow_mode=VideoWorkflowMode.TEXT_TO_VIDEO,
            visual_prompt="Show the certificate",
            business_claims=["ISO certified"],
        )


def test_image_to_video_requires_reference_asset():
    with pytest.raises(ValidationError):
        StoryboardShot(
            sequence=1,
            duration_seconds=5,
            purpose="product reveal",
            workflow_mode=VideoWorkflowMode.IMAGE_TO_VIDEO,
            visual_prompt="Animate the source product image",
        )
