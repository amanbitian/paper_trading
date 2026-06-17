"""
Refresh DB-backed stock news for one stock or a priority universe slice.

Examples:
  python scripts/ingest_stock_news.py --symbol SBIN.NS --force
  python scripts/ingest_stock_news.py --priority --limit-stocks 25
  python scripts/ingest_stock_news.py --summary
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import or_, select

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.append(str(BACKEND))

from app.database import SessionLocal  # noqa: E402
from app.models.stock import Stock  # noqa: E402
from app.services.news_service import (  # noqa: E402
    news_database_summary,
    refresh_priority_news,
    refresh_stock_news,
)
from app.utils.json_safe import to_json_safe  # noqa: E402


def resolve_stock_id(db, symbol: str) -> int:
    clean = symbol.strip().upper()
    symbol_only = clean.replace(".NS", "").replace(".BO", "")
    stock = db.scalar(
        select(Stock)
        .where(
            Stock.is_active.is_(True),
            or_(
                Stock.yahoo_symbol == clean,
                Stock.symbol == symbol_only,
                Stock.symbol == clean,
            ),
        )
        .order_by(Stock.exchange.desc())
        .limit(1)
    )
    if stock is None:
        raise SystemExit(f"No active stock matched {symbol!r}.")
    return int(stock.id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh stock news into Postgres.")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--stock-id", type=int, help="Refresh one stock by database id.")
    target.add_argument("--symbol", help="Refresh one stock by symbol or Yahoo symbol.")
    target.add_argument("--priority", action="store_true", help="Refresh Nifty/Sensex priority stocks.")
    target.add_argument("--summary", action="store_true", help="Print stock news table counts.")
    parser.add_argument("--limit", type=int, default=10, help="Articles per provider for one-stock refresh.")
    parser.add_argument("--limit-stocks", type=int, default=25, help="Priority stock count.")
    parser.add_argument("--force", action="store_true", help="Ignore freshness window.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.summary or not any([args.priority, args.stock_id, args.symbol]):
            result = news_database_summary(db)
        elif args.priority:
            result = refresh_priority_news(db, limit_stocks=args.limit_stocks, force=args.force)
        else:
            stock_id = args.stock_id or resolve_stock_id(db, args.symbol or "")
            result = refresh_stock_news(db, stock_id, force=args.force, limit=args.limit, mode="script")
        print(json.dumps(to_json_safe(result), indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
