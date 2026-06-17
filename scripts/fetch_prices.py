from __future__ import annotations

import argparse
from datetime import date, timedelta
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.append(str(BACKEND))

from app.database import SessionLocal  # noqa: E402
from app.models.stock import Stock  # noqa: E402
from app.services.market_data_service import (  # noqa: E402
    previous_business_day,
    sync_all_active_stocks,
    sync_stock_prices,
)


def parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def print_progress(event: dict) -> None:
    symbol = event.get("symbol", "UNKNOWN")
    stock_index = event.get("stock_index") or "?"
    total_symbols = event.get("total_symbols") or "?"
    if event.get("event") == "stock_skipped":
        print(
            f"[{stock_index}/{total_symbols}] {symbol} skipped: {event.get('reason')} "
            f"last={event.get('last_available_date')} target_end={event.get('end_date')}",
            flush=True,
        )
        return
    if event.get("event") == "chunk_started":
        print(
            f"[{stock_index}/{total_symbols}] {symbol} chunk "
            f"{event.get('chunk_index')}/{event.get('total_chunks')} "
            f"{event.get('start_date') or 'period'} -> {event.get('end_date') or 'latest'}",
            flush=True,
        )
        return
    if event.get("event") == "chunk_finished":
        print(
            f"    saved={event.get('rows_saved')} total_saved_for_stock={event.get('rows_saved_total')}",
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch daily yfinance candles into stock_prices.")
    parser.add_argument("--stock-id", type=int)
    parser.add_argument("--symbol")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offset", type=int, default=None, help="Skip N active stocks for batched sync.")
    parser.add_argument("--exchange", choices=["NSE", "BSE"])
    parser.add_argument("--period", default="1y")
    parser.add_argument(
        "--interval",
        choices=["1d"],
        default="1d",
        help="Daily candles only. Minute/intraday ingestion is intentionally not supported in this MVP.",
    )
    parser.add_argument("--years", type=int, help="Fetch long history using start/end dates, e.g. 10 or 15.")
    parser.add_argument("--start-date", help="YYYY-MM-DD. Overrides --period when provided.")
    parser.add_argument("--end-date", help="YYYY-MM-DD. Defaults to T-1 when --years or --incremental is used.")
    parser.add_argument(
        "--chunk-days",
        type=int,
        default=365,
        help="Split explicit date ranges into N-day yfinance requests. Default: 365.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=1.0,
        help="Sleep after each yfinance request to reduce throttling risk. Default: 1.0.",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Start from the last stored daily candle + 1 day and sync through T-1.",
    )
    parser.add_argument("--quiet-progress", action="store_true", help="Disable per-chunk terminal progress.")
    args = parser.parse_args()

    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.offset is not None and args.offset < 0:
        parser.error("--offset must be zero or greater")
    if args.chunk_days is not None and args.chunk_days < 1:
        parser.error("--chunk-days must be at least 1")
    if args.sleep_seconds < 0:
        parser.error("--sleep-seconds cannot be negative")

    end_date = parse_date(args.end_date)
    start_date = parse_date(args.start_date)
    if args.years:
        end_date = end_date or previous_business_day()
        start_date = start_date or (end_date - timedelta(days=args.years * 365 + args.years // 4))
    elif args.start_date or args.incremental:
        end_date = end_date or previous_business_day()
    if start_date and end_date and start_date > end_date:
        parser.error("--start-date must be before or equal to --end-date")

    progress_callback = None if args.quiet_progress else print_progress
    with SessionLocal() as db:
        if args.all:
            print(
                "Starting daily ingestion "
                f"mode={'incremental' if args.incremental else 'full'} "
                f"exchange={args.exchange or 'ALL'} limit={args.limit} offset={args.offset} "
                f"start={start_date} end={end_date} chunk_days={args.chunk_days} "
                f"sleep_seconds={args.sleep_seconds}",
                flush=True,
            )
            result = sync_all_active_stocks(
                db,
                limit=args.limit,
                offset=args.offset,
                period=args.period,
                interval=args.interval,
                start_date=start_date,
                end_date=end_date,
                exchange=args.exchange,
                chunk_days=args.chunk_days,
                sleep_seconds=args.sleep_seconds,
                incremental=args.incremental,
                progress_callback=progress_callback,
            )
            total_rows = sum(result.values())
            print(f"Finished daily ingestion. symbols={len(result)} rows_saved={total_rows}", flush=True)
            print(result)
            return
        stock_id = args.stock_id
        if args.symbol and not stock_id:
            stock = db.scalar(select(Stock).where(Stock.yahoo_symbol == args.symbol.upper()))
            if stock is None:
                stock = db.scalar(select(Stock).where(Stock.symbol == args.symbol.upper()))
            if stock:
                stock_id = stock.id
        if not stock_id:
            raise SystemExit("Provide --stock-id, --symbol, or --all")
        sync_result = sync_stock_prices(
            db,
            stock_id,
            period=args.period,
            interval=args.interval,
            start_date=start_date,
            end_date=end_date,
            chunk_days=args.chunk_days,
            sleep_seconds=args.sleep_seconds,
            incremental=args.incremental,
            progress_callback=progress_callback,
            stock_index=1,
            total_symbols=1,
        )
        date_range = f" start={start_date} end={end_date}" if start_date else f" period={args.period}"
        print(
            f"Saved {sync_result.rows_saved} candles for stock_id={stock_id}{date_range} "
            f"interval={args.interval} outcome={sync_result.outcome}"
        )


if __name__ == "__main__":
    main()
