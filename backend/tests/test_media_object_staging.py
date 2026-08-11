from hashlib import sha256
from io import BytesIO
import os

import pytest

from app.integrations.object_store import (
    ObjectStoreIntegrityError,
    S3CompatibleMediaObjectStore,
)


class FakeS3Client:
    def __init__(self, payload: bytes, *, content_type="video/mp4", metadata=None):
        self.payload = payload
        self.content_type = content_type
        self.metadata = metadata or {"sha256": sha256(payload).hexdigest()}
        self.get_calls = []

    def get_object(self, **kwargs):
        self.get_calls.append(kwargs)
        return {
            "ContentLength": len(self.payload),
            "ContentType": self.content_type,
            "Metadata": self.metadata,
            "Body": BytesIO(self.payload),
        }


def store(client):
    return S3CompatibleMediaObjectStore(
        quarantine_bucket="media-quarantine",
        asset_bucket="media-assets",
        key_prefix="tenant-media",
        kms_key_id="kms-key-1",
        client=client,
    )


def test_quarantined_object_is_streamed_to_private_ephemeral_file():
    payload = b"safe-media-payload"
    client = FakeS3Client(payload)
    object_store = store(client)

    with object_store.stage_quarantined(
        "quarantine/ba6e/upload-id",
        expected_sha256=sha256(payload).hexdigest(),
        expected_size_bytes=len(payload),
        expected_content_type="video/mp4",
    ) as path:
        staged_path = path
        assert path.is_absolute()
        assert path.read_bytes() == payload
        assert os.stat(path).st_mode & 0o777 == 0o600
        assert "ba6e" not in str(path)
        assert "upload-id" not in str(path)

    assert not staged_path.exists()
    assert not staged_path.parent.exists()
    assert client.get_calls == [
        {
            "Bucket": "media-quarantine",
            "Key": "tenant-media/quarantine/ba6e/upload-id",
        }
    ]


@pytest.mark.parametrize(
    ("payload", "expected_size", "expected_sha", "expected_message"),
    [
        (b"too-large", 4, sha256(b"too-").hexdigest(), "size"),
        (b"four", 4, "0" * 64, "checksum"),
    ],
)
def test_staging_rejects_size_or_checksum_mismatch(
    payload,
    expected_size,
    expected_sha,
    expected_message,
):
    with pytest.raises(ObjectStoreIntegrityError, match=expected_message):
        with store(FakeS3Client(payload)).stage_quarantined(
            "quarantine/ba6e/upload-id",
            expected_sha256=expected_sha,
            expected_size_bytes=expected_size,
            expected_content_type="video/mp4",
        ):
            pytest.fail("invalid object must never reach the scanner")


def test_staging_rejects_metadata_and_content_type_mismatch_before_reading():
    payload = b"four"
    client = FakeS3Client(
        payload,
        content_type="text/html",
        metadata={"sha256": sha256(payload).hexdigest()},
    )

    with pytest.raises(ObjectStoreIntegrityError, match="MIME"):
        with store(client).stage_quarantined(
            "quarantine/ba6e/upload-id",
            expected_sha256=sha256(payload).hexdigest(),
            expected_size_bytes=len(payload),
            expected_content_type="video/mp4",
        ):
            pytest.fail("MIME mismatch must never reach the scanner")

