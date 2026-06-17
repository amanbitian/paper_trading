"""Initial paper trading schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-05-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_name", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("starting_cash", sa.Numeric(18, 2), nullable=False, server_default="1000000"),
        sa.Column("current_cash", sa.Numeric(18, 2), nullable=False, server_default="1000000"),
        sa.Column("risk_profile", sa.String(length=50), nullable=False, server_default="moderate"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "stocks",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("symbol", sa.String(length=50), nullable=False),
        sa.Column("yahoo_symbol", sa.String(length=80), nullable=False),
        sa.Column("exchange", sa.String(length=20), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=True),
        sa.Column("sector", sa.String(length=120), nullable=True),
        sa.Column("industry", sa.String(length=120), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default="INR"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("symbol", "exchange", name="uq_stocks_symbol_exchange"),
        sa.UniqueConstraint("yahoo_symbol", name="uq_stocks_yahoo_symbol"),
    )
    op.create_index("ix_stocks_symbol", "stocks", ["symbol"])
    op.create_index("ix_stocks_yahoo_symbol", "stocks", ["yahoo_symbol"])
    op.create_index("ix_stocks_exchange", "stocks", ["exchange"])

    op.create_table(
        "strategy_templates",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("strategy_name", sa.String(length=120), nullable=False),
        sa.Column("strategy_type", sa.String(length=80), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("strategy_name", name="uq_strategy_templates_strategy_name"),
    )

    op.create_table(
        "stock_prices",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("price_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timeframe", sa.String(length=20), nullable=False, server_default="1d"),
        sa.Column("open", sa.Numeric(18, 4), nullable=True),
        sa.Column("high", sa.Numeric(18, 4), nullable=True),
        sa.Column("low", sa.Numeric(18, 4), nullable=True),
        sa.Column("close", sa.Numeric(18, 4), nullable=True),
        sa.Column("adjusted_close", sa.Numeric(18, 4), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="yfinance"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("stock_id", "price_datetime", "timeframe", name="uq_stock_prices_stock_dt_tf"),
    )
    op.create_index("ix_stock_prices_stock_id", "stock_prices", ["stock_id"])
    op.create_index("ix_stock_prices_price_datetime", "stock_prices", ["price_datetime"])

    op.create_table(
        "portfolios",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("portfolio_name", sa.String(length=120), nullable=False),
        sa.Column("portfolio_type", sa.String(length=40), nullable=False, server_default="manual"),
        sa.Column("base_currency", sa.String(length=10), nullable=False, server_default="INR"),
        sa.Column("starting_value", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "portfolio_holdings",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("average_buy_price", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("total_invested", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("realized_pnl", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("last_updated_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("portfolio_id", "stock_id", name="uq_holdings_portfolio_stock"),
    )

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=True),
        sa.Column("transaction_type", sa.String(length=30), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("price", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("gross_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("charges", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("net_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("transaction_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="manual"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"], ondelete="SET NULL"),
    )

    op.create_table(
        "paper_orders",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("order_type", sa.String(length=20), nullable=False),
        sa.Column("side", sa.String(length=10), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("limit_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("stop_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "paper_trades",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("side", sa.String(length=10), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("executed_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("trade_value", sa.Numeric(18, 2), nullable=False),
        sa.Column("charges", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["order_id"], ["paper_orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("order_id", name="uq_paper_trades_order_id"),
    )

    op.create_table(
        "portfolio_daily_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("invested_value", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("market_value", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("cash_balance", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("total_value", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("realized_pnl", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("unrealized_pnl", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("day_pnl", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("total_return_pct", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("portfolio_id", "snapshot_date", name="uq_snapshot_portfolio_date"),
    )
    op.create_index("ix_portfolio_daily_snapshot_snapshot_date", "portfolio_daily_snapshot", ["snapshot_date"])

    op.create_table(
        "user_strategies",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("strategy_template_id", sa.Integer(), nullable=False),
        sa.Column("strategy_name", sa.String(length=120), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("risk_settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["strategy_template_id"], ["strategy_templates.id"], ondelete="RESTRICT"),
    )

    op.create_table(
        "strategy_signals",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_strategy_id", sa.Integer(), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("signal_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signal_type", sa.String(length=10), nullable=False),
        sa.Column("confidence_score", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("suggested_quantity", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("suggested_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("indicators", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("executed_as_order", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_strategy_id"], ["user_strategies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("user_strategy_id", sa.Integer(), nullable=True),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("initial_capital", sa.Numeric(18, 2), nullable=False),
        sa.Column("final_value", sa.Numeric(18, 2), nullable=False),
        sa.Column("total_return_pct", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("max_drawdown_pct", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("sharpe_ratio", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("win_rate", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("total_trades", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_strategy_id"], ["user_strategies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "backtest_trades",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("backtest_id", sa.Integer(), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("side", sa.String(length=10), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("price", sa.Numeric(18, 4), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("pnl", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["backtest_id"], ["backtest_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("backtest_trades")
    op.drop_table("backtest_runs")
    op.drop_table("strategy_signals")
    op.drop_table("user_strategies")
    op.drop_index("ix_portfolio_daily_snapshot_snapshot_date", table_name="portfolio_daily_snapshot")
    op.drop_table("portfolio_daily_snapshot")
    op.drop_table("paper_trades")
    op.drop_table("paper_orders")
    op.drop_table("transactions")
    op.drop_table("portfolio_holdings")
    op.drop_table("portfolios")
    op.drop_index("ix_stock_prices_price_datetime", table_name="stock_prices")
    op.drop_index("ix_stock_prices_stock_id", table_name="stock_prices")
    op.drop_table("stock_prices")
    op.drop_table("strategy_templates")
    op.drop_index("ix_stocks_exchange", table_name="stocks")
    op.drop_index("ix_stocks_yahoo_symbol", table_name="stocks")
    op.drop_index("ix_stocks_symbol", table_name="stocks")
    op.drop_table("stocks")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
