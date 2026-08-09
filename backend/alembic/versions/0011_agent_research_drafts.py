"""Add sourced agent research jobs and evidence-bound outreach drafts.

Revision ID: 0011_agent_research_drafts
Revises: 0010_prospecting_icp_scores
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0011_agent_research_drafts"
down_revision: Union[str, None] = "0010_prospecting_icp_scores"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_research_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("objective", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("profile_evidence_json", sa.JSON(), nullable=False),
        sa.Column("market_signals_json", sa.JSON(), nullable=False),
        sa.Column("missing_fields_json", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_agent_research_user_status",
        "agent_research_jobs",
        ["user_id", "status", "updated_at"],
    )
    op.create_index(
        "idx_agent_research_customer",
        "agent_research_jobs",
        ["customer_id", "updated_at"],
    )
    op.create_table(
        "research_outreach_drafts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("research_job_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=30), nullable=False),
        sa.Column("language", sa.String(length=20), nullable=False),
        sa.Column("goal", sa.String(length=500), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("personalization_points_json", sa.JSON(), nullable=False),
        sa.Column("evidence_ids_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("research_version", sa.Integer(), nullable=False),
        sa.Column("resolved_model", sa.String(length=255), nullable=True),
        sa.Column("resolved_provider", sa.String(length=100), nullable=True),
        sa.Column("gateway_request_id", sa.String(length=255), nullable=True),
        sa.Column("usage_json", sa.JSON(), nullable=False),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["research_job_id"], ["agent_research_jobs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_research_draft_user_idempotency",
        ),
    )
    op.create_index(
        "idx_research_draft_job_status",
        "research_outreach_drafts",
        ["research_job_id", "status"],
    )


def downgrade() -> None:
    op.drop_table("research_outreach_drafts")
    op.drop_table("agent_research_jobs")
