"""Add evidence-backed media review records.

Revision ID: 0023_media_review_evidence
Revises: 0022_media_assets
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0023_media_review_evidence"
down_revision: Union[str, None] = "0022_media_assets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "media_scan_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("scanner", sa.String(100), nullable=False),
        sa.Column("scanner_version", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("asset_sha256", sa.String(64), nullable=False),
        sa.Column("findings_json", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["media_assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_media_scan_asset_created", "media_scan_reports", ["asset_id", "created_at"])
    op.create_index("idx_media_scan_org_status", "media_scan_reports", ["org_id", "status"])

    op.create_table(
        "media_rights_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("basis", sa.String(100), nullable=False),
        sa.Column("territories", sa.JSON(), nullable=False),
        sa.Column("channels", sa.JSON(), nullable=False),
        sa.Column("source_ref", sa.String(500), nullable=False),
        sa.Column("valid_from", sa.DateTime(), nullable=False),
        sa.Column("valid_until", sa.DateTime()),
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=False),
        sa.Column("revoked_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("valid_until IS NULL OR valid_until > valid_from", name="ck_media_rights_valid_range"),
        sa.ForeignKeyConstraint(["asset_id"], ["media_assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_media_rights_asset_created", "media_rights_records", ["asset_id", "created_at"])
    op.create_index("idx_media_rights_org_status", "media_rights_records", ["org_id", "status"])

    with op.batch_alter_table("media_consent_records") as batch_op:
        batch_op.add_column(sa.Column("asset_id", sa.Uuid(), nullable=False))
        batch_op.create_foreign_key(
            "fk_media_consent_asset",
            "media_assets",
            ["asset_id"],
            ["id"],
            ondelete="CASCADE",
        )

    with op.batch_alter_table("media_assets") as batch_op:
        batch_op.add_column(sa.Column("scan_report_id", sa.Uuid()))
        batch_op.add_column(sa.Column("rights_record_id", sa.Uuid()))
        batch_op.add_column(sa.Column("consent_record_id", sa.Uuid()))
        batch_op.create_foreign_key("fk_media_asset_scan_report", "media_scan_reports", ["scan_report_id"], ["id"])
        batch_op.create_foreign_key("fk_media_asset_rights_record", "media_rights_records", ["rights_record_id"], ["id"])
        batch_op.create_foreign_key("fk_media_asset_consent_record", "media_consent_records", ["consent_record_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("media_assets") as batch_op:
        batch_op.drop_constraint("fk_media_asset_consent_record", type_="foreignkey")
        batch_op.drop_constraint("fk_media_asset_rights_record", type_="foreignkey")
        batch_op.drop_constraint("fk_media_asset_scan_report", type_="foreignkey")
        batch_op.drop_column("consent_record_id")
        batch_op.drop_column("rights_record_id")
        batch_op.drop_column("scan_report_id")
    with op.batch_alter_table("media_consent_records") as batch_op:
        batch_op.drop_constraint("fk_media_consent_asset", type_="foreignkey")
        batch_op.drop_column("asset_id")
    op.drop_table("media_rights_records")
    op.drop_table("media_scan_reports")
