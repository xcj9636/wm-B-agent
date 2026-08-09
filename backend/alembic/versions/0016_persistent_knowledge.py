"""Add versioned tenant knowledge documents, chunks, and ACL grants.

Revision ID: 0016_persistent_knowledge
Revises: 0015_persistent_agent_memory
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0016_persistent_knowledge"
down_revision: Union[str, None] = "0015_persistent_agent_memory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_documents",
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_ref", sa.String(500), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("authority", sa.String(60), nullable=False),
        sa.Column("sensitivity", sa.String(20), nullable=False),
        sa.Column("acl_policy_version", sa.String(64), nullable=False),
        sa.Column("index_version", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("record_id"),
        sa.UniqueConstraint(
            "document_id",
            "version",
            name="uq_knowledge_document_version",
        ),
    )
    op.create_index(
        "idx_knowledge_document_org_status_source",
        "knowledge_documents",
        ["org_id", "status", "source_ref"],
    )

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_record_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.String(100), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.String(600), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["document_record_id"],
            ["knowledge_documents.record_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_record_id",
            "chunk_id",
            name="uq_knowledge_chunk_document_chunk",
        ),
    )
    op.create_index(
        "idx_knowledge_chunk_document",
        "knowledge_chunks",
        ["document_record_id"],
    )

    op.create_table(
        "knowledge_document_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_record_id", sa.Uuid(), nullable=False),
        sa.Column("principal_type", sa.String(20), nullable=False),
        sa.Column("principal_value", sa.String(100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["document_record_id"],
            ["knowledge_documents.record_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_record_id",
            "principal_type",
            "principal_value",
            name="uq_knowledge_document_grant",
        ),
    )
    op.create_index(
        "idx_knowledge_grant_document_principal",
        "knowledge_document_grants",
        ["document_record_id", "principal_type", "principal_value"],
    )


def downgrade() -> None:
    op.drop_table("knowledge_document_grants")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_documents")
