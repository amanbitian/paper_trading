"""
Batch-ingest daily OHLCV for all active stocks from a start date through T-1.

Use after load_indian_tickers.py. Resumable: re-run the same command; stocks that
already have full history for the range are still re-fetched (upsert is idempotent).
Prefer --incremental for daily updates after the initial backfill.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.append(str(BACKEND))

from app.database import SessionLocal  # noqa: E402
from app.models.stock import Stock  # noqa: E402
from app.services.market_data_service import (  # noqa: E402
    default_ingestion_workers,
    previous_business_day,
    sync_all_active_stocks,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def count_active_stocks(db, exchange: str | None) -> int:
    stmt = select(func.count()).select_from(Stock).where(Stock.is_active.is_(True))
    if exchange:
        stmt = stmt.where(Stock.exchange == exchange.upper())
    return int(db.scalar(stmt) or 0)


def main() -> None:
    default_workers = default_ingestion_workers()
    parser = argparse.ArgumentParser(
        description="Backfill or update daily prices for the full active stock universe in batches."
    )
    parser.add_argument("--exchange", choices=["NSE", "BSE"], help="Limit to one exchange (default: both).")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Symbols per ingestion batch (default: 500).",
    )
    parser.add_argument("--start-date", default="2010-01-01", help="YYYY-MM-DD (default: 2010-01-01).")
    parser.add_argument("--end-date", help="YYYY-MM-DD (default: previous business day).")
    parser.add_argument(
        "--chunk-days",
        type=int,
        default=0,
        help="Split Yahoo requests by N-day windows. 0 = one request per symbol (default, fastest).",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.0,
        help="Pause between chunk requests per symbol (default: 0).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=default_workers,
        help=f"Parallel save/fetch workers (default: {default_workers}, cpu={os.cpu_count()}).",
    )
    parser.add_argument(
        "--download-batch-size",
        type=int,
        default=200,
        help="Symbols per multi-ticker Yahoo download call (default: 200, notebook-style bulk fetch).",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Only fetch from last stored candle + 1 through end-date.",
    )
    parser.add_argument("--offset", type=int, default=0, help="Start batch at this symbol offset (resume).")
    parser.add_argument("--max-batches", type=int, help="Stop after N batches (for testing).")
    parser.add_argument(
        "--skip-analytics-refresh",
        action="store_true",
        help="Skip analytics snapshot refresh after ingestion (run refresh_analytics.py separately).",
    )
    args = parser.parse_args()

    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    if args.workers < 1:
        parser.error("--workers must be at least 1")

    start_date = date.fromisoformat(args.start_date)
    end_date = date.fromisoformat(args.end_date) if args.end_date else previous_business_day()
    if start_date > end_date:
        parser.error("--start-date must be on or before --end-date")

    with SessionLocal() as db:
        total = count_active_stocks(db, args.exchange)

    if total == 0:
        raise SystemExit("No active stocks found. Run load_indian_tickers.py first.")

    exchanges = [args.exchange] if args.exchange else ["NSE", "BSE"]

    for exchange in exchanges:
        with SessionLocal() as db:
            total = count_active_stocks(db, exchange)

        if total == 0:
            logger.warning("No active stocks for exchange=%s; skipping.", exchange)
            continue

        start_offset = args.offset if args.exchange else 0
        batches_total = (total - start_offset + args.batch_size - 1) // args.batch_size
        if args.max_batches is not None:
            batches_total = min(batches_total, args.max_batches)

        logger.info(
            "Starting %s ingestion exchange=%s total_symbols=%s offset=%s batch_size=%s "
            "batches=%s workers=%s download_batch_size=%s chunk_days=%s start=%s end=%s incremental=%s",
            "incremental" if args.incremental else "full",
            exchange,
            total,
            start_offset,
            args.batch_size,
            batches_total,
            args.workers,
            args.download_batch_size,
            args.chunk_days,
            start_date,
            end_date,
            args.incremental,
        )

        batch_num = 0
        offset = start_offset
        while offset < total:
            if args.max_batches is not None and batch_num >= args.max_batches:
                logger.info("Reached --max-batches=%s; stopping.", args.max_batches)
                break

            batch_num += 1
            batch_started = time.perf_counter()
            logger.info(
                "Batch %s/%s offset=%s limit=%s exchange=%s",
                batch_num,
                batches_total,
                offset,
                args.batch_size,
                exchange,
            )
            with SessionLocal() as db:
                result = sync_all_active_stocks(
                    db,
                    limit=args.batch_size,
                    offset=offset,
                    exchange=exchange,
                    start_date=None if args.incremental else start_date,
                    end_date=end_date,
                    chunk_days=args.chunk_days,
                    sleep_seconds=args.sleep_seconds,
                    incremental=args.incremental,
                    workers=args.workers,
                    download_batch_size=args.download_batch_size,
                    skip_probe=True,
                )
            rows = sum(result.values())
            elapsed = time.perf_counter() - batch_started
            logger.info(
                "Batch %s done in %.1fs: symbols=%s rows_saved=%s next_offset=%s exchange=%s",
                batch_num,
                elapsed,
                len(result),
                rows,
                offset + args.batch_size,
                exchange,
            )
            offset += args.batch_size

        logger.info("Ingestion run finished for exchange=%s at offset=%s (total=%s).", exchange, offset, total)

    if not args.incremental and not args.skip_analytics_refresh:
        logger.info("Refreshing analytics snapshots after backfill...")
        from app.services.analytics_refresh_service import refresh_all_analytics

        with SessionLocal() as db:
            stats = refresh_all_analytics(db)
        logger.info("Analytics refresh stats: %s", stats)


if __name__ == "__main__":
    main()
