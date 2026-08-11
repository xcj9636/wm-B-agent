"""Add fenced media provider reconciliation leases.

Revision ID: 0027_media_reconciliation
Revises: 0026_media_jobs
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0027_media_reconciliation"
down_revision: Union[str, None] = "0026_media_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "media_generation_jobs",
        sa.Column("provider_state", sa.String(30), nullable=True),
    )
    op.add_column(
        "media_generation_jobs",
        sa.Column(
            "reconcile_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "media_generation_jobs",
        sa.Column("next_reconcile_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "media_generation_jobs",
        sa.Column("last_reconciled_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "media_generation_jobs",
        sa.Column(
            "reconciliation_fencing_token",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "media_generation_jobs",
        sa.Column("reconciliation_leased_by", sa.String(100), nullable=True),
    )
    op.add_column(
        "media_generation_jobs",
        sa.Column("reconciliation_lease_until", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "idx_media_job_reconciliation",
        "media_generation_jobs",
        ["status", "next_reconcile_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_media_job_reconciliation",
        table_name="media_generation_jobs",
    )
    op.drop_column("media_generation_jobs", "reconciliation_lease_until")
    op.drop_column("media_generation_jobs", "reconciliation_leased_by")
    op.drop_column(
        "media_generation_jobs",
        "reconciliation_fencing_token",
    )
    op.drop_column("media_generation_jobs", "last_reconciled_at")
    op.drop_column("media_generation_jobs", "next_reconcile_at")
    op.drop_column("media_generation_jobs", "reconcile_count")
    op.drop_column("media_generation_jobs", "provider_state")
