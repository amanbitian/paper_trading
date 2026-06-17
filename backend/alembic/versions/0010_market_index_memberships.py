"""Add stock membership tags for market indices.

Revision ID: 0010_market_index_memberships
Revises: 0009_index_funds
Create Date: 2026-05-29
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_market_index_memberships"
down_revision = "0009_index_funds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_indices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("index_code", sa.String(length=40), nullable=False),
        sa.Column("index_name", sa.String(length=120), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False, server_default="NSE"),
        sa.Column("exchange", sa.String(length=20), nullable=False, server_default="NSE"),
        sa.Column("yahoo_symbol", sa.String(length=80), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("index_code", name="uq_market_indices_index_code"),
    )
    op.create_index("ix_market_indices_id", "market_indices", ["id"])
    op.create_index("ix_market_indices_index_code", "market_indices", ["index_code"])

    op.create_table(
        "stock_index_memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "index_id",
            sa.Integer(),
            sa.ForeignKey("market_indices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "stock_id",
            sa.Integer(),
            sa.ForeignKey("stocks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("symbol", sa.String(length=50), nullable=False),
        sa.Column("exchange", sa.String(length=20), nullable=False, server_default="NSE"),
        sa.Column("company_name", sa.String(length=255), nullable=True),
        sa.Column("industry", sa.String(length=120), nullable=True),
        sa.Column("series", sa.String(length=20), nullable=True),
        sa.Column("isin", sa.String(length=20), nullable=True),
        sa.Column("weight", sa.Numeric(10, 4), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("source", sa.String(length=80), nullable=False, server_default="manual"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("index_id", "stock_id", name="uq_stock_index_memberships_index_stock"),
    )
    op.create_index("ix_stock_index_memberships_id", "stock_index_memberships", ["id"])
    op.create_index("ix_stock_index_memberships_index_id", "stock_index_memberships", ["index_id"])
    op.create_index("ix_stock_index_memberships_stock_id", "stock_index_memberships", ["stock_id"])
    op.create_index("ix_stock_index_memberships_symbol", "stock_index_memberships", ["symbol"])
    op.create_index("ix_stock_index_memberships_exchange", "stock_index_memberships", ["exchange"])
    op.create_index(
        "ix_stock_index_memberships_index_active",
        "stock_index_memberships",
        ["index_id", "is_active"],
    )


def downgrade() -> None:
    op.drop_index("ix_stock_index_memberships_index_active", table_name="stock_index_memberships")
    op.drop_index("ix_stock_index_memberships_exchange", table_name="stock_index_memberships")
    op.drop_index("ix_stock_index_memberships_symbol", table_name="stock_index_memberships")
    op.drop_index("ix_stock_index_memberships_stock_id", table_name="stock_index_memberships")
    op.drop_index("ix_stock_index_memberships_index_id", table_name="stock_index_memberships")
    op.drop_index("ix_stock_index_memberships_id", table_name="stock_index_memberships")
    op.drop_table("stock_index_memberships")

    op.drop_index("ix_market_indices_index_code", table_name="market_indices")
    op.drop_index("ix_market_indices_id", table_name="market_indices")
    op.drop_table("market_indices")
