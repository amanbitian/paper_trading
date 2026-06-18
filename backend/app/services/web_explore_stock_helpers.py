from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import date, datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.stock import Stock, StockPrice
from app.services.algo_finding_service import (
    MIN_SIGNAL_ROWS,
    generate_stock_algo_findings,
)
from app.services.fundamentals_service import get_stock_fundamentals, serialize_stock_fundamentals
from app.services.market_data_service import DAILY_TIMEFRAME, get_latest_price
from app.services.news_service import list_stock_news
from app.services.stock_performance_service import list_stock_performance
from app.services.strategy_explainer_service import list_stock_strategy_explanations
from app.services.ticker_service import normalize_bse_symbol, normalize_nse_symbol, search_stocks
from app.services.web_backtesting_helpers import list_strategy_templates

logger = logging.getLogger(__name__)

PRICE_HISTORY_LIMIT = 10000
ACTION_TONES = {
    "BUY": "success",
    "SELL": "danger",
    "HOLD": "neutral",
    "NO_SIGNAL": "neutral",
}


def stock_route_key(stock: Stock | dict[str, Any]) -> str:
    if isinstance(stock, Stock):
        yahoo = (stock.yahoo_symbol or "").strip().upper()
        if yahoo:
            return yahoo
        symbol = (stock.symbol or "").strip().upper()
        exchange = (stock.exchange or "").strip().upper()
    else:
        yahoo = str(stock.get("yahoo_symbol") or "").strip().upper()
        if yahoo:
            return yahoo
        symbol = str(stock.get("symbol") or "").strip().upper()
        exchange = str(stock.get("exchange") or "").strip().upper()
    if not symbol:
        return ""
    if exchange == "BSE":
        return normalize_bse_symbol(symbol)
    return normalize_nse_symbol(symbol)


def stock_detail_url(stock: Stock | dict[str, Any]) -> str:
    key = stock_route_key(stock)
    if not key:
        return "/web/explore"
    return f"/web/explore?stock={key}"


def add_portfolio_url(stock: Stock | dict[str, Any]) -> str:
    key = stock_route_key(stock)
    if not key:
        return "/web/add-portfolio"
    return f"/web/add-portfolio?stock={key}"


def resolve_stock_for_prefill(
    db: Session,
    *,
    stock: str | None = None,
    stock_id: int | None = None,
    symbol: str | None = None,
    exchange: str | None = None,
) -> tuple[Stock | None, str | None]:
    if stock_id is not None:
        row = db.get(Stock, int(stock_id))
        if row and row.is_active:
            return row, None
        return None, f"No active stock found for id {stock_id}."

    if stock and str(stock).strip():
        return resolve_stock_by_route_key(db, str(stock).strip())

    clean_symbol = (symbol or "").strip().upper()
    clean_exchange = (exchange or "").strip().upper()
    if clean_symbol and clean_exchange:
        row = db.scalar(
            select(Stock)
            .where(
                Stock.symbol == clean_symbol,
                Stock.exchange == clean_exchange,
                Stock.is_active.is_(True),
            )
            .limit(1)
        )
        if row:
            return row, None
        return None, f"No stored stock matched {clean_symbol} on {clean_exchange}."

    return None, None


def build_preselected_stock_view(db: Session, stock: Stock) -> dict[str, Any]:
    performance = _performance_row_for_stock(db, stock)
    latest_close: float | None = None
    latest_date: str | None = None

    if performance:
        if performance.get("latest_price") is not None:
            latest_close = float(performance["latest_price"])
        dt = performance.get("latest_price_datetime")
        if dt is not None:
            if isinstance(dt, datetime):
                latest_date = dt.date().isoformat()
            elif isinstance(dt, date):
                latest_date = dt.isoformat()
            else:
                latest_date = str(dt)[:10]

    if latest_close is None:
        stored = get_latest_price(db, stock.id)
        if stored is not None:
            latest_close = float(stored)

    if latest_close is None:
        prices = _load_daily_prices(db, stock.id, limit=5)
        if prices and prices[-1].get("close") is not None:
            latest_close = float(prices[-1]["close"])
            if not latest_date and prices[-1].get("date"):
                latest_date = str(prices[-1]["date"])

    purchase_date_default = latest_date or date.today().isoformat()
    company_name = stock.company_name or stock.symbol
    route_key = stock_route_key(stock)

    return {
        "stock_id": stock.id,
        "company_name": company_name,
        "symbol": stock.symbol,
        "exchange": stock.exchange,
        "yahoo_ticker": stock.yahoo_symbol,
        "yahoo_symbol": stock.yahoo_symbol,
        "sector": stock.sector,
        "industry": stock.industry,
        "latest_close": latest_close,
        "latest_date": latest_date,
        "has_prices": latest_close is not None,
        "route_key": route_key,
        "search_label": f"{company_name} · {stock.symbol} · {stock.exchange}",
        "buy_price_default": latest_close,
        "purchase_date_default": purchase_date_default,
        "status_label": "Has prices" if latest_close is not None else "Missing prices",
        "status_tone": "success" if latest_close is not None else "warning",
    }


def resolve_stock_by_route_key(db: Session, route_key: str) -> tuple[Stock | None, str | None]:
    clean = (route_key or "").strip()
    if not clean:
        return None, "Empty stock key."

    normalized = clean.upper()
    stock = db.scalar(
        select(Stock).where(
            Stock.yahoo_symbol == normalized,
            Stock.is_active.is_(True),
        )
    )
    if stock:
        logger.info(
            "explore.stock_detail resolved id=%s symbol=%s exchange=%s yahoo=%s key=%s",
            stock.id,
            stock.symbol,
            stock.exchange,
            stock.yahoo_symbol,
            normalized,
        )
        return stock, None

    symbol_only = normalized.replace(".NS", "").replace(".BO", "")
    for exchange in ("NSE", "BSE"):
        stock = db.scalar(
            select(Stock)
            .where(
                Stock.symbol == symbol_only,
                Stock.exchange == exchange,
                Stock.is_active.is_(True),
            )
            .limit(1)
        )
        if stock:
            logger.info(
                "explore.stock_detail resolved_by_symbol id=%s symbol=%s exchange=%s yahoo=%s key=%s",
                stock.id,
                stock.symbol,
                stock.exchange,
                stock.yahoo_symbol,
                normalized,
            )
            return stock, None

    if "." not in normalized and symbol_only:
        for yahoo_guess, exchange in (
            (normalize_nse_symbol(symbol_only), "NSE"),
            (normalize_bse_symbol(symbol_only), "BSE"),
        ):
            stock = db.scalar(
                select(Stock).where(
                    Stock.yahoo_symbol == yahoo_guess,
                    Stock.is_active.is_(True),
                )
            )
            if stock:
                logger.info(
                    "explore.stock_detail resolved_by_suffix id=%s yahoo=%s key=%s",
                    stock.id,
                    stock.yahoo_symbol,
                    normalized,
                )
                return stock, None

    matches = search_stocks(db, normalized, limit=5)
    exact = next((row for row in matches if row.yahoo_symbol.upper() == normalized), None)
    if exact:
        logger.info(
            "explore.stock_detail resolved_by_search id=%s yahoo=%s key=%s",
            exact.id,
            exact.yahoo_symbol,
            normalized,
        )
        return exact, None
    if matches:
        chosen = matches[0]
        logger.info(
            "explore.stock_detail resolved_search_fallback id=%s yahoo=%s key=%s",
            chosen.id,
            chosen.yahoo_symbol,
            normalized,
        )
        return chosen, None

    logger.warning(
        "explore.stock_detail not_found key=%s symbol=%s",
        normalized,
        symbol_only,
    )
    return None, f"No stored stock matched '{clean}'."


def _performance_row_for_stock(db: Session, stock: Stock) -> dict[str, Any] | None:
    rows = list_stock_performance(db, query=stock.yahoo_symbol, limit=20)
    for row in rows:
        if row.get("id") == stock.id or row.get("yahoo_symbol") == stock.yahoo_symbol:
            return row
    rows = list_stock_performance(db, query=stock.symbol, limit=20)
    for row in rows:
        if row.get("id") == stock.id:
            return row
    return None


def _load_daily_prices(
    db: Session,
    stock_id: int,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = PRICE_HISTORY_LIMIT,
) -> list[dict[str, Any]]:
    stmt = (
        select(StockPrice)
        .where(StockPrice.stock_id == stock_id, StockPrice.timeframe == DAILY_TIMEFRAME)
        .order_by(StockPrice.price_datetime.asc())
        .limit(limit)
    )
    if start_date:
        stmt = stmt.where(StockPrice.price_datetime >= datetime.combine(start_date, datetime.min.time()))
    if end_date:
        stmt = stmt.where(StockPrice.price_datetime <= datetime.combine(end_date, datetime.max.time()))
    rows = db.scalars(stmt).all()
    return [
        {
            "date": row.price_datetime.date().isoformat() if row.price_datetime else None,
            "open": float(row.open) if row.open is not None else None,
            "high": float(row.high) if row.high is not None else None,
            "low": float(row.low) if row.low is not None else None,
            "close": float(row.close) if row.close is not None else None,
            "volume": float(row.volume) if row.volume is not None else None,
            "source": row.source,
        }
        for row in rows
    ]


def _compute_change_1d(prices: list[dict[str, Any]]) -> float | None:
    closes = [row["close"] for row in prices if row.get("close") is not None]
    if len(closes) < 2:
        return None
    previous, latest = closes[-2], closes[-1]
    if not previous:
        return None
    return round(((latest - previous) / previous) * 100, 2)


_STOCK_CHART_BG = "#050812"
_STOCK_CHART_PLOT_BG = "#050812"
_STOCK_CHART_FONT = "#e5e7eb"
_STOCK_CHART_GRID = "rgba(148, 163, 184, 0.16)"
_STOCK_CHART_ZERO = "rgba(148, 163, 184, 0.20)"


def _stock_chart_dark_layout(*, title: str, y_title: str, show_rangeslider: bool = False) -> dict[str, Any]:
    xaxis: dict[str, Any] = {
        "title": "Date",
        "gridcolor": _STOCK_CHART_GRID,
        "zerolinecolor": _STOCK_CHART_ZERO,
        "linecolor": _STOCK_CHART_GRID,
        "tickfont": {"color": _STOCK_CHART_FONT},
        "titlefont": {"color": _STOCK_CHART_FONT},
    }
    if not show_rangeslider:
        xaxis["rangeslider"] = {"visible": False}
    return {
        "template": "plotly_dark",
        "title": {"text": title, "font": {"color": _STOCK_CHART_FONT, "size": 14}},
        "autosize": True,
        "height": 460,
        "paper_bgcolor": _STOCK_CHART_BG,
        "plot_bgcolor": _STOCK_CHART_PLOT_BG,
        "font": {"color": _STOCK_CHART_FONT, "size": 12},
        "margin": {"l": 52, "r": 24, "t": 48, "b": 44},
        "hoverlabel": {
            "bgcolor": "#111827",
            "bordercolor": "rgba(148, 163, 184, 0.35)",
            "font": {"color": _STOCK_CHART_FONT},
        },
        "xaxis": xaxis,
        "yaxis": {
            "title": y_title,
            "gridcolor": _STOCK_CHART_GRID,
            "zerolinecolor": _STOCK_CHART_ZERO,
            "linecolor": _STOCK_CHART_GRID,
            "tickfont": {"color": _STOCK_CHART_FONT},
            "titlefont": {"color": _STOCK_CHART_FONT},
        },
        "legend": {"orientation": "h", "font": {"color": _STOCK_CHART_FONT}},
    }


def build_stock_ohlc_plotly(prices: list[dict[str, Any]], *, chart_type: str = "candlestick") -> dict[str, Any] | None:
    if not prices:
        return None
    dates = [row["date"] for row in prices if row.get("date")]
    if chart_type == "line":
        traces = [
            {
                "type": "scatter",
                "mode": "lines",
                "name": "Close",
                "x": dates,
                "y": [row.get("close") for row in prices],
                "line": {"color": "#38bdf8", "width": 2},
            }
        ]
        layout = _stock_chart_dark_layout(title="Daily close", y_title="Close")
        return {"data": traces, "layout": layout}

    traces = [
        {
            "type": "candlestick",
            "name": "OHLC",
            "x": dates,
            "open": [row.get("open") for row in prices],
            "high": [row.get("high") for row in prices],
            "low": [row.get("low") for row in prices],
            "close": [row.get("close") for row in prices],
            "increasing": {"line": {"color": "#22c55e"}, "fillcolor": "rgba(34, 197, 94, 0.35)"},
            "decreasing": {"line": {"color": "#ef4444"}, "fillcolor": "rgba(239, 68, 68, 0.35)"},
        }
    ]
    layout = _stock_chart_dark_layout(title="Daily OHLC", y_title="Price", show_rangeslider=False)
    return {"data": traces, "layout": layout}


def finding_chart_to_plotly(chart: dict[str, Any] | None) -> dict[str, Any] | None:
    if not chart or not chart.get("series"):
        return None
    traces: list[dict[str, Any]] = []
    for series in chart.get("series") or []:
        traces.append(
            {
                "type": "scatter",
                "mode": "lines",
                "name": series.get("name"),
                "x": chart.get("x") or [],
                "y": series.get("values") or [],
            }
        )
    layout = _stock_chart_dark_layout(
        title=str(chart.get("title") or "Algorithm chart"),
        y_title="Value",
        show_rangeslider=False,
    )
    layout["height"] = 360
    layout["margin"] = {"l": 48, "r": 16, "t": 42, "b": 36}
    layout["legend"] = {"orientation": "h", "y": -0.25}
    return {"data": traces, "layout": layout}


def serialize_algo_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for finding in findings:
        action = str(finding.get("action") or "HOLD").upper()
        chart_payload = finding_chart_to_plotly(finding.get("chart"))
        rows.append(
            {
                "algorithm_name": finding.get("algorithm_name"),
                "category": finding.get("category"),
                "action": action,
                "action_tone": ACTION_TONES.get(action, "neutral"),
                "confidence_score": finding.get("confidence_score"),
                "status": finding.get("status"),
                "reason": finding.get("reason"),
                "logic": finding.get("logic"),
                "data_requirements": finding.get("data_requirements"),
                "indicators": finding.get("indicators"),
                "has_chart": bool(chart_payload),
                "chart_json": chart_payload,
                "chart_json_str": json.dumps(chart_payload) if chart_payload else "",
            }
        )
    return rows


def build_stock_detail_context(
    db: Session,
    stock: Stock,
    *,
    chart_type: str = "candlestick",
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    performance = _performance_row_for_stock(db, stock)
    prices = _load_daily_prices(db, stock.id, start_date=start_date, end_date=end_date)
    route_key = stock_route_key(stock)
    findings = generate_stock_algo_findings(db, stock.id, limit=PRICE_HISTORY_LIMIT)
    status_counts = Counter(str(item.get("status") or "unknown") for item in findings)
    logger.info(
        "stock_detail.algorithm_findings stock=%s price_rows=%s findings_limit=%s "
        "findings_count=%s statuses=%s",
        route_key,
        len(prices),
        PRICE_HISTORY_LIMIT,
        len(findings),
        dict(status_counts),
    )
    if len(findings) == 1:
        only = findings[0] if findings else {}
        if (only.get("category") or "") == "Data Quality":
            logger.warning(
                "stock_detail.algorithm_findings single_data_quality_only stock=%s "
                "price_rows=%s min_signal_rows=%s reason=%s",
                route_key,
                len(prices),
                MIN_SIGNAL_ROWS,
                only.get("reason"),
            )
    chart_payload = build_stock_ohlc_plotly(prices, chart_type=chart_type)
    latest_close = prices[-1]["close"] if prices else (performance or {}).get("latest_price")
    change_1d = _compute_change_1d(prices)
    fundamentals = serialize_stock_fundamentals(get_stock_fundamentals(db, stock.id))
    strategy_explanations = list_stock_strategy_explanations(db, stock.id)
    news = list_stock_news(db, stock.id, limit=8)

    templates_list = list_strategy_templates(db)
    strategy_options = [
        {
            "id": template.id,
            "label": f"{template.strategy_name} ({template.strategy_type})",
            "description": template.description,
        }
        for template in templates_list
    ]

    return {
        "stock": {
            "id": stock.id,
            "symbol": stock.symbol,
            "exchange": stock.exchange,
            "company_name": stock.company_name or stock.symbol,
            "yahoo_symbol": stock.yahoo_symbol,
            "sector": stock.sector,
            "industry": stock.industry,
            "route_key": stock_route_key(stock),
        },
        "performance": performance or {},
        "has_prices": bool(prices),
        "price_rows": prices[-5:],
        "price_row_count": len(prices),
        "from_date": prices[0]["date"] if prices else None,
        "to_date": prices[-1]["date"] if prices else None,
        "latest_close": latest_close,
        "change_1d_pct": change_1d,
        "latest_volume": (performance or {}).get("latest_volume"),
        "chart_type": chart_type,
        "chart_json": chart_payload,
        "chart_json_str": json.dumps(chart_payload) if chart_payload else "",
        "findings": serialize_algo_findings(findings),
        "fundamentals": fundamentals,
        "strategy_explanations": strategy_explanations,
        "news": news,
        "strategy_options": strategy_options,
        "action_links": {
            "add_portfolio": add_portfolio_url(stock),
            "paper_trading": "/web/paper-trading",
            "backtesting": "/web/backtesting",
            "strategy_lab": f"/web/strategy-lab",
        },
    }
