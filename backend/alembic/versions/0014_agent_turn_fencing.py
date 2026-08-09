"""Add durable agent turns and chat generation fencing.

Revision ID: 0014_agent_turn_fencing
Revises: 0013_secure_mailbox_oauth
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0014_agent_turn_fencing"
down_revision: Union[str, None] = "0013_secure_mailbox_oauth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ai_chat_sessions") as batch:
        batch.add_column(
            sa.Column(
                "generation_epoch",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )

    op.create_table(
        "agent_turns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("generation_epoch", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("user_message_id", sa.Uuid()),
        sa.Column("assistant_message_id", sa.Uuid()),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime()),
        sa.ForeignKeyConstraint(
            ["session_id"], ["ai_chat_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_message_id"], ["ai_chat_messages.id"]),
        sa.ForeignKeyConstraint(
            ["assistant_message_id"], ["ai_chat_messages.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id",
            "sequence",
            name="uq_agent_turn_session_sequence",
        ),
        sa.UniqueConstraint(
            "session_id",
            "idempotency_key",
            name="uq_agent_turn_session_idempotency",
        ),
    )
    op.create_index(
        "idx_agent_turn_session_status",
        "agent_turns",
        ["session_id", "status"],
    )
    op.create_index(
        "uq_agent_turn_active_session",
        "agent_turns",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
        sqlite_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_table("agent_turns")
    with op.batch_alter_table("ai_chat_sessions") as batch:
        batch.drop_column("generation_epoch")
