"""Fence stale prospecting workers with a monotonic lease version.

Revision ID: 0009_prospecting_lease_fence
Revises: 0008_prospecting_jobs
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0009_prospecting_lease_fence"
down_revision: Union[str, None] = "0008_prospecting_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "prospecting_jobs",
        sa.Column("lease_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("prospecting_jobs", "lease_version")
