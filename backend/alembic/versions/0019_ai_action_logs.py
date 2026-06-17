"""AI Think Tank action audit logs.

Revision ID: 0019_ai_action_logs
Revises: 0018_email_alerts
Create Date: 2026-05-29
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0019_ai_action_logs"
down_revision = "0018_email_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_action_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="api"),
        sa.Column("action_type", sa.String(length=80), nullable=False),
        sa.Column("endpoint", sa.String(length=120), nullable=False),
        sa.Column("http_method", sa.String(length=10), nullable=False, server_default="POST"),
        sa.Column("model_name", sa.String(length=120), nullable=True),
        sa.Column("ollama_base_url", sa.String(length=200), nullable=True),
        sa.Column("ollama_connected", sa.Boolean(), nullable=True),
        sa.Column("request_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("response_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("llm_prompt", sa.Text(), nullable=True),
        sa.Column("llm_response", sa.Text(), nullable=True),
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="ok"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_ai_action_logs_action_type", "ai_action_logs", ["action_type"])
    op.create_index("ix_ai_action_logs_status", "ai_action_logs", ["status"])
    op.create_index("ix_ai_action_logs_created_at", "ai_action_logs", ["created_at"])
    op.create_index("ix_ai_action_logs_user_id", "ai_action_logs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_action_logs_user_id", table_name="ai_action_logs")
    op.drop_index("ix_ai_action_logs_created_at", table_name="ai_action_logs")
    op.drop_index("ix_ai_action_logs_status", table_name="ai_action_logs")
    op.drop_index("ix_ai_action_logs_action_type", table_name="ai_action_logs")
    op.drop_table("ai_action_logs")
