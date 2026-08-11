"""Sandboxed thumbnail derivation with durable media lineage."""

from datetime import datetime
from hashlib import sha256
import os
from pathlib import Path
import tempfile
from typing import Callable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.integrations.object_store import MediaObjectStore
from app.models.database import MediaAsset, MediaAssetRelation
from app.services.agent_runtime.contracts import ExecutionPrincipal
from app.services.media.assets import (
    MediaAssetConflict,
    MediaAssetForbidden,
    MediaAssetNotFound,
)
from app.services.media.inspection import CommandExecution, run_bounded_command


class ThumbnailGenerationError(RuntimeError):
    pass


class GeneratedThumbnail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=1)
    mime_type: str = "image/jpeg"


ThumbnailExecutor = Callable[..., CommandExecution]


class MediaThumbnailRunner:
    """Runs one fixed FFmpeg transform without a shell or network protocols."""

    def __init__(
        self,
        *,
        ffmpeg_path: str,
        timeout_seconds: int,
        max_output_bytes: int,
        max_thumbnail_bytes: int,
        executor: ThumbnailExecutor = None,
    ) -> None:
        executable = Path(ffmpeg_path)
        if not executable.is_absolute():
            raise ValueError("FFmpeg path must be absolute")
        if timeout_seconds < 1 or max_output_bytes < 1024:
            raise ValueError("Thumbnail process limits are invalid")
        if max_thumbnail_bytes < 1:
            raise ValueError("Thumbnail size limit must be positive")
        self._ffmpeg_path = str(executable)
        self._timeout_seconds = timeout_seconds
        self._max_output_bytes = max_output_bytes
        self._max_thumbnail_bytes = max_thumbnail_bytes
        self._executor = executor or run_bounded_command

    def generate(self, source_path: Path, output_path: Path) -> GeneratedThumbnail:
        if not source_path.is_absolute() or not output_path.is_absolute():
            raise ValueError("Thumbnail paths must be absolute")
        if source_path == output_path:
            raise ValueError("Thumbnail output must differ from its source")
        execution = self._executor(
            [
                self._ffmpeg_path,
                "-nostdin",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,crypto",
                "-i",
                str(source_path),
                "-frames:v",
                "1",
                "-vf",
                (
                    "scale=w=640:h=640:force_original_aspect_ratio=decrease,"
                    "setsar=1"
                ),
                "-map_metadata",
                "-1",
                "-an",
                "-sn",
                "-dn",
                "-c:v",
                "mjpeg",
                "-q:v",
                "3",
                "-f",
                "image2",
                "-y",
                str(output_path),
            ],
            timeout_seconds=self._timeout_seconds,
            max_output_bytes=self._max_output_bytes,
        )
        if (
            execution.returncode != 0
            or execution.timed_out
            or execution.output_truncated
        ):
            raise ThumbnailGenerationError("Thumbnail process failed")
        try:
            size_bytes = output_path.stat().st_size
        except OSError as exc:
            raise ThumbnailGenerationError("Thumbnail output is unavailable") from exc
        if size_bytes < 1 or size_bytes > self._max_thumbnail_bytes:
            raise ThumbnailGenerationError("Thumbnail output size is invalid")
        return GeneratedThumbnail(
            sha256=_file_sha256(output_path),
            size_bytes=size_bytes,
        )


class MediaThumbnailService:
    """Stages an approved source and records one inherited thumbnail asset."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def generate(
        self,
        asset_id: UUID,
        principal: ExecutionPrincipal,
        *,
        object_store: MediaObjectStore,
        runner: MediaThumbnailRunner,
    ) -> MediaAsset:
        if not principal.roles.intersection({"media_worker", "admin"}):
            raise MediaAssetForbidden("Thumbnail generation requires worker role")
        source = self._asset(asset_id)
        if source.org_id != principal.org_id:
            raise MediaAssetForbidden("Asset is outside the current organization")
        self._validate_source(source)
        existing = self._existing_thumbnail(source.id)
        if existing is not None:
            return existing

        with object_store.stage_asset(
            source.storage_key,
            expected_sha256=source.sha256,
            expected_size_bytes=source.size_bytes,
            expected_content_type=source.mime_type,
        ) as source_path:
            with tempfile.TemporaryDirectory(
                prefix="b-agent-media-thumbnail-"
            ) as directory:
                os.chmod(directory, 0o700)
                output_path = Path(directory) / "thumbnail.jpg"
                generated = runner.generate(source_path, output_path)
                logical_key = (
                    f"assets/{source.org_id}/derived/{source.id}/thumbnail.jpg"
                )
                stored = object_store.put_derived(
                    key=logical_key,
                    path=output_path,
                    content_type=generated.mime_type,
                    sha256=generated.sha256,
                )

        if (
            stored.sha256 != generated.sha256
            or stored.size_bytes != generated.size_bytes
            or stored.content_type != generated.mime_type
        ):
            raise MediaAssetConflict("Stored thumbnail failed integrity validation")

        thumbnail = MediaAsset(
            org_id=source.org_id,
            owner_user_id=source.owner_user_id,
            kind="image",
            source="thumbnail_derivation",
            storage_backend=getattr(object_store, "backend_name", "unknown"),
            storage_key=stored.key,
            sha256=stored.sha256,
            mime_type=stored.content_type,
            size_bytes=stored.size_bytes,
            sensitivity=source.sensitivity,
            quarantined=False,
            scan_status=source.scan_status,
            rights_status=source.rights_status,
            consent_required=source.consent_required,
            consent_status=source.consent_status,
            metadata_json={
                "derived_from_asset_id": str(source.id),
                "derivation": "thumbnail-v1",
            },
            reviewed_by_user_id=principal.user_id,
            reviewed_at=datetime.utcnow(),
        )
        self._db.add(thumbnail)
        self._db.flush()
        self._db.add(
            MediaAssetRelation(
                org_id=source.org_id,
                parent_asset_id=source.id,
                child_asset_id=thumbnail.id,
                relation_type="thumbnail_of",
            )
        )
        self._db.commit()
        self._db.refresh(thumbnail)
        return thumbnail

    def _asset(self, asset_id: UUID) -> MediaAsset:
        asset = self._db.get(MediaAsset, asset_id)
        if asset is None or asset.deleted_at is not None:
            raise MediaAssetNotFound("Media asset was not found")
        return asset

    def _existing_thumbnail(self, asset_id: UUID) -> MediaAsset | None:
        return (
            self._db.query(MediaAsset)
            .join(
                MediaAssetRelation,
                MediaAssetRelation.child_asset_id == MediaAsset.id,
            )
            .filter(
                MediaAssetRelation.parent_asset_id == asset_id,
                MediaAssetRelation.relation_type == "thumbnail_of",
                MediaAsset.deleted_at.is_(None),
            )
            .one_or_none()
        )

    @staticmethod
    def _validate_source(source: MediaAsset) -> None:
        if source.kind not in {"image", "video"}:
            raise MediaAssetConflict("Only image or video assets have thumbnails")
        if source.quarantined or not source.storage_key.startswith("assets/"):
            raise MediaAssetConflict("Only promoted assets have thumbnails")
        consent_valid = (
            not source.consent_required or source.consent_status == "valid"
        )
        if (
            source.scan_status != "passed"
            or source.rights_status != "verified"
            or not consent_valid
        ):
            raise MediaAssetConflict("Source approval evidence is incomplete")


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
