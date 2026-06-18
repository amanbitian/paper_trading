"""Add cached stock strategy explanations.

Revision ID: 0027_stock_strategy_explanations
Revises: 0026_bhav_index_flag
Create Date: 2026-06-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0027_stock_strategy_explanations"
down_revision = "0026_bhav_index_flag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stock_strategy_explanations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=50), nullable=False),
        sa.Column("exchange", sa.String(length=20), nullable=False),
        sa.Column("strategy_type", sa.String(length=80), nullable=False),
        sa.Column("strategy_name", sa.String(length=120), nullable=False),
        sa.Column("signal_type", sa.String(length=10), nullable=False),
        sa.Column("confidence_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("headline", sa.Text(), nullable=True),
        sa.Column("explanation_summary", sa.Text(), nullable=True),
        sa.Column("reasons_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("indicators_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("data_quality_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("price_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fundamentals_as_of", sa.Date(), nullable=True),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_version", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stock_id", "strategy_type", name="uq_stock_strategy_explanations_stock_strategy"),
    )
    op.create_index("ix_stock_strategy_explanations_id", "stock_strategy_explanations", ["id"])
    op.create_index("ix_stock_strategy_explanations_stock_id", "stock_strategy_explanations", ["stock_id"])
    op.create_index("ix_stock_strategy_explanations_symbol", "stock_strategy_explanations", ["symbol"])
    op.create_index("ix_stock_strategy_explanations_exchange", "stock_strategy_explanations", ["exchange"])
    op.create_index("ix_stock_strategy_explanations_strategy_type", "stock_strategy_explanations", ["strategy_type"])
    op.create_index("ix_stock_strategy_explanations_signal_type", "stock_strategy_explanations", ["signal_type"])
    op.create_index("ix_stock_strategy_explanations_calculated_at", "stock_strategy_explanations", ["calculated_at"])
    op.create_index("ix_stock_strategy_explanations_expires_at", "stock_strategy_explanations", ["expires_at"])
    op.create_index(
        "ix_stock_strategy_explanations_stock_calculated",
        "stock_strategy_explanations",
        ["stock_id", "calculated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_stock_strategy_explanations_stock_calculated", table_name="stock_strategy_explanations")
    op.drop_index("ix_stock_strategy_explanations_expires_at", table_name="stock_strategy_explanations")
    op.drop_index("ix_stock_strategy_explanations_calculated_at", table_name="stock_strategy_explanations")
    op.drop_index("ix_stock_strategy_explanations_signal_type", table_name="stock_strategy_explanations")
    op.drop_index("ix_stock_strategy_explanations_strategy_type", table_name="stock_strategy_explanations")
    op.drop_index("ix_stock_strategy_explanations_exchange", table_name="stock_strategy_explanations")
    op.drop_index("ix_stock_strategy_explanations_symbol", table_name="stock_strategy_explanations")
    op.drop_index("ix_stock_strategy_explanations_stock_id", table_name="stock_strategy_explanations")
    op.drop_index("ix_stock_strategy_explanations_id", table_name="stock_strategy_explanations")
    op.drop_table("stock_strategy_explanations")
