"""Add is_bhav_index flag to stocks and stock_performance_snapshots.

Revision ID: 0026_bhav_index_flag
Revises: 0025_historical_stock_financials
Create Date: 2026-06-18
"""

from alembic import op
import sqlalchemy as sa

revision = "0026_bhav_index_flag"
down_revision = "0025_historical_stock_financials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "stocks",
        sa.Column("is_bhav_index", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "stock_performance_snapshots",
        sa.Column("is_bhav_index", sa.Boolean(), nullable=False, server_default="false"),
    )
    # Partial index: only index TRUE rows (same pattern as is_nifty50 queries)
    op.create_index(
        "ix_stocks_is_bhav_index",
        "stocks",
        ["is_bhav_index"],
        postgresql_where=sa.text("is_bhav_index = TRUE"),
    )


def downgrade() -> None:
    op.drop_index("ix_stocks_is_bhav_index", table_name="stocks")
    op.drop_column("stocks", "is_bhav_index")
    op.drop_column("stock_performance_snapshots", "is_bhav_index")
