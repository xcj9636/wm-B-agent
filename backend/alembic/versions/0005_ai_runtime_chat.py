"""Add hot-loadable AI runtime configuration and operator chat.

Revision ID: 0005_ai_runtime_chat
Revises: 0004_outbox_resolution_approvals
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0005_ai_runtime_chat"
down_revision: Union[str, None] = "0004_outbox_resolution_approvals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_runtime_configurations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("backend", sa.String(length=20), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("allowed_providers", sa.JSON(), nullable=False),
        sa.Column("model_aliases", sa.JSON(), nullable=False),
        sa.Column("timeout_seconds", sa.Float(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "ai_chat_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("use_case", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_ai_chat_session_user_updated",
        "ai_chat_sessions",
        ["user_id", "updated_at"],
    )
    op.create_table(
        "ai_chat_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("resolved_model", sa.String(length=255), nullable=True),
        sa.Column("resolved_provider", sa.String(length=100), nullable=True),
        sa.Column("gateway_request_id", sa.String(length=255), nullable=True),
        sa.Column("usage_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"], ["ai_chat_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_ai_chat_message_session_created",
        "ai_chat_messages",
        ["session_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("ai_chat_messages")
    op.drop_table("ai_chat_sessions")
    op.drop_table("ai_runtime_configurations")
