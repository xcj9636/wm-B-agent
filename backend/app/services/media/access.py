"""Authorization and credential issuance for promoted media assets."""

from typing import Final
from uuid import UUID

from sqlalchemy.orm import Session

from app.integrations.object_store import MediaObjectStore, PresignedDownload
from app.models.database import MediaAsset
from app.services.agent_runtime.contracts import ExecutionPrincipal, Sensitivity
from app.services.media.assets import (
    MediaAssetConflict,
    MediaAssetForbidden,
    MediaAssetNotFound,
)


_MIME_EXTENSIONS: Final[dict[str, str]] = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "application/pdf": ".pdf",
}
_CONFIDENTIAL_ROLES: Final[frozenset[str]] = frozenset(
    {"admin", "compliance_reviewer", "media_security"}
)
_RESTRICTED_ROLES: Final[frozenset[str]] = frozenset(
    {"admin", "media_security"}
)


class MediaAssetAccessService:
    """Fail-closed access control for one-time media read credentials."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def create_download(
        self,
        asset_id: UUID,
        principal: ExecutionPrincipal,
        object_store: MediaObjectStore,
        *,
        expires_seconds: int = 120,
    ) -> PresignedDownload:
        asset = self._db.get(MediaAsset, asset_id)
        if asset is None or asset.deleted_at is not None:
            raise MediaAssetNotFound("Media asset was not found")
        if asset.org_id != principal.org_id:
            raise MediaAssetForbidden("Asset is outside the current organization")
        if asset.quarantined:
            raise MediaAssetConflict("Quarantined assets cannot be downloaded")
        self._require_approved_evidence(asset)
        self._authorize_sensitivity(asset, principal)

        extension = _MIME_EXTENSIONS.get(asset.mime_type.strip().lower(), ".bin")
        return object_store.create_download(
            key=asset.storage_key,
            content_type=asset.mime_type,
            download_name=f"{asset.id}{extension}",
            expires_seconds=expires_seconds,
        )

    @staticmethod
    def _require_approved_evidence(asset: MediaAsset) -> None:
        consent_valid = (
            not asset.consent_required or asset.consent_status == "valid"
        )
        if (
            asset.scan_status != "passed"
            or asset.rights_status != "verified"
            or not consent_valid
        ):
            raise MediaAssetConflict(
                "Asset safety, rights, or consent evidence is incomplete"
            )

    @staticmethod
    def _authorize_sensitivity(
        asset: MediaAsset,
        principal: ExecutionPrincipal,
    ) -> None:
        sensitivity = Sensitivity(asset.sensitivity)
        roles = {role.strip().lower() for role in principal.roles}
        if sensitivity in {Sensitivity.PUBLIC, Sensitivity.INTERNAL}:
            return
        if sensitivity == Sensitivity.CONFIDENTIAL:
            if (
                asset.owner_user_id == principal.user_id
                or roles & _CONFIDENTIAL_ROLES
            ):
                return
        elif sensitivity == Sensitivity.RESTRICTED and roles & _RESTRICTED_ROLES:
            return
        raise MediaAssetForbidden(
            "Actor is not permitted to download this sensitivity level"
        )
