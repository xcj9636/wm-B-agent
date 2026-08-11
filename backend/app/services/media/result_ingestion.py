"""SSRF-safe, bounded provider result download into private quarantine storage."""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import tempfile
from typing import AsyncContextManager, AsyncIterator, List, Optional, Protocol

import httpx
from sqlalchemy.orm import Session

from app.integrations.fal_media import MediaOutput
from app.integrations.object_store import MediaObjectStore
from app.integrations.provider_media import (
    ApprovedProviderMediaURL,
    ProviderMediaURLDenied,
    SafeProviderMediaURLPolicy,
)
from app.models.database import MediaAsset, MediaGenerationJob
from app.services.media.reconcile_runtime import MediaQuarantineReceipt


class MediaResultIngestionDenied(RuntimeError):
    """Provider output could not safely cross the quarantine boundary."""


@dataclass(frozen=True)
class MediaRemoteStream:
    status_code: int
    content_type: str
    content_length: Optional[int]
    peer_ip: str
    chunks: AsyncIterator[bytes]


class MediaRemoteFetcher(Protocol):
    def stream(
        self,
        approved: ApprovedProviderMediaURL,
    ) -> AsyncContextManager[MediaRemoteStream]: ...


class HttpxMediaRemoteFetcher:
    """HTTPS-only fetcher that exposes the actual socket peer for DNS pinning."""

    def __init__(
        self,
        *,
        http_client: Optional[httpx.AsyncClient] = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        if timeout_seconds <= 0 or timeout_seconds > 300:
            raise ValueError("media download timeout must be between 0 and 300")
        self._client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
        )
        self._owns_client = http_client is None

    @asynccontextmanager
    async def stream(
        self,
        approved: ApprovedProviderMediaURL,
    ) -> AsyncIterator[MediaRemoteStream]:
        async with self._client.stream(
            "GET",
            approved.url,
            headers={"Accept": "image/*, video/*"},
        ) as response:
            if response.history:
                raise MediaResultIngestionDenied(
                    "Provider media redirects are not accepted"
                )
            peer_ip = self._peer_ip(response)
            content_length = self._content_length(response)
            content_type = response.headers.get("content-type", "").split(";", 1)[0]
            yield MediaRemoteStream(
                status_code=response.status_code,
                content_type=content_type.strip().lower(),
                content_length=content_length,
                peer_ip=peer_ip,
                chunks=response.aiter_bytes(chunk_size=64 * 1024),
            )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def _peer_ip(response: httpx.Response) -> str:
        network_stream = response.extensions.get("network_stream")
        if network_stream is None or not hasattr(network_stream, "get_extra_info"):
            raise MediaResultIngestionDenied(
                "Provider media connection peer is unavailable"
            )
        address = network_stream.get_extra_info("server_addr")
        if not isinstance(address, tuple) or not address:
            raise MediaResultIngestionDenied(
                "Provider media connection peer is unavailable"
            )
        return str(address[0])

    @staticmethod
    def _content_length(response: httpx.Response) -> Optional[int]:
        raw = response.headers.get("content-length")
        if raw is None:
            return None
        try:
            value = int(raw)
        except ValueError as exc:
            raise MediaResultIngestionDenied(
                "Provider media size is invalid"
            ) from exc
        if value < 0:
            raise MediaResultIngestionDenied("Provider media size is invalid")
        return value


class ProviderResultIngestor:
    """Download one V1 provider output without persisting its remote URL."""

    ALLOWED_CONTENT_TYPES = {
        "image/jpeg": "image",
        "image/png": "image",
        "image/webp": "image",
        "video/mp4": "video",
        "video/webm": "video",
    }

    def __init__(
        self,
        db: Session,
        *,
        fetcher: MediaRemoteFetcher,
        object_store: MediaObjectStore,
        url_policy: SafeProviderMediaURLPolicy,
        max_bytes: int,
    ) -> None:
        if not 1024 <= max_bytes <= 2 * 1024 * 1024 * 1024:
            raise ValueError("media result size limit is outside safe bounds")
        self._db = db
        self._fetcher = fetcher
        self._object_store = object_store
        self._url_policy = url_policy
        self._max_bytes = max_bytes

    async def ingest(
        self,
        *,
        job: MediaGenerationJob,
        outputs: List[MediaOutput],
    ) -> MediaQuarantineReceipt:
        if len(outputs) != 1:
            raise MediaResultIngestionDenied(
                "V1 media ingestion requires exactly one provider output"
            )
        key = f"quarantine/generated/{job.org_id}/{job.id}/output-0"
        existing = (
            self._db.query(MediaAsset)
            .filter(MediaAsset.storage_key == key)
            .one_or_none()
        )
        if existing is not None:
            if existing.org_id != job.org_id or existing.owner_user_id != job.owner_user_id:
                raise MediaResultIngestionDenied(
                    "Generated media asset ownership does not match the job"
                )
            return self._receipt(existing)

        output = outputs[0]
        try:
            approved = self._url_policy.validate(output.url)
        except ProviderMediaURLDenied as exc:
            raise MediaResultIngestionDenied(
                "Provider media URL is not approved"
            ) from exc

        path: Optional[Path] = None
        try:
            path, digest, size_bytes, content_type = await self._download(
                approved,
                output,
            )
            metadata = self._object_store.put_quarantined_generated(
                key=key,
                path=path,
                content_type=content_type,
                sha256=digest,
                size_bytes=size_bytes,
            )
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

        normalized_type = metadata.content_type.strip().lower()
        asset = MediaAsset(
            org_id=job.org_id,
            owner_user_id=job.owner_user_id,
            kind=self.ALLOWED_CONTENT_TYPES[normalized_type],
            source="ai_generated",
            storage_backend=getattr(self._object_store, "backend_name", "unknown"),
            storage_key=metadata.key,
            sha256=metadata.sha256,
            mime_type=normalized_type,
            size_bytes=metadata.size_bytes,
            sensitivity=job.sensitivity,
            quarantined=True,
            scan_status="pending",
            rights_status="unknown",
            consent_required=True,
            consent_status="unknown",
            metadata_json={
                "generation_job_id": str(job.id),
                "provider": job.provider,
                "provider_request_id": job.provider_request_id,
            },
        )
        self._db.add(asset)
        self._db.commit()
        self._db.refresh(asset)
        return self._receipt(asset)

    async def _download(
        self,
        approved: ApprovedProviderMediaURL,
        output: MediaOutput,
    ) -> tuple[Path, str, int, str]:
        descriptor, raw_path = tempfile.mkstemp(prefix="b-agent-provider-", suffix=".media")
        path = Path(raw_path)
        os.chmod(path, 0o600)
        digest = sha256()
        size_bytes = 0
        try:
            with os.fdopen(descriptor, "wb") as handle:
                async with self._fetcher.stream(approved) as response:
                    self._validate_response(approved, output, response)
                    async for chunk in response.chunks:
                        if not isinstance(chunk, bytes) or not chunk:
                            continue
                        size_bytes += len(chunk)
                        if size_bytes > self._max_bytes:
                            raise MediaResultIngestionDenied(
                                "Provider media size exceeds the configured limit"
                            )
                        handle.write(chunk)
                        digest.update(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
                    if (
                        response.content_length is not None
                        and size_bytes != response.content_length
                    ):
                        raise MediaResultIngestionDenied(
                            "Provider media size does not match its response"
                        )
                    if size_bytes == 0:
                        raise MediaResultIngestionDenied(
                            "Provider media response is empty"
                        )
                    content_type = response.content_type
            return path, digest.hexdigest(), size_bytes, content_type
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def _validate_response(
        self,
        approved: ApprovedProviderMediaURL,
        output: MediaOutput,
        response: MediaRemoteStream,
    ) -> None:
        if response.status_code != 200:
            raise MediaResultIngestionDenied(
                "Provider media response status is not accepted"
            )
        try:
            self._url_policy.assert_connected_peer(approved, response.peer_ip)
        except ProviderMediaURLDenied as exc:
            raise MediaResultIngestionDenied(
                "Provider media connection peer is not approved"
            ) from exc
        if response.content_type not in self.ALLOWED_CONTENT_TYPES:
            raise MediaResultIngestionDenied(
                "Provider media content type is not accepted"
            )
        if (
            output.content_type is not None
            and output.content_type.strip().lower() != response.content_type
        ):
            raise MediaResultIngestionDenied(
                "Provider media content type changed during download"
            )
        if (
            response.content_length is not None
            and response.content_length > self._max_bytes
        ):
            raise MediaResultIngestionDenied(
                "Provider media size exceeds the configured limit"
            )

    @staticmethod
    def _receipt(asset: MediaAsset) -> MediaQuarantineReceipt:
        return MediaQuarantineReceipt(
            result_ref=f"quarantine://media-assets/{asset.id}",
            content_hash=asset.sha256,
        )
