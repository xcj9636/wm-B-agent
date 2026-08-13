"""Add two-person media submission-unknown resolution.

Revision ID: 0028_media_submission_resolution
Revises: 0027_media_reconciliation
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0028_media_submission_resolution"
down_revision: Union[str, None] = "0027_media_reconciliation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    action = sa.Enum(
        "confirmed_submitted",
        "confirmed_not_submitted",
        name="media_submission_resolution_action",
    )
    status = sa.Enum(
        "pending",
        "executed",
        name="media_submission_resolution_status",
    )
    op.create_table(
        "media_submission_resolution_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("submission_unknown_version", sa.DateTime(), nullable=False),
        sa.Column("action", action, nullable=False),
        sa.Column("evidence_reference", sa.String(128), nullable=False),
        sa.Column("provider_request_id", sa.String(128), nullable=True),
        sa.Column("status", status, nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("executed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["media_generation_jobs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "submission_unknown_version",
            name="uq_media_submission_resolution_cycle",
        ),
    )
    op.create_index(
        "idx_media_submission_resolution_job_status",
        "media_submission_resolution_requests",
        ["job_id", "status"],
    )
    op.create_table(
        "media_submission_resolution_approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("approved_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["media_submission_resolution_requests.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "request_id",
            "approved_by_user_id",
            name="uq_media_submission_resolution_approver",
        ),
    )
    op.create_index(
        "idx_media_submission_resolution_approval_request",
        "media_submission_resolution_approvals",
        ["request_id"],
    )


def downgrade() -> None:
    op.drop_table("media_submission_resolution_approvals")
    op.drop_table("media_submission_resolution_requests")
    bind = op.get_bind()
    sa.Enum(name="media_submission_resolution_status").drop(
        bind,
        checkfirst=True,
    )
    sa.Enum(name="media_submission_resolution_action").drop(
        bind,
        checkfirst=True,
    )
