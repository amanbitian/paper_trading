from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.stock import IngestionRun, MarketAnalyticsCache
from app.services.analytics_refresh_service import refresh_all_analytics
from app.services.fundamentals_service import (
    FUNDAMENTAL_METRICS,
    TABLE_NAME as FUNDAMENTALS_TABLE_NAME,
    store_fundamentals_sync_result,
    sync_all_stock_fundamentals,
)
from app.services.market_data_service import (
    MARKET_TIMEZONE,
    default_ingestion_workers,
    sync_all_active_stocks,
)
from app.services.market_movers_service import MARKET_MOVERS_CACHE_KEY
from app.services.market_overview_service import clear_market_overview_cache
from app.utils.observability import timed

logger = logging.getLogger(__name__)

_SYNC_LOCK = threading.Lock()
_SYNC_THREAD: threading.Thread | None = None
_SYNC_ACTIVE = False
STALE_RUN_AFTER = timedelta(hours=6)


def _serialize_run(run: IngestionRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "id": run.id,
        "status": run.status,
        "ingestion_mode": run.ingestion_mode,
        "total_symbols": run.total_symbols,
        "success_count": run.success_count,
        "failed_count": run.failed_count,
        "rows_saved": run.rows_saved,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "error_message": run.error_message,
    }


def _cleanup_stale_running_runs(db: Session) -> None:
    cutoff = datetime.now(UTC) - STALE_RUN_AFTER
    stale_runs = list(
        db.scalars(
            select(IngestionRun).where(
                IngestionRun.status == "RUNNING",
                IngestionRun.started_at < cutoff,
            )
        )
    )
    if not stale_runs:
        return
    now = datetime.now(UTC)
    for run in stale_runs:
        run.status = "FAILED"
        run.error_message = "Run was interrupted or timed out."
        run.finished_at = now
    db.commit()


def _get_running_run(db: Session) -> IngestionRun | None:
    return db.scalar(
        select(IngestionRun)
        .where(IngestionRun.status == "RUNNING")
        .order_by(desc(IngestionRun.started_at))
        .limit(1)
    )


def _get_last_successful_run(db: Session) -> IngestionRun | None:
    return db.scalar(
        select(IngestionRun)
        .where(
            IngestionRun.status.in_(("SUCCEEDED", "PARTIAL")),
            IngestionRun.finished_at.is_not(None),
        )
        .order_by(desc(IngestionRun.finished_at))
        .limit(1)
    )


def _resolve_record_date(db: Session) -> Any:
    cache_row = db.get(MarketAnalyticsCache, MARKET_MOVERS_CACHE_KEY)
    if cache_row and cache_row.payload:
        record_date = cache_row.payload.get("record_date")
        if record_date:
            return record_date
    last_success = _get_last_successful_run(db)
    if last_success and last_success.end_date:
        return last_success.end_date
    return None


@timed("market.get_sync_status")
def get_market_sync_status(db: Session) -> dict[str, Any]:
    _cleanup_stale_running_runs(db)
    running = _get_running_run(db)
    last_success = _get_last_successful_run(db)
    analytics = db.get(MarketAnalyticsCache, MARKET_MOVERS_CACHE_KEY)

    last_synced_at = None
    if last_success and last_success.finished_at:
        last_synced_at = last_success.finished_at
    elif analytics is not None:
        last_synced_at = analytics.refreshed_at

    is_running = _SYNC_ACTIVE or running is not None
    return {
        "is_running": is_running,
        "run_id": running.id if running else None,
        "last_synced_at": last_synced_at,
        "last_sync_status": last_success.status if last_success else None,
        "record_date": _resolve_record_date(db),
        "current_run": _serialize_run(running),
        "last_run": _serialize_run(last_success),
    }


def _run_sync_job() -> None:
    global _SYNC_THREAD, _SYNC_ACTIVE
    db = SessionLocal()
    try:
        logger.info("Background market sync job started")
        result = sync_all_active_stocks(
            db,
            incremental=True,
            end_date=datetime.now(MARKET_TIMEZONE).date(),
            workers=default_ingestion_workers(),
            download_batch_size=200,
            chunk_days=0,
            sleep_seconds=0,
            skip_probe=True,
        )
        logger.info(
            "Background market sync job finished: status=%s message=%s rows_saved=%s "
            "selected=%s skipped=%s latest_before=%s latest_after=%s provider_latest=%s",
            result.get("status"),
            result.get("message"),
            result.get("rows_saved"),
            result.get("symbols_selected"),
            result.get("symbols_skipped"),
            result.get("latest_stored_date_before"),
            result.get("latest_stored_date_after"),
            result.get("provider_latest_date"),
        )
        analytics_result = refresh_all_analytics(db)
        logger.info(
            "Background analytics refresh finished: performance_rows=%s outcomes=%s "
            "stocks_with_prices=%s movers_universe_count=%s",
            analytics_result.get("performance_rows"),
            analytics_result.get("outcomes_evaluated"),
            analytics_result.get("stocks_with_prices"),
            analytics_result.get("movers_universe_count"),
        )
        try:
            fundamentals_result = sync_all_stock_fundamentals(db, active_only=True, limit=None)
            logger.info(
                "Background fundamentals sync finished: status=%s table=%s selected=%s "
                "succeeded=%s failed=%s rows_upserted=%s columns=%s duration_seconds=%s",
                fundamentals_result.get("status"),
                fundamentals_result.get("table_name"),
                fundamentals_result.get("selected_stocks"),
                fundamentals_result.get("succeeded"),
                fundamentals_result.get("failed"),
                fundamentals_result.get("rows_upserted"),
                fundamentals_result.get("columns_ingested"),
                fundamentals_result.get("duration_seconds"),
            )
        except Exception:
            logger.exception("Background fundamentals sync failed after market sync")
            db.rollback()
            now = datetime.now(UTC)
            try:
                store_fundamentals_sync_result(
                    db,
                    {
                        "status": "failed",
                        "table_name": FUNDAMENTALS_TABLE_NAME,
                        "columns_ingested": len(FUNDAMENTAL_METRICS),
                        "metrics": list(FUNDAMENTAL_METRICS),
                        "selected_stocks": 0,
                        "succeeded": 0,
                        "failed": 0,
                        "rows_inserted": 0,
                        "rows_updated": 0,
                        "rows_upserted": 0,
                        "started_at": now,
                        "finished_at": now,
                        "duration_seconds": 0.0,
                        "source": "yfinance",
                        "failed_symbols": [],
                        "sample_success_symbols": [],
                        "fatal_error": "Fundamentals sync raised an unhandled exception. Check backend logs.",
                    },
                )
            except Exception:
                logger.exception("Unable to store fatal fundamentals sync status")
        from app.services.paper_trading_service import match_pending_orders

        match_pending_orders(db)
        clear_market_overview_cache()
    except Exception:
        logger.exception("Background market sync failed")
    finally:
        db.close()
        with _SYNC_LOCK:
            _SYNC_ACTIVE = False
            _SYNC_THREAD = None


@timed("market.start_sync")
def start_market_sync(db: Session) -> dict[str, Any]:
    global _SYNC_THREAD, _SYNC_ACTIVE
    _cleanup_stale_running_runs(db)

    with _SYNC_LOCK:
        if _SYNC_ACTIVE or (_SYNC_THREAD is not None and _SYNC_THREAD.is_alive()):
            running = _get_running_run(db)
            return {
                "started": False,
                "message": "A market sync is already running.",
                "run_id": running.id if running else None,
            }

        running = _get_running_run(db)
        if running is not None:
            return {
                "started": False,
                "message": "A market sync is already running.",
                "run_id": running.id,
            }

        _SYNC_ACTIVE = True
        _SYNC_THREAD = threading.Thread(target=_run_sync_job, daemon=True, name="market-sync")
        _SYNC_THREAD.start()

    return {
        "started": True,
        "message": "Market sync started. Prices, analytics, and fundamentals will refresh in the background.",
        "run_id": None,
    }
