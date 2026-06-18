"""Sync deep historical fundamentals into stock_financials.

Examples:
    python backend/scripts/sync_historical_fundamentals.py --symbol RELIANCE
    python backend/scripts/sync_historical_fundamentals.py --limit 5
    python backend/scripts/sync_historical_fundamentals.py --all --sleep-seconds 2.5
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import case, select


BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent
DEFAULT_CHECKPOINT_FILE = ROOT_DIR / "data" / "ingestion_checkpoints" / "historical_fundamentals_screener.json"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.database import SessionLocal  # noqa: E402
from app.models.stock import Stock  # noqa: E402
from app.services.fundamentals_service import sync_screener_historical_financials  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync historical financial statement rows from Screener exports.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--symbol", help="Sync one NSE/BSE symbol, e.g. RELIANCE.")
    group.add_argument("--all", action="store_true", help="Sync every eligible active stock.")
    group.add_argument("--limit", type=int, default=5, help="Sync the first N eligible stocks (default: 5).")
    parser.add_argument("--exchange", help="Restrict to one exchange, e.g. NSE or BSE.")
    parser.add_argument("--offset", type=int, default=0, help="Skip the first N eligible stocks.")
    parser.add_argument("--standalone", action="store_true", help="Use standalone Screener exports instead of consolidated.")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--sleep-seconds", type=float, default=2.0)
    parser.add_argument(
        "--checkpoint-file",
        type=Path,
        default=DEFAULT_CHECKPOINT_FILE,
        help=f"Resume/checkpoint JSON file. Default: {DEFAULT_CHECKPOINT_FILE}",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore completed symbols in the checkpoint, but still write progress.",
    )
    return parser.parse_args()


def _eligible_stocks(args: argparse.Namespace) -> list[Stock]:
    exchange_priority = case(
        (Stock.exchange == "NSE", 0),
        (Stock.exchange == "BSE", 1),
        else_=2,
    )
    stmt = select(Stock).where(Stock.is_active.is_(True)).order_by(exchange_priority, Stock.symbol.asc())
    if args.symbol:
        stmt = stmt.where(Stock.symbol == args.symbol.strip().upper())
    if args.exchange:
        stmt = stmt.where(Stock.exchange == args.exchange.strip().upper())
    if args.offset:
        stmt = stmt.offset(max(0, int(args.offset)))
    if not args.all and not args.symbol:
        stmt = stmt.limit(max(1, int(args.limit or 5)))
    with SessionLocal() as db:
        return list(db.scalars(stmt))


def _stock_key(stock: Stock) -> str:
    return f"{stock.exchange}:{stock.symbol}"


def _load_checkpoint(path: Path) -> dict:
    if not path.exists():
        return {
            "completed": {},
            "failed": {},
            "runs": [],
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "completed": {},
            "failed": {},
            "runs": [],
            "checkpoint_warning": f"Could not read checkpoint file: {path}",
        }


def _save_checkpoint(path: Path, checkpoint: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(checkpoint, indent=2, sort_keys=True), encoding="utf-8")


def _progress_line(
    *,
    current: int,
    total: int,
    successes: int,
    failures: int,
    skipped: int,
    rows_upserted: int,
    symbol: str,
    status: str,
) -> str:
    width = 30
    completed = int((current / total) * width) if total else width
    bar = "#" * completed + "-" * (width - completed)
    pct = (current / total * 100) if total else 100
    return (
        f"\r[{bar}] {current}/{total} {pct:5.1f}% "
        f"ok={successes} failed={failures} skipped={skipped} "
        f"rows={rows_upserted} last={symbol}:{status}"
    )


def _render_progress(**kwargs) -> None:
    print(_progress_line(**kwargs), end="", flush=True)


def _checkpoint_run_metadata(args: argparse.Namespace, stocks: list[Stock]) -> dict:
    return {
        "started_at": datetime.now(UTC).isoformat(),
        "source": "screener",
        "mode": "standalone" if args.standalone else "consolidated",
        "symbol": args.symbol.strip().upper() if args.symbol else None,
        "exchange": args.exchange.strip().upper() if args.exchange else None,
        "all": bool(args.all),
        "limit": None if args.all or args.symbol else int(args.limit or 5),
        "offset": max(0, int(args.offset or 0)),
        "selected_count": len(stocks),
    }


def main() -> int:
    args = parse_args()
    stocks = _eligible_stocks(args)
    if not stocks:
        print("No active stocks matched the selection.")
        return 1

    print(f"Selected {len(stocks)} stock(s). Source: Screener export.")
    checkpoint_path = args.checkpoint_file.resolve()
    checkpoint = _load_checkpoint(checkpoint_path)
    checkpoint.setdefault("completed", {})
    checkpoint.setdefault("failed", {})
    checkpoint.setdefault("runs", [])
    checkpoint["latest_run"] = _checkpoint_run_metadata(args, stocks)
    checkpoint["runs"].append(checkpoint["latest_run"])
    _save_checkpoint(checkpoint_path, checkpoint)
    print(f"Checkpoint: {checkpoint_path}")

    successes = 0
    failures = 0
    skipped = 0
    rows_upserted = 0
    failure_details: list[str] = []
    total = len(stocks)
    with SessionLocal() as db:
        for index, stock in enumerate(stocks, start=1):
            key = _stock_key(stock)
            if not args.no_resume and key in checkpoint["completed"]:
                skipped += 1
                _render_progress(
                    current=index,
                    total=total,
                    successes=successes,
                    failures=failures,
                    skipped=skipped,
                    rows_upserted=rows_upserted,
                    symbol=stock.symbol,
                    status="skipped",
                )
                continue
            if index > 1 and args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)
            try:
                result = sync_screener_historical_financials(
                    db,
                    stock,
                    consolidated=not args.standalone,
                    timeout_seconds=args.timeout_seconds,
                    commit=True,
                )
            except Exception as exc:
                db.rollback()
                failures += 1
                checkpoint["failed"][key] = {
                    "symbol": stock.symbol,
                    "exchange": stock.exchange,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "updated_at": datetime.now(UTC).isoformat(),
                }
                if len(failure_details) < 10:
                    failure_details.append(f"{stock.exchange}:{stock.symbol} - {type(exc).__name__}: {exc}")
                _save_checkpoint(checkpoint_path, checkpoint)
                _render_progress(
                    current=index,
                    total=total,
                    successes=successes,
                    failures=failures,
                    skipped=skipped,
                    rows_upserted=rows_upserted,
                    symbol=stock.symbol,
                    status="failed",
                )
                continue
            successes += 1
            rows_upserted += int(result["rows_upserted"])
            checkpoint["completed"][key] = {
                "symbol": stock.symbol,
                "exchange": stock.exchange,
                "rows_upserted": int(result["rows_upserted"]),
                "statement_counts": result["statement_counts"],
                "source_url": result["source_url"],
                "fetch_mode": result.get("fetch_mode"),
                "updated_at": datetime.now(UTC).isoformat(),
            }
            checkpoint["failed"].pop(key, None)
            _save_checkpoint(checkpoint_path, checkpoint)
            _render_progress(
                current=index,
                total=total,
                successes=successes,
                failures=failures,
                skipped=skipped,
                rows_upserted=rows_upserted,
                symbol=stock.symbol,
                status="ok",
            )

    print()
    print("\n=== Historical fundamentals sync ===")
    print(f"successes:     {successes}")
    print(f"failures:      {failures}")
    print(f"skipped:       {skipped}")
    print(f"rows upserted: {rows_upserted}")
    print(f"checkpoint:    {checkpoint_path}")
    if failure_details:
        print("\nRecent failures:")
        for detail in failure_details:
            print(f"- {detail}")
    return 0 if successes > 0 or (skipped > 0 and failures == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
