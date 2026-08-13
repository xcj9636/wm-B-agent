"""Add provider-bound media usage receipts.

Revision ID: 0030_media_usage_receipts
Revises: 0029_media_callback_inbox
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0030_media_usage_receipts"
down_revision: Union[str, None] = "0029_media_callback_inbox"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "media_provider_usage_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("runtime_revision_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("provider_request_id", sa.String(255), nullable=False),
        sa.Column("model_id", sa.String(255), nullable=False),
        sa.Column("billable_units", sa.Numeric(21, 9), nullable=False),
        sa.Column("pricing_status", sa.String(20), nullable=False),
        sa.Column("unit_price_microusd", sa.BigInteger(), nullable=True),
        sa.Column("cost_microusd", sa.BigInteger(), nullable=True),
        sa.Column("receipt_hash", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "billable_units >= 0",
            name="ck_media_usage_billable_units_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["media_generation_jobs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_revision_id"],
            ["media_runtime_revisions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_media_usage_job"),
        sa.UniqueConstraint(
            "provider",
            "provider_request_id",
            name="uq_media_usage_provider_request",
        ),
    )
    op.create_index(
        "idx_media_usage_runtime_model",
        "media_provider_usage_receipts",
        ["runtime_revision_id", "model_id"],
    )


def downgrade() -> None:
    op.drop_table("media_provider_usage_receipts")
