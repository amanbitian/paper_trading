from __future__ import annotations

import time as _time
from datetime import UTC, date, datetime
from threading import Lock as _Lock
from typing import Any

from sqlalchemy import desc, func, select, text
from sqlalchemy.orm import Session

from app.models.stock import (
    IngestionRun,
    MarketAnalyticsCache,
    Stock,
    StockPerformanceSnapshot,
    StockPrice,
)
from app.services.market_movers_service import MARKET_MOVERS_CACHE_KEY
from app.services.market_sync_service import get_market_sync_status
from app.services.search_telemetry_service import get_search_latency_summary
from app.utils.observability import timed

DAILY_TIMEFRAME = "1d"

# ── TTL cache: shared across overview / database-stats / ingestion-dashboard ──
_dashboard_cache: dict[str, Any] = {}
_dashboard_ts: float = 0.0
_DASHBOARD_TTL: float = 45.0
_dashboard_lock = _Lock()


def _run_duration_seconds(run: IngestionRun) -> float | None:
    if run.started_at is None or run.finished_at is None:
        return None
    return (run.finished_at - run.started_at).total_seconds()


def _serialize_run_summary(run: IngestionRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "status": run.status,
        "ingestion_mode": run.ingestion_mode,
        "exchange": run.exchange,
        "total_symbols": run.total_symbols,
        "success_count": run.success_count,
        "failed_count": run.failed_count,
        "rows_saved": run.rows_saved,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "duration_seconds": _run_duration_seconds(run),
        "error_message": run.error_message,
    }


def _get_database_info(db: Session) -> dict[str, Any]:
    database_row = db.execute(
        text(
            """
            SELECT
                current_database() AS database_name,
                current_schema() AS schema_name,
                current_user AS database_user,
                inet_server_addr()::text AS server_host,
                inet_server_port() AS server_port,
                version() AS postgres_version
            """
        )
    ).mappings().first()

    table_rows = db.execute(
        text(
            """
            SELECT
                n.nspname AS schema_name,
                c.relname AS table_name,
                GREATEST(c.reltuples::bigint, 0)::int AS row_estimate
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'r'
              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY n.nspname, c.relname
            """
        )
    ).mappings().all()

    postgres_version = database_row["postgres_version"] if database_row else None
    if postgres_version:
        postgres_version = postgres_version.split(" on ", 1)[0]

    return {
        "database_name": database_row["database_name"] if database_row else None,
        "schema_name": database_row["schema_name"] if database_row else None,
        "database_user": database_row["database_user"] if database_row else None,
        "server_host": database_row["server_host"] if database_row else None,
        "server_port": database_row["server_port"] if database_row else None,
        "postgres_version": postgres_version,
        "table_count": len(table_rows),
        "tables": [dict(row) for row in table_rows],
    }


def _compute_dashboard(db: Session, *, runs_limit: int) -> dict[str, Any]:
    """All DB queries for the dashboard, called only on cache miss."""
    now = datetime.now(UTC)
    sync_status = get_market_sync_status(db)

    # ── Batch 1: all stock counts in ONE query ────────────────────────────────
    stock_agg = db.execute(
        text(
            """
            SELECT
                COUNT(*)                                                             AS total_stocks,
                COUNT(*) FILTER (WHERE is_active = TRUE)                             AS active_stocks,
                COUNT(*) FILTER (WHERE sector   IS NOT NULL AND sector   != '')      AS stocks_with_sector,
                COUNT(*) FILTER (WHERE industry IS NOT NULL AND industry != '')      AS stocks_with_industry,
                COUNT(DISTINCT sector)   FILTER (WHERE sector   IS NOT NULL AND sector   != '') AS distinct_sectors,
                COUNT(DISTINCT industry) FILTER (WHERE industry IS NOT NULL AND industry != '') AS distinct_industries
            FROM stocks
            """
        )
    ).mappings().first()

    total_stocks        = int(stock_agg["total_stocks"]        or 0)
    active_stocks       = int(stock_agg["active_stocks"]       or 0)
    stocks_with_sector  = int(stock_agg["stocks_with_sector"]  or 0)
    stocks_with_industry = int(stock_agg["stocks_with_industry"] or 0)
    distinct_sectors    = int(stock_agg["distinct_sectors"]    or 0)
    distinct_industries = int(stock_agg["distinct_industries"] or 0)

    # ── Batch 2: stock_prices stats in ONE query ──────────────────────────────
    # COUNT(DISTINCT), MIN, MAX all in one scan; avoids 3 separate table scans.
    price_agg = db.execute(
        text(
            """
            SELECT
                COUNT(DISTINCT stock_id)         AS stocks_with_daily_prices,
                MIN(price_datetime)::date         AS earliest_price_date,
                MAX(price_datetime)::date         AS latest_price_date
            FROM stock_prices
            WHERE timeframe = :tf
            """
        ),
        {"tf": DAILY_TIMEFRAME},
    ).mappings().first()

    stocks_with_daily_prices = int(price_agg["stocks_with_daily_prices"] or 0) if price_agg else 0
    earliest_price_date      = price_agg["earliest_price_date"] if price_agg else None
    latest_price_date        = price_agg["latest_price_date"]   if price_agg else None

    # ── Row estimate from pg_class (replaces slow COUNT(*) on millions of rows) ─
    row_est = db.execute(
        text(
            "SELECT GREATEST(reltuples::bigint, 0) AS n FROM pg_class WHERE relname = 'stock_prices'"
        )
    ).scalar()
    total_daily_price_rows = int(row_est or 0)

    # ── Performance snapshots ─────────────────────────────────────────────────
    performance_snapshots = int(
        db.scalar(select(func.count()).select_from(StockPerformanceSnapshot)) or 0
    )

    # ── Exchange breakdown ────────────────────────────────────────────────────
    exchange_rows = db.execute(
        text(
            """
            SELECT
                s.exchange,
                COUNT(*)::int                    AS total_stocks,
                COUNT(DISTINCT sp.stock_id)::int AS stocks_with_prices
            FROM stocks s
            LEFT JOIN stock_prices sp ON sp.stock_id = s.id AND sp.timeframe = :tf
            WHERE s.is_active = TRUE
            GROUP BY s.exchange
            ORDER BY s.exchange
            """
        ),
        {"tf": DAILY_TIMEFRAME},
    ).mappings().all()

    # ── Analytics cache (PK lookup — instant) ─────────────────────────────────
    analytics_row = db.get(MarketAnalyticsCache, MARKET_MOVERS_CACHE_KEY)
    analytics_refreshed_at = analytics_row.refreshed_at if analytics_row else None
    movers_universe_count: int | None = None
    if analytics_row and analytics_row.payload:
        movers_universe_count = int(analytics_row.payload.get("eligible_count") or 0) or None
        cache_record_date = analytics_row.payload.get("record_date")
        if cache_record_date and isinstance(cache_record_date, str):
            latest_price_date = date.fromisoformat(cache_record_date)
        elif cache_record_date:
            latest_price_date = cache_record_date

    # ── Last sync timing ──────────────────────────────────────────────────────
    last_run_row = sync_status.get("last_run")
    last_sync_duration_seconds = None
    if last_run_row and last_run_row.get("started_at") and last_run_row.get("finished_at"):
        started = last_run_row["started_at"]
        finished = last_run_row["finished_at"]
        if isinstance(started, str):
            started = datetime.fromisoformat(started.replace("Z", "+00:00"))
        if isinstance(finished, str):
            finished = datetime.fromisoformat(finished.replace("Z", "+00:00"))
        last_sync_duration_seconds = (finished - started).total_seconds()

    # ── Recent runs ───────────────────────────────────────────────────────────
    recent_runs = list(
        db.scalars(select(IngestionRun).order_by(desc(IngestionRun.started_at)).limit(runs_limit))
    )

    coverage_denominator = active_stocks or total_stocks
    price_coverage_pct = (
        round(100.0 * stocks_with_daily_prices / coverage_denominator, 2)
        if coverage_denominator
        else 0.0
    )

    return {
        "as_of": now,
        "database_info": _get_database_info(db),
        "search_latency": get_search_latency_summary(recent_limit=25),
        "sync_is_running": bool(sync_status.get("is_running")),
        "last_synced_at": sync_status.get("last_synced_at"),
        "last_sync_status": sync_status.get("last_sync_status"),
        "last_sync_duration_seconds": last_sync_duration_seconds,
        "last_sync_mode": last_run_row.get("ingestion_mode") if last_run_row else None,
        "last_sync_symbols_attempted": last_run_row.get("total_symbols") if last_run_row else None,
        "last_sync_symbols_succeeded": last_run_row.get("success_count") if last_run_row else None,
        "last_sync_symbols_failed": last_run_row.get("failed_count") if last_run_row else None,
        "last_sync_rows_saved": last_run_row.get("rows_saved") if last_run_row else None,
        "latest_price_date": latest_price_date,
        "earliest_price_date": earliest_price_date,
        "analytics_refreshed_at": analytics_refreshed_at,
        "total_stocks": total_stocks,
        "active_stocks": active_stocks,
        "stocks_with_daily_prices": stocks_with_daily_prices,
        "price_coverage_pct": price_coverage_pct,
        "total_daily_price_rows": total_daily_price_rows,
        "performance_snapshots": performance_snapshots,
        "stocks_with_sector": stocks_with_sector,
        "stocks_with_industry": stocks_with_industry,
        "distinct_sectors": distinct_sectors,
        "distinct_industries": distinct_industries,
        "movers_universe_count": movers_universe_count,
        "exchange_breakdown": [
            {
                "exchange": row["exchange"],
                "total_stocks": int(row["total_stocks"]),
                "stocks_with_prices": int(row["stocks_with_prices"]),
            }
            for row in exchange_rows
        ],
        "recent_runs": [_serialize_run_summary(run) for run in recent_runs],
    }


@timed("data.get_ingestion_dashboard")
def get_data_ingestion_dashboard(db: Session, *, runs_limit: int = 25) -> dict[str, Any]:
    global _dashboard_ts
    # Fast path: serve from cache if still fresh (shared by overview, db-stats, ingestion-dashboard)
    with _dashboard_lock:
        if _dashboard_cache and (_time.monotonic() - _dashboard_ts) < _DASHBOARD_TTL:
            return dict(_dashboard_cache)

    result = _compute_dashboard(db, runs_limit=runs_limit)

    with _dashboard_lock:
        _dashboard_cache.clear()
        _dashboard_cache.update(result)
        _dashboard_ts = _time.monotonic()

    return result
