"""Add latest stock fundamentals snapshot table.

Revision ID: 0023_stock_fundamentals_latest
Revises: 0022_stock_index_flags
Create Date: 2026-05-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0023_stock_fundamentals_latest"
down_revision = "0022_stock_index_flags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stock_fundamentals_latest",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=50), nullable=False),
        sa.Column("exchange", sa.String(length=20), nullable=False),
        sa.Column("yahoo_ticker", sa.String(length=80), nullable=False),
        sa.Column("market_cap", sa.Numeric(24, 4), nullable=True),
        sa.Column("trailing_pe", sa.Numeric(18, 6), nullable=True),
        sa.Column("roe", sa.Numeric(18, 8), nullable=True),
        sa.Column("debt_to_equity", sa.Numeric(18, 6), nullable=True),
        sa.Column("sales_growth", sa.Numeric(18, 8), nullable=True),
        sa.Column("earnings_growth", sa.Numeric(18, 8), nullable=True),
        sa.Column("promoter_holding", sa.Numeric(18, 8), nullable=True),
        sa.Column("dividend_yield", sa.Numeric(18, 8), nullable=True),
        sa.Column("price_to_book", sa.Numeric(18, 6), nullable=True),
        sa.Column("average_volume", sa.Numeric(24, 4), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="yfinance"),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stock_id", name="uq_stock_fundamentals_latest_stock_id"),
    )
    op.create_index("ix_stock_fundamentals_latest_stock_id", "stock_fundamentals_latest", ["stock_id"])
    op.create_index("ix_stock_fundamentals_latest_symbol", "stock_fundamentals_latest", ["symbol"])
    op.create_index("ix_stock_fundamentals_latest_exchange", "stock_fundamentals_latest", ["exchange"])
    op.create_index(
        "ix_stock_fundamentals_latest_yahoo_ticker",
        "stock_fundamentals_latest",
        ["yahoo_ticker"],
    )
    op.create_index("ix_stock_fundamentals_latest_fetched_at", "stock_fundamentals_latest", ["fetched_at"])
    op.create_index("ix_stock_fundamentals_latest_status", "stock_fundamentals_latest", ["status"])


def downgrade() -> None:
    op.drop_index("ix_stock_fundamentals_latest_status", table_name="stock_fundamentals_latest")
    op.drop_index("ix_stock_fundamentals_latest_fetched_at", table_name="stock_fundamentals_latest")
    op.drop_index("ix_stock_fundamentals_latest_yahoo_ticker", table_name="stock_fundamentals_latest")
    op.drop_index("ix_stock_fundamentals_latest_exchange", table_name="stock_fundamentals_latest")
    op.drop_index("ix_stock_fundamentals_latest_symbol", table_name="stock_fundamentals_latest")
    op.drop_index("ix_stock_fundamentals_latest_stock_id", table_name="stock_fundamentals_latest")
    op.drop_table("stock_fundamentals_latest")
