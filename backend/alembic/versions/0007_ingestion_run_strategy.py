"""Track ingestion run strategy.

Revision ID: 0007_ingestion_run_strategy
Revises: 0006_ingestion_batch_bounds
Create Date: 2026-05-14
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_ingestion_run_strategy"
down_revision = "0006_ingestion_batch_bounds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ingestion_runs",
        sa.Column("ingestion_mode", sa.String(length=30), nullable=False, server_default="FULL"),
    )
    op.add_column("ingestion_runs", sa.Column("chunk_days", sa.Integer(), nullable=True))
    op.add_column("ingestion_runs", sa.Column("sleep_seconds", sa.Numeric(8, 2), nullable=True))
    op.create_index("ix_ingestion_runs_ingestion_mode", "ingestion_runs", ["ingestion_mode"])


def downgrade() -> None:
    op.drop_index("ix_ingestion_runs_ingestion_mode", table_name="ingestion_runs")
    op.drop_column("ingestion_runs", "sleep_seconds")
    op.drop_column("ingestion_runs", "chunk_days")
    op.drop_column("ingestion_runs", "ingestion_mode")
