from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy import desc, func, select, text
from sqlalchemy.orm import Session

from app.models.stock import IngestionRun, StockPrice
from app.services.data_ingestion_stats_service import get_data_ingestion_dashboard
from app.services.fundamentals_service import get_fundamentals_status
from app.services.market_sync_service import get_market_sync_status
from app.services.search_telemetry_service import get_search_latency_summary

logger = logging.getLogger(__name__)

DAILY_TIMEFRAME = "1d"
KEY_TABLE_NAMES = (
    "stocks",
    "stock_prices",
    "stock_fundamentals_latest",
    "portfolios",
    "holdings",
    "orders",
    "strategy_signals",
    "backtest_runs",
    "backtest_results",
    "ingestion_runs",
    "search_query_logs",
)

RUN_STATUS_TONES = {
    "SUCCEEDED": "success",
    "SUCCESS": "success",
    "PARTIAL": "warning",
    "FAILED": "danger",
    "RUNNING": "info",
}


def http_error_message(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list) and detail:
        first = detail[0]
        if isinstance(first, dict):
            return str(first.get("msg") or first)
    return str(exc) or "Request failed."


def format_duration_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    remainder = seconds % 60
    return f"{minutes}m {remainder:.0f}s"


def format_ms(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value):,.1f} ms"


def run_status_tone(status: str | None) -> str:
    if not status:
        return "neutral"
    return RUN_STATUS_TONES.get(status.upper(), "neutral")


def load_ingestion_dashboard(db: Session, *, runs_limit: int = 30) -> dict[str, Any]:
    return get_data_ingestion_dashboard(db, runs_limit=runs_limit)


def _latest_candle_date(db: Session) -> date | None:
    return db.scalar(
        select(func.max(func.date(StockPrice.price_datetime))).where(
            StockPrice.timeframe == DAILY_TIMEFRAME
        )
    )


def _provider_session_date(dashboard: dict[str, Any], sync_status: dict[str, Any]) -> date | None:
    record_date = sync_status.get("record_date")
    if isinstance(record_date, date):
        return record_date
    if isinstance(record_date, str):
        try:
            return date.fromisoformat(record_date[:10])
        except ValueError:
            pass
    return dashboard.get("latest_price_date")


def _last_ingestion_run(db: Session) -> IngestionRun | None:
    return db.scalar(select(IngestionRun).order_by(desc(IngestionRun.started_at)).limit(1))


def build_overview_context(db: Session, *, runs_limit: int = 30) -> dict[str, Any]:
    dashboard = load_ingestion_dashboard(db, runs_limit=runs_limit)
    sync_status = get_market_sync_status(db)
    latest_candle = _latest_candle_date(db)
    provider_session = _provider_session_date(dashboard, sync_status)
    last_run = sync_status.get("last_run") or {}
    current_run = sync_status.get("current_run") or {}
    active_run = current_run if sync_status.get("is_running") else last_run

    stale_count_row = db.execute(
        text(
            """
            WITH latest AS (
                SELECT MAX(price_datetime::date) AS latest_date
                FROM stock_prices
                WHERE timeframe = :timeframe
            )
            SELECT COUNT(*)::int AS stale_count
            FROM stocks s
            LEFT JOIN (
                SELECT stock_id, MAX(price_datetime::date) AS max_date
                FROM stock_prices
                WHERE timeframe = :timeframe
                GROUP BY stock_id
            ) sp ON sp.stock_id = s.id
            CROSS JOIN latest l
            WHERE s.is_active = TRUE
              AND (
                sp.max_date IS NULL
                OR (l.latest_date IS NOT NULL AND sp.max_date < l.latest_date)
              )
            """
        ),
        {"timeframe": DAILY_TIMEFRAME},
    ).mappings().first()

    failed_count_row = db.execute(
        text(
            """
            SELECT COUNT(*)::int AS failed_count
            FROM stocks s
            WHERE s.is_delisted = TRUE
               OR (
                    s.is_active = TRUE
                    AND NOT EXISTS (
                        SELECT 1
                        FROM stock_prices sp
                        WHERE sp.stock_id = s.id
                          AND sp.timeframe = :timeframe
                    )
               )
            """
        ),
        {"timeframe": DAILY_TIMEFRAME},
    ).mappings().first()
    failed_symbols_count = int(failed_count_row["failed_count"] or 0) if failed_count_row else 0

    return {
        "as_of": dashboard.get("as_of"),
        "latest_candle_date": latest_candle,
        "latest_provider_date": provider_session,
        "provider_date_note": (
            "From analytics movers cache / last successful sync end date."
            if provider_session
            else None
        ),
        "last_sync_run_time": sync_status.get("last_synced_at"),
        "last_sync_status": sync_status.get("last_sync_status"),
        "last_sync_rows_saved": active_run.get("rows_saved"),
        "active_symbols_selected": dashboard.get("active_stocks"),
        "failed_symbols_count": failed_symbols_count,
        "stale_symbols_count": int(stale_count_row["stale_count"] or 0) if stale_count_row else 0,
        "analytics_refreshed_at": dashboard.get("analytics_refreshed_at"),
        "sync_is_running": bool(sync_status.get("is_running")),
        "price_coverage_pct": dashboard.get("price_coverage_pct"),
    }


def build_freshness_context(db: Session) -> dict[str, Any]:
    latest_candle = _latest_candle_date(db)
    universe_row = db.execute(
        text(
            """
            WITH latest AS (
                SELECT MAX(price_datetime::date) AS latest_date
                FROM stock_prices
                WHERE timeframe = :timeframe
            ),
            per_stock AS (
                SELECT
                    s.id,
                    MAX(sp.price_datetime::date) AS max_date
                FROM stocks s
                LEFT JOIN stock_prices sp
                    ON sp.stock_id = s.id AND sp.timeframe = :timeframe
                WHERE s.is_active = TRUE
                GROUP BY s.id
            )
            SELECT
                COUNT(*) FILTER (WHERE max_date = (SELECT latest_date FROM latest))::int AS at_latest,
                COUNT(*) FILTER (
                    WHERE max_date IS NOT NULL
                      AND (SELECT latest_date FROM latest) IS NOT NULL
                      AND max_date = (SELECT latest_date FROM latest) - 1
                )::int AS behind_one_day,
                COUNT(*) FILTER (
                    WHERE max_date IS NULL
                      OR (
                        (SELECT latest_date FROM latest) IS NOT NULL
                        AND max_date < (SELECT latest_date FROM latest) - 1
                      )
                )::int AS behind_two_plus
            FROM per_stock
            """
        ),
        {"timeframe": DAILY_TIMEFRAME},
    ).mappings().first()

    exchange_rows = db.execute(
        text(
            """
            WITH latest AS (
                SELECT MAX(price_datetime::date) AS latest_date
                FROM stock_prices
                WHERE timeframe = :timeframe
            ),
            per_stock AS (
                SELECT
                    s.exchange,
                    s.id,
                    MAX(sp.price_datetime::date) AS max_date
                FROM stocks s
                LEFT JOIN stock_prices sp
                    ON sp.stock_id = s.id AND sp.timeframe = :timeframe
                WHERE s.is_active = TRUE
                GROUP BY s.exchange, s.id
            )
            SELECT
                exchange,
                MAX(max_date) AS latest_date,
                COUNT(*)::int AS symbols_count,
                COUNT(*) FILTER (
                    WHERE max_date IS NULL
                       OR (
                         (SELECT latest_date FROM latest) IS NOT NULL
                         AND max_date < (SELECT latest_date FROM latest)
                       )
                )::int AS stale_count,
                COALESCE(
                    MAX(
                        CASE
                            WHEN (SELECT latest_date FROM latest) IS NULL OR max_date IS NULL THEN NULL
                            ELSE ((SELECT latest_date FROM latest) - max_date)
                        END
                    ),
                    0
                )::int AS max_lag_days
            FROM per_stock
            GROUP BY exchange
            ORDER BY exchange
            """
        ),
        {"timeframe": DAILY_TIMEFRAME},
    ).mappings().all()

    sync_status = get_market_sync_status(db)
    provider_latest = _provider_session_date({}, sync_status)

    return {
        "latest_candle_date": latest_candle,
        "stocks_at_latest": int(universe_row["at_latest"] or 0) if universe_row else 0,
        "stocks_behind_one_day": int(universe_row["behind_one_day"] or 0) if universe_row else 0,
        "stocks_behind_two_plus": int(universe_row["behind_two_plus"] or 0) if universe_row else 0,
        "provider_latest_date": provider_latest,
        "exchange_rows": [dict(row) for row in exchange_rows],
        "trading_day_note": None,
    }


def serialize_ingestion_run(run: dict[str, Any] | IngestionRun) -> dict[str, Any]:
    if isinstance(run, IngestionRun):
        payload = {
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
            "error_message": run.error_message,
            "source": run.source,
            "end_date": run.end_date,
        }
        if run.started_at and run.finished_at:
            payload["duration_seconds"] = (run.finished_at - run.started_at).total_seconds()
        else:
            payload["duration_seconds"] = None
    else:
        payload = dict(run)
    total = int(payload.get("total_symbols") or 0)
    success = int(payload.get("success_count") or 0)
    failed = int(payload.get("failed_count") or 0)
    skipped = max(total - success - failed, 0)
    payload.update(
        {
            "provider": payload.get("source") or "yfinance",
            "symbols_selected": total,
            "symbols_synced": success,
            "symbols_skipped": skipped,
            "symbols_failed": failed,
            "latest_before": None,
            "latest_after": payload.get("end_date"),
            "provider_latest": payload.get("end_date"),
            "status_tone": run_status_tone(str(payload.get("status"))),
            "duration_label": format_duration_seconds(payload.get("duration_seconds")),
        }
    )
    return payload


def build_recent_runs_context(db: Session, *, limit: int = 30) -> dict[str, Any]:
    runs = list(
        db.scalars(select(IngestionRun).order_by(desc(IngestionRun.started_at)).limit(limit))
    )
    return {"runs": [serialize_ingestion_run(run) for run in runs], "limit": limit}


def build_ingestion_dashboard_context(db: Session, *, runs_limit: int = 25) -> dict[str, Any]:
    dashboard = load_ingestion_dashboard(db, runs_limit=runs_limit)
    recent = dashboard.get("recent_runs") or []
    latest_run = serialize_ingestion_run(recent[0]) if recent else None
    return {
        "dashboard": dashboard,
        "latest_run": latest_run,
        "runs_limit": runs_limit,
    }


def query_failed_symbols(db: Session, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT
                s.symbol,
                s.exchange,
                s.yahoo_symbol AS provider_ticker,
                s.delisted_detected_at AS last_attempted,
                CASE
                    WHEN s.is_delisted THEN 'delisted'
                    ELSE 'no_data'
                END AS error_type,
                COALESCE(
                    NULLIF(TRIM(s.delisted_reason), ''),
                    'No daily candles stored for this active symbol'
                ) AS error_message,
                CASE WHEN s.is_delisted THEN 'delisted' ELSE 'pending_sync' END AS retry_status
            FROM stocks s
            WHERE (
                s.is_delisted = TRUE
                OR (
                    s.is_active = TRUE
                    AND NOT EXISTS (
                        SELECT 1
                        FROM stock_prices sp
                        WHERE sp.stock_id = s.id
                          AND sp.timeframe = :timeframe
                    )
                )
            )
            ORDER BY s.is_delisted DESC, s.symbol
            LIMIT :limit
            """
        ),
        {"timeframe": DAILY_TIMEFRAME, "limit": limit},
    ).mappings().all()
    return [dict(row) for row in rows]


def query_stale_symbols(
    db: Session,
    *,
    min_lag_days: int = 1,
    exchange: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "timeframe": DAILY_TIMEFRAME,
        "min_lag": max(1, min_lag_days),
        "limit": limit,
    }
    exchange_clause = ""
    if exchange:
        exchange_clause = "AND s.exchange = :exchange"
        params["exchange"] = exchange.strip().upper()

    rows = db.execute(
        text(
            f"""
            WITH latest AS (
                SELECT MAX(price_datetime::date) AS latest_date
                FROM stock_prices
                WHERE timeframe = :timeframe
            )
            SELECT
                s.symbol,
                s.exchange,
                s.company_name,
                MAX(sp.price_datetime::date) AS latest_stored_date,
                (SELECT latest_date FROM latest) AS provider_latest_date,
                CASE
                    WHEN MAX(sp.price_datetime::date) IS NULL THEN NULL
                    ELSE ((SELECT latest_date FROM latest) - MAX(sp.price_datetime::date))
                END AS lag_days,
                'behind_universe' AS reason,
                NULL::text AS last_sync_status
            FROM stocks s
            LEFT JOIN stock_prices sp
                ON sp.stock_id = s.id AND sp.timeframe = :timeframe
            CROSS JOIN latest l
            WHERE s.is_active = TRUE
              {exchange_clause}
            GROUP BY s.id, s.symbol, s.exchange, s.company_name, l.latest_date
            HAVING
                MAX(sp.price_datetime::date) IS NULL
                OR (
                    l.latest_date IS NOT NULL
                    AND MAX(sp.price_datetime::date) < l.latest_date
                    AND (l.latest_date - MAX(sp.price_datetime::date)) >= :min_lag
                )
            ORDER BY lag_days DESC NULLS FIRST, s.symbol
            LIMIT :limit
            """
        ),
        params,
    ).mappings().all()
    return [dict(row) for row in rows]


def build_database_stats_context(db: Session) -> dict[str, Any]:
    dashboard = load_ingestion_dashboard(db, runs_limit=5)
    database_info = dashboard.get("database_info") or {}
    tables = database_info.get("tables") or []
    key_tables = [
        row
        for row in tables
        if row.get("table_name") in KEY_TABLE_NAMES
    ]
    missing = [name for name in KEY_TABLE_NAMES if name not in {row.get("table_name") for row in key_tables}]
    for name in missing:
        key_tables.append({"schema_name": database_info.get("schema_name"), "table_name": name, "row_estimate": None})
    key_tables.sort(key=lambda row: row.get("table_name") or "")
    return {"database_info": database_info, "key_tables": key_tables}


def build_search_latency_context(db: Session, *, recent_limit: int = 25) -> dict[str, Any]:
    summary = get_search_latency_summary(recent_limit=recent_limit)
    persisted = int(summary.get("total_searches") or 0) > 0
    return {"summary": summary, "persisted": persisted, "recent_limit": recent_limit}


def build_sync_panel_context(db: Session) -> dict[str, Any]:
    sync_status = get_market_sync_status(db)
    fundamentals_status = get_fundamentals_status(db)
    last_run = _last_ingestion_run(db)
    warning = None
    if last_run and last_run.status in ("SUCCEEDED", "PARTIAL"):
        if last_run.success_count == 0 and last_run.rows_saved == 0 and last_run.total_symbols > 0:
            warning = "Last sync completed with all symbols skipped (already up to date)."
    return {
        "sync_status": sync_status,
        "scheduler_enabled": False,
        "all_skipped_warning": warning,
        "latest_candle_date": _latest_candle_date(db),
        "latest_provider_date": _provider_session_date({}, sync_status),
        "fundamentals_status": fundamentals_status,
    }


def build_fundamentals_status_context(db: Session) -> dict[str, Any]:
    return {"fundamentals_status": get_fundamentals_status(db)}
