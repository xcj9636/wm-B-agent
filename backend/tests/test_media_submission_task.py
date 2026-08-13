import pytest

from app.tasks.celery_worker import celery
from app.tasks.media_tasks import submit_media_jobs_task


class FakeDB:
    def __init__(self):
        self.closed = False
        self.rolled_back = False

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_submission_task_and_beat_are_fail_closed(monkeypatch):
    monkeypatch.setattr(
        "app.tasks.media_tasks.settings.MEDIA_SUBMIT_ENABLED",
        False,
    )
    monkeypatch.setattr(
        "app.tasks.media_tasks.SessionLocal",
        lambda: pytest.fail("disabled task must not open the database"),
    )

    assert submit_media_jobs_task.run() == {
        "claimed": 0,
        "submitted": 0,
        "submission_unknown": 0,
        "failed_before_submission": 0,
        "deferred": 0,
        "status": "disabled",
    }
    beat = celery.conf.beat_schedule["submit-media-generation-jobs"]
    assert beat["task"] == "app.tasks.media_tasks.submit_media_jobs_task"
    assert beat.get("args", ()) == ()
    assert beat.get("kwargs", {}) == {}
    assert 5 <= beat["schedule"] <= 60


def test_enabled_submission_task_derives_worker_identity_server_side(monkeypatch):
    db = FakeDB()
    observed = {}

    async def run_configured(session, *, worker_id, now):
        observed["session"] = session
        observed["worker_id"] = worker_id
        observed["now"] = now
        return {
            "claimed": 1,
            "submitted": 1,
            "submission_unknown": 0,
            "failed_before_submission": 0,
            "deferred": 0,
        }

    monkeypatch.setattr(
        "app.tasks.media_tasks.settings.MEDIA_SUBMIT_ENABLED",
        True,
    )
    monkeypatch.setattr("app.tasks.media_tasks.SessionLocal", lambda: db)
    monkeypatch.setattr(
        "app.tasks.media_tasks._run_configured_media_submission",
        run_configured,
    )
    submit_media_jobs_task.request.hostname = "media-worker-private-host"

    result = submit_media_jobs_task.run()

    assert result["submitted"] == 1
    assert observed["session"] is db
    assert observed["worker_id"] == "media-worker-private-host"
    assert db.closed is True
    assert db.rolled_back is False
    assert "prompt" not in repr(result)
