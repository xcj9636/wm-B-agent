"""PostgreSQL-only concurrency coverage for media resolution approvals."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from threading import Barrier
import os
import uuid

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.models.database import (
    MediaBudgetAccount,
    MediaGenerationAttempt,
    MediaGenerationJob,
    MediaRuntimeRevision,
    MediaSubmissionResolutionApproval,
    MediaSubmissionResolutionRequest,
    MediaSubmissionResolutionStatus,
    User,
    VideoPersona,
    VideoPersonaVersion,
    VideoProject,
    VideoStoryboardVersion,
)
from app.services.media.submission_resolution import (
    MediaSubmissionResolutionCommand,
    MediaSubmissionResolutionConflict,
    MediaSubmissionResolutionService,
)


TEST_DATABASE_URL = os.getenv("TEST_POSTGRES_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_POSTGRES_DATABASE_URL is required for row-lock coverage",
)


@pytest.fixture(scope="module")
def postgres_session_factory():
    url = make_url(TEST_DATABASE_URL)
    if not url.drivername.startswith("postgresql"):
        pytest.fail("Concurrency test requires PostgreSQL")
    if "test" not in (url.database or "").lower():
        pytest.fail("Refusing to mutate a database whose name lacks 'test'")
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with engine.connect() as connection:
        revision = connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one()
    expected_revision = ScriptDirectory.from_config(
        Config("alembic.ini")
    ).get_current_head()
    assert revision == expected_revision
    yield session_factory
    engine.dispose()


def test_concurrent_second_approvals_execute_media_resolution_once(
    postgres_session_factory,
):
    marker = uuid.uuid4().hex
    org_id = uuid.uuid4()
    user_ids: list[int] = []
    job_id = None
    try:
        with postgres_session_factory() as session:
            admins = [
                User(
                    username=f"media-resolution-{index}-{marker}",
                    email=f"media-resolution-{index}-{marker}@example.com",
                    hashed_password="unused",
                    is_active=True,
                    is_superuser=True,
                )
                for index in range(3)
            ]
            session.add_all(admins)
            session.flush()
            user_ids = [admin.id for admin in admins]
            now = datetime.utcnow()
            persona = VideoPersona(
                org_id=org_id,
                owner_user_id=user_ids[0],
                created_at=now,
            )
            session.add(persona)
            session.flush()
            persona_version = VideoPersonaVersion(
                persona_id=persona.id,
                org_id=org_id,
                revision=1,
                idempotency_key=f"postgres-persona:{marker}",
                input_hash="d" * 64,
                spec_json={},
                spec_hash="e" * 64,
                status="approved",
                created_by_user_id=user_ids[0],
                approved_by_user_id=user_ids[1],
                approved_at=now,
                created_at=now,
            )
            session.add(persona_version)
            session.flush()
            project = VideoProject(
                org_id=org_id,
                owner_user_id=user_ids[0],
                idempotency_key=f"postgres-project:{marker}",
                input_hash="f" * 64,
                brief_json={},
                brief_hash="1" * 64,
                persona_version_id=persona_version.id,
                persona_snapshot_json={},
                persona_spec_hash=persona_version.spec_hash,
                sensitivity="internal",
                status="approved",
                created_at=now,
                updated_at=now,
            )
            session.add(project)
            session.flush()
            storyboard = VideoStoryboardVersion(
                project_id=project.id,
                org_id=org_id,
                revision=1,
                idempotency_key=f"postgres-storyboard:{marker}",
                input_hash="2" * 64,
                storyboard_json={},
                storyboard_hash="3" * 64,
                status="approved",
                created_by_user_id=user_ids[0],
                approved_by_user_id=user_ids[1],
                approved_at=now,
                created_at=now,
            )
            runtime = MediaRuntimeRevision(
                org_id=org_id,
                revision=1,
                provider="fal",
                enabled_modes=["text_to_video"],
                model_aliases={"text_to_video": "fal-ai/veo3/fast"},
                capability_snapshot={},
                capability_snapshot_hash="4" * 64,
                created_by_user_id=user_ids[0],
                created_at=now,
            )
            session.add_all([storyboard, runtime])
            session.flush()
            session.add(
                MediaBudgetAccount(
                    org_id=org_id,
                    period_start=date(now.year, now.month, 1),
                    limit_microusd=10_000_000,
                    reserved_microusd=2_500_000,
                    spent_microusd=0,
                )
            )
            job = MediaGenerationJob(
                org_id=org_id,
                owner_user_id=user_ids[0],
                project_id=project.id,
                storyboard_version_id=storyboard.id,
                shot_id=uuid.uuid4(),
                runtime_revision_id=runtime.id,
                idempotency_key=f"postgres-media-resolution:{marker}",
                input_hash="a" * 64,
                intent_hash="b" * 64,
                payload_ref="vault://media-intents/postgres-resolution",
                mode="text_to_video",
                provider="fal",
                model_id="fal-ai/veo3/fast",
                sensitivity="internal",
                status="submission_unknown",
                effect_state="unknown",
                reserved_cost_microusd=2_500_000,
                estimate_hash="c" * 64,
                budget_period_start=date(now.year, now.month, 1),
                deadline_at=now + timedelta(hours=1),
                created_at=now,
                updated_at=now,
            )
            session.add(job)
            session.flush()
            session.add(
                MediaGenerationAttempt(
                    job_id=job.id,
                    attempt_number=1,
                    fencing_token=1,
                    provider="fal",
                    model_id=job.model_id,
                    status="submission_unknown",
                    effect_state="unknown",
                    started_at=now,
                )
            )
            session.commit()
            job_id = job.id

        command = MediaSubmissionResolutionCommand(
            action="confirmed_submitted",
            evidence_reference=f"provider-audit/{marker}",
            provider_request_id=f"fal_{marker}",
        )
        with postgres_session_factory() as session:
            first = MediaSubmissionResolutionService(session).approve(
                job_id=job_id,
                org_id=org_id,
                admin_user_id=user_ids[0],
                command=command,
            )
            session.commit()
        assert first.status == MediaSubmissionResolutionStatus.PENDING

        start = Barrier(2)

        def compete(admin_user_id: int) -> str:
            with postgres_session_factory() as session:
                start.wait(timeout=10)
                try:
                    result = MediaSubmissionResolutionService(session).approve(
                        job_id=job_id,
                        org_id=org_id,
                        admin_user_id=admin_user_id,
                        command=command,
                    )
                    session.commit()
                    return result.status.value
                except MediaSubmissionResolutionConflict:
                    session.rollback()
                    return "conflict"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(compete, user_ids[1:]))

        assert sorted(outcomes) == ["conflict", "executed"]
        with postgres_session_factory() as session:
            job = session.get(MediaGenerationJob, job_id)
            resolution = (
                session.query(MediaSubmissionResolutionRequest)
                .filter(MediaSubmissionResolutionRequest.job_id == job_id)
                .one()
            )
            approvals = (
                session.query(MediaSubmissionResolutionApproval)
                .filter(
                    MediaSubmissionResolutionApproval.request_id
                    == resolution.id
                )
                .count()
            )
            assert job.status == "submitted"
            assert resolution.status == MediaSubmissionResolutionStatus.EXECUTED
            assert approvals == 2
    finally:
        if user_ids:
            with postgres_session_factory() as session:
                if job_id is not None:
                    resolution_ids = [
                        row[0]
                        for row in session.query(
                            MediaSubmissionResolutionRequest.id
                        )
                        .filter(
                            MediaSubmissionResolutionRequest.job_id == job_id
                        )
                        .all()
                    ]
                    if resolution_ids:
                        session.query(MediaSubmissionResolutionApproval).filter(
                            MediaSubmissionResolutionApproval.request_id.in_(
                                resolution_ids
                            )
                        ).delete(synchronize_session=False)
                    session.query(MediaSubmissionResolutionRequest).filter(
                        MediaSubmissionResolutionRequest.job_id == job_id
                    ).delete(synchronize_session=False)
                    session.query(MediaGenerationAttempt).filter(
                        MediaGenerationAttempt.job_id == job_id
                    ).delete(synchronize_session=False)
                    session.query(MediaGenerationJob).filter(
                        MediaGenerationJob.id == job_id
                    ).delete(synchronize_session=False)
                    session.query(MediaBudgetAccount).filter(
                        MediaBudgetAccount.org_id == org_id
                    ).delete(synchronize_session=False)
                    session.query(VideoStoryboardVersion).filter(
                        VideoStoryboardVersion.org_id == org_id
                    ).delete(synchronize_session=False)
                    session.query(VideoProject).filter(
                        VideoProject.org_id == org_id
                    ).delete(synchronize_session=False)
                    session.query(VideoPersonaVersion).filter(
                        VideoPersonaVersion.org_id == org_id
                    ).delete(synchronize_session=False)
                    session.query(VideoPersona).filter(
                        VideoPersona.org_id == org_id
                    ).delete(synchronize_session=False)
                    session.query(MediaRuntimeRevision).filter(
                        MediaRuntimeRevision.org_id == org_id
                    ).delete(synchronize_session=False)
                session.query(User).filter(User.id.in_(user_ids)).delete(
                    synchronize_session=False
                )
                session.commit()
