from datetime import date, datetime
import os
from uuid import uuid4

import pytest

from app.config import Settings
from app.models.database import (
    MediaGenerationJob,
    MediaRuntimeActivation,
    MediaRuntimeRevision,
)
from app.services.idempotency import canonical_hash
from app.services.media.runtime import (
    MediaCapabilityCatalog,
    MediaModelCapability,
    MediaWorkflowMode,
)
from app.services.media.worker_runtime import (
    MediaRuntimeUnavailable,
    PinnedMediaRuntimeFactory,
    ReservedEstimateCostResolver,
)


def catalog(model_id):
    return MediaCapabilityCatalog(
        provider="fal",
        schema_version=f"snapshot:{model_id}",
        models=[
            MediaModelCapability(
                id=model_id,
                display_name=model_id,
                modes=[MediaWorkflowMode.TEXT_TO_VIDEO],
            )
        ],
    )


def runtime(db_session, *, org_id, revision, model_id, user_id=7):
    snapshot = catalog(model_id).model_dump(mode="json")
    row = MediaRuntimeRevision(
        org_id=org_id,
        revision=revision,
        provider="fal",
        enabled_modes=[MediaWorkflowMode.TEXT_TO_VIDEO.value],
        model_aliases={MediaWorkflowMode.TEXT_TO_VIDEO.value: model_id},
        capability_snapshot=snapshot,
        capability_snapshot_hash=canonical_hash(snapshot),
        created_by_user_id=user_id,
    )
    db_session.add(row)
    db_session.flush()
    return row


def job(db_session, *, org_id, revision_id, model_id):
    row = MediaGenerationJob(
        org_id=org_id,
        owner_user_id=7,
        project_id=uuid4(),
        storyboard_version_id=uuid4(),
        shot_id=uuid4(),
        runtime_revision_id=revision_id,
        idempotency_key=f"worker-runtime:{uuid4()}",
        input_hash="a" * 64,
        intent_hash="b" * 64,
        payload_ref="vault://media-intents/worker/runtime",
        mode="text_to_video",
        provider="fal",
        model_id=model_id,
        sensitivity="internal",
        status="submitted",
        effect_state="confirmed",
        provider_request_id="fal-request-1",
        reserved_cost_microusd=2_500_000,
        estimate_hash="c" * 64,
        budget_period_start=date(2026, 8, 1),
        deadline_at=datetime(2026, 8, 12, 12, 0, 0),
    )
    db_session.add(row)
    db_session.commit()
    return row


def config(secret_dir):
    return Settings(
        _env_file=None,
        MEDIA_RUNTIME_SECRET_DIR=str(secret_dir),
    )


def write_key(secret_dir, revision_id, value):
    secret_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(secret_dir, 0o700)
    path = secret_dir / f"{revision_id}.key"
    path.write_text(value, encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def test_factory_uses_job_pinned_revision_not_current_activation(
    db_session,
    tmp_path,
):
    org_id = uuid4()
    pinned = runtime(
        db_session,
        org_id=org_id,
        revision=1,
        model_id="fal-ai/pinned-model",
    )
    active = runtime(
        db_session,
        org_id=org_id,
        revision=2,
        model_id="fal-ai/new-active-model",
    )
    db_session.add(
        MediaRuntimeActivation(
            org_id=org_id,
            active_revision_id=active.id,
            activated_by_user_id=7,
        )
    )
    db_session.commit()
    secret_dir = tmp_path / "runtime-secrets"
    write_key(secret_dir, pinned.id, "pinned-secret")
    write_key(secret_dir, active.id, "active-secret")
    generation = job(
        db_session,
        org_id=org_id,
        revision_id=pinned.id,
        model_id="fal-ai/pinned-model",
    )
    observed = {}

    def build_adapter(api_key, *, catalog):
        observed["api_key"] = api_key
        observed["catalog"] = catalog
        return object()

    adapter = PinnedMediaRuntimeFactory(
        db_session,
        config(secret_dir),
        adapter_builder=build_adapter,
    ).build(generation)

    assert adapter is not None
    assert observed["api_key"] == "pinned-secret"
    assert [model.id for model in observed["catalog"].models] == [
        "fal-ai/pinned-model"
    ]
    assert "active-secret" not in repr(observed["catalog"])


@pytest.mark.parametrize("mutation", ["hash", "model", "provider"])
def test_factory_fails_closed_for_tampered_snapshot_or_job(
    db_session,
    tmp_path,
    mutation,
):
    org_id = uuid4()
    pinned = runtime(
        db_session,
        org_id=org_id,
        revision=1,
        model_id="fal-ai/pinned-model",
    )
    secret_dir = tmp_path / "runtime-secrets"
    write_key(secret_dir, pinned.id, "pinned-secret")
    generation = job(
        db_session,
        org_id=org_id,
        revision_id=pinned.id,
        model_id="fal-ai/pinned-model",
    )
    if mutation == "hash":
        pinned.capability_snapshot_hash = "0" * 64
    elif mutation == "model":
        generation.model_id = "fal-ai/not-in-snapshot"
    else:
        generation.provider = "unapproved"
    db_session.commit()

    with pytest.raises(MediaRuntimeUnavailable):
        PinnedMediaRuntimeFactory(
            db_session,
            config(secret_dir),
            adapter_builder=lambda *_args, **_kwargs: pytest.fail(
                "invalid runtime must not construct an adapter"
            ),
        ).build(generation)


def test_factory_rejects_missing_or_overpermissive_secret_file(
    db_session,
    tmp_path,
):
    org_id = uuid4()
    pinned = runtime(
        db_session,
        org_id=org_id,
        revision=1,
        model_id="fal-ai/pinned-model",
    )
    db_session.commit()
    generation = job(
        db_session,
        org_id=org_id,
        revision_id=pinned.id,
        model_id="fal-ai/pinned-model",
    )
    secret_dir = tmp_path / "runtime-secrets"
    factory = PinnedMediaRuntimeFactory(db_session, config(secret_dir))

    with pytest.raises(MediaRuntimeUnavailable, match="unavailable"):
        factory.build(generation)

    path = write_key(secret_dir, pinned.id, "pinned-secret")
    os.chmod(path, 0o644)
    with pytest.raises(MediaRuntimeUnavailable, match="unavailable"):
        factory.build(generation)


def test_factory_rejects_symlinked_secret_file(db_session, tmp_path):
    org_id = uuid4()
    pinned = runtime(
        db_session,
        org_id=org_id,
        revision=1,
        model_id="fal-ai/pinned-model",
    )
    db_session.commit()
    generation = job(
        db_session,
        org_id=org_id,
        revision_id=pinned.id,
        model_id="fal-ai/pinned-model",
    )
    secret_dir = tmp_path / "runtime-secrets"
    secret_dir.mkdir(mode=0o700)
    target = tmp_path / "replacement.key"
    target.write_text("replacement-secret", encoding="utf-8")
    os.chmod(target, 0o600)
    (secret_dir / f"{pinned.id}.key").symlink_to(target)

    with pytest.raises(MediaRuntimeUnavailable, match="unavailable"):
        PinnedMediaRuntimeFactory(
            db_session,
            config(secret_dir),
            adapter_builder=lambda *_args, **_kwargs: pytest.fail(
                "symlinked secret must not construct an adapter"
            ),
        ).build(generation)


def test_reserved_estimate_cost_resolver_is_explicit_and_bounded(db_session):
    org_id = uuid4()
    pinned = runtime(
        db_session,
        org_id=org_id,
        revision=1,
        model_id="fal-ai/pinned-model",
    )
    generation = job(
        db_session,
        org_id=org_id,
        revision_id=pinned.id,
        model_id="fal-ai/pinned-model",
    )

    resolver = ReservedEstimateCostResolver()

    assert resolver.actual_cost_microusd(generation) == 2_500_000
    assert resolver.basis == "reserved_estimate_ceiling"
