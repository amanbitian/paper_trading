"""Add walk-forward columns to backtest_runs.

Revision ID: 0014_backtest_walk_forward
Revises: 0013_strategy_signal_outcomes
Create Date: 2026-05-29
"""

from alembic import op
import sqlalchemy as sa


revision = "0014_backtest_walk_forward"
down_revision = "0013_strategy_signal_outcomes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "backtest_runs",
        sa.Column("walk_forward_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("backtest_runs", sa.Column("is_sharpe_ratio", sa.Numeric(10, 4), nullable=True))
    op.add_column("backtest_runs", sa.Column("oos_sharpe_ratio", sa.Numeric(10, 4), nullable=True))
    op.add_column("backtest_runs", sa.Column("oos_total_return_pct", sa.Numeric(10, 4), nullable=True))
    op.add_column("backtest_runs", sa.Column("oos_max_drawdown_pct", sa.Numeric(10, 4), nullable=True))
    op.add_column("backtest_runs", sa.Column("overfitting_score", sa.Numeric(10, 4), nullable=True))


def downgrade() -> None:
    op.drop_column("backtest_runs", "overfitting_score")
    op.drop_column("backtest_runs", "oos_max_drawdown_pct")
    op.drop_column("backtest_runs", "oos_total_return_pct")
    op.drop_column("backtest_runs", "oos_sharpe_ratio")
    op.drop_column("backtest_runs", "is_sharpe_ratio")
    op.drop_column("backtest_runs", "walk_forward_enabled")
