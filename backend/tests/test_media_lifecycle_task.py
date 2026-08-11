from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.models.database import MediaAsset, User
from app.tasks.media_tasks import cleanup_media_assets_task, run_media_cleanup


class FakeDeleteStore:
    def __init__(self):
        self.calls = []

    def delete_asset(self, key):
        self.calls.append(key)


def test_maintenance_worker_uses_configured_durable_admin(db_session):
    config = Settings(_env_file=None)
    admin = User(
        id=91,
        username="media-maintainer",
        email="media-maintainer@example.test",
        hashed_password="unused",
        is_active=True,
        is_superuser=True,
    )
    target = MediaAsset(
        org_id=config.AGENT_ORG_ID,
        owner_user_id=admin.id,
        kind="image",
        source="user_upload",
        storage_backend="s3",
        storage_key=f"assets/{config.AGENT_ORG_ID}/expired",
        sha256="4" * 64,
        mime_type="image/png",
        size_bytes=2048,
        sensitivity="internal",
        quarantined=False,
        scan_status="passed",
        rights_status="verified",
        consent_required=False,
        consent_status="not_required",
        deleted_at=(
            datetime(2026, 8, 11) - timedelta(days=31)
        ),
    )
    db_session.add_all([admin, target])
    db_session.commit()
    store = FakeDeleteStore()

    result = run_media_cleanup(
        db_session,
        org_id=config.AGENT_ORG_ID,
        maintenance_user_id=admin.id,
        object_store=store,
        retention_days=30,
        batch_size=100,
        now=datetime(2026, 8, 11, tzinfo=timezone.utc),
    )

    assert result == {"cleaned": 1, "status": "completed"}
    assert store.calls == [target.storage_key]
    assert cleanup_media_assets_task.name == (
        "app.tasks.media_tasks.cleanup_media_assets_task"
    )
