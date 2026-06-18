"""Limit order matching columns on paper_orders.

Revision ID: 0015_limit_order_matching
Revises: 0014_backtest_walk_forward
Create Date: 2026-05-29
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_limit_order_matching"
down_revision = "0014_backtest_walk_forward"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paper_orders", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("paper_orders", sa.Column("matched_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("paper_orders", sa.Column("matched_price", sa.Numeric(18, 4), nullable=True))


def downgrade() -> None:
    op.drop_column("paper_orders", "matched_price")
    op.drop_column("paper_orders", "matched_at")
    op.drop_column("paper_orders", "expires_at")
