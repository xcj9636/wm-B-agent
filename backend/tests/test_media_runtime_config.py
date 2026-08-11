import stat

import pytest

from app.config import Settings
from app.services.media.runtime import (
    MediaCapabilityCatalog,
    MediaModelCapability,
    MediaProviderProbe,
    MediaRuntimeRevisionCreate,
    MediaRuntimeService,
    MediaWorkflowMode,
)


class FakeMediaProviderControl:
    def __init__(self) -> None:
        self.discover_calls: list[str] = []
        self.probe_ready = True

    async def discover_capabilities(self, api_key: str) -> MediaCapabilityCatalog:
        self.discover_calls.append(api_key)
        return MediaCapabilityCatalog(
            provider="fal",
            schema_version="fixture-v1",
            models=[
                MediaModelCapability(
                    id="fal-ai/acme-image",
                    display_name="Acme Image",
                    modes=[MediaWorkflowMode.TEXT_TO_IMAGE],
                ),
                MediaModelCapability(
                    id="fal-ai/acme-video",
                    display_name="Acme Video",
                    modes=[
                        MediaWorkflowMode.IMAGE_TO_VIDEO,
                        MediaWorkflowMode.TEXT_TO_VIDEO,
                    ],
                ),
            ],
        )

    async def probe(
        self,
        *,
        api_key: str,
        model_ids: list[str],
    ) -> MediaProviderProbe:
        assert api_key
        assert model_ids
        return MediaProviderProbe(
            ready=self.probe_ready,
            reachable=True,
            issues=[] if self.probe_ready else ["provider_probe_failed"],
        )


def runtime_settings(tmp_path, **overrides) -> Settings:
    values = {
        "_env_file": None,
        "MEDIA_RUNTIME_SECRET_DIR": str(tmp_path / "media-runtime"),
    }
    values.update(overrides)
    return Settings(**values)


def revision_command(**overrides) -> MediaRuntimeRevisionCreate:
    values = {
        "provider": "fal",
        "enabled_modes": [
            MediaWorkflowMode.TEXT_TO_IMAGE,
            MediaWorkflowMode.TEXT_TO_VIDEO,
        ],
        "model_aliases": {
            MediaWorkflowMode.TEXT_TO_IMAGE: "fal-ai/acme-image",
            MediaWorkflowMode.TEXT_TO_VIDEO: "fal-ai/acme-video",
        },
        "api_key": "write-only-secret",
    }
    values.update(overrides)
    return MediaRuntimeRevisionCreate(**values)


def test_media_runtime_defaults_fail_closed_and_is_secret_free(
    api_context,
    tmp_path,
):
    _, db, _ = api_context
    service = MediaRuntimeService(
        db,
        runtime_settings(tmp_path),
        provider_control=FakeMediaProviderControl(),
    )

    state = service.get_state()

    assert state.active_revision is None
    assert state.submission_enabled is False
    assert state.api_key_configured is False
    assert "secret" not in state.model_dump_json().lower()
    assert "api_key" not in state.model_dump_json().lower()


@pytest.mark.asyncio
async def test_media_runtime_revisions_are_immutable_and_activation_is_explicit(
    api_context,
    tmp_path,
):
    _, db, user = api_context
    provider = FakeMediaProviderControl()
    service = MediaRuntimeService(
        db,
        runtime_settings(tmp_path, MEDIA_SUBMIT_ENABLED=False),
        provider_control=provider,
    )

    first = await service.create_revision(
        revision_command(),
        created_by_user_id=user.id,
    )
    await service.probe_revision(first.id, probed_by_user_id=user.id)
    activated = service.activate_revision(first.id, activated_by_user_id=user.id)
    second = await service.create_revision(
        revision_command(
            enabled_modes=[MediaWorkflowMode.IMAGE_TO_VIDEO],
            model_aliases={
                MediaWorkflowMode.IMAGE_TO_VIDEO: "fal-ai/acme-video"
            },
            api_key=None,
        ),
        created_by_user_id=user.id,
    )

    assert first.revision == 1
    assert second.revision == 2
    assert activated.active_revision.id == first.id
    assert service.get_state().active_revision.id == first.id
    assert service.get_revision(first.id).model_aliases == {
        MediaWorkflowMode.TEXT_TO_IMAGE: "fal-ai/acme-image",
        MediaWorkflowMode.TEXT_TO_VIDEO: "fal-ai/acme-video",
    }
    assert second.model_aliases == {
        MediaWorkflowMode.IMAGE_TO_VIDEO: "fal-ai/acme-video"
    }
    assert first.capability_snapshot_hash == second.capability_snapshot_hash
    assert provider.discover_calls == ["write-only-secret", "write-only-secret"]

    secret_files = list((tmp_path / "media-runtime").glob("*.key"))
    assert len(secret_files) == 2
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in secret_files)
    serialized = first.model_dump_json() + second.model_dump_json()
    assert "write-only-secret" not in serialized
    assert str(tmp_path) not in serialized


@pytest.mark.asyncio
async def test_media_runtime_rejects_unknown_dynamic_or_incompatible_models(
    api_context,
    tmp_path,
):
    _, db, user = api_context
    service = MediaRuntimeService(
        db,
        runtime_settings(tmp_path),
        provider_control=FakeMediaProviderControl(),
    )

    invalid_commands = [
        revision_command(
            model_aliases={
                MediaWorkflowMode.TEXT_TO_IMAGE: "auto/fastest",
                MediaWorkflowMode.TEXT_TO_VIDEO: "fal-ai/acme-video",
            }
        ),
        revision_command(
            model_aliases={
                MediaWorkflowMode.TEXT_TO_IMAGE: "fal-ai/unknown",
                MediaWorkflowMode.TEXT_TO_VIDEO: "fal-ai/acme-video",
            }
        ),
        revision_command(
            model_aliases={
                MediaWorkflowMode.TEXT_TO_IMAGE: "fal-ai/acme-video",
                MediaWorkflowMode.TEXT_TO_VIDEO: "fal-ai/acme-video",
            }
        ),
    ]

    for command in invalid_commands:
        with pytest.raises(ValueError):
            await service.create_revision(command, created_by_user_id=user.id)

    assert service.list_revisions() == []


@pytest.mark.asyncio
async def test_media_runtime_requires_a_healthy_probe_before_activation(
    api_context,
    tmp_path,
):
    _, db, user = api_context
    provider = FakeMediaProviderControl()
    service = MediaRuntimeService(
        db,
        runtime_settings(tmp_path),
        provider_control=provider,
    )
    revision = await service.create_revision(
        revision_command(),
        created_by_user_id=user.id,
    )

    with pytest.raises(ValueError, match="healthy probe"):
        service.activate_revision(revision.id, activated_by_user_id=user.id)

    provider.probe_ready = False
    probe = await service.probe_revision(
        revision.id,
        probed_by_user_id=user.id,
    )
    assert probe.ready is False
    with pytest.raises(ValueError, match="healthy probe"):
        service.activate_revision(revision.id, activated_by_user_id=user.id)
