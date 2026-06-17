from __future__ import annotations

import json
import logging
import time
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.web_index_fund_helpers import (
    build_history_context,
    build_return_plot_context,
    build_return_plots_shell_context,
    build_strategy_ready_context,
    build_summary_context,
    get_index_sync_status,
    http_error_message,
    list_filter_options,
    list_instrument_options,
    list_universe_rows,
    search_plot_instruments,
    start_index_fund_sync,
)
from app.web_utils import templates

logger = logging.getLogger(__name__)
timing_logger = logging.getLogger("app.timing")

router = APIRouter(prefix="/web/partials/index-fund", tags=["web-index-fund-partials"])


def _parse_bool_field(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"true", "on", "1", "yes"}


def _parse_date_field(value: str | None) -> date | None:
    clean = str(value or "").strip()
    if not clean:
        return None
    return date.fromisoformat(clean)


def _parse_instrument_ids(values: list[str]) -> list[int]:
    parsed: list[int] = []
    for raw in values:
        try:
            parsed.append(int(str(raw).strip()))
        except (TypeError, ValueError):
            continue
    return list(dict.fromkeys(parsed))


def _attach_plot_json_strings(plot_context: dict[str, Any]) -> dict[str, Any]:
    plot_context["return_chart_json_str"] = (
        json.dumps(plot_context["return_chart_json"]) if plot_context.get("return_chart_json") else ""
    )
    plot_context["indexed_chart_json_str"] = (
        json.dumps(plot_context["indexed_chart_json"]) if plot_context.get("indexed_chart_json") else ""
    )
    plot_context["drawdown_chart_json_str"] = (
        json.dumps(plot_context["drawdown_chart_json"]) if plot_context.get("drawdown_chart_json") else ""
    )
    return plot_context


def _log_route(route: str, started_at: float, status: str = "ok") -> None:
    timing_logger.info(
        "operation=web_route route=%s status=%s duration_ms=%.2f",
        route,
        status,
        (time.perf_counter() - started_at) * 1000,
    )


def _render_sync_partial(
    request: Request,
    *,
    sync_message: str | None = None,
    sync_tone: str = "info",
    sync_error: str | None = None,
    sync_result: dict[str, Any] | None = None,
    hx_trigger: str | None = None,
    status_code: int = 200,
):
    sync_status = get_index_sync_status()
    response = templates.TemplateResponse(
        "partials/index_fund_sync_status.html",
        {
            "request": request,
            "sync_status": sync_status,
            "sync_message": sync_message,
            "sync_tone": sync_tone,
            "sync_error": sync_error,
            "sync_result": sync_result,
        },
        status_code=status_code,
    )
    if hx_trigger:
        response.headers["HX-Trigger"] = hx_trigger
    return response


@router.get("/summary", include_in_schema=False)
def index_fund_summary(request: Request, db: Session = Depends(get_db)):
    started_at = time.perf_counter()
    error_message = None
    summary: dict[str, Any] = {}
    try:
        summary = build_summary_context(db)
    except Exception as exc:
        logger.exception("index-fund summary failed")
        error_message = http_error_message(exc)
    response = templates.TemplateResponse(
        "partials/index_fund_summary.html",
        {"request": request, "summary": summary, "error_message": error_message},
    )
    _log_route("/web/partials/index-fund/summary", started_at, "error" if error_message else "ok")
    return response


@router.get("/sync-status", include_in_schema=False)
def index_fund_sync_status(request: Request):
    started_at = time.perf_counter()
    try:
        response = _render_sync_partial(request)
        _log_route("/web/partials/index-fund/sync-status", started_at)
        return response
    except Exception as exc:
        logger.exception("index-fund sync-status failed")
        _log_route("/web/partials/index-fund/sync-status", started_at, "error")
        return _render_sync_partial(
            request,
            sync_error=http_error_message(exc),
            sync_tone="danger",
            status_code=500,
        )


@router.post("/sync", include_in_schema=False)
def index_fund_sync(request: Request):
    started_at = time.perf_counter()
    logger.info("Index Fund Sync clicked")
    try:
        result = start_index_fund_sync()
        if result.get("started"):
            message = str(result.get("message") or "Index fund sync started.")
            tone = "success"
            hx_trigger = "index-fund-sync-started,market-sync-started"
        else:
            message = str(result.get("message") or "Index fund sync is already running.")
            tone = "warning"
            hx_trigger = None
        response = _render_sync_partial(
            request,
            sync_message=message,
            sync_tone=tone,
            sync_result=result,
            hx_trigger=hx_trigger,
        )
        _log_route("/web/partials/index-fund/sync", started_at)
        return response
    except Exception as exc:
        logger.exception("index-fund sync failed")
        _log_route("/web/partials/index-fund/sync", started_at, "error")
        return _render_sync_partial(
            request,
            sync_message="Index fund sync failed. Check backend logs.",
            sync_tone="danger",
            sync_error=http_error_message(exc),
            status_code=500,
        )


@router.get("/universe", include_in_schema=False)
def index_fund_universe(
    request: Request,
    query: str = Query(default=""),
    category: str | None = Query(default=None),
    currency: str | None = Query(default=None),
    has_prices: str = Query(default="all"),
    sort_by: str = Query(default="latest_date"),
    descending: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    started_at = time.perf_counter()
    error_message = None
    rows: list[dict[str, Any]] = []
    filter_options: dict[str, Any] = {"categories": [], "currencies": []}
    try:
        filter_options = list_filter_options(db)
        rows = list_universe_rows(
            db,
            query=query,
            category=category,
            currency=currency,
            has_prices=has_prices if has_prices in ("all", "with", "without") else "all",
            sort_by=sort_by,
            descending=descending,
            limit=limit,
        )
    except Exception as exc:
        logger.exception("index-fund universe failed")
        error_message = http_error_message(exc)
    response = templates.TemplateResponse(
        "partials/index_fund_universe.html",
        {
            "request": request,
            "rows": rows,
            "error_message": error_message,
            "filter_options": filter_options,
            "filters": {
                "query": query,
                "category": category or "",
                "currency": currency or "",
                "has_prices": has_prices,
                "sort_by": sort_by,
                "descending": descending,
                "limit": limit,
            },
        },
    )
    _log_route("/web/partials/index-fund/universe", started_at, "error" if error_message else "ok")
    return response


@router.get("/instrument-search", include_in_schema=False)
def index_fund_instrument_search(
    request: Request,
    query: str = Query(default=""),
    category: str | None = Query(default=None),
    has_prices: str = Query(default="all"),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    started_at = time.perf_counter()
    error_message = None
    results: list[dict[str, Any]] = []
    clean_query = query.strip()
    try:
        if len(clean_query) >= 1:
            results = search_plot_instruments(
                db,
                query=clean_query,
                category=category,
                has_prices=has_prices if has_prices in ("all", "with", "without") else "all",
                limit=limit,
            )
    except Exception as exc:
        logger.exception("index-fund instrument-search failed")
        error_message = http_error_message(exc)
    response = templates.TemplateResponse(
        "partials/index_fund_instrument_search_results.html",
        {
            "request": request,
            "results": results,
            "query": clean_query,
            "error_message": error_message,
        },
    )
    _log_route("/web/partials/index-fund/instrument-search", started_at, "error" if error_message else "ok")
    return response


@router.get("/return-plots", include_in_schema=False)
def index_fund_return_plots_shell(request: Request, db: Session = Depends(get_db)):
    started_at = time.perf_counter()
    error_message = None
    shell: dict[str, Any] = {"selected_instruments": [], "filters": {}}
    try:
        shell = build_return_plots_shell_context(db)
    except Exception as exc:
        logger.exception("index-fund return-plots shell failed")
        error_message = http_error_message(exc)
    response = templates.TemplateResponse(
        "partials/index_fund_return_plots.html",
        {
            "request": request,
            "selected_instruments": shell.get("selected_instruments") or [],
            "filters": shell.get("filters") or {},
            "error_message": error_message,
        },
    )
    _log_route("/web/partials/index-fund/return-plots", started_at, "error" if error_message else "ok")
    return response


@router.post("/return-plots", include_in_schema=False)
async def index_fund_return_plots(
    request: Request,
    db: Session = Depends(get_db),
):
    started_at = time.perf_counter()
    error_message = None
    validation_message: str | None = None
    plot_context: dict[str, Any] = {}
    form = await request.form()
    instrument_ids = _parse_instrument_ids(form.getlist("instrument_ids"))
    period = str(form.get("period") or "5y").strip() or "5y"
    start_date = _parse_date_field(form.get("start_date"))
    end_date = _parse_date_field(form.get("end_date"))
    normalize_indexed = _parse_bool_field(form.get("normalize_indexed"))
    compare_nifty = _parse_bool_field(form.get("compare_nifty"))

    logger.info(
        "index_fund.return_plots selected_count=%s instruments=%s start=%s end=%s normalize=%s compare_nifty=%s",
        len(instrument_ids),
        instrument_ids,
        start_date,
        end_date,
        normalize_indexed,
        compare_nifty,
    )

    if not instrument_ids:
        validation_message = "Select at least one instrument before generating return plots."
    else:
        try:
            plot_context = build_return_plot_context(
                db,
                ids=instrument_ids,
                period=period,
                start_date=start_date,
                end_date=end_date,
                normalize_indexed=normalize_indexed,
                compare_nifty=compare_nifty,
            )
            plot_context = _attach_plot_json_strings(plot_context)
        except Exception as exc:
            logger.exception("index-fund return-plots failed")
            error_message = http_error_message(exc)

    response = templates.TemplateResponse(
        "partials/index_fund_return_plots_results.html",
        {
            "request": request,
            "plot": plot_context,
            "error_message": error_message,
            "validation_message": validation_message,
        },
    )
    _log_route("/web/partials/index-fund/return-plots", started_at, "error" if error_message else "ok")
    return response


@router.get("/history", include_in_schema=False)
def index_fund_history(
    request: Request,
    index_fund_id: int | None = Query(default=None),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    limit: int = Query(default=250, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    started_at = time.perf_counter()
    error_message = None
    history: dict[str, Any] = {}
    instrument_options: list[dict[str, Any]] = []
    try:
        instrument_options = list_instrument_options(db, limit=100)
        history = build_history_context(
            db,
            index_fund_id=index_fund_id,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
        if history.get("error"):
            error_message = history["error"]
    except Exception as exc:
        logger.exception("index-fund history failed")
        error_message = http_error_message(exc)
    response = templates.TemplateResponse(
        "partials/index_fund_history.html",
        {
            "request": request,
            "history": history,
            "error_message": error_message,
            "instrument_options": instrument_options,
            "filters": {
                "index_fund_id": index_fund_id,
                "start_date": start_date.isoformat() if start_date else "",
                "end_date": end_date.isoformat() if end_date else "",
                "limit": limit,
            },
        },
    )
    _log_route("/web/partials/index-fund/history", started_at, "error" if error_message else "ok")
    return response


@router.get("/strategy-ready", include_in_schema=False)
def index_fund_strategy_ready(request: Request, db: Session = Depends(get_db)):
    started_at = time.perf_counter()
    error_message = None
    context: dict[str, Any] = {}
    try:
        context = build_strategy_ready_context(db)
    except Exception as exc:
        logger.exception("index-fund strategy-ready failed")
        error_message = http_error_message(exc)
    response = templates.TemplateResponse(
        "partials/index_fund_strategy_ready.html",
        {
            "request": request,
            "strategy_ready": context,
            "error_message": error_message,
        },
    )
    _log_route("/web/partials/index-fund/strategy-ready", started_at, "error" if error_message else "ok")
    return response
