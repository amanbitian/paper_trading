"""
Backfill or update daily OHLCV for index_funds from Yahoo Finance.

Initial load:
    python scripts/ingest_index_funds.py --start-date 2010-01-01 --chunk-days 365 --sleep-seconds 1

Daily update:
    python scripts/ingest_index_funds.py --incremental --sleep-seconds 1
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date
from pathlib import Path

from sqlalchemy import func, select


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.append(str(BACKEND))

from app.database import SessionLocal  # noqa: E402
from app.models.index_fund import IndexFund  # noqa: E402
from app.services.index_fund_service import sync_all_active_index_funds  # noqa: E402
from app.services.market_data_service import previous_business_day  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def count_active_index_funds(category: str | None) -> int:
    with SessionLocal() as db:
        stmt = select(func.count()).select_from(IndexFund).where(IndexFund.is_active.is_(True))
        if category:
            stmt = stmt.where(IndexFund.category == category.strip().lower())
        return int(db.scalar(stmt) or 0)


def print_progress(event: dict) -> None:
    symbol = event.get("symbol", "UNKNOWN")
    item_index = event.get("item_index") or "?"
    total_items = event.get("total_items") or "?"
    if event.get("event") == "index_fund_skipped":
        print(
            f"[{item_index}/{total_items}] {symbol} skipped: {event.get('reason')} "
            f"last={event.get('last_available_date')} target_end={event.get('end_date')}",
            flush=True,
        )
        return
    if event.get("event") == "chunk_started":
        print(
            f"[{item_index}/{total_items}] {symbol} chunk "
            f"{event.get('chunk_index')}/{event.get('total_chunks')} "
            f"{event.get('start_date')} -> {event.get('end_date')}",
            flush=True,
        )
        return
    if event.get("event") == "chunk_finished":
        print(
            f"    saved={event.get('rows_saved')} total_saved_for_item={event.get('rows_saved_total')}",
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill/update index_fund_prices for index and commodity tickers."
    )
    parser.add_argument("--start-date", default="2010-01-01", help="YYYY-MM-DD (default: 2010-01-01).")
    parser.add_argument("--end-date", help="YYYY-MM-DD (default: previous business day).")
    parser.add_argument("--category", choices=["index", "commodity"], help="Limit to one category.")
    parser.add_argument("--limit", type=int, help="Limit number of index funds for testing.")
    parser.add_argument("--offset", type=int, default=0, help="Skip N active index funds.")
    parser.add_argument(
        "--chunk-days",
        type=int,
        default=365,
        help="Split Yahoo requests by N-day windows (default: 365).",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=1.0,
        help="Pause after each Yahoo request to reduce throttling risk (default: 1.0).",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Fetch from last stored candle + 1 through end-date.",
    )
    parser.add_argument("--quiet-progress", action="store_true", help="Disable per-chunk terminal progress.")
    args = parser.parse_args()

    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.offset < 0:
        parser.error("--offset must be zero or greater")
    if args.chunk_days < 1:
        parser.error("--chunk-days must be at least 1")
    if args.sleep_seconds < 0:
        parser.error("--sleep-seconds cannot be negative")

    start_date = date.fromisoformat(args.start_date)
    end_date = date.fromisoformat(args.end_date) if args.end_date else previous_business_day()
    if start_date > end_date:
        parser.error("--start-date must be on or before --end-date")

    total = count_active_index_funds(args.category)
    if total == 0:
        raise SystemExit("No active index funds found. Run scripts/load_index_funds.py first.")

    logger.info(
        "Starting index ingestion total=%s category=%s limit=%s offset=%s start=%s end=%s "
        "chunk_days=%s sleep_seconds=%s incremental=%s",
        total,
        args.category or "ALL",
        args.limit,
        args.offset,
        start_date,
        end_date,
        args.chunk_days,
        args.sleep_seconds,
        args.incremental,
    )
    started_at = time.perf_counter()
    with SessionLocal() as db:
        result = sync_all_active_index_funds(
            db,
            limit=args.limit,
            offset=args.offset,
            category=args.category,
            start_date=None if args.incremental else start_date,
            end_date=end_date,
            chunk_days=args.chunk_days,
            sleep_seconds=args.sleep_seconds,
            incremental=args.incremental,
            progress_callback=None if args.quiet_progress else print_progress,
        )
    elapsed = time.perf_counter() - started_at
    logger.info(
        "Index ingestion finished in %.1fs: items=%s rows_saved=%s",
        elapsed,
        len(result),
        sum(result.values()),
    )
    print(result)


if __name__ == "__main__":
    main()
