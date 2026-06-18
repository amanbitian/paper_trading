"""
Populate sector and industry on stocks using Yahoo Finance metadata.

Run once after ticker load:

    python scripts/enrich_stock_metadata.py --only-with-prices
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yfinance as yf
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.append(str(BACKEND))

from app.database import SessionLocal  # noqa: E402
from app.models.stock import Stock, StockPerformanceSnapshot  # noqa: E402
from app.services.market_data_service import default_ingestion_workers  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _fetch_metadata(yahoo_symbol: str) -> tuple[str | None, str | None]:
    for attempt in range(3):
        try:
            info = yf.Ticker(yahoo_symbol).info or {}
            sector = info.get("sector")
            industry = info.get("industry")
            sector = str(sector).strip() if sector else None
            industry = str(industry).strip() if industry else None
            return sector, industry
        except Exception:
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
            continue
    return None, None


def _load_targets(*, only_with_prices: bool) -> list[tuple[int, str]]:
    with SessionLocal() as db:
        stmt = select(Stock.id, Stock.yahoo_symbol).where(
            Stock.is_active.is_(True),
            (Stock.industry.is_(None)) | (Stock.industry == ""),
        )
        if only_with_prices:
            stmt = stmt.join(
                StockPerformanceSnapshot,
                StockPerformanceSnapshot.stock_id == Stock.id,
            ).where(StockPerformanceSnapshot.latest_price.is_not(None))
        stmt = stmt.order_by(Stock.id.asc())
        return [(int(row[0]), str(row[1])) for row in db.execute(stmt).all()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich stocks with Yahoo sector/industry metadata.")
    parser.add_argument(
        "--only-with-prices",
        action="store_true",
        help="Only enrich stocks that already have stored daily prices.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=min(8, default_ingestion_workers()),
        help="Parallel Yahoo metadata workers (keep low to avoid Yahoo rate limits).",
    )
    parser.add_argument("--limit", type=int, help="Max symbols to enrich (for testing).")
    args = parser.parse_args()

    targets = _load_targets(only_with_prices=args.only_with_prices)
    if args.limit is not None:
        targets = targets[: args.limit]
    if not targets:
        logger.info("No stocks need metadata enrichment.")
        return

    logger.info("Enriching metadata for %s stocks with %s workers...", len(targets), args.workers)
    updated = 0
    with SessionLocal() as db:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {
                pool.submit(_fetch_metadata, yahoo_symbol): stock_id
                for stock_id, yahoo_symbol in targets
            }
            for index, future in enumerate(as_completed(futures), start=1):
                stock_id = futures[future]
                sector, industry = future.result()
                if not sector and not industry:
                    continue
                stock = db.get(Stock, stock_id)
                if stock is None:
                    continue
                if sector:
                    stock.sector = sector
                if industry:
                    stock.industry = industry
                updated += 1
                if updated % 100 == 0:
                    db.commit()
                    logger.info("Updated %s/%s stocks so far...", updated, len(targets))
        db.commit()
    logger.info("Metadata enrichment complete. Updated %s stocks.", updated)


if __name__ == "__main__":
    main()
