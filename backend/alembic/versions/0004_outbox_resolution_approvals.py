"""Add evidence-backed two-person outbox resolution approvals.

Revision ID: 0004_outbox_resolution_approvals
Revises: 0003_outbox_delivery_identity
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0004_outbox_resolution_approvals"
down_revision: Union[str, None] = "0003_outbox_delivery_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


resolution_action = sa.Enum(
    "confirmed_not_sent",
    "confirmed_sent",
    name="outbox_resolution_action",
)
resolution_status = sa.Enum(
    "pending",
    "executed",
    name="outbox_resolution_status",
)


def upgrade() -> None:
    op.create_table(
        "outbox_resolution_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("dead_letter_version", sa.DateTime(), nullable=False),
        sa.Column("action", resolution_action, nullable=False),
        sa.Column("evidence_reference", sa.String(length=128), nullable=False),
        sa.Column("external_message_id", sa.String(length=255), nullable=True),
        sa.Column("status", resolution_status, nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("executed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["outbox_events.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_id",
            "dead_letter_version",
            name="uq_outbox_resolution_cycle",
        ),
    )
    op.create_index(
        "idx_outbox_resolution_event_status",
        "outbox_resolution_requests",
        ["event_id", "status"],
    )

    op.create_table(
        "outbox_resolution_approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("approved_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["outbox_resolution_requests.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "request_id",
            "approved_by_user_id",
            name="uq_outbox_resolution_approver",
        ),
    )
    op.create_index(
        "idx_outbox_resolution_approval_request",
        "outbox_resolution_approvals",
        ["request_id"],
    )


def downgrade() -> None:
    op.drop_table("outbox_resolution_approvals")
    op.drop_table("outbox_resolution_requests")

    if op.get_bind().dialect.name == "postgresql":
        resolution_status.drop(op.get_bind(), checkfirst=True)
        resolution_action.drop(op.get_bind(), checkfirst=True)

