from hashlib import sha256

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.integrations.object_store import (
    ObjectStoreConfigurationError,
    ObjectStoreIntegrityError,
    PresignedDownload,
    PresignedProviderInput,
    PresignedUpload,
    S3CompatibleMediaObjectStore,
)


class FakeS3Client:
    def __init__(self):
        self.presign_calls = []
        self.head_calls = []
        self.copy_calls = []
        self.delete_calls = []
        self.put_calls = []
        self.asset_exists = False
        self.head_response = {
            "ContentLength": 4096,
            "ContentType": "image/png",
            "Metadata": {"sha256": "a" * 64},
            "VersionId": "version-1",
        }

    def generate_presigned_post(self, **kwargs):
        self.presign_calls.append(kwargs)
        return {
            "url": "https://objects.example.test/quarantine",
            "fields": dict(kwargs["Fields"]),
        }

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self.presign_calls.append(
            {
                "operation": operation,
                "Params": Params,
                "ExpiresIn": ExpiresIn,
            }
        )
        return "https://objects.example.test/signed-download"

    def head_object(self, **kwargs):
        self.head_calls.append(kwargs)
        if kwargs["Bucket"] == "media-assets" and not self.asset_exists:
            raise FakeS3NotFound()
        return self.head_response

    def copy_object(self, **kwargs):
        self.copy_calls.append(kwargs)
        self.asset_exists = True

    def delete_object(self, **kwargs):
        self.delete_calls.append(kwargs)

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        self.asset_exists = True


class FakeS3NotFound(RuntimeError):
    response = {"Error": {"Code": "NoSuchKey"}}


class GeneratedS3Client(FakeS3Client):
    def __init__(self):
        super().__init__()
        self.generated_exists = False
        self.generated_payload = b""

    def head_object(self, **kwargs):
        self.head_calls.append(kwargs)
        if kwargs["Bucket"] == "media-quarantine" and not self.generated_exists:
            raise FakeS3NotFound()
        return {
            "ContentLength": len(self.generated_payload),
            "ContentType": "video/mp4",
            "Metadata": {"sha256": sha256(self.generated_payload).hexdigest()},
        }

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        self.generated_payload = kwargs["Body"].read()
        self.generated_exists = True


def store(client=None, **overrides):
    values = {
        "client": client or FakeS3Client(),
        "quarantine_bucket": "media-quarantine",
        "asset_bucket": "media-assets",
        "key_prefix": "tenant-media",
        "kms_key_id": "kms-key-1",
    }
    values.update(overrides)
    return S3CompatibleMediaObjectStore(**values)


def test_presigned_upload_is_bound_to_quarantine_constraints():
    client = FakeS3Client()
    object_store = store(client)
    key = "quarantine/ba6e/upload-id"

    result = object_store.create_upload(
        key=key,
        content_type="image/png",
        size_bytes=4096,
        sha256="a" * 64,
        expires_seconds=600,
    )

    assert isinstance(result, PresignedUpload)
    assert result.key == key
    call = client.presign_calls[0]
    assert call["Bucket"] == "media-quarantine"
    assert call["Key"] == "tenant-media/quarantine/ba6e/upload-id"
    assert call["ExpiresIn"] == 600
    assert call["Fields"]["Content-Type"] == "image/png"
    assert call["Fields"]["x-amz-meta-sha256"] == "a" * 64
    assert call["Fields"]["x-amz-server-side-encryption"] == "aws:kms"
    assert call["Fields"]["x-amz-server-side-encryption-aws-kms-key-id"] == (
        "kms-key-1"
    )
    assert {"content-length-range": [4096, 4096]} in call["Conditions"]
    assert "acl" not in {key.lower() for key in call["Fields"]}


def test_presigned_download_is_short_lived_and_bound_to_asset_namespace():
    client = FakeS3Client()
    object_store = store(client)

    result = object_store.create_download(
        key="assets/ba6e/asset-id",
        content_type="image/png",
        download_name="asset-id.png",
        expires_seconds=120,
    )

    assert result == PresignedDownload(
        url="https://objects.example.test/signed-download",
        expires_seconds=120,
    )
    assert client.presign_calls == [
        {
            "operation": "get_object",
            "Params": {
                "Bucket": "media-assets",
                "Key": "tenant-media/assets/ba6e/asset-id",
                "ResponseContentType": "image/png",
                "ResponseContentDisposition": 'attachment; filename="asset-id.png"',
            },
            "ExpiresIn": 120,
        }
    ]


def test_provider_input_is_queue_lived_without_browser_download_headers():
    client = FakeS3Client()
    client.asset_exists = True
    object_store = store(client)

    result = object_store.create_provider_input(
        key="assets/ba6e/asset-id",
        content_type="image/png",
        expected_sha256="a" * 64,
        expected_size_bytes=4096,
        expires_seconds=3600,
    )

    assert result == PresignedProviderInput(
        url="https://objects.example.test/signed-download",
        expires_seconds=3600,
    )
    assert client.presign_calls == [
        {
            "operation": "get_object",
            "Params": {
                "Bucket": "media-assets",
                "Key": "tenant-media/assets/ba6e/asset-id",
                "ResponseContentType": "image/png",
                "VersionId": "version-1",
            },
            "ExpiresIn": 3600,
        }
    ]
    assert client.head_calls == [
        {
            "Bucket": "media-assets",
            "Key": "tenant-media/assets/ba6e/asset-id",
        }
    ]


def test_provider_input_revalidates_promoted_object_integrity_before_signing():
    client = FakeS3Client()
    client.asset_exists = True
    object_store = store(client)

    result = object_store.create_provider_input(
        key="assets/ba6e/asset-id",
        content_type="image/png",
        expected_sha256="a" * 64,
        expected_size_bytes=4096,
        expires_seconds=3600,
    )

    assert result.url == "https://objects.example.test/signed-download"

    client.head_response["Metadata"]["sha256"] = "b" * 64
    with pytest.raises(ObjectStoreIntegrityError, match="checksum"):
        object_store.create_provider_input(
            key="assets/ba6e/asset-id",
            content_type="image/png",
            expected_sha256="a" * 64,
            expected_size_bytes=4096,
            expires_seconds=3600,
        )


def test_provider_input_requires_a_versioned_promoted_object():
    client = FakeS3Client()
    client.asset_exists = True
    client.head_response.pop("VersionId")

    with pytest.raises(ObjectStoreIntegrityError, match="version"):
        store(client).create_provider_input(
            key="assets/ba6e/asset-id",
            content_type="image/png",
            expected_sha256="a" * 64,
            expected_size_bytes=4096,
            expires_seconds=3600,
        )


@pytest.mark.parametrize(
    ("key", "content_type", "expires_seconds"),
    [
        ("quarantine/ba6e/asset-id", "image/png", 3600),
        ("assets/../asset-id", "image/png", 3600),
        ("assets/ba6e/asset-id", "image/png\r\nX-Bad: true", 3600),
        ("assets/ba6e/asset-id", "image/png", 299),
        ("assets/ba6e/asset-id", "image/png", 86_401),
    ],
)
def test_provider_input_rejects_unsafe_scope_or_expiry(
    key,
    content_type,
    expires_seconds,
):
    with pytest.raises(ObjectStoreConfigurationError):
        store().create_provider_input(
            key=key,
            content_type=content_type,
            expected_sha256="a" * 64,
            expected_size_bytes=4096,
            expires_seconds=expires_seconds,
        )


@pytest.mark.parametrize(
    ("key", "download_name", "expires_seconds"),
    [
        ("quarantine/ba6e/asset-id", "asset.png", 120),
        ("assets/../asset-id", "asset.png", 120),
        ("assets/ba6e/asset-id", "bad\r\nname.png", 120),
        ("assets/ba6e/asset-id", "asset.png", 301),
    ],
)
def test_presigned_download_rejects_unsafe_or_overlong_credentials(
    key,
    download_name,
    expires_seconds,
):
    with pytest.raises(ObjectStoreConfigurationError):
        store().create_download(
            key=key,
            content_type="image/png",
            download_name=download_name,
            expires_seconds=expires_seconds,
        )


def test_head_reads_only_the_quarantine_bucket_and_validates_checksum():
    client = FakeS3Client()
    object_store = store(client)

    metadata = object_store.head("quarantine/ba6e/upload-id")

    assert client.head_calls == [
        {
            "Bucket": "media-quarantine",
            "Key": "tenant-media/quarantine/ba6e/upload-id",
        }
    ]
    assert metadata.key == "quarantine/ba6e/upload-id"
    assert metadata.size_bytes == 4096
    assert metadata.content_type == "image/png"
    assert metadata.sha256 == "a" * 64


def test_promotion_copies_to_asset_bucket_verifies_then_deletes_quarantine():
    client = FakeS3Client()
    object_store = store(client)

    promoted = object_store.promote(
        "quarantine/ba6e/upload-id",
        expected_sha256="a" * 64,
        expected_size_bytes=4096,
        expected_content_type="image/png",
    )

    assert promoted.key == "assets/ba6e/upload-id"
    assert client.copy_calls == [
        {
            "Bucket": "media-assets",
            "Key": "tenant-media/assets/ba6e/upload-id",
            "CopySource": {
                "Bucket": "media-quarantine",
                "Key": "tenant-media/quarantine/ba6e/upload-id",
            },
            "MetadataDirective": "COPY",
            "ServerSideEncryption": "aws:kms",
            "SSEKMSKeyId": "kms-key-1",
        }
    ]
    assert client.delete_calls == [
        {
            "Bucket": "media-quarantine",
            "Key": "tenant-media/quarantine/ba6e/upload-id",
        }
    ]


def test_promotion_recovers_existing_verified_destination_without_copying():
    client = FakeS3Client()
    client.asset_exists = True
    object_store = store(client)

    promoted = object_store.promote(
        "quarantine/ba6e/upload-id",
        expected_sha256="a" * 64,
        expected_size_bytes=4096,
        expected_content_type="image/png",
    )

    assert promoted.key == "assets/ba6e/upload-id"
    assert client.copy_calls == []
    assert len(client.delete_calls) == 1


def test_promotion_never_deletes_quarantine_when_destination_hash_is_wrong():
    client = FakeS3Client()
    client.asset_exists = True
    client.head_response["Metadata"]["sha256"] = "b" * 64

    with pytest.raises(ObjectStoreIntegrityError, match="checksum"):
        store(client).promote(
            "quarantine/ba6e/upload-id",
            expected_sha256="a" * 64,
            expected_size_bytes=4096,
            expected_content_type="image/png",
        )

    assert client.delete_calls == []


def test_promotion_keeps_quarantine_when_destination_size_is_wrong():
    client = FakeS3Client()
    client.asset_exists = True
    client.head_response["ContentLength"] = 4097

    with pytest.raises(ObjectStoreIntegrityError, match="size"):
        store(client).promote(
            "quarantine/ba6e/upload-id",
            expected_sha256="a" * 64,
            expected_size_bytes=4096,
            expected_content_type="image/png",
        )

    assert client.delete_calls == []


def test_derived_asset_is_encrypted_uploaded_and_integrity_checked(tmp_path):
    client = FakeS3Client()
    object_store = store(client)
    thumbnail = tmp_path / "thumbnail.jpg"
    thumbnail.write_bytes(b"generated-thumbnail")
    digest = sha256(b"generated-thumbnail").hexdigest()
    client.head_response = {
        "ContentLength": len(b"generated-thumbnail"),
        "ContentType": "image/jpeg",
        "Metadata": {"sha256": digest},
    }

    result = object_store.put_derived(
        key="assets/ba6e/derived/source-id/thumbnail.jpg",
        path=thumbnail,
        content_type="image/jpeg",
        sha256=digest,
    )

    call = client.put_calls[0]
    assert call["Bucket"] == "media-assets"
    assert call["Key"] == (
        "tenant-media/assets/ba6e/derived/source-id/thumbnail.jpg"
    )
    assert call["ContentType"] == "image/jpeg"
    assert call["Metadata"] == {"sha256": digest}
    assert call["ServerSideEncryption"] == "aws:kms"
    assert call["SSEKMSKeyId"] == "kms-key-1"
    assert call["Body"].closed
    assert result.key == "assets/ba6e/derived/source-id/thumbnail.jpg"


def test_derived_asset_write_rejects_noncanonical_or_non_derived_keys(tmp_path):
    thumbnail = tmp_path / "thumbnail.jpg"
    thumbnail.write_bytes(b"generated-thumbnail")

    for key in ["assets/ba6e/plain.jpg", "assets/../derived/thumb.jpg"]:
        with pytest.raises(ObjectStoreConfigurationError):
            store().put_derived(
                key=key,
                path=thumbnail,
                content_type="image/jpeg",
                sha256="9" * 64,
            )


def test_asset_delete_is_idempotent_and_cannot_target_quarantine():
    client = FakeS3Client()
    object_store = store(client)

    object_store.delete_asset("assets/ba6e/old-asset")

    assert client.delete_calls == [
        {
            "Bucket": "media-assets",
            "Key": "tenant-media/assets/ba6e/old-asset",
        }
    ]
    with pytest.raises(ObjectStoreConfigurationError):
        object_store.delete_asset("quarantine/ba6e/unsafe")


@pytest.mark.parametrize(
    "key",
    [
        "assets/not-quarantine",
        "quarantine/../asset",
        "quarantine\\asset",
        "/quarantine/asset",
    ],
)
def test_object_store_rejects_keys_outside_quarantine(key):
    with pytest.raises(ObjectStoreConfigurationError):
        store().create_upload(
            key=key,
            content_type="image/png",
            size_bytes=100,
            sha256="a" * 64,
        )


def test_object_store_requires_separate_nonempty_buckets():
    with pytest.raises(ObjectStoreConfigurationError):
        store(quarantine_bucket="same", asset_bucket="same")
    with pytest.raises(ObjectStoreConfigurationError):
        store(quarantine_bucket="")


def test_generated_provider_result_is_idempotently_written_to_quarantine(tmp_path):
    payload = b"generated-video"
    digest = sha256(payload).hexdigest()
    path = tmp_path / "provider-result.mp4"
    path.write_bytes(payload)
    client = GeneratedS3Client()
    object_store = store(client)
    key = "quarantine/generated/org-id/job-id/output-0"

    first = object_store.put_quarantined_generated(
        key=key,
        path=path,
        content_type="video/mp4",
        sha256=digest,
        size_bytes=len(payload),
    )
    replay = object_store.put_quarantined_generated(
        key=key,
        path=path,
        content_type="video/mp4",
        sha256=digest,
        size_bytes=len(payload),
    )

    assert first == replay
    assert first.key == key
    assert len(client.put_calls) == 1
    call = client.put_calls[0]
    assert call["Bucket"] == "media-quarantine"
    assert call["Key"] == f"tenant-media/{key}"
    assert call["ServerSideEncryption"] == "aws:kms"
    assert call["SSEKMSKeyId"] == "kms-key-1"
    assert call["Metadata"] == {"sha256": digest}


def test_generated_result_store_rejects_wrong_namespace_or_integrity(tmp_path):
    path = tmp_path / "provider-result.mp4"
    path.write_bytes(b"generated-video")
    object_store = store(GeneratedS3Client())

    with pytest.raises(ObjectStoreConfigurationError):
        object_store.put_quarantined_generated(
            key="quarantine/user-controlled/output-0",
            path=path,
            content_type="video/mp4",
            sha256=sha256(path.read_bytes()).hexdigest(),
            size_bytes=path.stat().st_size,
        )
    with pytest.raises(ObjectStoreIntegrityError):
        object_store.put_quarantined_generated(
            key="quarantine/generated/org-id/job-id/output-0",
            path=path,
            content_type="video/mp4",
            sha256="0" * 64,
            size_bytes=path.stat().st_size,
        )


def test_production_media_upload_requires_configured_s3_backend():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            DEPLOYMENT_ENVIRONMENT="production",
            MEDIA_UPLOAD_ENABLED=True,
            MEDIA_OBJECT_STORE_BACKEND="local",
        )

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            MEDIA_UPLOAD_ENABLED=True,
            MEDIA_OBJECT_STORE_BACKEND="s3",
        )

    configured = Settings(
        _env_file=None,
        DEPLOYMENT_ENVIRONMENT="production",
        MEDIA_UPLOAD_ENABLED=True,
        MEDIA_INSPECTION_ENABLED=True,
        MEDIA_OBJECT_STORE_BACKEND="s3",
        MEDIA_S3_QUARANTINE_BUCKET="media-quarantine",
        MEDIA_S3_ASSET_BUCKET="media-assets",
    )
    assert configured.MEDIA_OBJECT_STORE_BACKEND == "s3"


@pytest.mark.parametrize(
    "feature_flag",
    [
        "MEDIA_INSPECTION_ENABLED",
        "MEDIA_THUMBNAIL_ENABLED",
        "MEDIA_LIFECYCLE_ENABLED",
    ],
)
def test_every_media_worker_feature_requires_configured_buckets(feature_flag):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            MEDIA_OBJECT_STORE_BACKEND="s3",
            **{feature_flag: True},
        )
