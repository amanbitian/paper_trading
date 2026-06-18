"""Add index fund universe and daily price history.

Revision ID: 0009_index_funds
Revises: 0008_portfolio_analytics
Create Date: 2026-05-28
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_index_funds"
down_revision = "0008_portfolio_analytics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "index_funds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(length=120), nullable=False),
        sa.Column("yahoo_symbol", sa.String(length=80), nullable=False),
        sa.Column("base_currency", sa.String(length=10), nullable=False, server_default="INR"),
        sa.Column("latest_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("value_in_inr", sa.Numeric(18, 4), nullable=True),
        sa.Column("category", sa.String(length=40), nullable=False, server_default="index"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("symbol", name="uq_index_funds_symbol"),
        sa.UniqueConstraint("yahoo_symbol", name="uq_index_funds_yahoo_symbol"),
    )
    op.create_index("ix_index_funds_id", "index_funds", ["id"])
    op.create_index("ix_index_funds_symbol", "index_funds", ["symbol"])
    op.create_index("ix_index_funds_yahoo_symbol", "index_funds", ["yahoo_symbol"])
    op.create_index("ix_index_funds_category", "index_funds", ["category"])

    op.create_table(
        "index_fund_prices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "index_fund_id",
            sa.Integer(),
            sa.ForeignKey("index_funds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("price_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timeframe", sa.String(length=20), nullable=False, server_default="1d"),
        sa.Column("open", sa.Numeric(18, 4), nullable=True),
        sa.Column("high", sa.Numeric(18, 4), nullable=True),
        sa.Column("low", sa.Numeric(18, 4), nullable=True),
        sa.Column("close", sa.Numeric(18, 4), nullable=True),
        sa.Column("adjusted_close", sa.Numeric(18, 4), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="yfinance"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "index_fund_id",
            "price_datetime",
            "timeframe",
            name="uq_index_fund_prices_fund_dt_tf",
        ),
    )
    op.create_index("ix_index_fund_prices_id", "index_fund_prices", ["id"])
    op.create_index("ix_index_fund_prices_index_fund_id", "index_fund_prices", ["index_fund_id"])
    op.create_index("ix_index_fund_prices_price_datetime", "index_fund_prices", ["price_datetime"])
    op.create_index(
        "ix_index_fund_prices_fund_tf_datetime",
        "index_fund_prices",
        ["index_fund_id", "timeframe", "price_datetime"],
    )

    op.add_column("backtest_runs", sa.Column("index_fund_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_backtest_runs_index_fund_id_index_funds",
        "backtest_runs",
        "index_funds",
        ["index_fund_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_backtest_runs_index_fund_id", "backtest_runs", ["index_fund_id"])
    op.alter_column("backtest_runs", "stock_id", existing_type=sa.Integer(), nullable=True)

    op.add_column("backtest_trades", sa.Column("index_fund_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_backtest_trades_index_fund_id_index_funds",
        "backtest_trades",
        "index_funds",
        ["index_fund_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_backtest_trades_index_fund_id", "backtest_trades", ["index_fund_id"])
    op.alter_column("backtest_trades", "stock_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.alter_column("backtest_trades", "stock_id", existing_type=sa.Integer(), nullable=False)
    op.drop_index("ix_backtest_trades_index_fund_id", table_name="backtest_trades")
    op.drop_constraint("fk_backtest_trades_index_fund_id_index_funds", "backtest_trades", type_="foreignkey")
    op.drop_column("backtest_trades", "index_fund_id")

    op.alter_column("backtest_runs", "stock_id", existing_type=sa.Integer(), nullable=False)
    op.drop_index("ix_backtest_runs_index_fund_id", table_name="backtest_runs")
    op.drop_constraint("fk_backtest_runs_index_fund_id_index_funds", "backtest_runs", type_="foreignkey")
    op.drop_column("backtest_runs", "index_fund_id")

    op.drop_index("ix_index_fund_prices_fund_tf_datetime", table_name="index_fund_prices")
    op.drop_index("ix_index_fund_prices_price_datetime", table_name="index_fund_prices")
    op.drop_index("ix_index_fund_prices_index_fund_id", table_name="index_fund_prices")
    op.drop_index("ix_index_fund_prices_id", table_name="index_fund_prices")
    op.drop_table("index_fund_prices")

    op.drop_index("ix_index_funds_category", table_name="index_funds")
    op.drop_index("ix_index_funds_yahoo_symbol", table_name="index_funds")
    op.drop_index("ix_index_funds_symbol", table_name="index_funds")
    op.drop_index("ix_index_funds_id", table_name="index_funds")
    op.drop_table("index_funds")
