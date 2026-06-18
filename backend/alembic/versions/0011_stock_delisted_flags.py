"""Add delisted flags for Yahoo Finance ticker failures.

Revision ID: 0011_stock_delisted_flags
Revises: 0010_market_index_memberships
Create Date: 2026-05-29
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_stock_delisted_flags"
down_revision = "0010_market_index_memberships"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "stocks",
        sa.Column("is_delisted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("stocks", sa.Column("delisted_reason", sa.Text(), nullable=True))
    op.add_column("stocks", sa.Column("delisted_detected_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_stocks_is_delisted", "stocks", ["is_delisted"])


def downgrade() -> None:
    op.drop_index("ix_stocks_is_delisted", table_name="stocks")
    op.drop_column("stocks", "delisted_detected_at")
    op.drop_column("stocks", "delisted_reason")
    op.drop_column("stocks", "is_delisted")
