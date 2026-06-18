"""Copy index/commodity data from a mistaken separate DB into the main app DB.

This does not call Yahoo Finance. It reads source tables named index_funds and
index_fund_prices, upserts instruments by yahoo_symbol, and upserts prices by
instrument/date/timeframe.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import sessionmaker

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
sys.path.append(str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.models.index_fund import IndexFund, IndexFundPrice  # noqa: E402


def _source_table(source_engine, table_name: str) -> Table:
    metadata = MetaData()
    return Table(table_name, metadata, autoload_with=source_engine)


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row._mapping if hasattr(row, "_mapping") else row)


def migrate_index_funds(source_url: str, target_url: str, batch_size: int = 5000) -> dict[str, int]:
    source_engine = create_engine(source_url, pool_pre_ping=True)
    target_engine = create_engine(target_url, pool_pre_ping=True)
    TargetSession = sessionmaker(bind=target_engine, autoflush=False, autocommit=False)

    source_funds = _source_table(source_engine, "index_funds")
    source_prices = _source_table(source_engine, "index_fund_prices")

    source_to_target_id: dict[int, int] = {}
    funds_upserted = 0
    prices_upserted = 0

    with source_engine.connect() as source_conn, TargetSession() as target_db:
        for row in source_conn.execute(select(source_funds)).mappings():
            values = _row_dict(row)
            source_id = int(values["id"])
            stmt = insert(IndexFund).values(
                symbol=values["symbol"],
                yahoo_symbol=values["yahoo_symbol"],
                base_currency=values.get("base_currency") or "INR",
                latest_price=values.get("latest_price"),
                value_in_inr=values.get("value_in_inr"),
                category=values.get("category") or "index",
                is_active=values.get("is_active", True),
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_index_funds_yahoo_symbol",
                set_={
                    "symbol": stmt.excluded.symbol,
                    "base_currency": stmt.excluded.base_currency,
                    "latest_price": stmt.excluded.latest_price,
                    "value_in_inr": stmt.excluded.value_in_inr,
                    "category": stmt.excluded.category,
                    "is_active": stmt.excluded.is_active,
                },
            ).returning(IndexFund.id)
            target_id = target_db.scalar(stmt)
            if target_id is None:
                raise RuntimeError(f"Could not upsert index fund {values['yahoo_symbol']}")
            source_to_target_id[source_id] = int(target_id)
            funds_upserted += 1
        target_db.commit()

        buffer: list[dict[str, Any]] = []
        for row in source_conn.execute(select(source_prices)).mappings():
            values = _row_dict(row)
            source_fund_id = int(values["index_fund_id"])
            target_fund_id = source_to_target_id.get(source_fund_id)
            if target_fund_id is None:
                continue
            buffer.append(
                {
                    "index_fund_id": target_fund_id,
                    "price_datetime": values["price_datetime"],
                    "timeframe": values.get("timeframe") or "1d",
                    "open": values.get("open"),
                    "high": values.get("high"),
                    "low": values.get("low"),
                    "close": values.get("close"),
                    "adjusted_close": values.get("adjusted_close"),
                    "volume": values.get("volume"),
                    "source": values.get("source") or "yfinance",
                }
            )
            if len(buffer) >= batch_size:
                prices_upserted += _flush_prices(target_db, buffer)
                buffer = []
        if buffer:
            prices_upserted += _flush_prices(target_db, buffer)

    source_engine.dispose()
    target_engine.dispose()
    return {"funds_upserted": funds_upserted, "prices_upserted": prices_upserted}


def _flush_prices(target_db, rows: list[dict[str, Any]]) -> int:
    stmt = insert(IndexFundPrice).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_index_fund_prices_fund_dt_tf",
        set_={
            "open": stmt.excluded.open,
            "high": stmt.excluded.high,
            "low": stmt.excluded.low,
            "close": stmt.excluded.close,
            "adjusted_close": stmt.excluded.adjusted_close,
            "volume": stmt.excluded.volume,
            "source": stmt.excluded.source,
        },
    )
    target_db.execute(stmt)
    target_db.commit()
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Move index fund tables into the main paper_trading DB.")
    parser.add_argument(
        "--source-url",
        default=os.getenv(
            "INDEX_SOURCE_DATABASE_URL",
            "postgresql+psycopg2://postgres:postgres@localhost:5432/index_funds",
        ),
        help="Mistaken/source DB URL that contains index_funds and index_fund_prices.",
    )
    parser.add_argument(
        "--target-url",
        default=os.getenv("DATABASE_URL", settings.database_url),
        help="Main paper_trading DB URL.",
    )
    parser.add_argument("--batch-size", type=int, default=5000)
    args = parser.parse_args()

    result = migrate_index_funds(args.source_url, args.target_url, batch_size=args.batch_size)
    print(
        "Migrated index data into main DB: "
        f"funds_upserted={result['funds_upserted']} prices_upserted={result['prices_upserted']}"
    )


if __name__ == "__main__":
    main()
