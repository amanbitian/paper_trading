"""Add search query telemetry logs.

Revision ID: 0012_search_query_logs
Revises: 0011_stock_delisted_flags
Create Date: 2026-05-29
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_search_query_logs"
down_revision = "0011_stock_delisted_flags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "search_query_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("search_type", sa.String(length=50), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("filter_name", sa.String(length=50), nullable=True),
        sa.Column("filter_value", sa.String(length=120), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Numeric(12, 3), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="ok"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_search_query_logs_id", "search_query_logs", ["id"])
    op.create_index("ix_search_query_logs_created_at", "search_query_logs", ["created_at"])
    op.create_index("ix_search_query_logs_search_type", "search_query_logs", ["search_type"])
    op.create_index("ix_search_query_logs_status", "search_query_logs", ["status"])
    op.create_index(
        "ix_search_query_logs_type_created_at",
        "search_query_logs",
        ["search_type", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_search_query_logs_type_created_at", table_name="search_query_logs")
    op.drop_index("ix_search_query_logs_status", table_name="search_query_logs")
    op.drop_index("ix_search_query_logs_search_type", table_name="search_query_logs")
    op.drop_index("ix_search_query_logs_created_at", table_name="search_query_logs")
    op.drop_index("ix_search_query_logs_id", table_name="search_query_logs")
    op.drop_table("search_query_logs")
