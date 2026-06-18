from __future__ import annotations

import logging
import time
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.constants.market_indices import NSE_CSV_TREND_FILTER_OPTIONS, STOCK_INDEX_FILTER_OPTIONS
from app.database import get_db
from app.models.user import User
from app.services.analytics_refresh_service import get_cached_sequential_rankings
from app.services.market_overview_service import SAMPLE_PRICES, get_market_overview
from app.services.market_sync_service import get_market_sync_status
from app.services.stock_performance_service import list_stock_industries, list_stock_sectors
from app.services.web_explore_stock_helpers import (
    build_preselected_stock_view,
    build_stock_detail_context,
    resolve_stock_by_route_key,
    resolve_stock_for_prefill,
)
from app.security import get_current_user
from app.services.web_analytics_helpers import DEFAULT_TREND_PERIOD, TREND_PERIOD_OPTIONS
from app.services.web_backtesting_helpers import (
    BENCHMARK_OPTIONS,
    COST_MODEL_OPTIONS,
    EXECUTION_MODE_OPTIONS,
    INTRABAR_OPTIONS,
    get_index_filter_options,
    list_strategy_templates,
)
from app.services.web_trading_helpers import ensure_default_portfolios, list_user_portfolios
from app.web_utils import sort_quotes, templates

logger = logging.getLogger(__name__)
timing_logger = logging.getLogger("app.timing")

router = APIRouter(prefix="/web", tags=["web"])


def _log_page_route(route: str, started_at: float) -> None:
    timing_logger.info(
        "operation=web_route route=%s status=ok duration_ms=%.2f",
        route,
        (time.perf_counter() - started_at) * 1000,
    )


def _remove_sample_indices(overview: dict) -> tuple[list[dict], str | None]:
    indices: list[dict] = []
    hidden_sample_count = 0
    for quote in overview.get("indices") or []:
        sample = SAMPLE_PRICES.get(quote.get("yahoo_symbol"))
        if sample and round(float(quote.get("price") or 0), 2) == round(float(sample[0]), 2):
            hidden_sample_count += 1
            continue
        indices.append(quote)
    if hidden_sample_count:
        return indices, "No real index candles are available yet. Load or sync market data to populate index cards."
    return indices, None


@router.get("/", include_in_schema=False)
def web_home() -> RedirectResponse:
    return RedirectResponse(url="/web/explore", status_code=302)


def _parse_optional_date(value: str | None) -> date | None:
    clean = str(value or "").strip()
    if not clean:
        return None
    return date.fromisoformat(clean)


@router.get("/explore", include_in_schema=False)
def explore(
    request: Request,
    stock: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    chart_type: str = Query(default="candlestick"),
    db: Session = Depends(get_db),
):
    _t = time.perf_counter
    _t0 = _t()
    overview = get_market_overview(db=db)
    timing_logger.info("explore.step get_market_overview=%.0fms", (_t() - _t0) * 1000)
    _t0 = _t()
    # Reuse movers already computed inside get_market_overview — avoids a second heavy SQL pass
    movers = {
        "top_gainers": overview.get("top_gainers", []),
        "top_losers": overview.get("top_losers", []),
        "volume_shockers": overview.get("volume_shockers", []),
        "most_bought": overview.get("most_bought", []),
        "eligible_count": overview.get("movers_universe_count", 0),
        "nifty_index_label": None,
    }
    sync_status = get_market_sync_status(db)
    timing_logger.info("explore.step get_market_sync_status=%.0fms", (_t() - _t0) * 1000)
    _t0 = _t()
    sectors = list_stock_sectors(db, only_with_prices=True)
    timing_logger.info("explore.step list_stock_sectors=%.0fms", (_t() - _t0) * 1000)
    _t0 = _t()
    industries = list_stock_industries(db, only_with_prices=True)
    timing_logger.info("explore.step list_stock_industries=%.0fms", (_t() - _t0) * 1000)
    _t0 = _t()
    cached_rankings = get_cached_sequential_rankings(db) or {}
    timing_logger.info("explore.step get_cached_sequential_rankings=%.0fms", (_t() - _t0) * 1000)

    indices, data_warning = _remove_sample_indices(overview)

    quotes = sort_quotes(movers.get("top_gainers") or [], sort_by="trend", descending=True)

    stock_detail = None
    stock_detail_error = None
    chart_filters = {"start_date": start_date or "", "end_date": end_date or ""}
    if stock and stock.strip():
        resolved, resolve_error = resolve_stock_by_route_key(db, stock)
        if resolve_error:
            stock_detail_error = resolve_error
        elif resolved:
            try:
                plot_start = _parse_optional_date(start_date)
                plot_end = _parse_optional_date(end_date)
                resolved_chart = chart_type if chart_type in ("candlestick", "line") else "candlestick"
                stock_detail = build_stock_detail_context(
                    db,
                    resolved,
                    chart_type=resolved_chart,
                    start_date=plot_start,
                    end_date=plot_end,
                )
            except Exception:
                logger.exception("explore stock detail failed stock=%s", stock)
                stock_detail_error = "Unable to load stock detail right now."

    return templates.TemplateResponse(
        "pages/explore.html",
        {
            "request": request,
            "active_page": "explore",
            "title": "Explore",
            "subtitle": "Market overview, movers, search, and stored analytics.",
            "overview": {**overview, "indices": indices},
            "movers": movers,
            "sync_status": sync_status,
            "data_warning": data_warning,
            "active_group": "gainers",
            "active_sort": "trend",
            "quotes": quotes,
            "nifty_index_options": NSE_CSV_TREND_FILTER_OPTIONS,
            "stock_index_options": STOCK_INDEX_FILTER_OPTIONS,
            "sector_options": sectors,
            "industry_options": industries,
            "sequential_rankings": cached_rankings,
            "scheduler_enabled": False,
            "stock_detail": stock_detail,
            "stock_detail_error": stock_detail_error,
            "chart_filters": chart_filters,
            "selected_stock_key": stock.strip() if stock else "",
        },
    )


@router.get("/add-portfolio", include_in_schema=False)
def add_portfolio_page(
    request: Request,
    stock: str | None = Query(default=None),
    stock_id: int | None = Query(default=None),
    symbol: str | None = Query(default=None),
    exchange: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_default_portfolios(db, current_user)
    manual_portfolios = list_user_portfolios(db, current_user.id)
    selected_id = manual_portfolios[0].id if manual_portfolios else None

    preselected_stock: dict | None = None
    prefill_warning: str | None = None
    has_prefill_request = any(
        value not in (None, "")
        for value in (stock, stock_id, symbol, exchange)
    )
    resolved_stock, resolve_error = resolve_stock_for_prefill(
        db,
        stock=stock,
        stock_id=stock_id,
        symbol=symbol,
        exchange=exchange,
    )
    logger.info(
        "add_portfolio.prefill requested stock=%s stock_id=%s symbol=%s exchange=%s resolved=%s",
        stock,
        stock_id,
        symbol,
        exchange,
        resolved_stock.id if resolved_stock else None,
    )
    if resolved_stock:
        preselected_stock = build_preselected_stock_view(db, resolved_stock)
    elif has_prefill_request:
        prefill_warning = resolve_error or "Could not preselect stock from the link. Please search manually."

    default_buy_price = (
        preselected_stock["buy_price_default"]
        if preselected_stock and preselected_stock.get("buy_price_default") is not None
        else 100
    )
    default_purchase_date = (
        preselected_stock["purchase_date_default"]
        if preselected_stock
        else date.today().isoformat()
    )

    return templates.TemplateResponse(
        "pages/add_portfolio.html",
        {
            "request": request,
            "active_page": "add_portfolio",
            "title": "Add Portfolio",
            "subtitle": "Create portfolios and add manual holdings using stored market instruments.",
            "portfolios": manual_portfolios,
            "selected_portfolio_id": selected_id,
            "portfolio_types": ["manual", "paper", "sip", "algo"],
            "preselected_stock": preselected_stock,
            "prefill_warning": prefill_warning,
            "default_buy_price": default_buy_price,
            "default_purchase_date": default_purchase_date,
        },
    )


@router.get("/paper-trading", include_in_schema=False)
def paper_trading_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_default_portfolios(db, current_user)
    paper_portfolios = list_user_portfolios(db, current_user.id, portfolio_type="paper")
    selected_id = paper_portfolios[0].id if paper_portfolios else None
    empty_preview = {
        "errors": [],
        "warnings": [],
        "stock": None,
        "side": "BUY",
        "order_type": "MARKET",
        "quantity": 0,
        "latest_price": None,
        "gross_value": 0,
        "charges": 0,
        "estimated_total": 0,
        "available_cash": paper_portfolios[0].cash_balance if paper_portfolios else 0,
        "holding_qty": 0,
        "slippage_bps": 0,
    }
    return templates.TemplateResponse(
        "pages/paper_trading.html",
        {
            "request": request,
            "active_page": "paper_trading",
            "title": "Paper Trading",
            "subtitle": "Place simulated buy and sell orders using stored market prices.",
            "portfolios": paper_portfolios,
            "selected_portfolio_id": selected_id,
            "order_types": ["MARKET", "LIMIT", "STOP_LOSS"],
            "preview": empty_preview,
        },
    )


@router.get("/trends", include_in_schema=False)
def trends_page(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        return templates.TemplateResponse(
            "pages/trends.html",
            {
                "request": request,
                "active_page": "trends",
                "title": "Trends",
                "subtitle": "Compare stock performance across periods, sectors, industries, and indices.",
                "default_period": DEFAULT_TREND_PERIOD,
                "period_options": TREND_PERIOD_OPTIONS,
            },
        )
    finally:
        _log_page_route("/web/trends", started_at)


@router.get("/risk", include_in_schema=False)
def risk_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        ensure_default_portfolios(db, current_user)
        portfolios = list_user_portfolios(db, current_user.id)
        selected_id = portfolios[0].id if portfolios else None
        return templates.TemplateResponse(
            "pages/risk.html",
            {
                "request": request,
                "active_page": "risk",
                "title": "Risk Dashboard",
                "subtitle": "Measure portfolio exposure, concentration, drawdown, and downside risk.",
                "portfolios": portfolios,
                "selected_portfolio_id": selected_id,
                "default_lookback": 252,
            },
        )
    finally:
        _log_page_route("/web/risk", started_at)


@router.get("/backtesting", include_in_schema=False)
def backtesting_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        templates_list = list_strategy_templates(db)
        if not templates_list:
            return templates.TemplateResponse(
                "pages/backtesting.html",
                {
                    "request": request,
                    "active_page": "backtesting",
                    "title": "Backtesting",
                    "subtitle": "Test strategies on historical candles using realistic execution assumptions.",
                    "strategy_templates": [],
                    "no_strategies": True,
                    "index_filter_options": get_index_filter_options(db),
                    "execution_mode_options": EXECUTION_MODE_OPTIONS,
                    "intrabar_options": INTRABAR_OPTIONS,
                    "cost_model_options": COST_MODEL_OPTIONS,
                    "benchmark_options": BENCHMARK_OPTIONS,
                    "default_start_date": (date.today() - timedelta(days=365)).isoformat(),
                    "default_end_date": date.today().isoformat(),
                },
            )
        return templates.TemplateResponse(
            "pages/backtesting.html",
            {
                "request": request,
                "active_page": "backtesting",
                "title": "Backtesting",
                "subtitle": "Test strategies on historical candles using realistic execution assumptions.",
                "strategy_templates": templates_list,
                "no_strategies": False,
                "index_filter_options": get_index_filter_options(db),
                "execution_mode_options": EXECUTION_MODE_OPTIONS,
                "intrabar_options": INTRABAR_OPTIONS,
                "cost_model_options": COST_MODEL_OPTIONS,
                "benchmark_options": BENCHMARK_OPTIONS,
                "default_start_date": (date.today() - timedelta(days=365)).isoformat(),
                "default_end_date": date.today().isoformat(),
            },
        )
    finally:
        _log_page_route("/web/backtesting", started_at)


@router.get("/strategy-lab", include_in_schema=False)
def strategy_lab_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        ensure_default_portfolios(db, current_user)
        portfolios = list_user_portfolios(db, current_user.id)
        templates_list = list_strategy_templates(db)
        if not templates_list:
            return templates.TemplateResponse(
                "pages/strategy_lab.html",
                {
                    "request": request,
                    "active_page": "strategy_lab",
                    "title": "Strategy Lab",
                    "subtitle": "Create, tune, and preview strategy signals using stored market data.",
                    "strategy_templates": [],
                    "no_strategies": True,
                    "portfolios": portfolios,
                    "selected_portfolio_id": portfolios[0].id if portfolios else None,
                },
            )
        return templates.TemplateResponse(
            "pages/strategy_lab.html",
            {
                "request": request,
                "active_page": "strategy_lab",
                "title": "Strategy Lab",
                "subtitle": "Create, tune, and preview strategy signals using stored market data.",
                "strategy_templates": templates_list,
                "no_strategies": False,
                "portfolios": portfolios,
                "selected_portfolio_id": portfolios[0].id if portfolios else None,
            },
        )
    finally:
        _log_page_route("/web/strategy-lab", started_at)


@router.get("/data", include_in_schema=False)
def data_operations_page(request: Request):
    started_at = time.perf_counter()
    try:
        return templates.TemplateResponse(
            "pages/data.html",
            {
                "request": request,
                "active_page": "data",
                "title": "Data Operations",
                "subtitle": "Monitor market sync, ingestion health, data freshness, and backend performance.",
            },
        )
    finally:
        _log_page_route("/web/data", started_at)


@router.get("/ai-think-tank", include_in_schema=False)
def ai_think_tank_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        from app.config import settings
        from app.routers.ai import list_backtest_runs

        ensure_default_portfolios(db, current_user)
        portfolios = list_user_portfolios(db, current_user.id)
        backtest_runs = list_backtest_runs(db=db, current_user=current_user, limit=20)
        return templates.TemplateResponse(
            "pages/ai_think_tank.html",
            {
                "request": request,
                "active_page": "ai_think_tank",
                "title": "AI Think Tank",
                "subtitle": "Educational AI analysis for paper trading, portfolio review, and strategy interpretation.",
                "page_disclaimer": (
                    "Educational use only. Not financial advice, not a trade recommendation, "
                    "and not a future price prediction."
                ),
                "portfolios": portfolios,
                "selected_portfolio_id": portfolios[0].id if portfolios else None,
                "backtest_runs": backtest_runs,
                "default_model": settings.ollama_default_model,
                "analysis_modes": [
                    ("signal_synthesizer", "Signal Synthesizer"),
                    ("backtest_interpreter", "Backtest Interpreter"),
                    ("pre_trade_advisor", "Pre-Trade Advisor"),
                    ("nl_screener", "NL Screener"),
                    ("portfolio_health", "Portfolio Health"),
                    ("journal_insights", "Journal Insights"),
                    ("activity_log", "Activity Log"),
                ],
            },
        )
    finally:
        _log_page_route("/web/ai-think-tank", started_at)


@router.get("/index-fund", include_in_schema=False)
def index_fund_page(request: Request):
    started_at = time.perf_counter()
    try:
        return templates.TemplateResponse(
            "pages/index_fund.html",
            {
                "request": request,
                "active_page": "index_fund",
                "title": "Index Fund",
                "subtitle": "Analyze index, ETF, and benchmark instruments using stored price history.",
            },
        )
    finally:
        _log_page_route("/web/index-fund", started_at)
