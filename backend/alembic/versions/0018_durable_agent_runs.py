"""Add durable agent run lease and recovery state.

Revision ID: 0018_durable_agent_runs
Revises: 0017_durable_tool_execution
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0018_durable_agent_runs"
down_revision: Union[str, None] = "0017_durable_tool_execution"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Uuid()),
        sa.Column("turn_id", sa.Uuid()),
        sa.Column("use_case", sa.String(50), nullable=False),
        sa.Column("sensitivity", sa.String(20), nullable=False),
        sa.Column("generation_epoch", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("leased_by", sa.String(100)),
        sa.Column("lease_until", sa.DateTime()),
        sa.Column("heartbeat_at", sa.DateTime()),
        sa.Column("effect_state", sa.String(20), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(), nullable=False),
        sa.Column("error_code", sa.String(100)),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "idx_agent_run_status_deadline",
        "agent_runs",
        ["status", "deadline_at"],
    )
    op.create_index(
        "idx_agent_run_status_lease",
        "agent_runs",
        ["status", "lease_until"],
    )
    op.create_index(
        "idx_agent_run_org_created",
        "agent_runs",
        ["org_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("agent_runs")
