from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.v1.video import get_media_generation_job_creator
from app.main import app
from app.services.idempotency import IdempotencyConflict
from app.services.media.job_creator import MediaGenerationJobUnavailable


class FakeCreator:
    def __init__(self, *, failure=None, created=True):
        self.failure = failure
        self.created = created
        self.calls = []

    def create(self, request, principal, *, now):
        self.calls.append((request, principal, now))
        if self.failure is not None:
            raise self.failure
        return (
            SimpleNamespace(
                id=uuid4(),
                project_id=request.project_id,
                storyboard_version_id=request.storyboard_version_id,
                shot_id=request.shot_id,
                mode="text_to_video",
                provider="fal",
                model_id="fal-ai/t2v",
                sensitivity="internal",
                status="queued",
                effect_state="none",
                reserved_cost_microusd=2_500_000,
                provider_state=None,
                error_code=None,
                created_at=datetime(2026, 8, 13, 11, 0),
                updated_at=datetime(2026, 8, 13, 11, 0),
                completed_at=None,
            ),
            self.created,
        )


def payload():
    return {
        "idempotency_key": "media-generate-shot-v1",
        "storyboard_version_id": str(uuid4()),
    }


def endpoint(project_id, shot_id):
    return (
        f"/api/v1/video/projects/{project_id}/shots/{shot_id}"
        "/generation-jobs"
    )


def test_authenticated_job_create_is_server_owned_and_secret_free(api_context):
    client, _, _ = api_context
    project_id = uuid4()
    shot_id = uuid4()
    request_body = payload()
    service = FakeCreator()
    app.dependency_overrides[get_media_generation_job_creator] = lambda: service

    response = client.post(endpoint(project_id, shot_id), json=request_body)

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["model_id"] == "fal-ai/t2v"
    assert body["reservation_ceiling_microusd"] == 2_500_000
    assert set(body).isdisjoint(
        {
            "payload_ref",
            "intent_hash",
            "input_hash",
            "estimate_hash",
            "prompt",
            "org_id",
            "owner_user_id",
            "runtime_revision_id",
        }
    )
    request, principal, now = service.calls[0]
    assert request.project_id == project_id
    assert request.shot_id == shot_id
    assert str(request.storyboard_version_id) == request_body["storyboard_version_id"]
    assert principal.user_id > 0
    assert now.tzinfo is not None


def test_idempotent_replay_returns_200_and_strict_body(api_context):
    client, _, _ = api_context
    project_id, shot_id = uuid4(), uuid4()
    service = FakeCreator(created=False)
    app.dependency_overrides[get_media_generation_job_creator] = lambda: service

    replay = client.post(endpoint(project_id, shot_id), json=payload())
    spoofed = client.post(
        endpoint(project_id, shot_id),
        json={**payload(), "model_id": "attacker/model", "prompt": "bypass"},
    )

    assert replay.status_code == 200
    assert spoofed.status_code == 422
    assert len(service.calls) == 1


@pytest.mark.parametrize(
    ("failure", "status_code"),
    [
        (MediaGenerationJobUnavailable("secret path"), 503),
        (IdempotencyConflict("private hash"), 409),
        (PermissionError("cross organization"), 404),
        (LookupError("missing project"), 404),
    ],
)
def test_creation_errors_are_sanitized_and_hide_resource_existence(
    api_context,
    failure,
    status_code,
):
    client, _, _ = api_context
    app.dependency_overrides[get_media_generation_job_creator] = lambda: FakeCreator(
        failure=failure
    )

    response = client.post(endpoint(uuid4(), uuid4()), json=payload())

    assert response.status_code == status_code
    assert "secret path" not in response.text
    assert "private hash" not in response.text
    assert "cross organization" not in response.text
