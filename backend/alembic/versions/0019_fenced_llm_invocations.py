"""Bind resumable LLM invocations to agent run fencing tokens.

Revision ID: 0019_fenced_llm_invocations
Revises: 0018_durable_agent_runs
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0019_fenced_llm_invocations"
down_revision: Union[str, None] = "0018_durable_agent_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("llm_invocations") as batch_op:
        batch_op.add_column(
            sa.Column("agent_run_id", sa.Uuid(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("fencing_token", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_llm_invocation_agent_run",
            "agent_runs",
            ["agent_run_id"],
            ["id"],
        )
        batch_op.create_check_constraint(
            "ck_llm_invocation_agent_run_fence",
            "(agent_run_id IS NULL AND fencing_token IS NULL) OR "
            "(agent_run_id IS NOT NULL AND fencing_token IS NOT NULL)",
        )
        batch_op.create_index(
            "idx_llm_invocation_agent_run",
            ["agent_run_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("llm_invocations") as batch_op:
        batch_op.drop_index("idx_llm_invocation_agent_run")
        batch_op.drop_constraint(
            "ck_llm_invocation_agent_run_fence",
            type_="check",
        )
        batch_op.drop_constraint(
            "fk_llm_invocation_agent_run",
            type_="foreignkey",
        )
        batch_op.drop_column("fencing_token")
        batch_op.drop_column("agent_run_id")
