import pytest
from pydantic import ValidationError

from app.config import Settings
from app.integrations.object_store import (
    ObjectStoreConfigurationError,
    ObjectStoreIntegrityError,
    PresignedDownload,
    PresignedUpload,
    S3CompatibleMediaObjectStore,
)


class FakeS3Client:
    def __init__(self):
        self.presign_calls = []
        self.head_calls = []
        self.copy_calls = []
        self.delete_calls = []
        self.asset_exists = False
        self.head_response = {
            "ContentLength": 4096,
            "ContentType": "image/png",
            "Metadata": {"sha256": "a" * 64},
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


class FakeS3NotFound(RuntimeError):
    response = {"Error": {"Code": "NoSuchKey"}}


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
