"""Add execution and cost metadata to backtests.

Revision ID: 0020_backtest_exec_cost
Revises: 0019_ai_action_logs
Create Date: 2026-05-29
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0020_backtest_exec_cost"
down_revision = "0019_ai_action_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "backtest_runs",
        sa.Column(
            "execution_mode",
            sa.String(length=50),
            nullable=False,
            server_default="signal_on_close_execute_next_open",
        ),
    )
    op.add_column(
        "backtest_runs",
        sa.Column("intrabar_assumption", sa.String(length=40), nullable=False, server_default="conservative"),
    )
    op.add_column(
        "backtest_runs",
        sa.Column("cost_model", sa.String(length=40), nullable=False, server_default="zerodha_equity_delivery"),
    )
    op.add_column("backtest_runs", sa.Column("gross_pnl", sa.Numeric(18, 4), nullable=False, server_default="0"))
    op.add_column("backtest_runs", sa.Column("total_charges", sa.Numeric(18, 4), nullable=False, server_default="0"))
    op.add_column("backtest_runs", sa.Column("slippage_cost", sa.Numeric(18, 4), nullable=False, server_default="0"))
    op.add_column("backtest_runs", sa.Column("net_pnl", sa.Numeric(18, 4), nullable=False, server_default="0"))
    op.add_column("backtest_runs", sa.Column("gross_return_pct", sa.Numeric(10, 4), nullable=False, server_default="0"))
    op.add_column("backtest_runs", sa.Column("net_return_pct", sa.Numeric(10, 4), nullable=False, server_default="0"))

    op.add_column("backtest_trades", sa.Column("signal_date", sa.Date(), nullable=True))
    op.add_column("backtest_trades", sa.Column("quoted_price", sa.Numeric(18, 4), nullable=True))
    op.add_column("backtest_trades", sa.Column("gross_pnl", sa.Numeric(18, 4), nullable=False, server_default="0"))
    op.add_column("backtest_trades", sa.Column("charges", sa.Numeric(18, 4), nullable=False, server_default="0"))
    op.add_column("backtest_trades", sa.Column("slippage_cost", sa.Numeric(18, 4), nullable=False, server_default="0"))
    op.add_column("backtest_trades", sa.Column("net_pnl", sa.Numeric(18, 4), nullable=False, server_default="0"))
    op.add_column("backtest_trades", sa.Column("charges_breakdown", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("backtest_trades", "charges_breakdown")
    op.drop_column("backtest_trades", "net_pnl")
    op.drop_column("backtest_trades", "slippage_cost")
    op.drop_column("backtest_trades", "charges")
    op.drop_column("backtest_trades", "gross_pnl")
    op.drop_column("backtest_trades", "quoted_price")
    op.drop_column("backtest_trades", "signal_date")

    op.drop_column("backtest_runs", "net_return_pct")
    op.drop_column("backtest_runs", "gross_return_pct")
    op.drop_column("backtest_runs", "net_pnl")
    op.drop_column("backtest_runs", "slippage_cost")
    op.drop_column("backtest_runs", "total_charges")
    op.drop_column("backtest_runs", "gross_pnl")
    op.drop_column("backtest_runs", "cost_model")
    op.drop_column("backtest_runs", "intrabar_assumption")
    op.drop_column("backtest_runs", "execution_mode")
