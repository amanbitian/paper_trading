"""Precompute cached strategy explanations for stock detail pages.

Examples:
    python backend/scripts/refresh_strategy_explanations.py --symbol RELIANCE --exchange NSE
    python backend/scripts/refresh_strategy_explanations.py --limit 25
    python backend/scripts/refresh_strategy_explanations.py --exchange NSE --strategies quality_momentum,macd
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
from app.services.strategy_explainer_service import (  # noqa: E402
    DEFAULT_TTL_HOURS,
    SUPPORTED_STRATEGY_TYPES,
    refresh_stock_strategy_explanations,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh cached stock strategy explanations used by the stock detail page.",
    )
    parser.add_argument("--symbol", help="Refresh one symbol, e.g. RELIANCE.")
    parser.add_argument("--exchange", help="Restrict to one exchange, e.g. NSE or BSE.")
    parser.add_argument("--limit", type=int, default=25, help="Refresh the first N active stocks (default: 25).")
    parser.add_argument("--offset", type=int, default=0, help="Skip the first N active stocks.")
    parser.add_argument(
        "--strategies",
        help=f"Comma-separated strategy types. Default: {','.join(SUPPORTED_STRATEGY_TYPES)}",
    )
    parser.add_argument("--ttl-hours", type=int, default=DEFAULT_TTL_HOURS, help="Cache TTL in hours.")
    return parser.parse_args()


def _strategy_types(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return SUPPORTED_STRATEGY_TYPES
    selected = tuple(part.strip() for part in raw.split(",") if part.strip())
    unknown = sorted(set(selected) - set(SUPPORTED_STRATEGY_TYPES))
    if unknown:
        raise ValueError(f"Unsupported strategy type(s): {', '.join(unknown)}")
    return selected


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


def _progress_line(
    *,
    current: int,
    total: int,
    refreshed: int,
    failed: int,
    symbol: str,
    status: str,
) -> str:
    width = 30
    completed = int((current / total) * width) if total else width
    bar = "#" * completed + "-" * (width - completed)
    pct = (current / total * 100) if total else 100
    return (
        f"\r[{bar}] {current}/{total} {pct:5.1f}% "
        f"refreshed={refreshed} failed={failed} last={symbol}:{status}"
    )


def main() -> int:
    args = parse_args()
    try:
        strategy_types = _strategy_types(args.strategies)
    except ValueError as exc:
        print(exc)
        return 2

    stocks = _eligible_stocks(args)
    if not stocks:
        print("No active stocks matched the selection.")
        return 1

    print(f"Selected {len(stocks)} stock(s). Strategies: {', '.join(strategy_types)}.")
    refreshed = 0
    failed = 0
    failure_details: list[str] = []

    with SessionLocal() as db:
        total = len(stocks)
        for index, stock in enumerate(stocks, start=1):
            try:
                result = refresh_stock_strategy_explanations(
                    db,
                    stock,
                    strategy_types=strategy_types,
                    ttl_hours=max(1, int(args.ttl_hours)),
                    commit=True,
                )
            except Exception as exc:
                db.rollback()
                result = {"refreshed": [], "failed": [{"strategy_type": "all", "error": str(exc)}]}

            refreshed += len(result["refreshed"])
            failed += len(result["failed"])
            if result["failed"]:
                details = "; ".join(f"{row['strategy_type']}={row['error']}" for row in result["failed"])
                failure_details.append(f"{stock.exchange}:{stock.symbol} - {details}")
            status = "ok" if not result["failed"] else "partial" if result["refreshed"] else "failed"
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
    print("\n=== Strategy explanation refresh ===")
    print(f"stocks:     {len(stocks)}")
    print(f"refreshed:  {refreshed}")
    print(f"failed:     {failed}")
    if failure_details:
        print("\nFailures:")
        for detail in failure_details[:10]:
            print(f"- {detail}")
    return 0 if refreshed > 0 and failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
