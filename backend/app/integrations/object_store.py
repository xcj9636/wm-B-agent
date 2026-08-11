"""Object-store boundary used by quarantined media asset ingestion."""

from typing import Any, Dict, Optional, Protocol

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
    ) -> StoredObjectMetadata: ...


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


class S3CompatibleMediaObjectStore:
    """Quarantine-only browser upload adapter with server-owned object keys."""

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
        self._client.delete_object(
            Bucket=self._quarantine_bucket,
            Key=source_key,
        )
        return metadata

    def _metadata(
        self,
        *,
        bucket: str,
        physical_key: str,
        logical_key: str,
    ) -> StoredObjectMetadata:
        response = self._client.head_object(Bucket=bucket, Key=physical_key)
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

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        response = getattr(exc, "response", {})
        error = response.get("Error", {}) if isinstance(response, dict) else {}
        return str(error.get("Code", "")) in {"404", "NoSuchKey", "NotFound"}
