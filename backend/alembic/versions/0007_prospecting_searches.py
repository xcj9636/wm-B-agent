"""Add persistent prospect searches and normalized contact candidates.

Revision ID: 0007_prospecting_searches
Revises: 0006_connectors_hunter
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0007_prospecting_searches"
down_revision: Union[str, None] = "0006_connectors_hunter"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prospecting_searches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("mode", sa.String(length=30), nullable=False),
        sa.Column("query_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("connector_version", sa.Integer(), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_prospecting_search_user_created",
        "prospecting_searches",
        ["user_id", "created_at"],
    )
    op.create_index(
        "idx_prospecting_search_status_created",
        "prospecting_searches",
        ["status", "created_at"],
    )
    op.create_table(
        "prospecting_contacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("search_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=True),
        sa.Column("last_name", sa.String(length=100), nullable=True),
        sa.Column("company", sa.String(length=255), nullable=True),
        sa.Column("domain", sa.String(length=255), nullable=True),
        sa.Column("position", sa.String(length=255), nullable=True),
        sa.Column("department", sa.String(length=50), nullable=True),
        sa.Column("seniority", sa.String(length=50), nullable=True),
        sa.Column("contact_type", sa.String(length=30), nullable=True),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("decision_maker", sa.Boolean(), nullable=True),
        sa.Column("verification_status", sa.String(length=30), nullable=False),
        sa.Column("verification_date", sa.String(length=20), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("imported_customer_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["search_id"], ["prospecting_searches.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["imported_customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "search_id", "email", name="uq_prospecting_search_email"
        ),
    )
    op.create_index(
        "idx_prospecting_contact_search",
        "prospecting_contacts",
        ["search_id"],
    )
    op.create_index(
        "idx_prospecting_contact_email",
        "prospecting_contacts",
        ["email"],
    )
    op.create_index(
        "idx_prospecting_contact_imported",
        "prospecting_contacts",
        ["imported_customer_id"],
    )


def downgrade() -> None:
    op.drop_table("prospecting_contacts")
    op.drop_table("prospecting_searches")
