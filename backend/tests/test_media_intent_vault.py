import base64
import os
import stat
from pathlib import Path
from uuid import uuid4

import pytest

from app.services.agent_runtime.contracts import Sensitivity
from app.services.media.contracts import GenerationIntent, GenerationMode
from app.services.media.intent_vault import (
    EncryptedMediaIntentVault,
    MediaIntentVaultConflict,
    MediaIntentVaultUnavailable,
)


def generation_intent(*, attempt_id=None, prompt="Private approved export film"):
    return GenerationIntent(
        attempt_id=attempt_id or uuid4(),
        project_id=uuid4(),
        shot_id=uuid4(),
        persona_version_id=uuid4(),
        org_id=uuid4(),
        actor_user_id=7,
        mode=GenerationMode.TEXT_TO_VIDEO,
        prompt=prompt,
        sensitivity=Sensitivity.INTERNAL,
        persona_approved=True,
        storyboard_approved=True,
    )


def write_key(path: Path, *, mode=0o600) -> None:
    path.write_text(base64.urlsafe_b64encode(os.urandom(32)).decode("ascii"))
    path.chmod(mode)


def vault(tmp_path: Path) -> EncryptedMediaIntentVault:
    key_file = tmp_path / "media-intent.key"
    write_key(key_file)
    return EncryptedMediaIntentVault(
        root=tmp_path / "media-intents",
        key_file=key_file,
    )


def test_round_trip_is_encrypted_private_and_idempotent(tmp_path):
    store = vault(tmp_path)
    intent = generation_intent()

    payload_ref = store.store(intent)
    repeated_ref = store.store(intent)

    assert payload_ref == f"vault://media-intents/{intent.attempt_id}"
    assert repeated_ref == payload_ref
    assert store.load(payload_ref) == intent
    ciphertext_path = tmp_path / "media-intents" / f"{intent.attempt_id}.intent"
    raw = ciphertext_path.read_bytes()
    assert intent.prompt.encode("utf-8") not in raw
    assert stat.S_IMODE(ciphertext_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(ciphertext_path.parent.stat().st_mode) == 0o700


def test_attempt_id_cannot_be_reused_for_changed_intent(tmp_path):
    store = vault(tmp_path)
    intent = generation_intent()
    store.store(intent)

    with pytest.raises(MediaIntentVaultConflict):
        store.store(intent.model_copy(update={"prompt": "Changed after approval"}))


@pytest.mark.parametrize(
    "payload_ref",
    [
        "vault://media-intents/../secret",
        "vault://media-intents/not-a-uuid",
        "vault://other/00000000-0000-0000-0000-000000000000",
        "file:///tmp/intent",
    ],
)
def test_untrusted_or_traversing_reference_is_rejected(tmp_path, payload_ref):
    store = vault(tmp_path)

    with pytest.raises(MediaIntentVaultUnavailable):
        store.load(payload_ref)


def test_ciphertext_is_bound_to_reference_and_tampering_is_rejected(tmp_path):
    store = vault(tmp_path)
    intent = generation_intent()
    payload_ref = store.store(intent)
    original = tmp_path / "media-intents" / f"{intent.attempt_id}.intent"

    raw = bytearray(original.read_bytes())
    raw[-1] ^= 1
    original.write_bytes(raw)
    original.chmod(0o600)
    with pytest.raises(MediaIntentVaultUnavailable):
        store.load(payload_ref)

    other_id = uuid4()
    copied = tmp_path / "media-intents" / f"{other_id}.intent"
    copied.write_bytes(bytes(raw))
    copied.chmod(0o600)
    with pytest.raises(MediaIntentVaultUnavailable):
        store.load(f"vault://media-intents/{other_id}")


@pytest.mark.parametrize("mode", [0o644, 0o660])
def test_overpermissive_key_file_is_rejected(tmp_path, mode):
    key_file = tmp_path / "media-intent.key"
    write_key(key_file, mode=mode)

    with pytest.raises(MediaIntentVaultUnavailable):
        EncryptedMediaIntentVault(
            root=tmp_path / "media-intents",
            key_file=key_file,
        )


def test_symlinked_key_and_ciphertext_are_rejected(tmp_path):
    real_key = tmp_path / "real.key"
    write_key(real_key)
    linked_key = tmp_path / "linked.key"
    linked_key.symlink_to(real_key)
    with pytest.raises(MediaIntentVaultUnavailable):
        EncryptedMediaIntentVault(
            root=tmp_path / "media-intents",
            key_file=linked_key,
        )

    store = EncryptedMediaIntentVault(
        root=tmp_path / "media-intents",
        key_file=real_key,
    )
    intent = generation_intent()
    payload_ref = store.store(intent)
    ciphertext = tmp_path / "media-intents" / f"{intent.attempt_id}.intent"
    moved = tmp_path / "moved.intent"
    ciphertext.replace(moved)
    ciphertext.symlink_to(moved)

    with pytest.raises(MediaIntentVaultUnavailable):
        store.load(payload_ref)
