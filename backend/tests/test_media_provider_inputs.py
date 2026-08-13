from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.database import MediaAsset
from app.integrations.object_store import ObjectStoreIntegrityError
from app.services.agent_runtime.contracts import Sensitivity
from app.services.media.contracts import (
    AssetConsentStatus,
    AssetRightsStatus,
    AssetScanStatus,
    GenerationIntent,
    GenerationMode,
    MediaAssetPolicySnapshot,
)
from app.services.media.provider_inputs import (
    MediaProviderInputDenied,
    MediaProviderInputResolver,
    MediaProviderInputUnavailable,
)


NOW = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)


class FakeObjectStore:
    backend_name = "s3"

    def __init__(self):
        self.calls = []

    def create_provider_input(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            url="https://objects.example.test/signed-provider-input",
            expires_seconds=kwargs["expires_seconds"],
        )


class FakeAssetAuthorizer:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = []

    def asset_snapshot(self, asset_id, org_id, now, *, lock=False):
        self.calls.append((asset_id, org_id, now, lock))
        return self.snapshot


def image_intent(asset_id, *, org_id=None, mode=GenerationMode.IMAGE_TO_VIDEO):
    return GenerationIntent(
        project_id=uuid4(),
        shot_id=uuid4(),
        persona_version_id=uuid4(),
        org_id=org_id or uuid4(),
        actor_user_id=7,
        mode=mode,
        prompt="Slow product orbit, preserve all printed label details",
        reference_asset_ids=[asset_id],
        sensitivity=Sensitivity.INTERNAL,
        persona_approved=True,
        storyboard_approved=True,
    )


def approved_snapshot(asset_id, org_id):
    return MediaAssetPolicySnapshot(
        asset_id=asset_id,
        org_id=org_id,
        scan_status=AssetScanStatus.PASSED,
        rights_status=AssetRightsStatus.VERIFIED,
        consent_required=False,
        consent_status=AssetConsentStatus.NOT_REQUIRED,
        sensitivity=Sensitivity.INTERNAL,
    )


def promoted_asset(db_session, *, org_id, asset_id):
    asset = MediaAsset(
        id=asset_id,
        org_id=org_id,
        owner_user_id=7,
        kind="image",
        source="upload",
        storage_backend="s3",
        storage_key=f"assets/{org_id}/{asset_id}",
        sha256="a" * 64,
        mime_type="image/png",
        size_bytes=4096,
        sensitivity="internal",
        quarantined=False,
        scan_status="passed",
        rights_status="verified",
        consent_required=False,
        consent_status="not_required",
    )
    db_session.add(asset)
    db_session.commit()
    return asset


def resolver(db_session, snapshot, store=None):
    store = store or FakeObjectStore()
    return (
        MediaProviderInputResolver(
            db_session,
            object_store=store,
            asset_authorizer=FakeAssetAuthorizer(snapshot),
            expires_seconds=3600,
        ),
        store,
    )


def test_image_to_video_resolves_one_live_promoted_asset_server_side(db_session):
    asset_id = uuid4()
    org_id = uuid4()
    asset = promoted_asset(db_session, org_id=org_id, asset_id=asset_id)
    service, store = resolver(
        db_session,
        approved_snapshot(asset_id, org_id),
    )

    arguments = service.resolve(image_intent(asset_id, org_id=org_id), now=NOW)

    assert arguments == {
        "prompt": "Slow product orbit, preserve all printed label details",
        "image_url": "https://objects.example.test/signed-provider-input",
    }
    assert store.calls == [
        {
            "key": asset.storage_key,
            "content_type": "image/png",
            "expected_sha256": "a" * 64,
            "expected_size_bytes": 4096,
            "expires_seconds": 3600,
        }
    ]
    assert service._asset_authorizer.calls == [(asset_id, org_id, NOW, True)]


def test_object_store_signing_failure_is_retryable_not_policy_denial(db_session):
    class UnavailableObjectStore(FakeObjectStore):
        def create_provider_input(self, **kwargs):
            raise RuntimeError("storage endpoint unavailable")

    asset_id = uuid4()
    org_id = uuid4()
    promoted_asset(db_session, org_id=org_id, asset_id=asset_id)
    service, _ = resolver(
        db_session,
        approved_snapshot(asset_id, org_id),
        store=UnavailableObjectStore(),
    )

    with pytest.raises(MediaProviderInputUnavailable):
        service.resolve(image_intent(asset_id, org_id=org_id), now=NOW)


def test_object_store_integrity_failure_is_terminal_input_denial(db_session):
    class ReplacedObjectStore(FakeObjectStore):
        def create_provider_input(self, **kwargs):
            raise ObjectStoreIntegrityError("stored object was replaced")

    asset_id = uuid4()
    org_id = uuid4()
    promoted_asset(db_session, org_id=org_id, asset_id=asset_id)
    service, _ = resolver(
        db_session,
        approved_snapshot(asset_id, org_id),
        store=ReplacedObjectStore(),
    )

    with pytest.raises(MediaProviderInputDenied):
        service.resolve(image_intent(asset_id, org_id=org_id), now=NOW)


@pytest.mark.parametrize(
    "mutation",
    ["missing", "scan", "rights", "consent", "sensitivity"],
)
def test_image_to_video_fails_closed_when_live_authorization_changes(
    db_session,
    mutation,
):
    asset_id = uuid4()
    org_id = uuid4()
    promoted_asset(db_session, org_id=org_id, asset_id=asset_id)
    snapshot = approved_snapshot(asset_id, org_id)
    if mutation == "missing":
        snapshot = None
    elif mutation == "scan":
        snapshot = snapshot.model_copy(
            update={"scan_status": AssetScanStatus.PENDING}
        )
    elif mutation == "rights":
        snapshot = snapshot.model_copy(
            update={"rights_status": AssetRightsStatus.REVOKED}
        )
    elif mutation == "consent":
        snapshot = snapshot.model_copy(
            update={
                "consent_required": True,
                "consent_status": AssetConsentStatus.REVOKED,
            }
        )
    else:
        snapshot = snapshot.model_copy(
            update={"sensitivity": Sensitivity.RESTRICTED}
        )
    service, store = resolver(db_session, snapshot)

    with pytest.raises(MediaProviderInputDenied):
        service.resolve(image_intent(asset_id, org_id=org_id), now=NOW)

    assert store.calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("org_id", uuid4()),
        ("kind", "video"),
        ("storage_backend", "local"),
        ("storage_key", "quarantine/unsafe"),
        ("storage_key", "assets/../unsafe"),
        ("storage_key", "assets/unsafe\\object"),
        ("storage_key", f"assets/{uuid4()}/other-org-object"),
        ("mime_type", "image/svg+xml"),
        ("quarantined", True),
        ("deleted_at", NOW.replace(tzinfo=None)),
    ],
)
def test_image_to_video_rejects_unsafe_durable_object_state(
    db_session,
    field,
    value,
):
    asset_id = uuid4()
    org_id = uuid4()
    asset = promoted_asset(db_session, org_id=org_id, asset_id=asset_id)
    setattr(asset, field, value)
    db_session.commit()
    service, store = resolver(
        db_session,
        approved_snapshot(asset_id, org_id),
    )

    with pytest.raises(MediaProviderInputDenied):
        service.resolve(image_intent(asset_id, org_id=org_id), now=NOW)

    assert store.calls == []


def test_text_to_video_never_resolves_an_asset(db_session):
    intent = image_intent(uuid4(), mode=GenerationMode.TEXT_TO_VIDEO).model_copy(
        update={"reference_asset_ids": []}
    )
    service, store = resolver(db_session, None)

    assert service.resolve(intent, now=NOW) == {"prompt": intent.prompt}
    assert store.calls == []


@pytest.mark.parametrize(
    "intent",
    [
        image_intent(uuid4()).model_copy(update={"reference_asset_ids": []}),
        image_intent(uuid4()).model_copy(
            update={"reference_asset_ids": [uuid4(), uuid4()]}
        ),
        image_intent(uuid4(), mode=GenerationMode.REFERENCE_TO_VIDEO),
        image_intent(uuid4(), mode=GenerationMode.TEXT_TO_IMAGE),
    ],
)
def test_unimplemented_or_ambiguous_media_inputs_fail_closed(db_session, intent):
    service, store = resolver(db_session, None)

    with pytest.raises(MediaProviderInputDenied):
        service.resolve(intent, now=NOW)

    assert store.calls == []
