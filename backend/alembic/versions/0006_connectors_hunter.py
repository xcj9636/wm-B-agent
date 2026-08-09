"""Add connector control plane and contact verification history.

Revision ID: 0006_connectors_hunter
Revises: 0005_ai_runtime_chat
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0006_connectors_hunter"
down_revision: Union[str, None] = "0005_ai_runtime_chat"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "connector_configurations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("secret_ref", sa.String(length=500), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("last_status", sa.String(length=30), nullable=False),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "name",
            name="uq_connector_provider_name",
        ),
    )
    op.create_index(
        "idx_connector_provider_enabled",
        "connector_configurations",
        ["provider", "enabled"],
    )
    op.create_table(
        "contact_verifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("legal_restricted", sa.Boolean(), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_contact_verification_customer_verified",
        "contact_verifications",
        ["customer_id", "verified_at"],
    )
    op.create_index(
        "idx_contact_verification_email_status",
        "contact_verifications",
        ["email", "status"],
    )


def downgrade() -> None:
    op.drop_table("contact_verifications")
    op.drop_table("connector_configurations")
