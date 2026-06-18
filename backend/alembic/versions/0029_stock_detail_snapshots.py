"""Add precomputed stock detail snapshots.

Revision ID: 0029_stock_detail_snapshots
Revises: 0028_hot_path_indexes
Create Date: 2026-06-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0029_stock_detail_snapshots"
down_revision = "0028_hot_path_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stock_detail_snapshots",
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=50), nullable=False),
        sa.Column("yahoo_symbol", sa.String(length=80), nullable=False),
        sa.Column("exchange", sa.String(length=20), nullable=False),
        sa.Column("summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("price_rows_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("chart_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("algo_findings_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fundamentals_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("strategy_explanations_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("news_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("strategy_options_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("price_row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("from_date", sa.Date(), nullable=True),
        sa.Column("to_date", sa.Date(), nullable=True),
        sa.Column("latest_close", sa.Numeric(18, 4), nullable=True),
        sa.Column("change_1d_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("latest_volume", sa.BigInteger(), nullable=True),
        sa.Column("source_version", sa.String(length=40), nullable=False),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("stock_id"),
    )
    op.create_index("ix_stock_detail_snapshots_symbol", "stock_detail_snapshots", ["symbol"])
    op.create_index("ix_stock_detail_snapshots_yahoo_symbol", "stock_detail_snapshots", ["yahoo_symbol"])
    op.create_index("ix_stock_detail_snapshots_exchange", "stock_detail_snapshots", ["exchange"])
    op.create_index("ix_stock_detail_snapshots_expires_at", "stock_detail_snapshots", ["expires_at"])
    op.create_index(
        "ix_stock_detail_snapshots_exchange_refreshed",
        "stock_detail_snapshots",
        ["exchange", "refreshed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_stock_detail_snapshots_exchange_refreshed", table_name="stock_detail_snapshots")
    op.drop_index("ix_stock_detail_snapshots_expires_at", table_name="stock_detail_snapshots")
    op.drop_index("ix_stock_detail_snapshots_exchange", table_name="stock_detail_snapshots")
    op.drop_index("ix_stock_detail_snapshots_yahoo_symbol", table_name="stock_detail_snapshots")
    op.drop_index("ix_stock_detail_snapshots_symbol", table_name="stock_detail_snapshots")
    op.drop_table("stock_detail_snapshots")
