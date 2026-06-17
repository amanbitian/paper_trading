"""Track ingestion batch bounds.

Revision ID: 0006_ingestion_batch_bounds
Revises: 0005_ingestion_rows_saved
Create Date: 2026-05-14
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_ingestion_batch_bounds"
down_revision = "0005_ingestion_rows_saved"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ingestion_runs", sa.Column("batch_offset", sa.Integer(), nullable=True))
    op.add_column("ingestion_runs", sa.Column("batch_limit", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("ingestion_runs", "batch_limit")
    op.drop_column("ingestion_runs", "batch_offset")
