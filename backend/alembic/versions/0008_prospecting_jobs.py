"""Add resumable multi-domain prospecting jobs.

Revision ID: 0008_prospecting_jobs
Revises: 0007_prospecting_searches
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0008_prospecting_jobs"
down_revision: Union[str, None] = "0007_prospecting_searches"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prospecting_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("connector_version", sa.Integer(), nullable=False),
        sa.Column("page_size", sa.Integer(), nullable=False),
        sa.Column("max_pages_per_domain", sa.Integer(), nullable=False),
        sa.Column("request_budget", sa.Integer(), nullable=False),
        sa.Column("requests_used", sa.Integer(), nullable=False),
        sa.Column("provider_remaining", sa.Float(), nullable=True),
        sa.Column("provider_usage_unit", sa.String(length=30), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("leased_by", sa.String(length=100), nullable=True),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_prospecting_job_user_created",
        "prospecting_jobs",
        ["user_id", "created_at"],
    )
    op.create_index(
        "idx_prospecting_job_status_due",
        "prospecting_jobs",
        ["status", "next_attempt_at"],
    )
    op.create_index(
        "idx_prospecting_job_lease",
        "prospecting_jobs",
        ["status", "lease_until"],
    )

    op.create_table(
        "prospecting_job_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("search_id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("next_offset", sa.Integer(), nullable=False),
        sa.Column("pages_completed", sa.Integer(), nullable=False),
        sa.Column("requests_used", sa.Integer(), nullable=False),
        sa.Column("contacts_found", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"], ["prospecting_jobs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["search_id"], ["prospecting_searches.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "domain", name="uq_prospecting_job_domain"),
        sa.UniqueConstraint("search_id"),
    )
    op.create_index(
        "idx_prospecting_job_item_status_due",
        "prospecting_job_items",
        ["job_id", "status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_table("prospecting_job_items")
    op.drop_table("prospecting_jobs")
