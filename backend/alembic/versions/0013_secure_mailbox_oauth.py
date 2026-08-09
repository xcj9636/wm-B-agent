"""Add secure mailbox OAuth sessions and secret references.

Revision ID: 0013_secure_mailbox_oauth
Revises: 0012_agent_outreach_deliveries
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0013_secure_mailbox_oauth"
down_revision: Union[str, None] = "0012_agent_outreach_deliveries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.add_column(sa.Column("credential_secret_ref", sa.String(1024)))
        batch.add_column(sa.Column("oauth_subject", sa.String(255)))
        batch.add_column(sa.Column("oauth_scopes_json", sa.JSON()))
        batch.add_column(sa.Column("token_expires_at", sa.DateTime()))
        batch.add_column(
            sa.Column(
                "connection_status",
                sa.String(30),
                nullable=False,
                server_default="reconnect_required",
            )
        )
        batch.add_column(
            sa.Column(
                "credential_version",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(sa.Column("last_verified_at", sa.DateTime()))
        batch.add_column(sa.Column("last_error_code", sa.String(100)))
        batch.drop_constraint("uq_account_email_type", type_="unique")
        batch.create_unique_constraint(
            "uq_account_user_email_type",
            ["user_id", "email", "account_type"],
        )

    # Existing embedded tokens are intentionally not copied into the new store.
    # Operators must reconnect each mailbox through OAuth.
    op.execute(
        "UPDATE accounts SET credentials_json = NULL, is_verified = false, "
        "connection_status = 'reconnect_required' "
        "WHERE account_type IN ('gmail', 'outlook')"
    )

    op.create_table(
        "mailbox_oauth_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("code_verifier_ref", sa.String(1024), nullable=False),
        sa.Column("return_to", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime()),
        sa.Column("error_code", sa.String(100)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_hash"),
    )
    op.create_index(
        "ix_mailbox_oauth_sessions_state_hash",
        "mailbox_oauth_sessions",
        ["state_hash"],
    )
    op.create_index(
        "idx_mailbox_oauth_user_status",
        "mailbox_oauth_sessions",
        ["user_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("mailbox_oauth_sessions")
    with op.batch_alter_table("accounts") as batch:
        batch.drop_constraint("uq_account_user_email_type", type_="unique")
        batch.create_unique_constraint(
            "uq_account_email_type", ["email", "account_type"]
        )
        batch.drop_column("last_error_code")
        batch.drop_column("last_verified_at")
        batch.drop_column("credential_version")
        batch.drop_column("connection_status")
        batch.drop_column("token_expires_at")
        batch.drop_column("oauth_scopes_json")
        batch.drop_column("oauth_subject")
        batch.drop_column("credential_secret_ref")
