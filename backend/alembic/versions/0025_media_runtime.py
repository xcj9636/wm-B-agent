"""Add immutable media-provider runtime revisions.

Revision ID: 0025_media_runtime
Revises: 0024_video_personas
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0025_media_runtime"
down_revision: Union[str, None] = "0024_video_personas"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "media_runtime_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("enabled_modes", sa.JSON(), nullable=False),
        sa.Column("model_aliases", sa.JSON(), nullable=False),
        sa.Column("capability_snapshot", sa.JSON(), nullable=False),
        sa.Column("capability_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_media_runtime_revision_positive",
        ),
        sa.CheckConstraint(
            "provider = 'fal'",
            name="ck_media_runtime_provider_supported",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "revision",
            name="uq_media_runtime_org_revision",
        ),
    )
    op.create_index(
        "idx_media_runtime_revision_org_created",
        "media_runtime_revisions",
        ["org_id", "created_at"],
    )
    op.create_table(
        "media_runtime_probe_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("ready", sa.Boolean(), nullable=False),
        sa.Column("reachable", sa.Boolean(), nullable=False),
        sa.Column("issues", sa.JSON(), nullable=False),
        sa.Column("capability_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("probed_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["media_runtime_revisions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["probed_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_media_runtime_probe_revision_created",
        "media_runtime_probe_records",
        ["revision_id", "created_at"],
    )
    op.create_table(
        "media_runtime_activations",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("active_revision_id", sa.Uuid(), nullable=False),
        sa.Column("activated_by_user_id", sa.Integer(), nullable=False),
        sa.Column("activated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["active_revision_id"],
            ["media_runtime_revisions.id"],
        ),
        sa.ForeignKeyConstraint(["activated_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("org_id"),
    )


def downgrade() -> None:
    op.drop_table("media_runtime_activations")
    op.drop_table("media_runtime_probe_records")
    op.drop_table("media_runtime_revisions")
