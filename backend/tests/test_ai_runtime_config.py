import pytest

from app.config import Settings
from app.services.ai_runtime import AIRuntimeConfigUpdate, AIRuntimeService


def runtime_settings(tmp_path, **overrides):
    values = {
        "_env_file": None,
        "LLM_BACKEND": "omniroute",
        "OMNIROUTE_BASE_URL": "http://omniroute.test",
        "OMNIROUTE_API_KEY": "environment-secret",
        "OMNIROUTE_ALLOWED_PROVIDERS": ["approved-provider"],
        "OMNIROUTE_MODEL_MESSAGE_DRAFT": "draft-v1",
        "OMNIROUTE_MODEL_LIVE_REPLY": "reply-v1",
        "AI_RUNTIME_SECRET_FILE": str(tmp_path / "omniroute.key"),
    }
    values.update(overrides)
    return Settings(**values)


def test_runtime_config_is_secret_free_and_uses_environment_fallback(api_context, tmp_path):
    _, db, _ = api_context
    service = AIRuntimeService(db, runtime_settings(tmp_path))

    config = service.get_config()

    assert config.backend == "omniroute"
    assert config.source == "environment"
    assert config.version == 0
    assert config.api_key_configured is True
    assert "secret" not in config.model_dump_json()


def test_runtime_update_is_persistent_versioned_and_fail_closed(api_context, tmp_path):
    _, db, user = api_context
    service = AIRuntimeService(db, runtime_settings(tmp_path))

    updated = service.update_config(
        AIRuntimeConfigUpdate(
            backend="omniroute",
            base_url="http://gateway.internal:20128",
            allowed_providers=[" OpenAI ", "openai", "AZURE-OPENAI"],
            model_aliases={
                "message_draft": "draft-safe-v2",
                "live_reply": "reply-safe-v2",
            },
            timeout_seconds=45,
            api_key="runtime-secret",
        ),
        updated_by_user_id=user.id,
    )
    db.commit()

    reloaded = AIRuntimeService(db, runtime_settings(tmp_path)).get_config()
    assert updated.version == 1
    assert reloaded.version == 1
    assert reloaded.source == "runtime"
    assert reloaded.allowed_providers == ["openai", "azure-openai"]
    assert reloaded.model_aliases["live_reply"] == "reply-safe-v2"
    assert reloaded.api_key_configured is True
    assert "runtime-secret" not in reloaded.model_dump_json()
    assert (tmp_path / "omniroute.key").read_text() == "runtime-secret"


@pytest.mark.parametrize(
    "update",
    [
        AIRuntimeConfigUpdate(
            backend="omniroute",
            base_url="http://gateway.internal",
            allowed_providers=[],
            model_aliases={"message_draft": "draft", "live_reply": "reply"},
        ),
        AIRuntimeConfigUpdate(
            backend="omniroute",
            base_url="http://gateway.internal",
            allowed_providers=["openai"],
            model_aliases={"message_draft": "draft", "live_reply": "auto/fastest"},
        ),
    ],
)
def test_runtime_config_rejects_unsafe_routes_before_persistence(api_context, tmp_path, update):
    _, db, user = api_context
    service = AIRuntimeService(db, runtime_settings(tmp_path))

    with pytest.raises(ValueError):
        service.update_config(update, updated_by_user_id=user.id)

