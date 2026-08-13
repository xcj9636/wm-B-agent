from datetime import datetime, timezone
from uuid import uuid4

from app.main import app
from app.services.media.runtime import (
    MediaCapabilityCatalog,
    MediaModelCapability,
    MediaRuntimeProbeResponse,
    MediaRuntimeRevisionResponse,
    MediaRuntimeState,
    MediaWorkflowMode,
    get_media_runtime_service,
)


class FakeMediaRuntimeService:
    def __init__(self) -> None:
        self.revision_id = uuid4()
        self.org_id = uuid4()
        self.created = None
        self.probed = None
        self.activated = None
        self.catalog = MediaCapabilityCatalog(
            provider="fal",
            schema_version="curated-2026-08-11",
            models=[
                MediaModelCapability(
                    id="fal-ai/flux/schnell",
                    display_name="FLUX.1 schnell",
                    modes=[MediaWorkflowMode.TEXT_TO_IMAGE],
                ),
                MediaModelCapability(
                    id="fal-ai/kling-video/v2/master/text-to-video",
                    display_name="Kling Video V2 Master",
                    modes=[MediaWorkflowMode.TEXT_TO_VIDEO],
                ),
            ],
        )
        self.revision = MediaRuntimeRevisionResponse(
            id=self.revision_id,
            org_id=self.org_id,
            revision=2,
            provider="fal",
            enabled_modes=[MediaWorkflowMode.TEXT_TO_VIDEO],
            model_aliases={
                MediaWorkflowMode.TEXT_TO_VIDEO:
                    "fal-ai/kling-video/v2/master/text-to-video"
            },
            capability_snapshot=self.catalog,
            capability_snapshot_hash="a" * 64,
            pricing_configured=True,
            pricing_snapshot_hash="b" * 64,
            api_key_configured=True,
            created_at=datetime(2026, 8, 11, tzinfo=timezone.utc),
        )

    def get_state(self):
        return MediaRuntimeState(
            active_revision=self.revision if self.activated else None,
            submission_enabled=False,
            api_key_configured=bool(self.activated),
        )

    def get_capabilities(self):
        return self.catalog

    def list_revisions(self):
        return [self.revision]

    async def create_revision(self, command, *, created_by_user_id):
        self.created = (command, created_by_user_id)
        return self.revision

    async def probe_revision(self, revision_id, *, probed_by_user_id):
        self.probed = (revision_id, probed_by_user_id)
        return MediaRuntimeProbeResponse(
            id=uuid4(),
            revision_id=revision_id,
            ready=True,
            reachable=True,
            issues=[],
            capability_snapshot_hash="a" * 64,
            created_at=datetime(2026, 8, 11, tzinfo=timezone.utc),
        )

    def activate_revision(self, revision_id, *, activated_by_user_id):
        self.activated = (revision_id, activated_by_user_id)
        return self.get_state()


def test_media_runtime_admin_api_is_admin_only(api_context):
    client, db, user = api_context
    runtime = FakeMediaRuntimeService()
    app.dependency_overrides[get_media_runtime_service] = lambda: runtime

    assert client.get("/api/v1/admin/media/runtime").status_code == 403
    assert client.get("/api/v1/admin/media/runtime/capabilities").status_code == 403
    assert client.get("/api/v1/admin/media/runtime/revisions").status_code == 403

    user.is_superuser = True
    db.commit()
    state = client.get("/api/v1/admin/media/runtime")
    assert state.status_code == 200


def test_media_runtime_admin_can_create_probe_and_activate_revision(api_context):
    client, db, user = api_context
    user.is_superuser = True
    db.commit()
    runtime = FakeMediaRuntimeService()
    app.dependency_overrides[get_media_runtime_service] = lambda: runtime

    capabilities = client.get("/api/v1/admin/media/runtime/capabilities")
    created = client.post(
        "/api/v1/admin/media/runtime/revisions",
        json={
            "provider": "fal",
            "enabled_modes": ["text_to_video"],
            "model_aliases": {
                "text_to_video": "fal-ai/kling-video/v2/master/text-to-video"
            },
            "api_key": "write-only-secret",
        },
    )
    probed = client.post(
        f"/api/v1/admin/media/runtime/revisions/{runtime.revision_id}/probe"
    )
    activated = client.post(
        f"/api/v1/admin/media/runtime/revisions/{runtime.revision_id}/activate"
    )

    assert capabilities.status_code == 200
    assert capabilities.json()["schema_version"] == "curated-2026-08-11"
    assert created.status_code == 201
    assert "write-only-secret" not in created.text
    assert created.json()["pricing_configured"] is True
    assert created.json()["pricing_snapshot_hash"] == "b" * 64
    assert "unit_price" not in created.text
    assert runtime.created[1] == user.id
    assert probed.json()["ready"] is True
    assert runtime.probed == (runtime.revision_id, user.id)
    assert activated.json()["active_revision"]["id"] == str(runtime.revision_id)
    assert runtime.activated == (runtime.revision_id, user.id)


def test_media_runtime_admin_api_rejects_unknown_fields(api_context):
    client, db, user = api_context
    user.is_superuser = True
    db.commit()
    runtime = FakeMediaRuntimeService()
    app.dependency_overrides[get_media_runtime_service] = lambda: runtime

    response = client.post(
        "/api/v1/admin/media/runtime/revisions",
        json={
            "provider": "fal",
            "enabled_modes": ["text_to_video"],
            "model_aliases": {
                "text_to_video": "fal-ai/kling-video/v2/master/text-to-video"
            },
            "api_key": "write-only-secret",
            "base_url": "http://127.0.0.1/admin",
        },
    )

    assert response.status_code == 422
    assert runtime.created is None
