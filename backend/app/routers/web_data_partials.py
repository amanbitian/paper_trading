from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.analytics_refresh_service import refresh_all_analytics
from app.services.market_sync_service import get_market_sync_status, start_market_sync
from app.services.web_data_helpers import (
    build_database_stats_context,
    build_freshness_context,
    build_fundamentals_status_context,
    build_ingestion_dashboard_context,
    build_overview_context,
    build_recent_runs_context,
    build_search_latency_context,
    build_sync_panel_context,
    http_error_message,
    query_failed_symbols,
    query_stale_symbols,
)
from app.web_utils import templates

logger = logging.getLogger(__name__)
timing_logger = logging.getLogger("app.timing")

router = APIRouter(prefix="/web/partials/data", tags=["web-data-partials"])


def _log_route(route: str, started_at: float, status: str = "ok") -> None:
    timing_logger.info(
        "operation=web_route route=%s status=%s duration_ms=%.2f",
        route,
        status,
        (time.perf_counter() - started_at) * 1000,
    )


def _render_sync_partial(
    request: Request,
    db: Session,
    *,
    sync_message: str | None = None,
    sync_tone: str = "info",
    sync_error: str | None = None,
    sync_result: dict[str, Any] | None = None,
    hx_trigger: str | None = None,
    status_code: int = 200,
):
    context = build_sync_panel_context(db)
    response = templates.TemplateResponse(
        "partials/data_sync_status.html",
        {
            "request": request,
            "sync_message": sync_message,
            "sync_tone": sync_tone,
            "sync_error": sync_error,
            "sync_result": sync_result,
            **context,
        },
        status_code=status_code,
    )
    if hx_trigger:
        response.headers["HX-Trigger"] = hx_trigger
    return response


@router.get("/overview", include_in_schema=False)
def data_overview(request: Request, db: Session = Depends(get_db)):
    started_at = time.perf_counter()
    error_message = None
    overview = {}
    try:
        overview = build_overview_context(db)
    except Exception as exc:
        logger.exception("data overview failed")
        error_message = http_error_message(exc)
    response = templates.TemplateResponse(
        "partials/data_overview.html",
        {"request": request, "overview": overview, "error_message": error_message},
    )
    _log_route("/web/partials/data/overview", started_at, "error" if error_message else "ok")
    return response


@router.get("/sync-status", include_in_schema=False)
def data_sync_status(request: Request, db: Session = Depends(get_db)):
    started_at = time.perf_counter()
    try:
        response = _render_sync_partial(request, db)
        _log_route("/web/partials/data/sync-status", started_at)
        return response
    except Exception as exc:
        logger.exception("data sync-status failed")
        _log_route("/web/partials/data/sync-status", started_at, "error")
        return _render_sync_partial(
            request,
            db,
            sync_error=http_error_message(exc),
            sync_tone="danger",
            status_code=500,
        )


@router.get("/fundamentals-status", include_in_schema=False)
def data_fundamentals_status(request: Request, db: Session = Depends(get_db)):
    started_at = time.perf_counter()
    error_message = None
    context: dict[str, Any] = {}
    try:
        context = build_fundamentals_status_context(db)
    except Exception as exc:
        logger.exception("data fundamentals-status failed")
        error_message = http_error_message(exc)
    response = templates.TemplateResponse(
        "partials/data_fundamentals_status.html",
        {"request": request, "error_message": error_message, **context},
    )
    _log_route("/web/partials/data/fundamentals-status", started_at, "error" if error_message else "ok")
    return response


@router.post("/sync-now", include_in_schema=False)
def data_sync_now(request: Request, db: Session = Depends(get_db)):
    started_at = time.perf_counter()
    logger.info("Data Operations Sync Now clicked")
    try:
        result = start_market_sync(db)
        duration = time.perf_counter() - started_at
        logger.info(
            "Data sync start completed in %.2fs: started=%s message=%s",
            duration,
            result.get("started"),
            result.get("message"),
        )
        if result.get("started"):
            message = str(result.get("message") or "Market sync started in the background.")
            tone = "success"
            hx_trigger = "market-sync-started"
        else:
            message = str(result.get("message") or "Market sync is already running.")
            tone = "warning"
            hx_trigger = None
        response = _render_sync_partial(
            request,
            db,
            sync_message=message,
            sync_tone=tone,
            sync_result=result,
            hx_trigger=hx_trigger,
        )
        _log_route("/web/partials/data/sync-now", started_at)
        return response
    except Exception as exc:
        logger.exception("Data sync-now failed")
        _log_route("/web/partials/data/sync-now", started_at, "error")
        return _render_sync_partial(
            request,
            db,
            sync_message="Market sync failed. Check backend logs for details.",
            sync_tone="danger",
            sync_error=http_error_message(exc),
            status_code=500,
        )


@router.post("/refresh-analytics", include_in_schema=False)
def data_refresh_analytics(request: Request, db: Session = Depends(get_db)):
    started_at = time.perf_counter()
    try:
        payload = refresh_all_analytics(db)
        message = (
            f"Analytics refreshed: {payload.get('performance_rows', 0)} performance rows, "
            f"{payload.get('movers_universe_count', 0)} movers universe."
        )
        tone = "success"
        _log_route("/web/partials/data/refresh-analytics", started_at)
        return templates.TemplateResponse(
            "partials/data_refresh_result.html",
            {
                "request": request,
                "message": message,
                "tone": tone,
                "payload": payload,
            },
        )
    except Exception as exc:
        logger.exception("data refresh-analytics failed")
        _log_route("/web/partials/data/refresh-analytics", started_at, "error")
        return templates.TemplateResponse(
            "partials/data_refresh_result.html",
            {
                "request": request,
                "message": http_error_message(exc),
                "tone": "danger",
                "payload": None,
            },
            status_code=500,
        )


@router.get("/ingestion-dashboard", include_in_schema=False)
def data_ingestion_dashboard(
    request: Request,
    runs_limit: int = Query(default=25, ge=1, le=50),
    db: Session = Depends(get_db),
):
    started_at = time.perf_counter()
    error_message = None
    context: dict[str, Any] = {}
    try:
        context = build_ingestion_dashboard_context(db, runs_limit=runs_limit)
    except Exception as exc:
        logger.exception("data ingestion-dashboard failed")
        error_message = http_error_message(exc)
    response = templates.TemplateResponse(
        "partials/data_ingestion_dashboard.html",
        {"request": request, "error_message": error_message, **context},
    )
    _log_route("/web/partials/data/ingestion-dashboard", started_at, "error" if error_message else "ok")
    return response


@router.get("/freshness", include_in_schema=False)
def data_freshness(request: Request, db: Session = Depends(get_db)):
    started_at = time.perf_counter()
    error_message = None
    freshness: dict[str, Any] = {}
    try:
        freshness = build_freshness_context(db)
    except Exception as exc:
        logger.exception("data freshness failed")
        error_message = http_error_message(exc)
    response = templates.TemplateResponse(
        "partials/data_freshness.html",
        {"request": request, "freshness": freshness, "error_message": error_message},
    )
    _log_route("/web/partials/data/freshness", started_at, "error" if error_message else "ok")
    return response


@router.get("/recent-runs", include_in_schema=False)
def data_recent_runs(
    request: Request,
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    started_at = time.perf_counter()
    error_message = None
    runs: list[dict[str, Any]] = []
    try:
        runs = build_recent_runs_context(db, limit=limit)["runs"]
    except Exception as exc:
        logger.exception("data recent-runs failed")
        error_message = http_error_message(exc)
    response = templates.TemplateResponse(
        "partials/data_recent_runs.html",
        {"request": request, "runs": runs, "error_message": error_message, "limit": limit},
    )
    _log_route("/web/partials/data/recent-runs", started_at, "error" if error_message else "ok")
    return response


@router.get("/failed-symbols", include_in_schema=False)
def data_failed_symbols(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    started_at = time.perf_counter()
    error_message = None
    rows: list[dict[str, Any]] = []
    try:
        rows = query_failed_symbols(db, limit=limit)
    except Exception as exc:
        logger.exception("data failed-symbols failed")
        error_message = http_error_message(exc)
    response = templates.TemplateResponse(
        "partials/data_failed_symbols.html",
        {
            "request": request,
            "rows": rows,
            "error_message": error_message,
            "limit": limit,
        },
    )
    _log_route("/web/partials/data/failed-symbols", started_at, "error" if error_message else "ok")
    return response


@router.get("/stale-symbols", include_in_schema=False)
def data_stale_symbols(
    request: Request,
    min_lag: int = Query(default=1, ge=1, le=30),
    exchange: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    started_at = time.perf_counter()
    error_message = None
    rows: list[dict[str, Any]] = []
    try:
        rows = query_stale_symbols(db, min_lag_days=min_lag, exchange=exchange, limit=limit)
    except Exception as exc:
        logger.exception("data stale-symbols failed")
        error_message = http_error_message(exc)
    response = templates.TemplateResponse(
        "partials/data_stale_symbols.html",
        {
            "request": request,
            "rows": rows,
            "error_message": error_message,
            "min_lag": min_lag,
            "exchange": exchange or "",
            "limit": limit,
        },
    )
    _log_route("/web/partials/data/stale-symbols", started_at, "error" if error_message else "ok")
    return response


@router.get("/database-stats", include_in_schema=False)
def data_database_stats(request: Request, db: Session = Depends(get_db)):
    started_at = time.perf_counter()
    error_message = None
    context: dict[str, Any] = {}
    try:
        context = build_database_stats_context(db)
    except Exception as exc:
        logger.exception("data database-stats failed")
        error_message = http_error_message(exc)
    response = templates.TemplateResponse(
        "partials/data_database_stats.html",
        {"request": request, "error_message": error_message, **context},
    )
    _log_route("/web/partials/data/database-stats", started_at, "error" if error_message else "ok")
    return response


@router.get("/search-latency", include_in_schema=False)
def data_search_latency(
    request: Request,
    recent_limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    started_at = time.perf_counter()
    error_message = None
    context: dict[str, Any] = {"summary": {}, "persisted": False}
    try:
        context = build_search_latency_context(db, recent_limit=recent_limit)
    except Exception as exc:
        logger.exception("data search-latency failed")
        error_message = http_error_message(exc)
    response = templates.TemplateResponse(
        "partials/data_search_latency.html",
        {"request": request, "error_message": error_message, **context},
    )
    _log_route("/web/partials/data/search-latency", started_at, "error" if error_message else "ok")
    return response
