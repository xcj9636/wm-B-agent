"""Secret-safe adapter construction for each job's immutable runtime revision."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.integrations.fal_media import FalMediaAdapter
from app.models.database import MediaGenerationJob, MediaRuntimeRevision
from app.services.idempotency import canonical_hash
from app.services.media.runtime import MediaCapabilityCatalog


class MediaRuntimeUnavailable(RuntimeError):
    """Pinned provider runtime cannot be safely reconstructed."""


AdapterBuilder = Callable[..., FalMediaAdapter]


class PinnedMediaRuntimeFactory:
    """Build from the job revision only; active runtime is intentionally ignored."""

    def __init__(
        self,
        db: Session,
        config: Settings = settings,
        *,
        adapter_builder: Optional[AdapterBuilder] = None,
    ) -> None:
        self._db = db
        self._settings = config
        self._adapter_builder = adapter_builder or FalMediaAdapter

    def build(self, job: MediaGenerationJob) -> FalMediaAdapter:
        revision = (
            self._db.query(MediaRuntimeRevision)
            .filter(
                MediaRuntimeRevision.id == job.runtime_revision_id,
                MediaRuntimeRevision.org_id == job.org_id,
            )
            .one_or_none()
        )
        if revision is None or revision.provider != "fal" or job.provider != "fal":
            raise MediaRuntimeUnavailable("Pinned media runtime is unavailable")
        snapshot = dict(revision.capability_snapshot or {})
        if canonical_hash(snapshot) != revision.capability_snapshot_hash:
            raise MediaRuntimeUnavailable("Pinned media runtime is unavailable")
        try:
            catalog = MediaCapabilityCatalog.model_validate(snapshot)
        except Exception as exc:
            raise MediaRuntimeUnavailable(
                "Pinned media runtime is unavailable"
            ) from exc
        models = {model.id: model for model in catalog.models}
        model = models.get(job.model_id)
        if model is None or job.mode not in {mode.value for mode in model.modes}:
            raise MediaRuntimeUnavailable("Pinned media runtime is unavailable")
        aliases = dict(revision.model_aliases or {})
        if aliases.get(job.mode) != job.model_id:
            raise MediaRuntimeUnavailable("Pinned media runtime is unavailable")
        api_key = self._read_secret(revision.id)
        return self._adapter_builder(api_key, catalog=catalog)

    def _read_secret(self, revision_id) -> str:
        path = Path(self._settings.MEDIA_RUNTIME_SECRET_DIR) / f"{revision_id}.key"
        try:
            info = path.stat()
        except OSError as exc:
            raise MediaRuntimeUnavailable(
                "Pinned media runtime secret is unavailable"
            ) from exc
        if not path.is_file() or info.st_mode & 0o077:
            raise MediaRuntimeUnavailable(
                "Pinned media runtime secret is unavailable"
            )
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise MediaRuntimeUnavailable(
                "Pinned media runtime secret is unavailable"
            ) from exc
        if not value:
            raise MediaRuntimeUnavailable(
                "Pinned media runtime secret is unavailable"
            )
        return value


class ReservedEstimateCostResolver:
    """Conservative fallback until a provider billing receipt is verifiable."""

    basis = "reserved_estimate_ceiling"

    def actual_cost_microusd(self, job: MediaGenerationJob) -> int:
        value = job.reserved_cost_microusd
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("reserved media estimate is invalid")
        return value
