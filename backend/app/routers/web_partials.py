from __future__ import annotations

import time
from typing import Any

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.constants.market_indices import NSE_CSV_TREND_FILTER_OPTIONS, STOCK_INDEX_FILTER_OPTIONS
from app.database import get_db
from app.services.analytics_refresh_service import (
    get_cached_sequential_rankings,
    refresh_sequential_rankings_cache,
)
from app.services.market_movers_service import compute_market_movers_from_db
from app.services.market_overview_service import SAMPLE_PRICES, get_market_overview
from app.services.market_sync_service import get_market_sync_status, start_market_sync
from app.services.fundamentals_service import get_fundamentals_status
from app.services.search_telemetry_service import record_search_query
from app.services.stock_performance_service import (
    get_stock_performance_by_ids,
    list_stock_industries,
    list_stock_performance,
    list_stock_sectors,
)
from app.services.market_data_service import get_latest_prices_map
from app.services.ticker_service import search_stocks
from app.services.news_service import list_news_curator
from app.web_utils import sort_quotes, templates


router = APIRouter(prefix="/web/partials", tags=["web-partials"])
logger = logging.getLogger(__name__)


ALL_STOCK_SORTS: dict[str, tuple[str, bool, str]] = {
    "latest_date": ("latest_price_datetime", True, "Latest date newest"),
    "latest_price": ("latest_price", True, "Latest price high to low"),
    "volume": ("latest_volume", True, "Volume high to low"),
    "change_1m": ("change_1m_pct", True, "1M change"),
    "change_3m": ("change_3m_pct", True, "3M change"),
    "change_6m": ("change_6m_pct", True, "6M change"),
    "change_1y": ("change_1y_pct", True, "1Y change"),
}

# Web sort key → (service sort_by column, sort_desc) passed to list_stock_performance
_SORT_SERVICE_MAP: dict[str, tuple[str | None, bool]] = {
    "latest_date": ("latest_price_datetime", True),
    "latest_price": ("latest_price", True),
    "volume": ("latest_volume", True),
    "change_1m": ("change_1m_pct", True),
    "change_3m": ("change_3m_pct", True),
    "change_6m": ("change_6m_pct", True),
    "change_1y": ("change_1y_pct", True),
}


def _real_index_overview(db: Session) -> tuple[dict, str | None]:
    overview = get_market_overview(db=db)
    indices = []
    hidden_sample_count = 0
    for quote in overview.get("indices") or []:
        sample = SAMPLE_PRICES.get(quote.get("yahoo_symbol"))
        if sample and round(float(quote.get("price") or 0), 2) == round(float(sample[0]), 2):
            hidden_sample_count += 1
            continue
        indices.append(quote)
    warning = (
        "No real index candles are available yet. Load or sync market data to populate index cards."
        if hidden_sample_count
        else None
    )
    return {**overview, "indices": indices}, warning



def _filter_value(value: str | None) -> str | None:
    if value is None:
        return None
    clean = value.strip()
    return clean or None


def _partial_error_message(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, str):
            return detail
        if isinstance(detail, list) and detail:
            first = detail[0]
            if isinstance(first, dict) and first.get("msg"):
                return str(first["msg"])
    return "Unable to load this section right now. Try again or adjust filters."


@router.get("/stocks/search", include_in_schema=False)
def stock_search_results(
    request: Request,
    query: str = Query(default=""),
    q: str | None = Query(default=None),
    exchange: str | None = Query(default=None),
    selectable: bool = Query(default=False),
    mode: str = Query(default=""),
    db: Session = Depends(get_db),
):
    started_at = time.perf_counter()
    stocks = []
    latest_prices: dict[int, float] = {}
    latest_dates: dict[int, str] = {}
    clean_query = (query or q or "").strip()
    try:
        if clean_query:
            stocks = search_stocks(
                db,
                query=clean_query,
                exchange=exchange.strip().upper() if exchange else None,
                limit=12,
            )
        if selectable and stocks:
            stock_ids = [row.id for row in stocks]
            price_map = get_latest_prices_map(db, stock_ids)
            for row in stocks:
                price = price_map.get(row.id)
                if price is not None:
                    latest_prices[row.id] = float(price)
            # Targeted fetch: only the IDs we actually need, not a full 5000-row scan
            perf_by_id = get_stock_performance_by_ids(db, stock_ids)
            for row in stocks:
                perf = perf_by_id.get(row.id)
                if not perf:
                    continue
                dt = perf.get("latest_price_datetime")
                if dt is not None:
                    latest_dates[row.id] = str(dt)[:10]
                if row.id not in latest_prices and perf.get("latest_price") is not None:
                    latest_prices[row.id] = float(perf["latest_price"])
        return templates.TemplateResponse(
            "partials/stock_search_results.html",
            {
                "request": request,
                "query": clean_query,
                "exchange": exchange or "",
                "stocks": stocks,
                "selectable": selectable,
                "latest_prices": latest_prices,
                "latest_dates": latest_dates,
                "mode": (
                    "link"
                    if mode == "link"
                    else ("select" if selectable or mode == "select" else "")
                ),
            },
        )
    finally:
        if clean_query:
            duration_ms = (time.perf_counter() - started_at) * 1000
            record_search_query(
                search_type="stock_search",
                query_text=clean_query,
                filter_name="exchange" if exchange else None,
                filter_value=exchange.strip().upper() if exchange else None,
                result_count=len(stocks),
                duration_ms=duration_ms,
            )


@router.get("/explore/index-cards", include_in_schema=False)
def explore_index_cards(request: Request, db: Session = Depends(get_db)):
    try:
        overview, data_warning = _real_index_overview(db)
    except Exception as exc:
        logger.exception("explore index-cards failed")
        overview, data_warning = {"indices": []}, _partial_error_message(exc)
    return templates.TemplateResponse(
        "partials/explore_index_cards.html",
        {"request": request, "overview": overview, "data_warning": data_warning},
    )


@router.get("/explore/top-movers", include_in_schema=False)
def explore_top_movers(
    request: Request,
    group: str | None = Query(default=None),
    bucket: str | None = Query(default=None),
    sort: str = Query(default="trend"),
    index: str | None = Query(default=None),
    direction: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    active_group = bucket or group or "gainers"
    normalized_index = _filter_value(index)
    error_message = None
    try:
        movers = compute_market_movers_from_db(db, limit=50, nifty_index=normalized_index)
    except Exception as exc:
        logger.exception("explore top-movers failed")
        error_message = _partial_error_message(exc)
        movers = {
            "top_gainers": [],
            "top_losers": [],
            "volume_shockers": [],
            "eligible_count": 0,
            "nifty_index_label": None,
        }
    group_key = {
        "gainers": "top_gainers",
        "losers": "top_losers",
        "volume": "volume_shockers",
    }.get(active_group, "top_gainers")
    descending = direction == "desc" if direction else not (active_group == "losers" and sort == "trend")
    quotes = sort_quotes(movers.get(group_key) or [], sort_by=sort, descending=descending)
    return templates.TemplateResponse(
        "partials/explore_top_movers.html",
        {
            "request": request,
            "movers": movers,
            "nifty_index_options": NSE_CSV_TREND_FILTER_OPTIONS,
            "selected_index": normalized_index or "",
            "active_group": active_group if active_group in {"gainers", "losers", "volume"} else "gainers",
            "active_sort": sort if sort in {"trend", "price", "volume"} else "trend",
            "active_direction": "desc" if descending else "asc",
            "quotes": quotes,
            "error_message": error_message,
        },
    )


@router.get("/explore/all-stocks", include_in_schema=False)
def explore_all_stocks(
    request: Request,
    search: str = Query(default=""),
    exchange: str = Query(default=""),
    index: str = Query(default="BHAV_INDEX"),
    sector: str = Query(default=""),
    industry: str = Query(default=""),
    sort_by: str = Query(default="latest_date"),
    limit: int = Query(default=200, ge=25, le=500),
    db: Session = Depends(get_db),
):
    exchange_filter = _filter_value(exchange)
    index_filter = _filter_value(index)
    sector_filter = _filter_value(sector)
    industry_filter = _filter_value(industry)
    clean_search = search.strip()

    sectors = list_stock_sectors(
        db,
        exchange=exchange_filter,
        index_code=index_filter,
        only_with_prices=True,
    )
    industries = list_stock_industries(
        db,
        exchange=exchange_filter,
        sector=sector_filter,
        index_code=index_filter,
        only_with_prices=True,
    )
    service_sort, service_desc = _SORT_SERVICE_MAP.get(sort_by, ("latest_price_datetime", True))
    error_message = None
    try:
        rows = list_stock_performance(
            db,
            query=clean_search or None,
            exchange=exchange_filter,
            sector=sector_filter,
            industry=industry_filter,
            index_code=index_filter,
            limit=limit,
            only_with_prices=True,
            sort_by=service_sort,
            sort_desc=service_desc,
        )
    except Exception as exc:
        logger.exception("explore all-stocks failed")
        error_message = _partial_error_message(exc)
        rows = []

    return templates.TemplateResponse(
        "partials/explore_all_stocks.html",
        {
            "request": request,
            "rows": rows,
            "search": clean_search,
            "selected_exchange": exchange_filter or "",
            "selected_index": index_filter or "",
            "selected_sector": sector_filter or "",
            "selected_industry": industry_filter or "",
            "selected_sort": sort_by if sort_by in ALL_STOCK_SORTS else "latest_date",
            "selected_limit": limit,
            "stock_index_options": STOCK_INDEX_FILTER_OPTIONS,
            "sector_options": sectors,
            "industry_options": industries,
            "sort_options": ALL_STOCK_SORTS,
            "error_message": error_message,
        },
    )


@router.get("/explore/sequential-rankings", include_in_schema=False)
def explore_sequential_rankings(
    request: Request,
    side: str = Query(default="buy"),
    refresh: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    error_message = None
    try:
        if refresh:
            rankings = refresh_sequential_rankings_cache(db, limit=15, universe_limit=2000)
        else:
            rankings = get_cached_sequential_rankings(db)
            if rankings is None:
                rankings = refresh_sequential_rankings_cache(db, limit=15, universe_limit=2000)
    except Exception as exc:
        logger.exception("explore sequential-rankings failed")
        error_message = _partial_error_message(exc)
        rankings = {"eligible_count": 0, "rows_scanned": 0, "top_buys": [], "top_sells": []}

    active_side = "sell" if side == "sell" else "buy"
    rows = rankings.get("top_sells" if active_side == "sell" else "top_buys") or []
    return templates.TemplateResponse(
        "partials/explore_sequential_rankings.html",
        {
            "request": request,
            "rankings": rankings,
            "active_side": active_side,
            "rows": rows,
            "error_message": error_message,
        },
    )


def _render_sync_status_partial(
    request: Request,
    db: Session,
    *,
    sync_message: str | None = None,
    sync_tone: str = "info",
    sync_error: str | None = None,
    sync_result: dict[str, Any] | None = None,
    scheduler_enabled: bool = False,
    scheduler_message: str | None = None,
    hx_trigger: str | None = None,
    status_code: int = 200,
):
    sync_status = get_market_sync_status(db)
    fundamentals_status = get_fundamentals_status(db)
    response = templates.TemplateResponse(
        "partials/sync_status.html",
        {
            "request": request,
            "sync_status": sync_status,
            "sync_message": sync_message,
            "sync_tone": sync_tone,
            "sync_error": sync_error,
            "sync_result": sync_result,
            "fundamentals_status": fundamentals_status,
            "scheduler_enabled": scheduler_enabled,
            "scheduler_message": scheduler_message,
        },
        status_code=status_code,
    )
    if hx_trigger:
        response.headers["HX-Trigger"] = hx_trigger
    return response


@router.get("/explore/sync-status", include_in_schema=False)
def explore_sync_status(request: Request, db: Session = Depends(get_db)):
    return _render_sync_status_partial(request, db)


@router.post("/explore/sync-now", include_in_schema=False)
def explore_sync_now(request: Request, db: Session = Depends(get_db)):
    started_at = time.perf_counter()
    logger.info("Explore Sync Now clicked")

    try:
        logger.info("Starting market sync from web UI")
        result = start_market_sync(db)
        duration = time.perf_counter() - started_at
        logger.info(
            "Market sync start completed in %.2fs: started=%s message=%s run_id=%s",
            duration,
            result.get("started"),
            result.get("message"),
            result.get("run_id"),
        )

        sync_status = get_market_sync_status(db)
        current_run = sync_status.get("current_run") or {}
        last_run = sync_status.get("last_run") or {}
        logger.info(
            "Sync status after start: is_running=%s run_id=%s last_status=%s rows_saved=%s",
            sync_status.get("is_running"),
            sync_status.get("run_id"),
            sync_status.get("last_sync_status"),
            last_run.get("rows_saved"),
        )

        if result.get("started"):
            message = str(result.get("message") or "Market sync started in the background.")
            tone = "success"
            hx_trigger = "market-sync-started"
        else:
            message = str(result.get("message") or "Market sync is already running.")
            tone = "warning"
            hx_trigger = None

        return _render_sync_status_partial(
            request,
            db,
            sync_message=message,
            sync_tone=tone,
            sync_result=result,
            hx_trigger=hx_trigger,
        )
    except Exception as exc:
        duration = time.perf_counter() - started_at
        logger.exception("Market sync failed from Explore Sync Now after %.2fs", duration)
        try:
            sync_status = get_market_sync_status(db)
        except Exception:
            logger.exception("Unable to load sync status after sync failure")
            sync_status = None
        return _render_sync_status_partial(
            request,
            db,
            sync_message="Market sync failed. Check backend logs for details.",
            sync_tone="danger",
            sync_error=str(exc),
            sync_result={"started": False, "message": str(exc)},
            status_code=500,
        )


@router.get("/explore/news-curator", include_in_schema=False)
def explore_news_curator(
    request: Request,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20),
    provider: str | None = Query(default=None),
    search: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    per_page = per_page if per_page in (10, 20, 30, 50) else 20
    data = list_news_curator(db, page=page, per_page=per_page, provider=provider, search=search)
    return templates.TemplateResponse(
        "partials/explore_news_curator.html",
        {"request": request, "curator": data, "per_page": per_page, "provider": provider or "", "search": search or ""},
    )


@router.post("/explore/toggle-sync-schedule", include_in_schema=False)
def explore_toggle_sync_schedule(
    request: Request,
    enabled: str = Form(default="false"),
    db: Session = Depends(get_db),
):
    requested_enabled = enabled.lower() in {"true", "1", "on", "yes"}
    message = (
        "UI ready; backend scheduler control is not implemented yet. "
        "Sync interval selection and auto-run while this page is open are not available in the web UI."
        if requested_enabled
        else "Persistent scheduler is off."
    )
    return _render_sync_status_partial(
        request,
        db,
        scheduler_message=message,
    )
