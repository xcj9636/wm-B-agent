"""Add reliable LLM audit and transactional outbox tables.

Revision ID: 0002_reliable_execution
Revises: 0001_initial_schema
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0002_reliable_execution"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


llm_invocation_status = sa.Enum(
    "pending", "succeeded", "failed", "unknown", name="llm_invocation_status"
)
llm_attempt_status = sa.Enum(
    "succeeded", "failed", "unknown", name="llm_attempt_status"
)
outbox_status = sa.Enum(
    "pending",
    "processing",
    "retry",
    "sent",
    "dead_letter",
    name="outbox_status",
)


def upgrade() -> None:
    op.create_table(
        "llm_invocations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("use_case", sa.String(length=50), nullable=False),
        sa.Column("backend", sa.String(length=50), nullable=False),
        sa.Column("status", llm_invocation_status, nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column("response_json", sa.JSON(), nullable=True),
        sa.Column("workflow_execution_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("error_kind", sa.String(length=50), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workflow_execution_id"], ["workflow_executions.id"]
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index(
        "idx_llm_invocation_status_created",
        "llm_invocations",
        ["status", "created_at"],
    )
    op.create_index(
        "idx_llm_invocation_workflow",
        "llm_invocations",
        ["workflow_execution_id"],
    )
    op.create_index(
        "idx_llm_invocation_conversation",
        "llm_invocations",
        ["conversation_id"],
    )

    op.create_table(
        "llm_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("invocation_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", llm_attempt_status, nullable=False),
        sa.Column("gateway_request_id", sa.String(length=255), nullable=True),
        sa.Column("provider", sa.String(length=100), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("cost", sa.Float(), nullable=True),
        sa.Column("cost_status", sa.String(length=20), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("fallback_reason", sa.String(length=255), nullable=True),
        sa.Column("error_kind", sa.String(length=50), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["invocation_id"], ["llm_invocations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "invocation_id", "attempt_number", name="uq_llm_attempt_number"
        ),
    )
    op.create_index(
        "idx_llm_attempt_gateway_request",
        "llm_attempts",
        ["gateway_request_id"],
    )
    op.create_index(
        "idx_llm_attempt_provider_model",
        "llm_attempts",
        ["provider", "model"],
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_type", sa.String(length=50), nullable=False),
        sa.Column("aggregate_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("business_key", sa.String(length=255), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("status", outbox_status, nullable=False),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("leased_by", sa.String(length=100), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel",
            "business_key",
            "event_type",
            name="uq_outbox_business_action",
        ),
    )
    op.create_index(
        "idx_outbox_dispatch", "outbox_events", ["status", "available_at"]
    )
    op.create_index(
        "idx_outbox_lease", "outbox_events", ["status", "lease_until"]
    )
    op.create_index(
        "idx_outbox_aggregate",
        "outbox_events",
        ["aggregate_type", "aggregate_id"],
    )


def downgrade() -> None:
    op.drop_table("outbox_events")
    op.drop_table("llm_attempts")
    op.drop_table("llm_invocations")

    if op.get_bind().dialect.name == "postgresql":
        outbox_status.drop(op.get_bind(), checkfirst=True)
        llm_attempt_status.drop(op.get_bind(), checkfirst=True)
        llm_invocation_status.drop(op.get_bind(), checkfirst=True)
