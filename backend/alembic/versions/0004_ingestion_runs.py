"""Add ingestion audit runs.

Revision ID: 0004_ingestion_runs
Revises: 0003_name_username
Create Date: 2026-05-14
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_ingestion_runs"
down_revision = "0003_name_username"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="yfinance"),
        sa.Column("exchange", sa.String(length=20), nullable=True),
        sa.Column("timeframe", sa.String(length=20), nullable=False, server_default="1d"),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="RUNNING"),
        sa.Column("total_symbols", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ingestion_runs_exchange", "ingestion_runs", ["exchange"])
    op.create_index("ix_ingestion_runs_timeframe", "ingestion_runs", ["timeframe"])
    op.create_index("ix_ingestion_runs_status", "ingestion_runs", ["status"])
    op.create_index("ix_ingestion_runs_started_at", "ingestion_runs", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_ingestion_runs_started_at", table_name="ingestion_runs")
    op.drop_index("ix_ingestion_runs_status", table_name="ingestion_runs")
    op.drop_index("ix_ingestion_runs_timeframe", table_name="ingestion_runs")
    op.drop_index("ix_ingestion_runs_exchange", table_name="ingestion_runs")
    op.drop_table("ingestion_runs")
