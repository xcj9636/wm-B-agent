"""Add durable media generation jobs and budget ledger.

Revision ID: 0026_media_jobs
Revises: 0025_media_runtime
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0026_media_jobs"
down_revision: Union[str, None] = "0025_media_runtime"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "media_budget_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("limit_microusd", sa.BigInteger(), nullable=False),
        sa.Column("reserved_microusd", sa.BigInteger(), nullable=False),
        sa.Column("spent_microusd", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "limit_microusd >= 0 AND reserved_microusd >= 0 "
            "AND spent_microusd >= 0",
            name="ck_media_budget_nonnegative",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "period_start",
            name="uq_media_budget_org_period",
        ),
    )
    op.create_table(
        "media_generation_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("storyboard_version_id", sa.Uuid(), nullable=False),
        sa.Column("shot_id", sa.Uuid(), nullable=False),
        sa.Column("runtime_revision_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("intent_hash", sa.String(64), nullable=False),
        sa.Column("payload_ref", sa.String(1000), nullable=False),
        sa.Column("mode", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("model_id", sa.String(255), nullable=False),
        sa.Column("sensitivity", sa.String(20), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("effect_state", sa.String(20), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("leased_by", sa.String(100), nullable=True),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("reserved_cost_microusd", sa.BigInteger(), nullable=False),
        sa.Column("estimate_hash", sa.String(64), nullable=False),
        sa.Column("budget_period_start", sa.Date(), nullable=False),
        sa.Column("actual_cost_microusd", sa.BigInteger(), nullable=True),
        sa.Column("budget_finalized_at", sa.DateTime(), nullable=True),
        sa.Column("provider_request_id", sa.String(255), nullable=True),
        sa.Column("result_ref", sa.String(1000), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("deadline_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "reserved_cost_microusd >= 0",
            name="ck_media_job_reserved_cost_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["video_projects.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_revision_id"],
            ["media_runtime_revisions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["storyboard_version_id"],
            ["video_storyboard_versions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "owner_user_id",
            "idempotency_key",
            name="uq_media_job_scope_idempotency",
        ),
    )
    op.create_index(
        "idx_media_job_dispatch",
        "media_generation_jobs",
        ["status", "created_at"],
    )
    op.create_index(
        "idx_media_job_org_created",
        "media_generation_jobs",
        ["org_id", "created_at"],
    )
    op.create_table(
        "media_generation_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("model_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("effect_state", sa.String(20), nullable=False),
        sa.Column("provider_request_id", sa.String(255), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "attempt_number > 0",
            name="ck_media_attempt_number_positive",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["media_generation_jobs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "attempt_number",
            name="uq_media_attempt_job_number",
        ),
        sa.UniqueConstraint(
            "provider",
            "provider_request_id",
            name="uq_media_attempt_provider_request",
        ),
    )
    op.create_table(
        "media_generation_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("data_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["media_generation_jobs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "sequence",
            name="uq_media_event_job_sequence",
        ),
    )
    op.create_index(
        "idx_media_event_job_created",
        "media_generation_events",
        ["job_id", "created_at"],
    )
    op.create_table(
        "media_budget_ledger_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("entry_type", sa.String(30), nullable=False),
        sa.Column("amount_microusd", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("estimate_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "amount_microusd >= 0",
            name="ck_media_ledger_amount_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["media_generation_jobs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "idx_media_ledger_org_period",
        "media_budget_ledger_entries",
        ["org_id", "period_start"],
    )


def downgrade() -> None:
    op.drop_table("media_budget_ledger_entries")
    op.drop_table("media_generation_events")
    op.drop_table("media_generation_attempts")
    op.drop_table("media_generation_jobs")
    op.drop_table("media_budget_accounts")
