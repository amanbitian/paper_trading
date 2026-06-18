from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.security import get_current_user
from app.services.web_analytics_helpers import (
    DEFAULT_TREND_PERIOD,
    TREND_PERIOD_OPTIONS,
    build_trend_summary_context,
    build_trend_table_rows,
    build_treemap_plotly_json,
    fetch_market_trends,
    get_cached_trend_filters,
    resolve_trends_filter_state,
    validate_treemap_figure,
)
from app.web_utils import templates

logger = logging.getLogger(__name__)
timing_logger = logging.getLogger("app.timing")

router = APIRouter(prefix="/web/partials/trends", tags=["web-trends-partials"])


def _log_route(route: str, started_at: float, status: str = "ok") -> None:
    timing_logger.info(
        "operation=web_route route=%s status=%s duration_ms=%.2f",
        route,
        status,
        (time.perf_counter() - started_at) * 1000,
    )


def _read_trends_params(
    *,
    period: str,
    index_universe: str | None,
    market: str | None,
    industry: str | None,
    industry_group: str | None,
    nifty_index: str,
    rows: int | None,
    limit: int | None,
    rows_mode: str,
    sort_by: str,
) -> dict[str, str | int | None]:
    return {
        "period": period,
        "index_universe": index_universe or market,
        "industry": industry or industry_group,
        "nifty_index": nifty_index,
        "rows": rows if rows is not None else limit,
        "rows_mode": rows_mode,
        "sort_by": sort_by,
    }


def _trend_context(db: Session, raw_params: dict[str, Any], *, partial: str) -> dict:
    filter_payload = get_cached_trend_filters(db)
    state = resolve_trends_filter_state(
        filter_payload=filter_payload,
        period=raw_params.get("period"),
        index_universe=raw_params.get("index_universe"),
        industry=raw_params.get("industry"),
        nifty_index=raw_params.get("nifty_index"),
        rows=raw_params.get("rows"),
        rows_mode=raw_params.get("rows_mode"),
        sort_by=raw_params.get("sort_by"),
    )
    logger.info(
        "trends partial=%s period=%s universe=%s industry=%s nifty_index=%s "
        "rows_mode=%s requested_rows=%s effective_rows=%s sort_by=%s returned_rows=pending",
        partial,
        state["period"],
        state["index_universe"],
        state["industry"],
        state["nifty_index"],
        state["rows_mode"],
        state.get("requested_rows"),
        state["rows"],
        state["sort_by"],
    )
    payload = fetch_market_trends(db, state["query"])
    items = payload.get("items") or []
    logger.info(
        "trends partial=%s period=%s nifty_index=%s rows_mode=%s effective_rows=%s returned_rows=%s",
        partial,
        state["period"],
        state["nifty_index"],
        state["rows_mode"],
        state["rows"],
        len(items),
    )
    return {"state": state, "query": state["query"], "payload": payload, "items": items}


@router.get("/filters", include_in_schema=False)
def trends_filters(
    request: Request,
    period: str = Query(default=DEFAULT_TREND_PERIOD),
    index_universe: str | None = Query(default=None),
    market: str | None = Query(default=None),
    industry: str | None = Query(default=None),
    industry_group: str | None = Query(default=None),
    nifty_index: str = Query(default="All indices"),
    rows: int | None = Query(default=None),
    limit: int | None = Query(default=None),
    rows_mode: str = Query(default="auto"),
    sort_by: str = Query(default="size"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    raw = _read_trends_params(
        period=period,
        index_universe=index_universe,
        market=market,
        industry=industry,
        industry_group=industry_group,
        nifty_index=nifty_index,
        rows=rows,
        limit=limit,
        rows_mode=rows_mode,
        sort_by=sort_by,
    )
    try:
        filter_payload = get_cached_trend_filters(db)
        state = resolve_trends_filter_state(filter_payload=filter_payload, **raw)
        logger.info(
            "trends partial=filters period=%s universe=%s industry=%s nifty_index=%s "
            "rows_mode=%s requested_rows=%s effective_rows=%s sort_by=%s",
            state["period"],
            state["index_universe"],
            state["industry"],
            state["nifty_index"],
            state["rows_mode"],
            state.get("requested_rows"),
            state["rows"],
            state["sort_by"],
        )
        return templates.TemplateResponse(
            "partials/trends_filters.html",
            {
                "request": request,
                "filter_payload": filter_payload,
                "period": state["period"],
                "index_universe": state["index_universe"],
                "industry": state["industry"],
                "nifty_index": state["nifty_index"],
                "rows": state["rows"],
                "rows_mode": state["rows_mode"],
                "sort_by": state["sort_by"],
                "max_stocks": state["max_stocks"],
                "period_options": TREND_PERIOD_OPTIONS,
            },
        )
    finally:
        _log_route("/web/partials/trends/filters", started_at)


@router.get("/summary", include_in_schema=False)
def trends_summary(
    request: Request,
    period: str = Query(default=DEFAULT_TREND_PERIOD),
    index_universe: str | None = Query(default=None),
    market: str | None = Query(default=None),
    industry: str | None = Query(default=None),
    industry_group: str | None = Query(default=None),
    nifty_index: str = Query(default="All indices"),
    rows: int | None = Query(default=None),
    limit: int | None = Query(default=None),
    rows_mode: str = Query(default="auto"),
    sort_by: str = Query(default="size"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    raw = _read_trends_params(
        period=period,
        index_universe=index_universe,
        market=market,
        industry=industry,
        industry_group=industry_group,
        nifty_index=nifty_index,
        rows=rows,
        limit=limit,
        rows_mode=rows_mode,
        sort_by=sort_by,
    )
    try:
        ctx = _trend_context(db, raw, partial="summary")
        summary = build_trend_summary_context(ctx["payload"], ctx["items"])
        return templates.TemplateResponse(
            "partials/trends_summary.html",
            {"request": request, "summary": summary, "error": None},
        )
    except Exception as exc:
        logger.exception("Trends summary failed")
        return templates.TemplateResponse(
            "partials/trends_summary.html",
            {"request": request, "summary": None, "error": str(exc)},
        )
    finally:
        _log_route("/web/partials/trends/summary", started_at)


@router.get("/treemap", include_in_schema=False)
def trends_treemap(
    request: Request,
    period: str = Query(default=DEFAULT_TREND_PERIOD),
    index_universe: str | None = Query(default=None),
    market: str | None = Query(default=None),
    industry: str | None = Query(default=None),
    industry_group: str | None = Query(default=None),
    nifty_index: str = Query(default="All indices"),
    rows: int | None = Query(default=None),
    limit: int | None = Query(default=None),
    rows_mode: str = Query(default="auto"),
    sort_by: str = Query(default="size"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    raw = _read_trends_params(
        period=period,
        index_universe=index_universe,
        market=market,
        industry=industry,
        industry_group=industry_group,
        nifty_index=nifty_index,
        rows=rows,
        limit=limit,
        rows_mode=rows_mode,
        sort_by=sort_by,
    )
    try:
        ctx = _trend_context(db, raw, partial="treemap")
        items = ctx["items"]
        figure = build_treemap_plotly_json(items)
        validation = validate_treemap_figure(figure)
        if not validation.get("valid"):
            figure = None
        return templates.TemplateResponse(
            "partials/trends_treemap.html",
            {
                "request": request,
                "figure": figure,
                "row_count": len(items),
                "error": None,
            },
        )
    except Exception as exc:
        logger.exception("Trends treemap failed")
        return templates.TemplateResponse(
            "partials/trends_treemap.html",
            {
                "request": request,
                "figure": None,
                "row_count": 0,
                "error": str(exc),
            },
        )
    finally:
        _log_route("/web/partials/trends/treemap", started_at)


@router.get("/table", include_in_schema=False)
def trends_table(
    request: Request,
    period: str = Query(default=DEFAULT_TREND_PERIOD),
    index_universe: str | None = Query(default=None),
    market: str | None = Query(default=None),
    industry: str | None = Query(default=None),
    industry_group: str | None = Query(default=None),
    nifty_index: str = Query(default="All indices"),
    rows: int | None = Query(default=None),
    limit: int | None = Query(default=None),
    rows_mode: str = Query(default="auto"),
    sort_by: str = Query(default="size"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    raw = _read_trends_params(
        period=period,
        index_universe=index_universe,
        market=market,
        industry=industry,
        industry_group=industry_group,
        nifty_index=nifty_index,
        rows=rows,
        limit=limit,
        rows_mode=rows_mode,
        sort_by=sort_by,
    )
    try:
        ctx = _trend_context(db, raw, partial="table")
        rows_data = build_trend_table_rows(ctx["items"])
        return templates.TemplateResponse(
            "partials/trends_table.html",
            {
                "request": request,
                "rows": rows_data,
                "payload": ctx["payload"],
                "error": None,
            },
        )
    except Exception as exc:
        logger.exception("Trends table failed")
        return templates.TemplateResponse(
            "partials/trends_table.html",
            {
                "request": request,
                "rows": [],
                "payload": {},
                "error": str(exc),
            },
        )
    finally:
        _log_route("/web/partials/trends/table", started_at)
