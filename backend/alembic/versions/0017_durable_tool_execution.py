"""Add durable, fenced agent tool execution state.

Revision ID: 0017_durable_tool_execution
Revises: 0016_persistent_knowledge
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0017_durable_tool_execution"
down_revision: Union[str, None] = "0016_persistent_knowledge"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_tool_executions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(69), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("generation_epoch", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("tool_version", sa.String(30), nullable=False),
        sa.Column("risk", sa.String(20), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("purpose", sa.String(255), nullable=False),
        sa.Column("approval_required", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("approved_by_user_id", sa.Integer()),
        sa.Column("approval_entitlements_hash", sa.String(64)),
        sa.Column("approved_at", sa.DateTime()),
        sa.Column("result_json", sa.JSON()),
        sa.Column("result_hash", sa.String(64)),
        sa.Column("error_code", sa.String(100)),
        sa.Column("outbox_event_id", sa.Uuid()),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime()),
        sa.ForeignKeyConstraint(["outbox_event_id"], ["outbox_events.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("outbox_event_id"),
    )
    op.create_index(
        "idx_agent_tool_run_status",
        "agent_tool_executions",
        ["run_id", "status"],
    )
    op.create_index(
        "idx_agent_tool_turn_status",
        "agent_tool_executions",
        ["turn_id", "status"],
    )
    op.create_index(
        "idx_agent_tool_org_created",
        "agent_tool_executions",
        ["org_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("agent_tool_executions")
