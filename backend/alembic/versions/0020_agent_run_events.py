"""Add durable ordered agent run events.

Revision ID: 0020_agent_run_events
Revises: 0019_fenced_llm_invocations
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0020_agent_run_events"
down_revision: Union[str, None] = "0019_fenced_llm_invocations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "event_sequence",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "event_bytes",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )

    op.create_table(
        "agent_run_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("data_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "sequence",
            name="uq_agent_run_event_sequence",
        ),
    )
    op.create_index(
        "idx_agent_run_event_replay",
        "agent_run_events",
        ["run_id", "sequence"],
    )
    op.create_index(
        "idx_agent_run_event_expiry",
        "agent_run_events",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_table("agent_run_events")
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.drop_column("event_bytes")
        batch_op.drop_column("event_sequence")
