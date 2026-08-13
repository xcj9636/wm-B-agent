"""Resolve provider media inputs from approved server-owned asset identifiers."""
from __future__ import annotations

from datetime import datetime
from typing import Protocol
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.integrations.object_store import (
    MediaObjectStore,
    ObjectStoreIntegrityError,
)
from app.models.database import MediaAsset
from app.services.agent_runtime.contracts import Sensitivity
from app.services.media.contracts import (
    AssetConsentStatus,
    AssetRightsStatus,
    AssetScanStatus,
    GenerationIntent,
    GenerationMode,
    MediaAssetPolicySnapshot,
)


class MediaProviderInputDenied(RuntimeError):
    """A durable reference cannot safely leave the controlled asset boundary."""


class MediaProviderInputUnavailable(RuntimeError):
    """A safe provider credential could not be produced right now."""


class MediaAssetAuthorizer(Protocol):
    def asset_snapshot(
        self,
        asset_id,
        org_id,
        now: datetime,
        *,
        lock: bool = False,
    ) -> MediaAssetPolicySnapshot | None: ...


class MediaProviderInputResolver:
    """Turn one immutable I2V asset ID into a just-in-time signed URL."""

    _IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
    _SENSITIVITY_RANK = {
        Sensitivity.PUBLIC: 0,
        Sensitivity.INTERNAL: 1,
        Sensitivity.CONFIDENTIAL: 2,
        Sensitivity.RESTRICTED: 3,
    }

    def __init__(
        self,
        db: Session,
        *,
        object_store: MediaObjectStore,
        asset_authorizer: MediaAssetAuthorizer,
        expires_seconds: int,
    ) -> None:
        if not 300 <= expires_seconds <= 86_400:
            raise ValueError("provider input expiry is outside the safe range")
        self._db = db
        self._object_store = object_store
        self._asset_authorizer = asset_authorizer
        self._expires_seconds = expires_seconds

    def resolve(
        self,
        intent: GenerationIntent,
        *,
        now: datetime,
    ) -> dict[str, str]:
        if intent.mode == GenerationMode.TEXT_TO_VIDEO:
            if intent.reference_asset_ids:
                raise MediaProviderInputDenied(
                    "Text-to-video cannot include reference assets"
                )
            return {"prompt": intent.prompt}
        if intent.mode != GenerationMode.IMAGE_TO_VIDEO:
            raise MediaProviderInputDenied("Media input mode is not implemented")
        if len(intent.reference_asset_ids) != 1:
            raise MediaProviderInputDenied(
                "Image-to-video requires exactly one reference asset"
            )

        asset_id = intent.reference_asset_ids[0]
        snapshot = self._asset_authorizer.asset_snapshot(
            asset_id,
            intent.org_id,
            now,
            lock=True,
        )
        asset = (
            self._db.query(MediaAsset)
            .filter(MediaAsset.id == asset_id)
            .populate_existing()
            .one_or_none()
        )
        self._require_authorized_asset(asset, snapshot, intent)
        try:
            credential = self._object_store.create_provider_input(
                key=asset.storage_key,
                content_type=asset.mime_type,
                expected_sha256=asset.sha256,
                expected_size_bytes=asset.size_bytes,
                expires_seconds=self._expires_seconds,
            )
        except ObjectStoreIntegrityError as exc:
            raise MediaProviderInputDenied(
                "Provider input object no longer matches its approval"
            ) from exc
        except Exception as exc:
            raise MediaProviderInputUnavailable(
                "Provider input credential is temporarily unavailable"
            ) from exc
        self._require_safe_credential(credential.url)
        return {"prompt": intent.prompt, "image_url": credential.url}

    def _require_authorized_asset(
        self,
        asset: MediaAsset | None,
        snapshot: MediaAssetPolicySnapshot | None,
        intent: GenerationIntent,
    ) -> None:
        if asset is None or snapshot is None:
            raise MediaProviderInputDenied("Reference asset is unavailable")
        storage_segments = asset.storage_key.split("/") if asset else []
        expected_namespace = f"assets/{intent.org_id}/"
        allowed = (
            asset.id == snapshot.asset_id
            and asset.org_id == snapshot.org_id == intent.org_id
            and asset.deleted_at is None
            and not asset.quarantined
            and asset.kind == "image"
            and asset.storage_backend == self._object_store.backend_name
            and asset.storage_key.startswith(expected_namespace)
            and "\\" not in asset.storage_key
            and all(
                segment not in {"", ".", ".."}
                for segment in storage_segments
            )
            and asset.mime_type.strip().lower() in self._IMAGE_TYPES
            and snapshot.scan_status == AssetScanStatus.PASSED
            and snapshot.rights_status == AssetRightsStatus.VERIFIED
            and (
                not snapshot.consent_required
                or snapshot.consent_status == AssetConsentStatus.VALID
            )
            and self._SENSITIVITY_RANK[snapshot.sensitivity]
            <= self._SENSITIVITY_RANK[intent.sensitivity]
        )
        if not allowed:
            raise MediaProviderInputDenied(
                "Reference asset is not currently approved for submission"
            )

    @staticmethod
    def _require_safe_credential(url: str) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise MediaProviderInputDenied(
                "Provider input credential is not a safe HTTPS URL"
            )
