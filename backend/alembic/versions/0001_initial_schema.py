"""Establish the initial B-agent schema baseline.

Revision ID: 0001_initial_schema
Revises:

This migration is deliberately self-contained. Importing application models here
would make the historical baseline change whenever a new model is added.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


workflow_status = sa.Enum(
    "DRAFT", "ACTIVE", "PAUSED", "ARCHIVED", name="workflowstatus"
)
execution_status = sa.Enum(
    "PENDING",
    "RUNNING",
    "PAUSED",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    name="executionstatus",
)
outreach_status = sa.Enum(
    "PENDING",
    "SCHEDULED",
    "SENT",
    "DELIVERED",
    "OPENED",
    "REPLIED",
    "FAILED",
    "BOUNCED",
    name="outreachstatus",
)
conversation_status = sa.Enum(
    "ACTIVE", "PAUSED", "CLOSED", "ARCHIVED", name="conversationstatus"
)
task_status = sa.Enum(
    "PENDING",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "RETRY",
    name="taskstatus",
)
intent_level = sa.Enum(
    "LOW", "MEDIUM", "HIGH", "VERY_HIGH", name="intentlevel"
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=100), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("is_superuser", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("last_login", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_id", "users", ["id"])
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "workflows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", workflow_status, nullable=True),
        sa.Column("config_json", sa.JSON(), nullable=True),
        sa.Column("variables", sa.JSON(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("version", sa.String(length=20), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflows_name", "workflows", ["name"])
    op.create_index(
        "idx_workflow_user_status", "workflows", ["user_id", "status"]
    )

    op.create_table(
        "workflow_executions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=True),
        sa.Column("status", execution_status, nullable=True),
        sa.Column("current_step", sa.String(length=100), nullable=True),
        sa.Column("context_json", sa.JSON(), nullable=True),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("error_stack", sa.Text(), nullable=True),
        sa.Column("input_data", sa.JSON(), nullable=True),
        sa.Column("output_data", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("completed_steps", sa.JSON(), nullable=True),
        sa.Column("failed_steps", sa.JSON(), nullable=True),
        sa.Column("paused_steps", sa.JSON(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_execution_workflow_status",
        "workflow_executions",
        ["workflow_id", "status"],
    )
    op.create_index(
        "idx_execution_started_at", "workflow_executions", ["started_at"]
    )

    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=True),
        sa.Column("platform", sa.String(length=50), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("whatsapp", sa.String(length=20), nullable=True),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("country", sa.String(length=10), nullable=True),
        sa.Column("category", sa.String(length=50), nullable=True),
        sa.Column("subcategory", sa.String(length=50), nullable=True),
        sa.Column("follower_count", sa.Integer(), nullable=True),
        sa.Column("account_type", sa.String(length=20), nullable=True),
        sa.Column("intent_level", intent_level, nullable=True),
        sa.Column("tags_json", sa.JSON(), nullable=True),
        sa.Column("source_data_json", sa.JSON(), nullable=True),
        sa.Column("contact_info", sa.JSON(), nullable=True),
        sa.Column("social_links", sa.JSON(), nullable=True),
        sa.Column("website", sa.String(length=255), nullable=True),
        sa.Column("company_name", sa.String(length=100), nullable=True),
        sa.Column("job_title", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("first_contacted_at", sa.DateTime(), nullable=True),
        sa.Column("last_contacted_at", sa.DateTime(), nullable=True),
        sa.Column("last_replied_at", sa.DateTime(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("custom_fields", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "username", "platform", name="uq_customer_username_platform"
        ),
    )
    op.create_index("ix_customers_id", "customers", ["id"])
    op.create_index("ix_customers_email", "customers", ["email"])
    op.create_index("ix_customers_country", "customers", ["country"])
    op.create_index(
        "idx_customer_platform_status", "customers", ["platform", "status"]
    )
    op.create_index(
        "idx_customer_country_category", "customers", ["country", "category"]
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("platform", sa.String(length=50), nullable=True),
        sa.Column(
            "platform_conversation_id", sa.String(length=255), nullable=True
        ),
        sa.Column("status", conversation_status, nullable=True),
        sa.Column("intent_level_json", sa.JSON(), nullable=True),
        sa.Column("current_intent", sa.String(length=50), nullable=True),
        sa.Column("intent_confidence", sa.Float(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("custom_fields", sa.JSON(), nullable=True),
        sa.Column("ai_handled", sa.Boolean(), nullable=True),
        sa.Column("manual_takeover", sa.Boolean(), nullable=True),
        sa.Column("takeover_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("last_message_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_conversation_customer_status",
        "conversations",
        ["customer_id", "status"],
    )
    op.create_index(
        "idx_conversation_platform_id",
        "conversations",
        ["platform", "platform_conversation_id"],
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("platform_message_id", sa.String(length=255), nullable=True),
        sa.Column("ai_generated", sa.Boolean(), nullable=True),
        sa.Column("ai_confidence", sa.Float(), nullable=True),
        sa.Column("intent_detected", sa.String(length=50), nullable=True),
        sa.Column("suggested_actions", sa.JSON(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("failed_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attachments", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_message_conversation_sent",
        "messages",
        ["conversation_id", "sent_at"],
    )

    op.create_table(
        "outreach_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("channel", sa.String(length=50), nullable=True),
        sa.Column("status", outreach_status, nullable=True),
        sa.Column("message_id", sa.String(length=255), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("template_id", sa.String(length=100), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("account_type", sa.String(length=20), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.Column("opened_at", sa.DateTime(), nullable=True),
        sa.Column("clicked_at", sa.DateTime(), nullable=True),
        sa.Column("replied_at", sa.DateTime(), nullable=True),
        sa.Column("bounced_at", sa.DateTime(), nullable=True),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=True),
        sa.Column("max_retries", sa.Integer(), nullable=True),
        sa.Column("cost", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_outreach_customer_status",
        "outreach_logs",
        ["customer_id", "status"],
    )
    op.create_index(
        "idx_outreach_channel_status", "outreach_logs", ["channel", "status"]
    )
    op.create_index(
        "idx_outreach_scheduled", "outreach_logs", ["scheduled_at"]
    )

    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("account_type", sa.String(length=20), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone_number", sa.String(length=20), nullable=True),
        sa.Column("credentials_json", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("is_verified", sa.Boolean(), nullable=True),
        sa.Column("last_used", sa.DateTime(), nullable=True),
        sa.Column("daily_limit", sa.Integer(), nullable=True),
        sa.Column("today_sent", sa.Integer(), nullable=True),
        sa.Column("last_reset", sa.DateTime(), nullable=True),
        sa.Column("labels", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "email", "account_type", name="uq_account_email_type"
        ),
    )
    op.create_index("ix_accounts_id", "accounts", ["id"])
    op.create_index(
        "idx_account_user_type", "accounts", ["user_id", "account_type"]
    )

    op.create_table(
        "task_queue",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_type", sa.String(length=50), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("status", task_status, nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(), nullable=True),
        sa.Column("executed_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=True),
        sa.Column("max_retries", sa.Integer(), nullable=True),
        sa.Column("retry_after", sa.DateTime(), nullable=True),
        sa.Column("worker_id", sa.String(length=100), nullable=True),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("error_stack", sa.Text(), nullable=True),
        sa.Column("depends_on", sa.JSON(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_task_type_status", "task_queue", ["task_type", "status"]
    )
    op.create_index("idx_task_scheduled", "task_queue", ["scheduled_at"])
    op.create_index("idx_task_celery_id", "task_queue", ["celery_task_id"])
    op.create_index(
        "ix_task_queue_celery_task_id", "task_queue", ["celery_task_id"]
    )

    op.create_table(
        "templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("template_type", sa.String(length=20), nullable=True),
        sa.Column("language", sa.String(length=10), nullable=True),
        sa.Column("category", sa.String(length=50), nullable=True),
        sa.Column("subject_template", sa.String(length=255), nullable=True),
        sa.Column("body_template", sa.Text(), nullable=True),
        sa.Column("variables", sa.JSON(), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=True),
        sa.Column("success_rate", sa.Float(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_templates_id", "templates", ["id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=50), nullable=True),
        sa.Column("resource_type", sa.String(length=50), nullable=True),
        sa.Column("resource_id", sa.String(length=255), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=True),
        sa.Column("ip_address", sa.String(length=50), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=True),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_audit_user_action", "audit_logs", ["user_id", "action"]
    )
    op.create_index(
        "idx_audit_resource", "audit_logs", ["resource_type", "resource_id"]
    )
    op.create_index("idx_audit_created", "audit_logs", ["created_at"])

    op.create_table(
        "stats_daily",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("date", sa.DateTime(), nullable=True),
        sa.Column("new_customers", sa.Integer(), nullable=True),
        sa.Column("active_customers", sa.Integer(), nullable=True),
        sa.Column("converted_customers", sa.Integer(), nullable=True),
        sa.Column("emails_sent", sa.Integer(), nullable=True),
        sa.Column("whatsapp_sent", sa.Integer(), nullable=True),
        sa.Column("emails_opened", sa.Integer(), nullable=True),
        sa.Column("emails_replied", sa.Integer(), nullable=True),
        sa.Column("new_conversations", sa.Integer(), nullable=True),
        sa.Column("active_conversations", sa.Integer(), nullable=True),
        sa.Column("ai_handled", sa.Integer(), nullable=True),
        sa.Column("manual_takeovers", sa.Integer(), nullable=True),
        sa.Column("workflows_executed", sa.Integer(), nullable=True),
        sa.Column("workflows_completed", sa.Integer(), nullable=True),
        sa.Column("workflows_failed", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "date", name="uq_stats_user_date"),
    )
    op.create_index("ix_stats_daily_id", "stats_daily", ["id"])
    op.create_index("ix_stats_daily_date", "stats_daily", ["date"])


def downgrade() -> None:
    for table_name in (
        "stats_daily",
        "audit_logs",
        "templates",
        "task_queue",
        "accounts",
        "outreach_logs",
        "messages",
        "conversations",
        "customers",
        "workflow_executions",
        "workflows",
        "users",
    ):
        op.drop_table(table_name)

    if op.get_bind().dialect.name == "postgresql":
        for enum_type in (
            intent_level,
            task_status,
            conversation_status,
            outreach_status,
            execution_status,
            workflow_status,
        ):
            enum_type.drop(op.get_bind(), checkfirst=True)
