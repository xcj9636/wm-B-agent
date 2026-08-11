from pathlib import Path
from uuid import uuid4

import pytest

from app.integrations.object_store import StoredObjectMetadata
from app.models.database import MediaAsset, MediaAssetRelation
from app.services.agent_runtime.contracts import ExecutionPrincipal
from app.services.media.assets import MediaAssetConflict, MediaAssetForbidden
from app.services.media.inspection import CommandExecution
from app.services.media.thumbnail import (
    GeneratedThumbnail,
    MediaThumbnailRunner,
    MediaThumbnailService,
    ThumbnailGenerationError,
)


class FakeThumbnailStore:
    backend_name = "s3"

    def __init__(self, staged_path: Path):
        self.staged_path = staged_path
        self.stage_calls = []
        self.put_calls = []

    class _Stage:
        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self.value

        def __exit__(self, *_):
            return False

    def stage_asset(self, key, **expected):
        self.stage_calls.append({"key": key, **expected})
        return self._Stage(self.staged_path)

    def put_derived(self, *, key, path, content_type, sha256):
        self.put_calls.append(
            {
                "key": key,
                "path": path,
                "content_type": content_type,
                "sha256": sha256,
            }
        )
        return StoredObjectMetadata(
            key=key,
            size_bytes=path.stat().st_size,
            content_type=content_type,
            sha256=sha256,
        )


class FakeThumbnailRunner:
    def __init__(self):
        self.calls = []

    def generate(self, source_path, output_path):
        self.calls.append((source_path, output_path))
        output_path.write_bytes(b"generated-jpeg")
        return GeneratedThumbnail(
            sha256="9" * 64,
            size_bytes=len(b"generated-jpeg"),
            mime_type="image/jpeg",
        )


def principal(org_id, *, user_id=71, roles=None):
    return ExecutionPrincipal(
        org_id=org_id,
        user_id=user_id,
        roles=set(roles or {"media_worker"}),
        entitlements_hash="a" * 64,
        authn_context="worker",
    )


def source_asset(db, org_id, *, owner_user_id=7, kind="video"):
    asset = MediaAsset(
        org_id=org_id,
        owner_user_id=owner_user_id,
        kind=kind,
        source="user_upload",
        storage_backend="s3",
        storage_key=f"assets/{org_id}/{uuid4().hex}",
        sha256="8" * 64,
        mime_type="video/mp4" if kind == "video" else "image/png",
        size_bytes=4096,
        sensitivity="confidential",
        quarantined=False,
        scan_status="passed",
        rights_status="verified",
        consent_required=True,
        consent_status="valid",
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


def test_thumbnail_is_persisted_as_inherited_derived_asset(
    db_session,
    tmp_path,
):
    org_id = uuid4()
    source = source_asset(db_session, org_id)
    staged = tmp_path / "source.mp4"
    staged.write_bytes(b"source")
    store = FakeThumbnailStore(staged)
    runner = FakeThumbnailRunner()

    thumbnail = MediaThumbnailService(db_session).generate(
        source.id,
        principal(org_id),
        object_store=store,
        runner=runner,
    )

    assert thumbnail.kind == "image"
    assert thumbnail.source == "thumbnail_derivation"
    assert thumbnail.sensitivity == source.sensitivity
    assert thumbnail.scan_status == "passed"
    assert thumbnail.rights_status == source.rights_status
    assert thumbnail.consent_status == source.consent_status
    assert thumbnail.storage_key == (
        f"assets/{org_id}/derived/{source.id}/thumbnail.jpg"
    )
    relation = db_session.query(MediaAssetRelation).one()
    assert relation.parent_asset_id == source.id
    assert relation.child_asset_id == thumbnail.id
    assert relation.relation_type == "thumbnail_of"
    assert store.stage_calls[0]["key"] == source.storage_key
    assert store.put_calls[0]["key"] == thumbnail.storage_key
    assert not store.put_calls[0]["path"].exists()


def test_thumbnail_generation_is_idempotent_per_live_source(db_session, tmp_path):
    org_id = uuid4()
    source = source_asset(db_session, org_id)
    staged = tmp_path / "source.mp4"
    staged.write_bytes(b"source")
    store = FakeThumbnailStore(staged)
    runner = FakeThumbnailRunner()
    service = MediaThumbnailService(db_session)

    first = service.generate(
        source.id,
        principal(org_id),
        object_store=store,
        runner=runner,
    )
    second = service.generate(
        source.id,
        principal(org_id),
        object_store=store,
        runner=runner,
    )

    assert second.id == first.id
    assert len(runner.calls) == 1
    assert len(store.put_calls) == 1


def test_thumbnail_generation_rejects_wrong_tenant_or_role(db_session, tmp_path):
    org_id = uuid4()
    source = source_asset(db_session, org_id)
    store = FakeThumbnailStore(tmp_path / "unused")
    service = MediaThumbnailService(db_session)

    with pytest.raises(MediaAssetForbidden):
        service.generate(
            source.id,
            principal(uuid4()),
            object_store=store,
            runner=FakeThumbnailRunner(),
        )
    with pytest.raises(MediaAssetForbidden):
        service.generate(
            source.id,
            principal(org_id, roles={"user"}),
            object_store=store,
            runner=FakeThumbnailRunner(),
        )


def test_thumbnail_generation_requires_approved_image_or_video(db_session, tmp_path):
    org_id = uuid4()
    source = source_asset(db_session, org_id, kind="audio")
    store = FakeThumbnailStore(tmp_path / "unused")

    with pytest.raises(MediaAssetConflict):
        MediaThumbnailService(db_session).generate(
            source.id,
            principal(org_id),
            object_store=store,
            runner=FakeThumbnailRunner(),
        )


def test_thumbnail_runner_uses_fixed_bounded_ffmpeg_command(tmp_path):
    calls = []

    def executor(argv, **limits):
        calls.append((argv, limits))
        Path(argv[-1]).write_bytes(b"jpeg")
        return CommandExecution(returncode=0)

    source = (tmp_path / "source.mp4").resolve()
    output = (tmp_path / "thumbnail.jpg").resolve()
    source.write_bytes(b"source")
    result = MediaThumbnailRunner(
        ffmpeg_path="/usr/bin/ffmpeg",
        timeout_seconds=30,
        max_output_bytes=1024,
        max_thumbnail_bytes=1_000_000,
        executor=executor,
    ).generate(source, output)

    argv, limits = calls[0]
    assert argv[0] == "/usr/bin/ffmpeg"
    assert argv[-1] == str(output)
    assert str(source) in argv
    assert "-nostdin" in argv
    assert "-frames:v" in argv
    assert limits == {"timeout_seconds": 30, "max_output_bytes": 1024}
    assert result.size_bytes == 4
    assert result.mime_type == "image/jpeg"


@pytest.mark.parametrize(
    "execution",
    [
        CommandExecution(returncode=1),
        CommandExecution(returncode=0, timed_out=True),
        CommandExecution(returncode=0, output_truncated=True),
    ],
)
def test_thumbnail_runner_fails_closed_on_process_error(tmp_path, execution):
    source = (tmp_path / "source.mp4").resolve()
    output = (tmp_path / "thumbnail.jpg").resolve()
    source.write_bytes(b"source")

    with pytest.raises(ThumbnailGenerationError):
        MediaThumbnailRunner(
            ffmpeg_path="/usr/bin/ffmpeg",
            timeout_seconds=30,
            max_output_bytes=1024,
            max_thumbnail_bytes=1_000_000,
            executor=lambda *_args, **_kwargs: execution,
        ).generate(source, output)
