"""
Bulk news + fundamentals ingestion in priority order.

  Nifty50 → Sensex → Nifty100 → Nifty200 → Nifty500 → AllIndex → AllStocks

Features
--------
* Checkpoint after every stock  →  safe to kill & resume any time
* tqdm progress bar with ETA in terminal
* Quota-safe: provider token exhaustion never stops the run (falls back to free providers)
* Automatic retry for fundamentals (network blips)

Usage
-----
  python scripts/ingest_priority.py                        # full run
  python scripts/ingest_priority.py --resume               # continue from last checkpoint
  python scripts/ingest_priority.py --reset                # wipe checkpoint, start fresh
  python scripts/ingest_priority.py --news-only            # skip fundamentals
  python scripts/ingest_priority.py --fundamentals-only    # skip news
  python scripts/ingest_priority.py --start-tier Nifty500  # begin at a specific tier
  python scripts/ingest_priority.py --skip-done            # skip stocks with fundamentals today
  python scripts/ingest_priority.py --force                # ignore news freshness window
  python scripts/ingest_priority.py --dry-run              # count only, no API calls
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tqdm import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm
from sqlalchemy import or_, select

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
LOGS_DIR = ROOT / "logs"
CHECKPOINT_FILE = LOGS_DIR / "ingest_checkpoint.json"
LOG_FILE = LOGS_DIR / "ingest_priority.log"

sys.path.append(str(BACKEND))

from app.database import SessionLocal  # noqa: E402
from app.models.fundamentals import StockFundamentalsLatest  # noqa: E402
from app.models.stock import Stock  # noqa: E402
from app.services.fundamentals_service import sync_stock_fundamentals  # noqa: E402
from app.services.news_service import refresh_stock_news  # noqa: E402

LOGS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tier definitions — order is priority; each tier only sees NEW stocks
# ---------------------------------------------------------------------------
TIERS: list[tuple[str, object]] = [
    ("Nifty50",   Stock.is_nifty50),
    ("Sensex",    Stock.is_sensex),
    ("Nifty100",  Stock.is_nifty100),
    ("Nifty200",  Stock.is_nifty200),
    ("Nifty500",  Stock.is_nifty500),
    ("AllIndex",  or_(Stock.is_banknifty, Stock.is_finnifty, Stock.is_midcpnifty)),
    ("AllStocks", Stock.is_active),
]
TIER_NAMES = [t[0] for t in TIERS]

NEWS_SLEEP = 1.0          # seconds between news calls
FUND_SLEEP = 0.2          # seconds between fundamentals calls
FUND_TIMEOUT = 30.0       # yfinance timeout per stock
MAX_RETRIES = 2           # fundamentals retry attempts
RETRY_BACKOFF = 5.0       # seconds × attempt number on retry


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def _fetch_tier(db, flag_expr, exclude_ids: set[int]) -> list[Stock]:
    rows = list(
        db.scalars(
            select(Stock)
            .where(Stock.is_active.is_(True), flag_expr)
            .order_by(Stock.symbol.asc())
        )
    )
    return [s for s in rows if s.id not in exclude_ids]


def _already_done_today(db) -> set[int]:
    cutoff = datetime.now(UTC) - timedelta(hours=20)
    return set(
        db.scalars(
            select(StockFundamentalsLatest.stock_id).where(
                StockFundamentalsLatest.fetched_at >= cutoff
            )
        ).all()
    )


# ---------------------------------------------------------------------------
# Per-stock runners — never raise; always return a dict
# ---------------------------------------------------------------------------
def _run_news(db, stock: Stock, *, force: bool) -> dict:
    try:
        return refresh_stock_news(db, stock.id, force=force, limit=10, mode="bulk")
    except Exception as exc:
        db.rollback()
        logger.warning("news error %s: %s", stock.yahoo_symbol, exc)
        return {"status": "failed", "error": str(exc), "links_new": 0}


def _run_fundamentals(db, stock: Stock) -> dict:
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            result = sync_stock_fundamentals(db, stock, timeout_seconds=FUND_TIMEOUT)
            db.commit()
            return result
        except Exception as exc:
            db.rollback()
            if attempt <= MAX_RETRIES:
                wait = RETRY_BACKOFF * attempt
                logger.warning(
                    "fundamentals retry %d/%d %s (sleep %.0fs): %s",
                    attempt, MAX_RETRIES, stock.yahoo_symbol, wait, exc,
                )
                time.sleep(wait)
            else:
                logger.error("fundamentals failed %s: %s", stock.yahoo_symbol, exc)
                return {"status": "failed", "error": str(exc), "metrics_present": 0}


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------
def _load_checkpoint() -> dict | None:
    if not CHECKPOINT_FILE.exists():
        return None
    try:
        return json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8-sig"))  # handles BOM too
    except Exception as e:
        logger.warning("Could not load checkpoint: %s", e)
        return None


def _save_checkpoint(data: dict) -> None:
    data["updated_at"] = datetime.now(UTC).isoformat()
    try:
        CHECKPOINT_FILE.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    except Exception as e:
        logger.warning("Could not save checkpoint: %s", e)


def _clear_checkpoint() -> None:
    if CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()
        logger.info("Checkpoint cleared.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Priority news + fundamentals ingestion with checkpointing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--news-only", action="store_true", help="Skip fundamentals.")
    parser.add_argument("--fundamentals-only", action="store_true", help="Skip news.")
    parser.add_argument("--force", action="store_true", help="Ignore news freshness window.")
    parser.add_argument("--dry-run", action="store_true", help="Count stocks only, no API calls.")
    parser.add_argument("--start-tier", choices=TIER_NAMES, default=None,
                        help="Skip all tiers before this one.")
    parser.add_argument("--skip-done", action="store_true",
                        help="Skip stocks whose fundamentals were fetched today.")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from the last saved checkpoint.")
    parser.add_argument("--reset", action="store_true",
                        help="Delete checkpoint file and start fresh.")
    args = parser.parse_args()

    if args.reset:
        _clear_checkpoint()

    do_news = not args.fundamentals_only
    do_fundamentals = not args.news_only

    # ---- Resolve start point (checkpoint takes priority over --start-tier) ----
    resume_tier: str | None = None
    resume_offset: int = 0  # 0-based index of first unprocessed stock within resume_tier

    if args.resume:
        cp = _load_checkpoint()
        if cp:
            resume_tier = cp.get("tier")
            resume_offset = int(cp.get("next_stock_index") or 0)
            logger.info(
                "RESUMING — tier=%s  next_index=%d  last_symbol=%s",
                resume_tier, resume_offset, cp.get("last_symbol", "?"),
            )
        else:
            logger.info("No checkpoint found — starting from the beginning.")

    effective_start = resume_tier or args.start_tier
    active = effective_start is None

    # ---- Session + counters ----
    db = SessionLocal()
    seen: set[int] = set()
    done_today: set[int] = set()
    total_processed = total_skipped = total_news_links = total_fundamentals_ok = total_failed = 0

    if args.skip_done and not args.dry_run:
        done_today = _already_done_today(db)
        logger.info("skip-done: %d stocks already have fundamentals in last 20h", len(done_today))

    checkpoint: dict = {
        "tier": None,
        "next_stock_index": 0,
        "last_symbol": None,
        "started_at": datetime.now(UTC).isoformat(),
        "stats": {},
    }

    try:
        logger.info(
            "=== Ingestion started  news=%s  fund=%s  start=%s  resume=%s"
            "  skip_done=%s  force=%s  dry_run=%s ===",
            do_news, do_fundamentals,
            effective_start or "Nifty50",
            args.resume, args.skip_done, args.force, args.dry_run,
        )

        with logging_redirect_tqdm():
            for tier_name, flag_expr in TIERS:

                # ---- Skip tiers before the effective start ----
                if not active:
                    if tier_name == effective_start:
                        active = True
                    else:
                        for s in _fetch_tier(db, flag_expr, exclude_ids=seen):
                            seen.add(s.id)
                        logger.info("Tier %-10s  SKIPPED (before start)", tier_name)
                        continue

                all_stocks = _fetch_tier(db, flag_expr, exclude_ids=seen)

                # ---- Apply resume offset (once, for the first resumed tier) ----
                skip_n = 0
                if tier_name == resume_tier and resume_offset > 0:
                    skip_n = min(resume_offset, len(all_stocks))
                    for s in all_stocks[:skip_n]:
                        seen.add(s.id)
                    logger.info(
                        "Tier %-10s  resuming at index %d/%d — skipping %d already-done stocks",
                        tier_name, skip_n, len(all_stocks), skip_n,
                    )
                    resume_tier = None  # only apply once

                stocks = all_stocks[skip_n:]

                if not stocks:
                    logger.info("Tier %-10s  0 stocks (all covered)", tier_name)
                    continue

                logger.info("Tier %-10s  %d stocks to process", tier_name, len(stocks))
                checkpoint["tier"] = tier_name

                pbar = tqdm(
                    total=len(stocks),
                    desc=f"  {tier_name:<10}",
                    unit="stock",
                    dynamic_ncols=True,
                    colour="cyan",
                    bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}] {postfix}",
                )

                try:
                    for rel_idx, stock in enumerate(stocks):
                        abs_idx = skip_n + rel_idx
                        seen.add(stock.id)
                        pbar.set_postfix_str(stock.yahoo_symbol, refresh=False)

                        if args.dry_run:
                            pbar.update(1)
                            continue

                        if args.skip_done and stock.id in done_today:
                            total_skipped += 1
                            pbar.update(1)
                            continue

                        total_processed += 1
                        news_ok = fund_ok = True

                        if do_news:
                            result = _run_news(db, stock, force=args.force)
                            links = int(result.get("links_new") or 0)
                            total_news_links += links
                            status = result.get("status", "?")
                            if status == "failed":
                                news_ok = False
                            logger.info(
                                "[%s %d/%d] %-22s  news=%-12s  links=%d",
                                tier_name, rel_idx + 1, len(stocks),
                                stock.yahoo_symbol, status, links,
                            )
                            time.sleep(NEWS_SLEEP)

                        if do_fundamentals:
                            result = _run_fundamentals(db, stock)
                            status = result.get("status", "?")
                            present = result.get("metrics_present", "-")
                            if status not in ("success", "partial"):
                                fund_ok = False
                            else:
                                total_fundamentals_ok += 1
                            logger.info(
                                "[%s %d/%d] %-22s  fund=%-10s  metrics=%s",
                                tier_name, rel_idx + 1, len(stocks),
                                stock.yahoo_symbol, status, present,
                            )
                            time.sleep(FUND_SLEEP)

                        if not news_ok or not fund_ok:
                            total_failed += 1

                        # ---- Save checkpoint after every stock ----
                        checkpoint["next_stock_index"] = abs_idx + 1
                        checkpoint["last_symbol"] = stock.yahoo_symbol
                        checkpoint["stats"] = {
                            "processed": total_processed,
                            "skipped": total_skipped,
                            "news_links_new": total_news_links,
                            "fundamentals_ok": total_fundamentals_ok,
                            "failed": total_failed,
                        }
                        _save_checkpoint(checkpoint)
                        pbar.update(1)

                finally:
                    pbar.close()

        # All tiers complete — clear the checkpoint
        _clear_checkpoint()

        logger.info(
            "=== Complete ===  processed=%d  skipped=%d"
            "  news_links=%d  fundamentals_ok=%d  failed=%d",
            total_processed if not args.dry_run else len(seen),
            total_skipped, total_news_links, total_fundamentals_ok, total_failed,
        )

    finally:
        db.close()


if __name__ == "__main__":
    main()
