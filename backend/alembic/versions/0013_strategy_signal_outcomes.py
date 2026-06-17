"""Add strategy signal outcomes and unique strategy_type on templates.

Revision ID: 0013_strategy_signal_outcomes
Revises: 0012_search_query_logs
Create Date: 2026-05-29
"""

from alembic import op
import sqlalchemy as sa


revision = "0013_strategy_signal_outcomes"
down_revision = "0012_search_query_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_strategy_templates_strategy_type",
        "strategy_templates",
        ["strategy_type"],
    )
    op.create_table(
        "strategy_signal_outcomes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=True),
        sa.Column("index_fund_id", sa.Integer(), nullable=True),
        sa.Column("signal_type", sa.String(length=10), nullable=True),
        sa.Column("signal_date", sa.Date(), nullable=False),
        sa.Column("signal_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("price_5d", sa.Numeric(18, 4), nullable=True),
        sa.Column("price_10d", sa.Numeric(18, 4), nullable=True),
        sa.Column("price_20d", sa.Numeric(18, 4), nullable=True),
        sa.Column("return_5d_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("return_10d_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("return_20d_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("profitable_5d", sa.Boolean(), nullable=True),
        sa.Column("profitable_10d", sa.Boolean(), nullable=True),
        sa.Column("profitable_20d", sa.Boolean(), nullable=True),
        sa.Column("stop_hit", sa.Boolean(), nullable=True),
        sa.Column("stop_hit_date", sa.Date(), nullable=True),
        sa.Column("outcome_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["index_fund_id"], ["index_funds.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["signal_id"], ["strategy_signals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("signal_id"),
    )
    op.create_index("ix_strategy_signal_outcomes_id", "strategy_signal_outcomes", ["id"])


def downgrade() -> None:
    op.drop_index("ix_strategy_signal_outcomes_id", table_name="strategy_signal_outcomes")
    op.drop_table("strategy_signal_outcomes")
    op.drop_constraint("uq_strategy_templates_strategy_type", "strategy_templates", type_="unique")
