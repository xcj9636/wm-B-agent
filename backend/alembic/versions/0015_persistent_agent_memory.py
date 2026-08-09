"""Add persistent three-tier agent memory and invalidation epochs.

Revision ID: 0015_persistent_agent_memory
Revises: 0014_agent_turn_fencing
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0015_persistent_agent_memory"
down_revision: Union[str, None] = "0014_agent_turn_fencing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_memory_epochs",
        sa.Column("scope_key", sa.String(64), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer()),
        sa.Column("session_id", sa.Uuid()),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("scope_key"),
    )
    op.create_index(
        "idx_agent_memory_epoch_org",
        "agent_memory_epochs",
        ["org_id"],
    )

    op.create_table(
        "agent_memories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope_key", sa.String(64), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer()),
        sa.Column("session_id", sa.Uuid()),
        sa.Column("tier", sa.String(20), nullable=False),
        sa.Column("kind", sa.String(100), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("source_type", sa.String(60), nullable=False),
        sa.Column("source_ref", sa.String(500), nullable=False),
        sa.Column("sensitivity", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("correction_of", sa.Uuid()),
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
        sa.ForeignKeyConstraint(["correction_of"], ["agent_memories.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_agent_memory_scope_status_tier",
        "agent_memories",
        ["scope_key", "status", "tier"],
    )
    op.create_index(
        "idx_agent_memory_org_created",
        "agent_memories",
        ["org_id", "created_at"],
    )

    op.create_table(
        "agent_memory_purge_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("memory_id", sa.Uuid(), nullable=False),
        sa.Column("tombstone_epoch", sa.Integer(), nullable=False),
        sa.Column("targets", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime()),
        sa.ForeignKeyConstraint(["memory_id"], ["agent_memories.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_agent_memory_purge_status_created",
        "agent_memory_purge_jobs",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("agent_memory_purge_jobs")
    op.drop_table("agent_memories")
    op.drop_table("agent_memory_epochs")
