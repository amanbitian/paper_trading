"""One-off CLI to backfill the deepest available daily price history.

Unlike the regular incremental sync (which only fetches the handful of
trading days since each symbol's last stored date) and the regular full sync
(which uses `settings.yfinance_default_period`, typically a rolling window
of a few years), this pulls everything yfinance has on file for each active
symbol going back to `--start-date` (default 1998-01-01 -- see
`market_data_service.BACKFILL_EARLIEST_START_DATE`).

This is a slow, API-heavy operation across the whole universe -- prefer
`--limit` for a smoke test before running `--all`.

Examples:
    python scripts/backfill_stock_history.py --limit 5
    python scripts/backfill_stock_history.py --all --chunk-days 1825 --sleep-seconds 0.5
    python scripts/backfill_stock_history.py --all --start-date 2005-01-01 --exchange NSE
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.database import SessionLocal  # noqa: E402
from app.services.market_data_service import (  # noqa: E402
    BACKFILL_DEFAULT_CHUNK_DAYS,
    BACKFILL_EARLIEST_START_DATE,
    backfill_full_price_history,
    default_ingestion_workers,
)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date {value!r}; expected YYYY-MM-DD") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill the deepest available daily OHLCV history for active stocks.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="Backfill every eligible active stock.")
    group.add_argument("--limit", type=int, default=5, help="Limit eligible stocks for a test run (default: 5).")
    parser.add_argument("--offset", type=int, default=None, help="Skip the first N eligible stocks.")
    parser.add_argument("--exchange", type=str, default=None, help="Restrict to one exchange, e.g. NSE or BSE.")
    parser.add_argument(
        "--start-date",
        type=_parse_date,
        default=None,
        help=f"Earliest date to request (YYYY-MM-DD). Default: {BACKFILL_EARLIEST_START_DATE.isoformat()}.",
    )
    parser.add_argument(
        "--end-date",
        type=_parse_date,
        default=None,
        help="Latest date to request (YYYY-MM-DD). Default: most recent completed trading day.",
    )
    parser.add_argument(
        "--chunk-days",
        type=int,
        default=BACKFILL_DEFAULT_CHUNK_DAYS,
        help=f"Split each symbol's request into windows of this many days (default: {BACKFILL_DEFAULT_CHUNK_DAYS}).",
    )
    parser.add_argument("--sleep-seconds", type=float, default=0.5, help="Pause between symbols (default: 0.5s).")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel sync workers. Default 1 (sequential); pass 0 to use the default worker count for this machine.",
    )
    parser.add_argument("--download-batch-size", type=int, default=40)
    return parser.parse_args()


def _print_summary(result: dict) -> None:
    print("\n=== Backfill summary ===")
    print(f"status:               {result.get('status')}")
    print(f"message:              {result.get('message')}")
    print(f"symbols selected:     {result.get('symbols_selected')}")
    print(f"symbols attempted:    {result.get('symbols_attempted')}")
    print(f"symbols synced:       {result.get('symbols_synced')}")
    print(f"symbols skipped:      {result.get('symbols_skipped')}")
    print(f"symbols succeeded:    {result.get('symbols_success')}")
    print(f"symbols failed:       {result.get('symbols_failed')}")
    print(f"rows saved:           {result.get('rows_saved')}")
    print(f"effective start date: {result.get('effective_start_date')}")
    print(f"effective end date:   {result.get('effective_end_date')}")
    print(f"latest stored before: {result.get('latest_stored_date_before')}")
    print(f"latest stored after:  {result.get('latest_stored_date_after')}")
    print(f"skip breakdown:       {result.get('skip_breakdown')}")
    print(f"ingestion run id:     {result.get('run_id')}")


def main() -> int:
    args = parse_args()
    limit = None if args.all else max(1, int(args.limit or 5))
    workers = default_ingestion_workers() if args.workers == 0 else max(1, int(args.workers))

    db = SessionLocal()
    try:
        result = backfill_full_price_history(
            db,
            start_date=args.start_date,
            end_date=args.end_date,
            limit=limit,
            offset=args.offset,
            exchange=args.exchange,
            chunk_days=args.chunk_days,
            sleep_seconds=max(0.0, float(args.sleep_seconds)),
            workers=workers,
            download_batch_size=max(1, int(args.download_batch_size)),
        )
        _print_summary(result)
        return 0 if result.get("status") in {"success", "partial", "warning"} else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
