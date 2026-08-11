"""Add immutable, approvable video persona revisions.

Revision ID: 0024_video_personas
Revises: 0023_media_review_evidence
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0024_video_personas"
down_revision: Union[str, None] = "0023_media_review_evidence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "video_personas",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("retired_at", sa.DateTime()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_video_persona_org_created",
        "video_personas",
        ["org_id", "created_at"],
    )

    op.create_table(
        "video_persona_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("persona_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("spec_json", sa.JSON(), nullable=False),
        sa.Column("spec_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("approved_by_user_id", sa.Integer()),
        sa.Column("approved_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_video_persona_revision_positive",
        ),
        sa.ForeignKeyConstraint(
            ["persona_id"],
            ["video_personas.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "persona_id",
            "revision",
            name="uq_video_persona_revision",
        ),
        sa.UniqueConstraint(
            "org_id",
            "created_by_user_id",
            "idempotency_key",
            name="uq_video_persona_scope_idempotency",
        ),
    )
    op.create_index(
        "idx_video_persona_version_persona_status",
        "video_persona_versions",
        ["persona_id", "status", "revision"],
    )
    op.create_index(
        "idx_video_persona_version_org_created",
        "video_persona_versions",
        ["org_id", "created_at"],
    )

    op.create_table(
        "video_projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("brief_json", sa.JSON(), nullable=False),
        sa.Column("brief_hash", sa.String(64), nullable=False),
        sa.Column("persona_version_id", sa.Uuid(), nullable=False),
        sa.Column("persona_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("persona_spec_hash", sa.String(64), nullable=False),
        sa.Column("sensitivity", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["persona_version_id"],
            ["video_persona_versions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "owner_user_id",
            "idempotency_key",
            name="uq_video_project_scope_idempotency",
        ),
    )
    op.create_index(
        "idx_video_project_org_created",
        "video_projects",
        ["org_id", "created_at"],
    )
    op.create_index(
        "idx_video_project_persona",
        "video_projects",
        ["persona_version_id"],
    )

    op.create_table(
        "video_project_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_record_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("document_version", sa.Integer(), nullable=False),
        sa.Column("source_ref", sa.String(500), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("authority", sa.String(60), nullable=False),
        sa.Column("sensitivity", sa.String(20), nullable=False),
        sa.Column("acl_policy_version", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("added_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["knowledge_record_id"],
            ["knowledge_documents.record_id"],
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["video_projects.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "knowledge_record_id",
            name="uq_video_project_evidence_record",
        ),
    )
    op.create_index(
        "idx_video_project_evidence_project",
        "video_project_evidence",
        ["project_id"],
    )

    op.create_table(
        "video_storyboard_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("storyboard_json", sa.JSON(), nullable=False),
        sa.Column("storyboard_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("approved_by_user_id", sa.Integer()),
        sa.Column("approved_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_video_storyboard_revision_positive",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["video_projects.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "revision",
            name="uq_video_storyboard_revision",
        ),
        sa.UniqueConstraint(
            "org_id",
            "created_by_user_id",
            "idempotency_key",
            name="uq_video_storyboard_scope_idempotency",
        ),
    )
    op.create_index(
        "idx_video_storyboard_project_status",
        "video_storyboard_versions",
        ["project_id", "status", "revision"],
    )


def downgrade() -> None:
    op.drop_table("video_storyboard_versions")
    op.drop_table("video_project_evidence")
    op.drop_table("video_projects")
    op.drop_table("video_persona_versions")
    op.drop_table("video_personas")
