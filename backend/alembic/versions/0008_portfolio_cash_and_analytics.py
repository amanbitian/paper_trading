"""Portfolio cash balance, price index, analytics snapshot tables.

Revision ID: 0008_portfolio_analytics
Revises: 0007_ingestion_run_strategy
Create Date: 2026-05-25
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0008_portfolio_analytics"
down_revision = "0007_ingestion_run_strategy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portfolios",
        sa.Column("cash_balance", sa.Numeric(18, 2), nullable=False, server_default="0"),
    )
    op.execute(
        """
        UPDATE portfolios p
        SET cash_balance = u.current_cash
        FROM users u
        WHERE p.user_id = u.id AND p.portfolio_type = 'paper'
        """
    )

    op.create_table(
        "stock_performance_snapshots",
        sa.Column("stock_id", sa.Integer(), sa.ForeignKey("stocks.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("symbol", sa.String(length=50), nullable=False),
        sa.Column("yahoo_symbol", sa.String(length=80), nullable=False),
        sa.Column("exchange", sa.String(length=20), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=True),
        sa.Column("sector", sa.String(length=120), nullable=True),
        sa.Column("latest_price_datetime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("latest_volume", sa.BigInteger(), nullable=True),
        sa.Column("price_1m", sa.Numeric(18, 4), nullable=True),
        sa.Column("price_3m", sa.Numeric(18, 4), nullable=True),
        sa.Column("price_6m", sa.Numeric(18, 4), nullable=True),
        sa.Column("price_1y", sa.Numeric(18, 4), nullable=True),
        sa.Column("change_1m_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("change_3m_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("change_6m_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("change_1y_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_stock_perf_snap_exchange", "stock_performance_snapshots", ["exchange"])
    op.create_index("ix_stock_perf_snap_symbol", "stock_performance_snapshots", ["symbol"])

    op.create_table(
        "market_analytics_cache",
        sa.Column("cache_key", sa.String(length=80), primary_key=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_index(
        "ix_stock_prices_stock_tf_datetime",
        "stock_prices",
        ["stock_id", "timeframe", "price_datetime"],
    )


def downgrade() -> None:
    op.drop_index("ix_stock_prices_stock_tf_datetime", table_name="stock_prices")
    op.drop_table("market_analytics_cache")
    op.drop_index("ix_stock_perf_snap_symbol", table_name="stock_performance_snapshots")
    op.drop_index("ix_stock_perf_snap_exchange", table_name="stock_performance_snapshots")
    op.drop_table("stock_performance_snapshots")
    op.drop_column("portfolios", "cash_balance")
