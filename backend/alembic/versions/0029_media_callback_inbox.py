"""Add authenticated media callback inbox.

Revision ID: 0029_media_callback_inbox
Revises: 0028_media_submission_resolution
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0029_media_callback_inbox"
down_revision: Union[str, None] = "0028_media_submission_resolution"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "media_callback_inbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("provider_account_ref_hash", sa.String(64), nullable=False),
        sa.Column("provider_request_id", sa.String(255), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("body_sha256", sa.String(64), nullable=False),
        sa.Column("delivery_hint", sa.String(20), nullable=False),
        sa.Column("signature_timestamp", sa.DateTime(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["media_generation_jobs.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "provider_account_ref_hash",
            "provider_request_id",
            name="uq_media_callback_provider_account_request",
        ),
    )
    op.create_index(
        "idx_media_callback_job_received",
        "media_callback_inbox",
        ["job_id", "received_at"],
    )


def downgrade() -> None:
    op.drop_table("media_callback_inbox")
