"""Precompute stock detail snapshots for fast stock-open pages.

Examples:
    python backend/scripts/refresh_stock_detail_snapshots.py --symbol RELIANCE --exchange NSE
    python backend/scripts/refresh_stock_detail_snapshots.py --exchange NSE --limit 100
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import case, select


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.database import SessionLocal  # noqa: E402
from app.models.stock import Stock  # noqa: E402
from app.services.stock_detail_snapshot_service import DEFAULT_TTL_HOURS  # noqa: E402
from app.services.web_explore_stock_helpers import refresh_stock_detail_snapshot  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh cached stock detail snapshots used by stock detail pages.",
    )
    parser.add_argument("--symbol", help="Refresh one symbol, e.g. RELIANCE.")
    parser.add_argument("--exchange", help="Restrict to one exchange, e.g. NSE or BSE.")
    parser.add_argument("--limit", type=int, default=25, help="Refresh first N active stocks (default: 25).")
    parser.add_argument("--offset", type=int, default=0, help="Skip the first N active stocks.")
    parser.add_argument("--ttl-hours", type=int, default=DEFAULT_TTL_HOURS, help="Snapshot TTL in hours.")
    return parser.parse_args()


def _progress_line(*, current: int, total: int, refreshed: int, failed: int, symbol: str, status: str) -> str:
    width = 30
    completed = int((current / total) * width) if total else width
    bar = "#" * completed + "-" * (width - completed)
    pct = (current / total * 100) if total else 100
    return (
        f"\r[{bar}] {current}/{total} {pct:5.1f}% "
        f"refreshed={refreshed} failed={failed} last={symbol}:{status}"
    )


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
    if args.limit and not args.symbol:
        stmt = stmt.limit(max(1, int(args.limit)))
    with SessionLocal() as db:
        return list(db.scalars(stmt))


def main() -> int:
    args = parse_args()
    stocks = _eligible_stocks(args)
    if not stocks:
        print("No active stocks matched the selection.")
        return 1

    print(f"Selected {len(stocks)} stock(s).")
    refreshed = 0
    failed = 0
    failure_details: list[str] = []

    with SessionLocal() as db:
        total = len(stocks)
        for index, stock in enumerate(stocks, start=1):
            try:
                refresh_stock_detail_snapshot(
                    db,
                    stock,
                    ttl_hours=max(1, int(args.ttl_hours)),
                    commit=True,
                )
                status = "ok"
                refreshed += 1
            except Exception as exc:
                db.rollback()
                status = "failed"
                failed += 1
                failure_details.append(f"{stock.exchange}:{stock.symbol} - {exc}")
            print(
                _progress_line(
                    current=index,
                    total=total,
                    refreshed=refreshed,
                    failed=failed,
                    symbol=stock.symbol,
                    status=status,
                ),
                end="",
                flush=True,
            )

    print()
    print("\n=== Stock detail snapshot refresh ===")
    print(f"stocks:     {refreshed + failed}")
    print(f"refreshed:  {refreshed}")
    print(f"failed:     {failed}")
    if failure_details:
        print("\nFailures:")
        for detail in failure_details[:10]:
            print(f"- {detail}")
    return 0 if refreshed > 0 and failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
