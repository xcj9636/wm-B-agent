from datetime import datetime
from uuid import uuid4

import pytest

from app.models.database import MediaGenerationEvent, MediaGenerationJob
from app.services.media.job_access import MediaGenerationJobAccessService


def stored_job(db_session, *, org_id, owner_user_id, suffix):
    job = MediaGenerationJob(
        org_id=org_id,
        owner_user_id=owner_user_id,
        project_id=uuid4(),
        storyboard_version_id=uuid4(),
        shot_id=uuid4(),
        runtime_revision_id=uuid4(),
        idempotency_key=f"media-access-{suffix}",
        input_hash="a" * 64,
        intent_hash="b" * 64,
        payload_ref=f"vault://media-intents/{uuid4()}",
        mode="text_to_video",
        provider="fal",
        model_id="fal-ai/t2v",
        sensitivity="internal",
        status="queued",
        effect_state="none",
        event_sequence=3,
        reserved_cost_microusd=100,
        estimate_hash="c" * 64,
        budget_period_start=datetime(2026, 8, 1).date(),
        deadline_at=datetime(2026, 8, 13, 12, 0),
    )
    db_session.add(job)
    db_session.flush()
    db_session.add_all(
        [
            MediaGenerationEvent(
                job_id=job.id,
                sequence=1,
                event_type="job.created",
                data_json={"prompt": "must-not-leak"},
                created_at=datetime(2026, 8, 13, 11, 0),
            ),
            MediaGenerationEvent(
                job_id=job.id,
                sequence=2,
                event_type="submission.accepted",
                data_json={"provider_request_id": "secret-provider-id"},
                created_at=datetime(2026, 8, 13, 11, 1),
            ),
            MediaGenerationEvent(
                job_id=job.id,
                sequence=3,
                event_type="job.failed",
                data_json={"error_code": "provider_failed"},
                created_at=datetime(2026, 8, 13, 11, 2),
            ),
        ]
    )
    db_session.commit()
    return job


def test_owner_reads_job_and_incremental_sanitized_events(db_session):
    org_id = uuid4()
    job = stored_job(db_session, org_id=org_id, owner_user_id=7, suffix="owner")
    service = MediaGenerationJobAccessService(db_session)

    found = service.get(job.id, org_id=org_id, user_id=7, is_admin=False)
    events, next_sequence = service.list_events(
        job.id,
        org_id=org_id,
        user_id=7,
        is_admin=False,
        after_sequence=1,
        limit=10,
    )

    assert found.id == job.id
    assert [event.sequence for event in events] == [2, 3]
    assert next_sequence == 3
    assert events[0].data == {}
    assert events[1].data == {"error_code": "provider_failed"}
    assert "secret-provider-id" not in repr(events)
    assert "must-not-leak" not in repr(events)


def test_cross_owner_and_cross_org_are_hidden_but_same_org_admin_can_read(db_session):
    org_id = uuid4()
    job = stored_job(db_session, org_id=org_id, owner_user_id=7, suffix="hidden")
    service = MediaGenerationJobAccessService(db_session)

    for other_org, other_user, is_admin in [
        (org_id, 8, False),
        (uuid4(), 7, True),
    ]:
        with pytest.raises(KeyError):
            service.get(
                job.id,
                org_id=other_org,
                user_id=other_user,
                is_admin=is_admin,
            )
        with pytest.raises(KeyError):
            service.list_events(
                job.id,
                org_id=other_org,
                user_id=other_user,
                is_admin=is_admin,
                after_sequence=0,
                limit=10,
            )

    assert service.get(
        job.id,
        org_id=org_id,
        user_id=99,
        is_admin=True,
    ).id == job.id


@pytest.mark.parametrize(
    ("after_sequence", "limit"),
    [(-1, 10), (0, 0), (0, 101)],
)
def test_event_cursor_bounds_are_validated(
    db_session,
    after_sequence,
    limit,
):
    with pytest.raises(ValueError):
        MediaGenerationJobAccessService(db_session).list_events(
            uuid4(),
            org_id=uuid4(),
            user_id=7,
            is_admin=False,
            after_sequence=after_sequence,
            limit=limit,
        )
