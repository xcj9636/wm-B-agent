"""Add immutable media pricing snapshots.

Revision ID: 0031_media_pricing_snapshots
Revises: 0030_media_usage_receipts
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0031_media_pricing_snapshots"
down_revision: Union[str, None] = "0030_media_usage_receipts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMPTY_SNAPSHOT_HASH = (
    "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
)


def upgrade() -> None:
    op.add_column(
        "media_runtime_revisions",
        sa.Column(
            "pricing_snapshot",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "media_runtime_revisions",
        sa.Column(
            "pricing_snapshot_hash",
            sa.String(64),
            nullable=False,
            server_default=EMPTY_SNAPSHOT_HASH,
        ),
    )
    op.add_column(
        "media_provider_usage_receipts",
        sa.Column("pricing_snapshot_hash", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("media_provider_usage_receipts", "pricing_snapshot_hash")
    op.drop_column("media_runtime_revisions", "pricing_snapshot_hash")
    op.drop_column("media_runtime_revisions", "pricing_snapshot")
