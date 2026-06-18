"""Add hot-path indexes for stock detail and search.

Revision ID: 0028_hot_path_indexes
Revises: 0027_stock_strategy_explanations
Create Date: 2026-06-18
"""

from alembic import op
import sqlalchemy as sa


revision = "0028_hot_path_indexes"
down_revision = "0027_stock_strategy_explanations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_index(
        "ix_stock_prices_1d_stock_datetime_desc",
        "stock_prices",
        ["stock_id", sa.text("price_datetime DESC")],
        postgresql_where=sa.text("timeframe = '1d'"),
    )
    op.create_index(
        "ix_stock_perf_snap_exchange_latest_dt",
        "stock_performance_snapshots",
        ["exchange", sa.text("latest_price_datetime DESC")],
        postgresql_where=sa.text("latest_price_datetime IS NOT NULL"),
    )
    op.create_index(
        "ix_stocks_active_sector",
        "stocks",
        ["sector"],
        postgresql_where=sa.text("is_active IS TRUE AND sector IS NOT NULL AND sector <> ''"),
    )
    op.create_index(
        "ix_stocks_active_industry",
        "stocks",
        ["industry"],
        postgresql_where=sa.text("is_active IS TRUE AND industry IS NOT NULL AND industry <> ''"),
    )
    op.create_index(
        "ix_stocks_active_sector_industry",
        "stocks",
        ["sector", "industry"],
        postgresql_where=sa.text(
            "is_active IS TRUE AND sector IS NOT NULL AND sector <> '' "
            "AND industry IS NOT NULL AND industry <> ''"
        ),
    )
    op.create_index(
        "ix_stocks_symbol_trgm",
        "stocks",
        ["symbol"],
        postgresql_using="gin",
        postgresql_ops={"symbol": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_stocks_yahoo_symbol_trgm",
        "stocks",
        ["yahoo_symbol"],
        postgresql_using="gin",
        postgresql_ops={"yahoo_symbol": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_stocks_company_name_trgm",
        "stocks",
        ["company_name"],
        postgresql_using="gin",
        postgresql_ops={"company_name": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_stocks_sector_trgm",
        "stocks",
        ["sector"],
        postgresql_using="gin",
        postgresql_ops={"sector": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_stocks_industry_trgm",
        "stocks",
        ["industry"],
        postgresql_using="gin",
        postgresql_ops={"industry": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_stocks_industry_trgm", table_name="stocks")
    op.drop_index("ix_stocks_sector_trgm", table_name="stocks")
    op.drop_index("ix_stocks_company_name_trgm", table_name="stocks")
    op.drop_index("ix_stocks_yahoo_symbol_trgm", table_name="stocks")
    op.drop_index("ix_stocks_symbol_trgm", table_name="stocks")
    op.drop_index("ix_stocks_active_sector_industry", table_name="stocks")
    op.drop_index("ix_stocks_active_industry", table_name="stocks")
    op.drop_index("ix_stocks_active_sector", table_name="stocks")
    op.drop_index("ix_stock_perf_snap_exchange_latest_dt", table_name="stock_performance_snapshots")
    op.drop_index("ix_stock_prices_1d_stock_datetime_desc", table_name="stock_prices")
