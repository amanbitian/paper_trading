"""Rename ingestion audit row counter.

Revision ID: 0005_ingestion_rows_saved
Revises: 0004_ingestion_runs
Create Date: 2026-05-14
"""

from alembic import op


revision = "0005_ingestion_rows_saved"
down_revision = "0004_ingestion_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("ingestion_runs", "rows_inserted", new_column_name="rows_saved")


def downgrade() -> None:
    op.alter_column("ingestion_runs", "rows_saved", new_column_name="rows_inserted")
