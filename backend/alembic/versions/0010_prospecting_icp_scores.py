"""Add explainable ICP profiles and prospect scores.

Revision ID: 0010_prospecting_icp_scores
Revises: 0009_prospecting_lease_fence
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0010_prospecting_icp_scores"
down_revision: Union[str, None] = "0009_prospecting_lease_fence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prospecting_icp_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("target_departments_json", sa.JSON(), nullable=False),
        sa.Column("target_seniorities_json", sa.JSON(), nullable=False),
        sa.Column("title_keywords_json", sa.JSON(), nullable=False),
        sa.Column("preferred_contact_types_json", sa.JSON(), nullable=False),
        sa.Column("weights_json", sa.JSON(), nullable=False),
        sa.Column("minimum_score", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_table(
        "prospecting_contact_scores",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("base_score", sa.Float(), nullable=False),
        sa.Column("factor_scores_json", sa.JSON(), nullable=False),
        sa.Column("reasons_json", sa.JSON(), nullable=False),
        sa.Column("missing_signals_json", sa.JSON(), nullable=False),
        sa.Column("review_status", sa.String(length=30), nullable=False),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("score_adjustment", sa.Integer(), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("scored_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["prospecting_contacts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["prospecting_icp_profiles.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("contact_id"),
    )
    op.create_index(
        "idx_prospecting_score_profile_score",
        "prospecting_contact_scores",
        ["profile_id", "base_score"],
    )
    op.create_index(
        "idx_prospecting_score_review",
        "prospecting_contact_scores",
        ["review_status", "reviewed_at"],
    )


def downgrade() -> None:
    op.drop_table("prospecting_contact_scores")
    op.drop_table("prospecting_icp_profiles")
