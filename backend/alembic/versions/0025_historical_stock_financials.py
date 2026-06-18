"""Add historical stock financial statement table.

Revision ID: 0025_historical_stock_financials
Revises: 0024_stock_news
Create Date: 2026-06-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0025_historical_stock_financials"
down_revision = "0024_stock_news"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stock_financials",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=50), nullable=False),
        sa.Column("exchange", sa.String(length=20), nullable=False),
        sa.Column("statement_type", sa.String(length=30), nullable=False),
        sa.Column("period_type", sa.String(length=20), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=True),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("normalized_field", sa.String(length=180), nullable=False),
        sa.Column("value", sa.Numeric(24, 6), nullable=True),
        sa.Column("unit", sa.String(length=40), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "stock_id",
            "statement_type",
            "period_type",
            "period_end",
            "normalized_field",
            "source",
            name="uq_stock_financials_metric_period_source",
        ),
    )
    op.create_index("ix_stock_financials_id", "stock_financials", ["id"])
    op.create_index("ix_stock_financials_stock_id", "stock_financials", ["stock_id"])
    op.create_index("ix_stock_financials_symbol", "stock_financials", ["symbol"])
    op.create_index("ix_stock_financials_exchange", "stock_financials", ["exchange"])
    op.create_index("ix_stock_financials_statement_type", "stock_financials", ["statement_type"])
    op.create_index("ix_stock_financials_period_type", "stock_financials", ["period_type"])
    op.create_index("ix_stock_financials_period_end", "stock_financials", ["period_end"])
    op.create_index("ix_stock_financials_normalized_field", "stock_financials", ["normalized_field"])
    op.create_index("ix_stock_financials_source", "stock_financials", ["source"])
    op.create_index("ix_stock_financials_fetched_at", "stock_financials", ["fetched_at"])
    op.create_index("ix_stock_financials_stock_period", "stock_financials", ["stock_id", "period_end"])
    op.create_index(
        "ix_stock_financials_statement_field",
        "stock_financials",
        ["statement_type", "normalized_field"],
    )


def downgrade() -> None:
    op.drop_index("ix_stock_financials_statement_field", table_name="stock_financials")
    op.drop_index("ix_stock_financials_stock_period", table_name="stock_financials")
    op.drop_index("ix_stock_financials_fetched_at", table_name="stock_financials")
    op.drop_index("ix_stock_financials_source", table_name="stock_financials")
    op.drop_index("ix_stock_financials_normalized_field", table_name="stock_financials")
    op.drop_index("ix_stock_financials_period_end", table_name="stock_financials")
    op.drop_index("ix_stock_financials_period_type", table_name="stock_financials")
    op.drop_index("ix_stock_financials_statement_type", table_name="stock_financials")
    op.drop_index("ix_stock_financials_exchange", table_name="stock_financials")
    op.drop_index("ix_stock_financials_symbol", table_name="stock_financials")
    op.drop_index("ix_stock_financials_stock_id", table_name="stock_financials")
    op.drop_index("ix_stock_financials_id", table_name="stock_financials")
    op.drop_table("stock_financials")
