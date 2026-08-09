"""Add approval-gated agent outreach deliveries.

Revision ID: 0012_agent_outreach_deliveries
Revises: 0011_agent_research_drafts
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0012_agent_outreach_deliveries"
down_revision: Union[str, None] = "0011_agent_research_drafts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_outreach_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=30), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("account_name", sa.String(length=100), nullable=False),
        sa.Column("sender", sa.String(length=255), nullable=False),
        sa.Column("recipient", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("research_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(), nullable=False),
        sa.Column("outbox_event_id", sa.Uuid(), nullable=True),
        sa.Column("external_message_id", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"], ["research_outreach_drafts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["outbox_event_id"], ["outbox_events.id"]),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_agent_delivery_user_idempotency",
        ),
    )
    op.create_index(
        "idx_agent_delivery_user_status",
        "agent_outreach_deliveries",
        ["user_id", "status", "updated_at"],
    )
    op.create_index(
        "idx_agent_delivery_account_schedule",
        "agent_outreach_deliveries",
        ["account_id", "scheduled_at"],
    )


def downgrade() -> None:
    op.drop_table("agent_outreach_deliveries")
