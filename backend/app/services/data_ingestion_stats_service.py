from __future__ import annotations

from datetime import UTC, date, datetime
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


@timed("data.get_ingestion_dashboard")
def get_data_ingestion_dashboard(db: Session, *, runs_limit: int = 25) -> dict[str, Any]:
    sync_status = get_market_sync_status(db)
    now = datetime.now(UTC)

    total_stocks = int(db.scalar(select(func.count()).select_from(Stock)) or 0)
    active_stocks = int(
        db.scalar(select(func.count()).select_from(Stock).where(Stock.is_active.is_(True))) or 0
    )
    stocks_with_daily_prices = int(
        db.scalar(
            select(func.count(func.distinct(StockPrice.stock_id))).where(
                StockPrice.timeframe == DAILY_TIMEFRAME
            )
        )
        or 0
    )
    total_daily_price_rows = int(
        db.scalar(
            select(func.count()).select_from(StockPrice).where(StockPrice.timeframe == DAILY_TIMEFRAME)
        )
        or 0
    )

    price_date_row = db.execute(
        text(
            """
            SELECT
                MIN(price_datetime)::date AS earliest_price_date,
                MAX(price_datetime)::date AS latest_price_date
            FROM stock_prices
            WHERE timeframe = :timeframe
            """
        ),
        {"timeframe": DAILY_TIMEFRAME},
    ).mappings().first()

    earliest_price_date = price_date_row["earliest_price_date"] if price_date_row else None
    latest_price_date = price_date_row["latest_price_date"] if price_date_row else None

    performance_snapshots = int(
        db.scalar(select(func.count()).select_from(StockPerformanceSnapshot)) or 0
    )
    stocks_with_sector = int(
        db.scalar(
            select(func.count())
            .select_from(Stock)
            .where(Stock.sector.is_not(None), Stock.sector != "")
        )
        or 0
    )
    stocks_with_industry = int(
        db.scalar(
            select(func.count())
            .select_from(Stock)
            .where(Stock.industry.is_not(None), Stock.industry != "")
        )
        or 0
    )
    distinct_sectors = int(
        db.scalar(
            select(func.count(func.distinct(Stock.sector))).where(
                Stock.sector.is_not(None), Stock.sector != ""
            )
        )
        or 0
    )
    distinct_industries = int(
        db.scalar(
            select(func.count(func.distinct(Stock.industry))).where(
                Stock.industry.is_not(None), Stock.industry != ""
            )
        )
        or 0
    )

    exchange_rows = db.execute(
        text(
            """
            SELECT
                s.exchange,
                COUNT(*)::int AS total_stocks,
                COUNT(DISTINCT sp.stock_id)::int AS stocks_with_prices
            FROM stocks s
            LEFT JOIN stock_prices sp
                ON sp.stock_id = s.id AND sp.timeframe = :timeframe
            WHERE s.is_active = TRUE
            GROUP BY s.exchange
            ORDER BY s.exchange
            """
        ),
        {"timeframe": DAILY_TIMEFRAME},
    ).mappings().all()

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
