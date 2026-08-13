"""Object-store boundary used by quarantined media asset ingestion."""

from contextlib import contextmanager
from hashlib import sha256
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, Iterator, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field


class StoredObjectMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1, max_length=1000)
    size_bytes: int = Field(ge=1)
    content_type: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class MediaObjectStore(Protocol):
    backend_name: str

    def create_upload(
        self,
        *,
        key: str,
        content_type: str,
        size_bytes: int,
        sha256: str,
        expires_seconds: int = 900,
    ) -> "PresignedUpload": ...

    def head(self, key: str) -> StoredObjectMetadata: ...

    def promote(
        self,
        key: str,
        *,
        expected_sha256: str,
        expected_size_bytes: int,
        expected_content_type: str,
    ) -> StoredObjectMetadata: ...

    def stage_quarantined(
        self,
        key: str,
        *,
        expected_sha256: str,
        expected_size_bytes: int,
        expected_content_type: str,
    ): ...

    def stage_asset(
        self,
        key: str,
        *,
        expected_sha256: str,
        expected_size_bytes: int,
        expected_content_type: str,
    ): ...

    def put_derived(
        self,
        *,
        key: str,
        path: Path,
        content_type: str,
        sha256: str,
    ) -> StoredObjectMetadata: ...

    def put_quarantined_generated(
        self,
        *,
        key: str,
        path: Path,
        content_type: str,
        sha256: str,
        size_bytes: int,
    ) -> StoredObjectMetadata: ...

    def delete_asset(self, key: str) -> None: ...

    def create_download(
        self,
        *,
        key: str,
        content_type: str,
        download_name: str,
        expires_seconds: int = 120,
    ) -> "PresignedDownload": ...

    def create_provider_input(
        self,
        *,
        key: str,
        content_type: str,
        expected_sha256: str,
        expected_size_bytes: int,
        expires_seconds: int = 3600,
    ) -> "PresignedProviderInput": ...


class ObjectStoreConfigurationError(RuntimeError):
    pass


class ObjectStoreIntegrityError(RuntimeError):
    pass


class PresignedUpload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str = Field(min_length=1, max_length=4000)
    fields: Dict[str, str]
    key: str = Field(min_length=1, max_length=1000)
    expires_seconds: int = Field(ge=60, le=3600)


class PresignedDownload(BaseModel):
    """Opaque, short-lived read credential; storage coordinates stay private."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str = Field(min_length=1, max_length=4000)
    expires_seconds: int = Field(ge=30, le=300)


class PresignedProviderInput(BaseModel):
    """Opaque read credential created only inside a submission worker."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str = Field(min_length=1, max_length=4000)
    expires_seconds: int = Field(ge=300, le=86_400)


class S3CompatibleMediaObjectStore:
    """Server-owned quarantine, promotion, and controlled-read adapter."""

    backend_name = "s3"

    def __init__(
        self,
        *,
        quarantine_bucket: str,
        asset_bucket: str,
        key_prefix: str = "",
        kms_key_id: str = "",
        endpoint_url: str = "",
        region_name: str = "us-east-1",
        client: Optional[Any] = None,
    ) -> None:
        self._quarantine_bucket = quarantine_bucket.strip()
        self._asset_bucket = asset_bucket.strip()
        if not self._quarantine_bucket or not self._asset_bucket:
            raise ObjectStoreConfigurationError("Media buckets are required")
        if self._quarantine_bucket == self._asset_bucket:
            raise ObjectStoreConfigurationError(
                "Quarantine and asset buckets must be separate"
            )
        self._key_prefix = key_prefix.strip().strip("/")
        self._kms_key_id = kms_key_id.strip()
        if client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - deployment guard
                raise ObjectStoreConfigurationError(
                    "boto3 is required for the S3 media object store"
                ) from exc
            client = boto3.client(
                "s3",
                endpoint_url=endpoint_url or None,
                region_name=region_name,
            )
        self._client = client

    def create_upload(
        self,
        *,
        key: str,
        content_type: str,
        size_bytes: int,
        sha256: str,
        expires_seconds: int = 900,
    ) -> PresignedUpload:
        if not 60 <= expires_seconds <= 3600:
            raise ObjectStoreConfigurationError(
                "Upload expiry must be between 60 and 3600 seconds"
            )
        if size_bytes < 1:
            raise ObjectStoreConfigurationError("Upload size must be positive")
        physical_key = self._physical_quarantine_key(key)
        content_type = content_type.strip().lower()
        if not content_type:
            raise ObjectStoreConfigurationError("Content type is required")
        if len(sha256) != 64 or any(
            character not in "0123456789abcdef" for character in sha256
        ):
            raise ObjectStoreConfigurationError("SHA-256 must be lowercase hex")

        fields = {
            "Content-Type": content_type,
            "x-amz-meta-sha256": sha256,
        }
        conditions = [
            {"Content-Type": content_type},
            {"x-amz-meta-sha256": sha256},
            {"content-length-range": [size_bytes, size_bytes]},
        ]
        if self._kms_key_id:
            fields["x-amz-server-side-encryption"] = "aws:kms"
            fields[
                "x-amz-server-side-encryption-aws-kms-key-id"
            ] = self._kms_key_id
            conditions.extend(
                [
                    {"x-amz-server-side-encryption": "aws:kms"},
                    {
                        "x-amz-server-side-encryption-aws-kms-key-id": (
                            self._kms_key_id
                        )
                    },
                ]
            )
        else:
            fields["x-amz-server-side-encryption"] = "AES256"
            conditions.append(
                {"x-amz-server-side-encryption": "AES256"}
            )

        response = self._client.generate_presigned_post(
            Bucket=self._quarantine_bucket,
            Key=physical_key,
            Fields=fields,
            Conditions=conditions,
            ExpiresIn=expires_seconds,
        )
        return PresignedUpload(
            url=response["url"],
            fields=response["fields"],
            key=key,
            expires_seconds=expires_seconds,
        )

    def create_download(
        self,
        *,
        key: str,
        content_type: str,
        download_name: str,
        expires_seconds: int = 120,
    ) -> PresignedDownload:
        """Create a least-privilege credential for one promoted object."""
        if not 30 <= expires_seconds <= 300:
            raise ObjectStoreConfigurationError(
                "Download expiry must be between 30 and 300 seconds"
            )
        physical_key = self._physical_asset_key(key)
        normalized_type = content_type.strip().lower()
        if (
            not normalized_type
            or "\r" in normalized_type
            or "\n" in normalized_type
        ):
            raise ObjectStoreConfigurationError("Content type is invalid")
        if not self._is_safe_download_name(download_name):
            raise ObjectStoreConfigurationError("Download name is invalid")
        url = self._client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self._asset_bucket,
                "Key": physical_key,
                "ResponseContentType": normalized_type,
                "ResponseContentDisposition": (
                    f'attachment; filename="{download_name}"'
                ),
            },
            ExpiresIn=expires_seconds,
        )
        return PresignedDownload(url=url, expires_seconds=expires_seconds)

    def create_provider_input(
        self,
        *,
        key: str,
        content_type: str,
        expected_sha256: str,
        expected_size_bytes: int,
        expires_seconds: int = 3600,
    ) -> PresignedProviderInput:
        """Sign one promoted object for a provider without browser headers."""
        if not 300 <= expires_seconds <= 86_400:
            raise ObjectStoreConfigurationError(
                "Provider input expiry must be between 300 and 86400 seconds"
            )
        physical_key = self._physical_asset_key(key)
        normalized_type = content_type.strip().lower()
        if (
            not normalized_type
            or "\r" in normalized_type
            or "\n" in normalized_type
        ):
            raise ObjectStoreConfigurationError("Content type is invalid")
        response = self._client.head_object(
            Bucket=self._asset_bucket,
            Key=physical_key,
        )
        metadata = self._metadata_from_response(response, logical_key=key)
        if metadata.sha256 != expected_sha256:
            raise ObjectStoreIntegrityError(
                "Provider input checksum does not match the approved asset"
            )
        if metadata.size_bytes != expected_size_bytes:
            raise ObjectStoreIntegrityError(
                "Provider input size does not match the approved asset"
            )
        if metadata.content_type.strip().lower() != normalized_type:
            raise ObjectStoreIntegrityError(
                "Provider input MIME does not match the approved asset"
            )
        version_id = str(response.get("VersionId") or "").strip()
        if not version_id or len(version_id) > 1024:
            raise ObjectStoreIntegrityError(
                "Provider input requires a versioned promoted object"
            )
        url = self._client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self._asset_bucket,
                "Key": physical_key,
                "ResponseContentType": normalized_type,
                "VersionId": version_id,
            },
            ExpiresIn=expires_seconds,
        )
        return PresignedProviderInput(
            url=url,
            expires_seconds=expires_seconds,
        )

    def head(self, key: str) -> StoredObjectMetadata:
        physical_key = self._physical_quarantine_key(key)
        return self._metadata(
            bucket=self._quarantine_bucket,
            physical_key=physical_key,
            logical_key=key,
        )

    def promote(
        self,
        key: str,
        *,
        expected_sha256: str,
        expected_size_bytes: int,
        expected_content_type: str,
    ) -> StoredObjectMetadata:
        """Copy verified quarantine data to the asset bucket, then delete source."""
        source_key = self._physical_quarantine_key(key)
        asset_key = f"assets/{key.removeprefix('quarantine/')}"
        destination_key = self._physical_asset_key(asset_key)
        try:
            metadata = self._metadata(
                bucket=self._asset_bucket,
                physical_key=destination_key,
                logical_key=asset_key,
            )
        except Exception as exc:
            if not self._is_not_found(exc):
                raise
            copy_options = {
                "Bucket": self._asset_bucket,
                "Key": destination_key,
                "CopySource": {
                    "Bucket": self._quarantine_bucket,
                    "Key": source_key,
                },
                "MetadataDirective": "COPY",
            }
            if self._kms_key_id:
                copy_options.update(
                    {
                        "ServerSideEncryption": "aws:kms",
                        "SSEKMSKeyId": self._kms_key_id,
                    }
                )
            else:
                copy_options["ServerSideEncryption"] = "AES256"
            self._client.copy_object(**copy_options)
            metadata = self._metadata(
                bucket=self._asset_bucket,
                physical_key=destination_key,
                logical_key=asset_key,
            )

        if metadata.sha256 != expected_sha256:
            raise ObjectStoreIntegrityError(
                "Promoted object checksum does not match the asset"
            )
        if metadata.size_bytes != expected_size_bytes:
            raise ObjectStoreIntegrityError(
                "Promoted object size does not match the asset"
            )
        if (
            metadata.content_type.strip().lower()
            != expected_content_type.strip().lower()
        ):
            raise ObjectStoreIntegrityError(
                "Promoted object MIME does not match the asset"
            )
        self._client.delete_object(
            Bucket=self._quarantine_bucket,
            Key=source_key,
        )
        return metadata

    def put_derived(
        self,
        *,
        key: str,
        path: Path,
        content_type: str,
        sha256: str,
    ) -> StoredObjectMetadata:
        """Upload a server-generated derivative, then verify it from storage."""
        physical_key = self._physical_derived_key(key)
        if not path.is_absolute() or not path.is_file():
            raise ObjectStoreConfigurationError(
                "Derived media path must be an absolute regular file"
            )
        normalized_type = content_type.strip().lower()
        if normalized_type != "image/jpeg":
            raise ObjectStoreConfigurationError(
                "Derived thumbnail content type must be image/jpeg"
            )
        actual_sha256 = self._file_sha256(path)
        if actual_sha256 != sha256:
            raise ObjectStoreIntegrityError(
                "Derived object checksum does not match generated output"
            )
        options = {
            "Bucket": self._asset_bucket,
            "Key": physical_key,
            "ContentType": normalized_type,
            "Metadata": {"sha256": sha256},
        }
        if self._kms_key_id:
            options.update(
                {
                    "ServerSideEncryption": "aws:kms",
                    "SSEKMSKeyId": self._kms_key_id,
                }
            )
        else:
            options["ServerSideEncryption"] = "AES256"
        with path.open("rb") as body:
            self._client.put_object(Body=body, **options)

        metadata = self._metadata(
            bucket=self._asset_bucket,
            physical_key=physical_key,
            logical_key=key,
        )
        if (
            metadata.sha256 != sha256
            or metadata.size_bytes != path.stat().st_size
            or metadata.content_type.strip().lower() != normalized_type
        ):
            self._client.delete_object(
                Bucket=self._asset_bucket,
                Key=physical_key,
            )
            raise ObjectStoreIntegrityError(
                "Stored derivative failed integrity validation"
            )
        return metadata

    def put_quarantined_generated(
        self,
        *,
        key: str,
        path: Path,
        content_type: str,
        sha256: str,
        size_bytes: int,
    ) -> StoredObjectMetadata:
        """Idempotently store a server-fetched provider result in quarantine."""
        if not key.startswith("quarantine/generated/"):
            raise ObjectStoreConfigurationError(
                "Generated media key is outside its quarantine namespace"
            )
        physical_key = self._physical_quarantine_key(key)
        if not path.is_absolute() or not path.is_file():
            raise ObjectStoreConfigurationError(
                "Generated media path must be an absolute regular file"
            )
        if size_bytes < 1 or path.stat().st_size != size_bytes:
            raise ObjectStoreIntegrityError("Generated media size does not match")
        if self._file_sha256(path) != sha256:
            raise ObjectStoreIntegrityError("Generated media checksum does not match")
        normalized_type = content_type.strip().lower()
        if not normalized_type:
            raise ObjectStoreConfigurationError(
                "Generated media content type is required"
            )
        try:
            existing = self._metadata(
                bucket=self._quarantine_bucket,
                physical_key=physical_key,
                logical_key=key,
            )
        except Exception as exc:
            if not self._is_not_found(exc):
                raise
        else:
            self._assert_generated_metadata(
                existing,
                sha256=sha256,
                size_bytes=size_bytes,
                content_type=normalized_type,
            )
            return existing

        options = {
            "Bucket": self._quarantine_bucket,
            "Key": physical_key,
            "ContentType": normalized_type,
            "ContentLength": size_bytes,
            "Metadata": {"sha256": sha256},
        }
        if self._kms_key_id:
            options.update(
                {
                    "ServerSideEncryption": "aws:kms",
                    "SSEKMSKeyId": self._kms_key_id,
                }
            )
        else:
            options["ServerSideEncryption"] = "AES256"
        with path.open("rb") as handle:
            self._client.put_object(Body=handle, **options)
        stored = self._metadata(
            bucket=self._quarantine_bucket,
            physical_key=physical_key,
            logical_key=key,
        )
        self._assert_generated_metadata(
            stored,
            sha256=sha256,
            size_bytes=size_bytes,
            content_type=normalized_type,
        )
        return stored

    @staticmethod
    def _assert_generated_metadata(
        metadata: StoredObjectMetadata,
        *,
        sha256: str,
        size_bytes: int,
        content_type: str,
    ) -> None:
        if metadata.sha256 != sha256:
            raise ObjectStoreIntegrityError(
                "Stored generated media checksum does not match"
            )
        if metadata.size_bytes != size_bytes:
            raise ObjectStoreIntegrityError(
                "Stored generated media size does not match"
            )
        if metadata.content_type.strip().lower() != content_type:
            raise ObjectStoreIntegrityError(
                "Stored generated media content type does not match"
            )

    def delete_asset(self, key: str) -> None:
        """Idempotently delete one canonical promoted-object key."""
        self._client.delete_object(
            Bucket=self._asset_bucket,
            Key=self._physical_asset_key(key),
        )

    @contextmanager
    def stage_quarantined(
        self,
        key: str,
        *,
        expected_sha256: str,
        expected_size_bytes: int,
        expected_content_type: str,
    ) -> Iterator[Path]:
        """Stream a quarantine object to a private, integrity-checked file."""
        physical_key = self._physical_quarantine_key(key)
        with self._stage_object(
            bucket=self._quarantine_bucket,
            physical_key=physical_key,
            expected_sha256=expected_sha256,
            expected_size_bytes=expected_size_bytes,
            expected_content_type=expected_content_type,
        ) as path:
            yield path

    @contextmanager
    def stage_asset(
        self,
        key: str,
        *,
        expected_sha256: str,
        expected_size_bytes: int,
        expected_content_type: str,
    ) -> Iterator[Path]:
        """Stream one promoted asset to a private integrity-checked file."""
        physical_key = self._physical_asset_key(key)
        with self._stage_object(
            bucket=self._asset_bucket,
            physical_key=physical_key,
            expected_sha256=expected_sha256,
            expected_size_bytes=expected_size_bytes,
            expected_content_type=expected_content_type,
        ) as path:
            yield path

    @contextmanager
    def _stage_object(
        self,
        *,
        bucket: str,
        physical_key: str,
        expected_sha256: str,
        expected_size_bytes: int,
        expected_content_type: str,
    ) -> Iterator[Path]:
        response = self._client.get_object(
            Bucket=bucket,
            Key=physical_key,
        )
        body = response.get("Body")
        if body is None or not callable(getattr(body, "read", None)):
            raise ObjectStoreIntegrityError("Stored object body is unavailable")
        try:
            self._validate_staged_headers(
                response,
                expected_sha256=expected_sha256,
                expected_size_bytes=expected_size_bytes,
                expected_content_type=expected_content_type,
            )
            with tempfile.TemporaryDirectory(
                prefix="b-agent-media-stage-"
            ) as directory:
                os.chmod(directory, 0o700)
                staged_path = Path(directory) / "input.bin"
                descriptor = os.open(
                    staged_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                digest = sha256()
                written = 0
                with os.fdopen(descriptor, "wb") as staged_file:
                    while True:
                        chunk = body.read(
                            min(1024 * 1024, expected_size_bytes - written + 1)
                        )
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > expected_size_bytes:
                            raise ObjectStoreIntegrityError(
                                "Stored object size exceeds upload intent"
                            )
                        digest.update(chunk)
                        staged_file.write(chunk)
                    staged_file.flush()
                    os.fsync(staged_file.fileno())
                if written != expected_size_bytes:
                    raise ObjectStoreIntegrityError(
                        "Stored object size does not match upload intent"
                    )
                if digest.hexdigest() != expected_sha256:
                    raise ObjectStoreIntegrityError(
                        "Stored object checksum does not match upload intent"
                    )
                yield staged_path
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()

    @staticmethod
    def _validate_staged_headers(
        response: dict,
        *,
        expected_sha256: str,
        expected_size_bytes: int,
        expected_content_type: str,
    ) -> None:
        if response.get("ContentLength") != expected_size_bytes:
            raise ObjectStoreIntegrityError(
                "Stored object size does not match upload intent"
            )
        if (
            str(response.get("ContentType") or "").strip().lower()
            != expected_content_type.strip().lower()
        ):
            raise ObjectStoreIntegrityError(
                "Stored object MIME does not match upload intent"
            )
        metadata = response.get("Metadata") or {}
        if str(metadata.get("sha256") or "").strip().lower() != expected_sha256:
            raise ObjectStoreIntegrityError(
                "Stored object checksum metadata does not match upload intent"
            )

    def _metadata(
        self,
        *,
        bucket: str,
        physical_key: str,
        logical_key: str,
    ) -> StoredObjectMetadata:
        response = self._client.head_object(Bucket=bucket, Key=physical_key)
        return self._metadata_from_response(response, logical_key=logical_key)

    @staticmethod
    def _metadata_from_response(
        response: dict,
        *,
        logical_key: str,
    ) -> StoredObjectMetadata:
        metadata = response.get("Metadata") or {}
        sha256 = str(metadata.get("sha256") or "").strip().lower()
        if len(sha256) != 64:
            raise ObjectStoreIntegrityError(
                "Stored object is missing its SHA-256 metadata"
            )
        return StoredObjectMetadata(
            key=logical_key,
            size_bytes=response["ContentLength"],
            content_type=response["ContentType"],
            sha256=sha256,
        )

    def _physical_quarantine_key(self, key: str) -> str:
        if (
            not key.startswith("quarantine/")
            or key.startswith("/")
            or "\\" in key
        ):
            raise ObjectStoreConfigurationError(
                "Object key is outside the quarantine namespace"
            )
        segments = key.split("/")
        if any(segment in {"", ".", ".."} for segment in segments):
            raise ObjectStoreConfigurationError("Object key is not canonical")
        return f"{self._key_prefix}/{key}" if self._key_prefix else key

    def _physical_asset_key(self, key: str) -> str:
        if not key.startswith("assets/") or key.startswith("/") or "\\" in key:
            raise ObjectStoreConfigurationError(
                "Object key is outside the asset namespace"
            )
        segments = key.split("/")
        if any(segment in {"", ".", ".."} for segment in segments):
            raise ObjectStoreConfigurationError("Object key is not canonical")
        return f"{self._key_prefix}/{key}" if self._key_prefix else key

    def _physical_derived_key(self, key: str) -> str:
        physical_key = self._physical_asset_key(key)
        if "/derived/" not in key:
            raise ObjectStoreConfigurationError(
                "Object key is outside the derived asset namespace"
            )
        return physical_key

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _is_safe_download_name(value: str) -> bool:
        if not 1 <= len(value) <= 255 or value in {".", ".."}:
            return False
        return all(
            character.isascii()
            and (character.isalnum() or character in {".", "_", "-"})
            for character in value
        )

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        response = getattr(exc, "response", {})
        error = response.get("Error", {}) if isinstance(response, dict) else {}
        return str(error.get("Code", "")) in {"404", "NoSuchKey", "NotFound"}
