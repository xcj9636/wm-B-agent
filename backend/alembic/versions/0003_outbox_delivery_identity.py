"""Store external provider identity for completed outbox delivery.

Revision ID: 0003_outbox_delivery_identity
Revises: 0002_reliable_execution
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0003_outbox_delivery_identity"
down_revision: Union[str, None] = "0002_reliable_execution"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "outbox_events",
        sa.Column("external_message_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("outbox_events", "external_message_id")
