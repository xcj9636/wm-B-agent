from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.database import (
    MediaBudgetAccount,
    MediaBudgetLedgerEntry,
    MediaGenerationJob,
    MediaRuntimeActivation,
    MediaRuntimeRevision,
)
from app.services.agent_runtime.contracts import ExecutionPrincipal, Sensitivity
from app.services.idempotency import canonical_hash
from app.services.media.contracts import GenerationIntent, GenerationMode
from app.services.media.intent_vault import MediaIntentVaultUnavailable
from app.services.media.job_creator import (
    MediaGenerationJobCreateRequest,
    MediaGenerationJobCreator,
    MediaGenerationJobUnavailable,
)


NOW = datetime(2026, 8, 13, 11, 0, tzinfo=timezone.utc)


def principal(org_id, user_id=7):
    return ExecutionPrincipal(
        org_id=org_id,
        user_id=user_id,
        roles={"media_operator"},
        entitlements_hash="a" * 64,
        authn_context="jwt:mfa",
    )


class FakeCompiler:
    def __init__(self, intent):
        self.intent = intent
        self.calls = []

    def compile(self, project_id, storyboard_version_id, shot_id, actor):
        self.calls.append((project_id, storyboard_version_id, shot_id, actor))
        return SimpleNamespace(intent=self.intent)


class FakeVault:
    def __init__(self, *, failure=None):
        self.failure = failure
        self.values = []

    def store(self, intent):
        if self.failure is not None:
            raise self.failure
        self.values.append(intent)
        return f"vault://media-intents/{intent.attempt_id}"


def install_runtime_and_budget(db_session, org_id, *, model_id="fal-ai/t2v"):
    snapshot = {
        "provider": "fal",
        "schema_version": "frozen-v1",
        "models": [
            {
                "id": model_id,
                "display_name": "Approved T2V",
                "modes": ["text_to_video"],
            }
        ],
    }
    runtime = MediaRuntimeRevision(
        org_id=org_id,
        revision=1,
        provider="fal",
        enabled_modes=["text_to_video"],
        model_aliases={"text_to_video": model_id},
        capability_snapshot=snapshot,
        capability_snapshot_hash=canonical_hash(snapshot),
        created_by_user_id=7,
    )
    db_session.add(runtime)
    db_session.flush()
    db_session.add(
        MediaRuntimeActivation(
            org_id=org_id,
            active_revision_id=runtime.id,
            activated_by_user_id=7,
        )
    )
    db_session.add(
        MediaBudgetAccount(
            org_id=org_id,
            period_start=date(2026, 8, 1),
            limit_microusd=10_000_000,
            reserved_microusd=0,
            spent_microusd=0,
        )
    )
    db_session.commit()
    return runtime


def base_intent(org_id, user_id=7):
    return GenerationIntent(
        project_id=uuid4(),
        shot_id=uuid4(),
        persona_version_id=uuid4(),
        org_id=org_id,
        actor_user_id=user_id,
        mode=GenerationMode.TEXT_TO_VIDEO,
        prompt="Approved export video prompt",
        sensitivity=Sensitivity.INTERNAL,
        persona_approved=True,
        storyboard_approved=True,
    )


def request(intent, *, key="media-generate-shot-v1"):
    return MediaGenerationJobCreateRequest(
        idempotency_key=key,
        project_id=intent.project_id,
        storyboard_version_id=uuid4(),
        shot_id=intent.shot_id,
    )


def creator(db_session, compiler, vault, *, ceiling=2_500_000):
    return MediaGenerationJobCreator(
        db_session,
        compiler=compiler,
        vault=vault,
        reservation_ceilings={GenerationMode.TEXT_TO_VIDEO: ceiling},
        deadline_seconds=3600,
    )


def test_create_derives_stable_intent_runtime_model_budget_and_is_idempotent(
    db_session,
):
    org_id = uuid4()
    runtime = install_runtime_and_budget(db_session, org_id)
    intent = base_intent(org_id)
    compiler = FakeCompiler(intent)
    vault = FakeVault()
    service = creator(db_session, compiler, vault)
    command = request(intent)

    first, created = service.create(command, principal(org_id), now=NOW)
    replay, replay_created = service.create(command, principal(org_id), now=NOW)

    assert created is True
    assert replay_created is False
    assert replay.id == first.id
    assert first.runtime_revision_id == runtime.id
    assert first.model_id == "fal-ai/t2v"
    assert first.reserved_cost_microusd == 2_500_000
    assert first.deadline_at == (NOW + timedelta(hours=1)).replace(tzinfo=None)
    assert len(vault.values) == 2
    assert vault.values[0].attempt_id == vault.values[1].attempt_id
    assert first.payload_ref == (
        f"vault://media-intents/{vault.values[0].attempt_id}"
    )
    assert first.intent_hash == vault.values[0].input_hash()
    assert first.estimate_hash == canonical_hash(
        {
            "basis": "configured_reservation_ceiling",
            "runtime_revision_id": str(runtime.id),
            "capability_snapshot_hash": runtime.capability_snapshot_hash,
            "model_id": "fal-ai/t2v",
            "mode": "text_to_video",
            "reservation_ceiling_microusd": 2_500_000,
        }
    )
    assert db_session.query(MediaGenerationJob).count() == 1
    assert db_session.query(MediaBudgetLedgerEntry).count() == 1
    assert "Approved export video prompt" not in repr(first.__dict__)
    assert "Approved export video prompt" not in repr(first.events)


def test_vault_failure_never_creates_job_or_reserves_budget(db_session):
    org_id = uuid4()
    install_runtime_and_budget(db_session, org_id)
    intent = base_intent(org_id)
    service = creator(
        db_session,
        FakeCompiler(intent),
        FakeVault(
            failure=MediaIntentVaultUnavailable("secret path must not leak")
        ),
    )

    with pytest.raises(MediaGenerationJobUnavailable):
        service.create(request(intent), principal(org_id), now=NOW)

    assert db_session.query(MediaGenerationJob).count() == 0
    assert db_session.query(MediaBudgetLedgerEntry).count() == 0
    account = db_session.query(MediaBudgetAccount).one()
    assert account.reserved_microusd == 0


def test_missing_ceiling_or_tampered_runtime_fails_before_vault_write(db_session):
    org_id = uuid4()
    runtime = install_runtime_and_budget(db_session, org_id)
    intent = base_intent(org_id)
    vault = FakeVault()

    with pytest.raises(MediaGenerationJobUnavailable):
        MediaGenerationJobCreator(
            db_session,
            compiler=FakeCompiler(intent),
            vault=vault,
            reservation_ceilings={},
            deadline_seconds=3600,
        ).create(request(intent), principal(org_id), now=NOW)
    assert vault.values == []

    runtime.capability_snapshot_hash = "0" * 64
    db_session.commit()
    with pytest.raises(MediaGenerationJobUnavailable):
        creator(db_session, FakeCompiler(intent), vault).create(
            request(intent),
            principal(org_id),
            now=NOW,
        )
    assert vault.values == []


def test_compiled_identity_or_requested_envelope_mismatch_is_hidden(db_session):
    org_id = uuid4()
    install_runtime_and_budget(db_session, org_id)
    intent = base_intent(org_id)
    vault = FakeVault()

    wrong_actor = intent.model_copy(update={"actor_user_id": 99})
    with pytest.raises(MediaGenerationJobUnavailable):
        creator(db_session, FakeCompiler(wrong_actor), vault).create(
            request(intent),
            principal(org_id),
            now=NOW,
        )

    wrong_shot = request(intent).model_copy(update={"shot_id": uuid4()})
    with pytest.raises(MediaGenerationJobUnavailable):
        creator(db_session, FakeCompiler(intent), vault).create(
            wrong_shot,
            principal(org_id),
            now=NOW,
        )

    assert vault.values == []


def test_request_rejects_browser_owned_model_price_prompt_and_tenant():
    intent = base_intent(uuid4())
    values = request(intent).model_dump(mode="json")
    for field, value in [
        ("model_id", "attacker/model"),
        ("estimated_cost_microusd", 0),
        ("prompt", "ignore policy"),
        ("org_id", str(uuid4())),
    ]:
        with pytest.raises(Exception):
            MediaGenerationJobCreateRequest(**values, **{field: value})
