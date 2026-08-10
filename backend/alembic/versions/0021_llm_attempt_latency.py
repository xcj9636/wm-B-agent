"""Record provider latency and streaming time to first token.

Revision ID: 0021_llm_attempt_latency
Revises: 0020_agent_run_events
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0021_llm_attempt_latency"
down_revision: Union[str, None] = "0020_agent_run_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("llm_attempts") as batch_op:
        batch_op.add_column(
            sa.Column("latency_ms", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("ttft_ms", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("e2e_latency_ms", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "consumer_backpressure_ms",
                sa.Integer(),
                nullable=True,
            )
        )
        batch_op.create_check_constraint(
            "ck_llm_attempt_latency_nonnegative",
            "latency_ms IS NULL OR latency_ms >= 0",
        )
        batch_op.create_check_constraint(
            "ck_llm_attempt_ttft_nonnegative",
            "ttft_ms IS NULL OR ttft_ms >= 0",
        )
        batch_op.create_check_constraint(
            "ck_llm_attempt_e2e_latency_nonnegative",
            "e2e_latency_ms IS NULL OR e2e_latency_ms >= 0",
        )
        batch_op.create_check_constraint(
            "ck_llm_attempt_backpressure_nonnegative",
            "consumer_backpressure_ms IS NULL OR consumer_backpressure_ms >= 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("llm_attempts") as batch_op:
        batch_op.drop_constraint(
            "ck_llm_attempt_backpressure_nonnegative",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_llm_attempt_e2e_latency_nonnegative",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_llm_attempt_ttft_nonnegative",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_llm_attempt_latency_nonnegative",
            type_="check",
        )
        batch_op.drop_column("ttft_ms")
        batch_op.drop_column("consumer_backpressure_ms")
        batch_op.drop_column("e2e_latency_ms")
        batch_op.drop_column("latency_ms")
