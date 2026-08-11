from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_worker_image_contains_inspection_tools_and_runs_non_root():
    dockerfile = (REPOSITORY_ROOT / "backend" / "Dockerfile").read_text()

    assert "ffmpeg" in dockerfile
    assert "clamav" in dockerfile
    assert "USER app" in dockerfile


def test_media_worker_has_private_tmp_and_reduced_linux_privileges():
    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text()

    assert "no-new-privileges:true" in compose
    assert "cap_drop:" in compose
    assert "- ALL" in compose
    assert "clamav_data:/var/lib/clamav:ro" in compose
    assert "mode=0700,noexec,nosuid,nodev" in compose


def test_media_inspection_configuration_is_documented():
    env_example = (REPOSITORY_ROOT / ".env.example").read_text()

    for setting in [
        "MEDIA_INSPECTION_ENABLED",
        "MEDIA_OBJECT_STORE_BACKEND",
        "MEDIA_S3_QUARANTINE_BUCKET",
        "MEDIA_S3_ASSET_BUCKET",
        "MEDIA_CLAMSCAN_PATH",
        "MEDIA_FFPROBE_PATH",
        "MEDIA_INSPECTION_TIMEOUT_SECONDS",
    ]:
        assert f"{setting}=" in env_example


def test_thumbnail_and_lifecycle_worker_configuration_is_deployable():
    env_example = (REPOSITORY_ROOT / ".env.example").read_text()
    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text()
    celery_worker = (
        REPOSITORY_ROOT / "backend" / "app" / "tasks" / "celery_worker.py"
    ).read_text()

    for setting in [
        "MEDIA_THUMBNAIL_ENABLED",
        "MEDIA_FFMPEG_PATH",
        "MEDIA_THUMBNAIL_MAX_BYTES",
        "MEDIA_LIFECYCLE_ENABLED",
        "MEDIA_RETENTION_DAYS",
        "MEDIA_MAINTENANCE_USER_ID",
    ]:
        assert f"{setting}=" in env_example
        assert setting in compose
    assert "cleanup_media_assets_task" in celery_worker
