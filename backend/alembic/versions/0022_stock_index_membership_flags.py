"""Add stock index membership flags.

Revision ID: 0022_stock_index_flags
Revises: 0021_backtest_benchmark_metrics
Create Date: 2026-05-29
"""

from alembic import op
import sqlalchemy as sa


revision = "0022_stock_index_flags"
down_revision = "0021_backtest_benchmark_metrics"
branch_labels = None
depends_on = None


FLAGS = (
    "is_nifty50",
    "is_nifty100",
    "is_nifty200",
    "is_nifty500",
    "is_banknifty",
    "is_finnifty",
    "is_midcpnifty",
    "is_sensex",
)
TABLES = ("stocks", "stock_performance_snapshots")


def upgrade() -> None:
    for table_name in TABLES:
        for flag in FLAGS:
            op.add_column(
                table_name,
                sa.Column(flag, sa.Boolean(), nullable=False, server_default=sa.false()),
            )
            op.create_index(f"ix_{table_name}_{flag}", table_name, [flag])


def downgrade() -> None:
    for table_name in reversed(TABLES):
        for flag in reversed(FLAGS):
            op.drop_index(f"ix_{table_name}_{flag}", table_name=table_name)
            op.drop_column(table_name, flag)
