"""Add quarantined media assets, upload intents, lineage, and consent.

Revision ID: 0022_media_assets
Revises: 0021_llm_attempt_latency
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0022_media_assets"
down_revision: Union[str, None] = "0021_llm_attempt_latency"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "media_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("storage_backend", sa.String(50), nullable=False),
        sa.Column("storage_key", sa.String(1000), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sensitivity", sa.String(20), nullable=False),
        sa.Column("quarantined", sa.Boolean(), nullable=False),
        sa.Column("scan_status", sa.String(30), nullable=False),
        sa.Column("rights_status", sa.String(30), nullable=False),
        sa.Column("consent_required", sa.Boolean(), nullable=False),
        sa.Column("consent_status", sa.String(30), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Integer()),
        sa.Column("reviewed_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime()),
        sa.CheckConstraint("size_bytes > 0", name="ck_media_asset_size_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("idx_media_asset_org_created", "media_assets", ["org_id", "created_at"])
    op.create_index("idx_media_asset_org_hash", "media_assets", ["org_id", "sha256"])
    op.create_index("idx_media_asset_quarantine", "media_assets", ["quarantined", "scan_status"])

    op.create_table(
        "media_upload_intents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(1000), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("expected_mime_type", sa.String(255), nullable=False),
        sa.Column("expected_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("expected_sha256", sa.String(64), nullable=False),
        sa.Column("sensitivity", sa.String(20), nullable=False),
        sa.Column("consent_required", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("asset_id", sa.Uuid()),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime()),
        sa.ForeignKeyConstraint(["asset_id"], ["media_assets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
        sa.UniqueConstraint("org_id", "actor_user_id", "idempotency_key", name="uq_media_upload_scope_idempotency"),
    )
    op.create_index("idx_media_upload_status_expiry", "media_upload_intents", ["status", "expires_at"])
    op.create_index("idx_media_upload_org_created", "media_upload_intents", ["org_id", "created_at"])

    op.create_table(
        "media_asset_relations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("parent_asset_id", sa.Uuid(), nullable=False),
        sa.Column("child_asset_id", sa.Uuid(), nullable=False),
        sa.Column("relation_type", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("parent_asset_id <> child_asset_id", name="ck_media_asset_relation_not_self"),
        sa.ForeignKeyConstraint(["child_asset_id"], ["media_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_asset_id"], ["media_assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("parent_asset_id", "child_asset_id", "relation_type", name="uq_media_asset_relation"),
    )
    op.create_index("idx_media_asset_relation_child", "media_asset_relations", ["child_asset_id"])

    op.create_table(
        "media_consent_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("subject_ref", sa.String(255), nullable=False),
        sa.Column("purpose", sa.String(500), nullable=False),
        sa.Column("regions", sa.JSON(), nullable=False),
        sa.Column("media_types", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("valid_from", sa.DateTime(), nullable=False),
        sa.Column("valid_until", sa.DateTime()),
        sa.Column("evidence_asset_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("revoked_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("valid_until IS NULL OR valid_until > valid_from", name="ck_media_consent_valid_range"),
        sa.ForeignKeyConstraint(["evidence_asset_id"], ["media_assets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_media_consent_org_status", "media_consent_records", ["org_id", "status"])


def downgrade() -> None:
    op.drop_table("media_consent_records")
    op.drop_table("media_asset_relations")
    op.drop_table("media_upload_intents")
    op.drop_table("media_assets")
