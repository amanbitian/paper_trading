"""Add benchmark metrics to backtest runs.

Revision ID: 0021_backtest_benchmark_metrics
Revises: 0020_backtest_exec_cost
Create Date: 2026-05-29
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0021_backtest_benchmark_metrics"
down_revision = "0020_backtest_exec_cost"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("backtest_runs", sa.Column("benchmark_code", sa.String(length=40), nullable=True))
    op.add_column("backtest_runs", sa.Column("benchmark_symbol", sa.String(length=80), nullable=True))
    op.add_column("backtest_runs", sa.Column("benchmark_name", sa.String(length=120), nullable=True))
    op.add_column("backtest_runs", sa.Column("benchmark_return", sa.Numeric(10, 4), nullable=True))
    op.add_column("backtest_runs", sa.Column("excess_return", sa.Numeric(10, 4), nullable=True))
    op.add_column("backtest_runs", sa.Column("alpha", sa.Numeric(10, 4), nullable=True))
    op.add_column("backtest_runs", sa.Column("beta", sa.Numeric(10, 4), nullable=True))
    op.add_column("backtest_runs", sa.Column("tracking_error", sa.Numeric(10, 4), nullable=True))
    op.add_column("backtest_runs", sa.Column("information_ratio", sa.Numeric(10, 4), nullable=True))
    op.add_column("backtest_runs", sa.Column("upside_capture", sa.Numeric(10, 4), nullable=True))
    op.add_column("backtest_runs", sa.Column("downside_capture", sa.Numeric(10, 4), nullable=True))
    op.add_column("backtest_runs", sa.Column("benchmark_warnings", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("backtest_runs", "benchmark_warnings")
    op.drop_column("backtest_runs", "downside_capture")
    op.drop_column("backtest_runs", "upside_capture")
    op.drop_column("backtest_runs", "information_ratio")
    op.drop_column("backtest_runs", "tracking_error")
    op.drop_column("backtest_runs", "beta")
    op.drop_column("backtest_runs", "alpha")
    op.drop_column("backtest_runs", "excess_return")
    op.drop_column("backtest_runs", "benchmark_return")
    op.drop_column("backtest_runs", "benchmark_name")
    op.drop_column("backtest_runs", "benchmark_symbol")
    op.drop_column("backtest_runs", "benchmark_code")
