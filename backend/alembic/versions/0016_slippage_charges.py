"""Slippage and charges breakdown on paper_trades.

Revision ID: 0016_slippage_charges
Revises: 0015_limit_order_matching
Create Date: 2026-05-29
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0016_slippage_charges"
down_revision = "0015_limit_order_matching"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paper_trades", sa.Column("slippage_bps", sa.Integer(), server_default="0", nullable=False))
    op.add_column("paper_trades", sa.Column("slippage_cost", sa.Numeric(18, 4), server_default="0", nullable=False))
    op.add_column("paper_trades", sa.Column("quoted_price", sa.Numeric(18, 4), nullable=True))
    op.add_column("paper_trades", sa.Column("charges_breakdown", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("paper_trades", "charges_breakdown")
    op.drop_column("paper_trades", "quoted_price")
    op.drop_column("paper_trades", "slippage_cost")
    op.drop_column("paper_trades", "slippage_bps")
