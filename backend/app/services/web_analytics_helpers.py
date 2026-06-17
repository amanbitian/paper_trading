from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.index_fund import IndexFund, IndexFundPrice
from app.models.portfolio import PortfolioDailySnapshot, PortfolioHolding
from app.services.market_data_service import DAILY_TIMEFRAME, prices_to_dataframe
from app.services.market_trends_service import get_market_trend_filters, get_market_trends
from app.services.portfolio_service import D, calculate_portfolio_value
from app.services.risk_service import get_portfolio_risk_metrics
from app.web_utils import _as_float, format_inr, format_pct

logger = logging.getLogger(__name__)

TREND_PERIOD_OPTIONS: list[tuple[str, str]] = [
    ("Daily", "daily"),
    ("Weekly", "weekly"),
    ("Monthly", "monthly"),
    ("Quarterly", "quarterly"),
    ("6 Month", "six_month"),
    ("Annual", "annual"),
]

DEFAULT_TREND_PERIOD = "daily"
DEFAULT_TREND_LIMIT = 100

_FILTER_CACHE: dict[str, Any] = {"payload": None, "expires_at": 0.0}
_FILTER_CACHE_TTL_SECONDS = 300.0  # 5 minutes
_FILTER_FETCH_LOCK = threading.Lock()
_TREND_DATA_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_TREND_DATA_CACHE_TTL_SECONDS = 300.0  # 5 minutes
_TREND_FETCH_LOCK = threading.Lock()


def get_cached_trend_filters(db: Session) -> dict[str, Any]:
    now = time.monotonic()
    cached = _FILTER_CACHE.get("payload")
    if cached is not None and now < float(_FILTER_CACHE.get("expires_at") or 0):
        return cached  # Fast path — no lock needed
    with _FILTER_FETCH_LOCK:
        # Re-check after acquiring the lock; another thread may have already fetched.
        now = time.monotonic()
        cached = _FILTER_CACHE.get("payload")
        if cached is not None and now < float(_FILTER_CACHE.get("expires_at") or 0):
            return cached
        payload = get_market_trend_filters(db)
        _FILTER_CACHE["payload"] = payload
        _FILTER_CACHE["expires_at"] = now + _FILTER_CACHE_TTL_SECONDS
        return payload


def auto_rows_for_selection(
    filter_payload: dict[str, Any],
    *,
    nifty_index: str | None,
    nifty_index_options: list[dict[str, Any]],
) -> int:
    max_cap = max_stocks_for_filters(
        filter_payload,
        nifty_index=None if nifty_index in (None, "", "All indices") else nifty_index,
        nifty_index_options=nifty_index_options,
    )
    if nifty_index and nifty_index not in ("", "All indices"):
        selected = next(
            (option for option in nifty_index_options if option.get("value") == nifty_index),
            None,
        )
        count = int((selected or {}).get("constituent_count") or 0)
        if count > 0:
            return max(50, min(count, max_cap))
    return max(50, min(DEFAULT_TREND_LIMIT, max_cap))


def resolve_trends_filter_state(
    *,
    period: str | None = None,
    index_universe: str | None = None,
    market: str | None = None,
    industry: str | None = None,
    industry_group: str | None = None,
    nifty_index: str | None = None,
    rows: int | None = None,
    limit: int | None = None,
    rows_mode: str | None = None,
    sort_by: str | None = None,
    filter_payload: dict[str, Any] | None = None,
    db: Session | None = None,
) -> dict[str, Any]:
    if filter_payload is None:
        if db is None:
            raise ValueError("filter_payload or db is required")
        filter_payload = get_cached_trend_filters(db)

    nifty_options = filter_payload.get("nifty_indices") or []
    effective_period = (period or DEFAULT_TREND_PERIOD).strip().lower()
    effective_index_universe = (index_universe or market or "stocks").strip().lower()
    effective_industry = (industry or industry_group or "All industries").strip()
    effective_nifty_index = (nifty_index or "All indices").strip()
    effective_sort_by = (sort_by or "size").strip().lower()
    normalized_rows_mode = (rows_mode or "auto").strip().lower()
    if normalized_rows_mode not in ("auto", "manual"):
        normalized_rows_mode = "auto"

    parsed_nifty = (
        None
        if effective_nifty_index in ("", "All indices")
        else effective_nifty_index
    )
    max_stocks = max_stocks_for_filters(
        filter_payload,
        nifty_index=parsed_nifty,
        nifty_index_options=nifty_options,
    )
    requested_rows = rows if rows is not None else limit
    if normalized_rows_mode == "auto":
        effective_rows = auto_rows_for_selection(
            filter_payload,
            nifty_index=parsed_nifty,
            nifty_index_options=nifty_options,
        )
    else:
        effective_rows = max(
            50,
            min(int(requested_rows or DEFAULT_TREND_LIMIT), max_stocks),
        )

    return {
        "period": effective_period,
        "index_universe": effective_index_universe,
        "industry": effective_industry,
        "nifty_index": effective_nifty_index,
        "nifty_index_value": parsed_nifty,
        "rows": effective_rows,
        "rows_mode": normalized_rows_mode,
        "sort_by": effective_sort_by,
        "max_stocks": max_stocks,
        "requested_rows": int(requested_rows) if requested_rows is not None else None,
        "query": parse_trend_query(
            period=effective_period,
            market_filter=effective_index_universe,
            industry_group=effective_industry,
            nifty_index=effective_nifty_index,
            limit=effective_rows,
            sort_by=effective_sort_by,
        ),
    }


def parse_trend_query(
    *,
    period: str | None = None,
    market: str | None = None,
    market_filter: str | None = None,
    industry_group: str | None = None,
    industry: str | None = None,
    nifty_index: str | None = None,
    limit: int | None = None,
    rows: int | None = None,
    sort_by: str | None = None,
) -> dict[str, Any]:
    normalized_period = (period or DEFAULT_TREND_PERIOD).strip().lower()
    normalized_market = (market_filter or market or "stocks").strip().lower()
    row_limit = rows if rows is not None else limit
    normalized_limit = int(row_limit) if row_limit is not None else DEFAULT_TREND_LIMIT
    normalized_limit = max(50, min(normalized_limit, 5000))
    selected_industry = (industry_group or industry or "All industries").strip()
    params: dict[str, Any] = {
        "period": normalized_period,
        "market_filter": normalized_market,
        "limit": normalized_limit,
        "sort_by": (sort_by or "size").strip().lower(),
        "industry_group": None,
        "nifty_index": None,
    }
    if selected_industry and selected_industry != "All industries":
        params["industry_group"] = selected_industry
    if nifty_index and nifty_index.strip() and nifty_index.strip() not in ("", "All indices"):
        params["nifty_index"] = nifty_index.strip()
    return params


def _trend_cache_key(query: dict[str, Any]) -> str:
    return "|".join(
        [
            str(query.get("period")),
            str(query.get("market_filter")),
            str(query.get("industry_group")),
            str(query.get("nifty_index")),
            str(query.get("limit")),
            str(query.get("sort_by")),
        ]
    )


def fetch_market_trends(db: Session, query: dict[str, Any]) -> dict[str, Any]:
    cache_key = _trend_cache_key(query)
    now = time.monotonic()
    cached = _TREND_DATA_CACHE.get(cache_key)
    if cached is not None and now < cached[0]:
        return cached[1]  # Fast path — no lock needed
    with _TREND_FETCH_LOCK:
        # Re-check after acquiring the lock; another thread may have already fetched.
        # This prevents thundering-herd: when treemap, table, and summary all fire
        # simultaneously on cold cache, only one runs the heavy SQL.
        now = time.monotonic()
        cached = _TREND_DATA_CACHE.get(cache_key)
        if cached is not None and now < cached[0]:
            return cached[1]
        payload = get_market_trends(
            db,
            period=query["period"],
            limit=query["limit"],
            market_filter=query["market_filter"],
            nifty_index=query["nifty_index"],
            industry_group=query["industry_group"],
            sort_by=query["sort_by"],
        )
        now = time.monotonic()
        _TREND_DATA_CACHE[cache_key] = (now + _TREND_DATA_CACHE_TTL_SECONDS, payload)
        return payload


def build_trend_summary_context(payload: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    changes = [_as_float(row.get("change_pct")) for row in items]
    changes = [value for value in changes if value is not None]
    median_change = None
    average_change = None
    best = None
    worst = None
    if changes:
        sorted_changes = sorted(changes)
        mid = len(sorted_changes) // 2
        median_change = (
            sorted_changes[mid]
            if len(sorted_changes) % 2
            else (sorted_changes[mid - 1] + sorted_changes[mid]) / 2
        )
        average_change = sum(changes) / len(changes)
        best_row = max(items, key=lambda row: _as_float(row.get("change_pct")) or float("-inf"))
        worst_row = min(items, key=lambda row: _as_float(row.get("change_pct")) or float("inf"))
        best = {
            "symbol": best_row.get("symbol"),
            "change_pct": best_row.get("change_pct"),
        }
        worst = {
            "symbol": worst_row.get("symbol"),
            "change_pct": worst_row.get("change_pct"),
        }
    return {
        "period_label": payload.get("period_label") or payload.get("period"),
        "lookback_days": payload.get("lookback_days"),
        "market_label": payload.get("market_label"),
        "baseline_date": payload.get("baseline_date"),
        "record_date": payload.get("record_date"),
        "row_count": payload.get("row_count", len(items)),
        "universe_eligible_count": payload.get("universe_eligible_count"),
        "median_change": median_change,
        "average_change": average_change,
        "best": best,
        "worst": worst,
        "calculation_basis": payload.get("calculation_basis"),
        "limit_requested": payload.get("limit_requested"),
        "has_items": bool(items),
    }


def _treemap_color_cap(items: list[dict[str, Any]]) -> float:
    values = [abs(_as_float(row.get("change_pct")) or 0) for row in items]
    if not values:
        return 2.0
    values.sort()
    index = max(0, int(len(values) * 0.9) - 1)
    cap = values[index] if values else 2.0
    return max(2.0, min(cap, 50.0))


def validate_treemap_figure(figure: dict[str, Any] | None) -> dict[str, Any]:
    if not figure:
        return {"valid": False, "trace_count": 0, "node_count": 0, "reason": "no_figure"}
    traces = figure.get("data") or []
    if not traces:
        return {"valid": False, "trace_count": 0, "node_count": 0, "reason": "no_traces"}
    trace = traces[0]
    labels = trace.get("labels") or []
    values = trace.get("values") or []
    if not labels or not values:
        return {"valid": False, "trace_count": len(traces), "node_count": 0, "reason": "empty_labels_or_values"}
    if len(labels) != len(values):
        return {
            "valid": False,
            "trace_count": len(traces),
            "node_count": len(labels),
            "reason": "labels_values_length_mismatch",
        }
    return {"valid": True, "trace_count": len(traces), "node_count": len(labels), "reason": "ok"}


def build_treemap_plotly_json(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not items:
        return None

    color_cap = _treemap_color_cap(items)
    nodes: dict[str, dict[str, Any]] = {}

    def ensure_node(node_id: str, label: str, parent_id: str) -> None:
        if node_id in nodes:
            return
        nodes[node_id] = {
            "id": node_id,
            "label": label,
            "parent_id": parent_id,
            "value": 0.0,
            "color": None,
            "customdata": None,
        }

    root_id = "root"
    ensure_node(root_id, "Market", "")

    for row in items:
        market_bucket = (row.get("market_bucket") or "Stocks").strip() or "Stocks"
        industry_group = (row.get("industry_group") or row.get("sector") or "Unknown").strip() or "Unknown"
        display_name = (row.get("company_name") or row.get("symbol") or "Unknown").strip()
        symbol = row.get("symbol") or ""
        exchange = row.get("exchange") or ""
        size_value = max(_as_float(row.get("size_value")) or 1.0, 1.0)
        change_pct = _as_float(row.get("change_pct"))

        market_id = f"market::{market_bucket}"
        industry_id = f"industry::{market_bucket}::{industry_group}"
        leaf_id = f"leaf::{symbol}::{display_name}"

        ensure_node(market_id, market_bucket, root_id)
        ensure_node(industry_id, industry_group, market_id)
        latest_dt = row.get("latest_price_datetime")
        baseline_dt = row.get("baseline_price_datetime")
        latest_date = latest_dt.date().isoformat() if isinstance(latest_dt, datetime) else str(latest_dt or "-")
        baseline_date = (
            baseline_dt.date().isoformat() if isinstance(baseline_dt, datetime) else str(baseline_dt or "-")
        )
        nodes[leaf_id] = {
            "id": leaf_id,
            "label": display_name,
            "parent_id": industry_id,
            "value": size_value,
            "color": change_pct,
            "customdata": [
                symbol,
                exchange,
                format_inr(row.get("latest_price")),
                format_inr(row.get("baseline_price")),
                format_inr(row.get("latest_return_price")),
                format_inr(row.get("baseline_return_price")),
                format_pct(change_pct, signed=True),
                baseline_date,
                latest_date,
            ],
        }
        nodes[market_id]["value"] += size_value
        nodes[industry_id]["value"] += size_value
        nodes[root_id]["value"] += size_value

    labels: list[str] = []
    parents: list[str] = []
    ids: list[str] = []
    values: list[float] = []
    colors: list[float | None] = []
    customdata: list[list[Any]] = []

    for node_id, node in nodes.items():
        labels.append(node["label"])
        ids.append(node["id"])
        parents.append("" if not node["parent_id"] else node["parent_id"])
        values.append(float(node["value"] or 0))
        colors.append(node["color"] if node["color"] is not None else 0.0)
        customdata.append(node["customdata"] or [None] * 9)

    leaf_count = sum(1 for node in nodes.values() if node["customdata"] is not None)
    if leaf_count == 0:
        return None

    return {
        "data": [
            {
                "type": "treemap",
                "labels": labels,
                "ids": ids,
                "parents": parents,
                "values": values,
                "marker": {
                    "colors": colors,
                    "colorscale": [
                        [0.0, "#6f1620"],
                        [0.22, "#b73a43"],
                        [0.44, "#e26767"],
                        [0.5, "#2b3038"],
                        [0.56, "#4f9f68"],
                        [0.78, "#2e8b57"],
                        [1.0, "#1f6f43"],
                    ],
                    "cmin": -color_cap,
                    "cmax": color_cap,
                    "cmid": 0,
                    "colorbar": {"title": "Change %", "ticksuffix": "%"},
                },
                "customdata": customdata,
                "texttemplate": "<b>%{label}</b><br>%{customdata[6]}",
                "hovertemplate": (
                    "<b>%{label}</b><br>"
                    "Symbol: %{customdata[0]}<br>"
                    "Exchange: %{customdata[1]}<br>"
                    "From: %{customdata[7]} at %{customdata[3]}<br>"
                    "To: %{customdata[8]} at %{customdata[2]}<br>"
                    "Adjusted from: %{customdata[5]}<br>"
                    "Adjusted to: %{customdata[4]}<br>"
                    "Change: %{customdata[6]}"
                    "<extra></extra>"
                ),
                "branchvalues": "total",
            }
        ],
        "layout": {
            "autosize": True,
            "height": 520,
            "margin": {"l": 0, "r": 0, "t": 10, "b": 0},
            "paper_bgcolor": "#050607",
            "plot_bgcolor": "#050607",
            "font": {"color": "#f4f7fb", "size": 12},
        },
    }


def build_trend_table_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        latest_dt = item.get("latest_price_datetime")
        latest_date = latest_dt.date() if isinstance(latest_dt, datetime) else latest_dt
        latest_price = _as_float(item.get("latest_price"))
        latest_volume = item.get("latest_volume")
        traded_value = None
        if latest_price is not None and latest_volume is not None:
            try:
                traded_value = latest_price * float(latest_volume)
            except (TypeError, ValueError):
                traded_value = None
        rows.append(
            {
                "company_name": item.get("company_name") or item.get("symbol"),
                "symbol": item.get("symbol"),
                "exchange": item.get("exchange"),
                "sector": item.get("sector"),
                "industry": item.get("industry"),
                "industry_group": item.get("industry_group"),
                "latest_date": latest_date,
                "latest_price": latest_price,
                "change_pct": item.get("change_pct"),
                "latest_volume": latest_volume,
                "traded_value": traded_value,
                "market_bucket": item.get("market_bucket"),
                "instrument_type": item.get("instrument_type"),
            }
        )
    return rows


def max_stocks_for_filters(
    filter_payload: dict[str, Any],
    *,
    nifty_index: str | None,
    nifty_index_options: list[dict[str, Any]],
) -> int:
    if nifty_index:
        selected = next(
            (option for option in nifty_index_options if option.get("value") == nifty_index),
            None,
        )
        cap = int((selected or {}).get("constituent_count") or 50)
    else:
        cap = int(filter_payload.get("all_stocks_eligible_max") or 5000)
    return max(50, min(cap, 5000))


def fetch_portfolio_risk(db: Session, portfolio_id: int, lookback_days: int) -> dict[str, Any]:
    bounded_lookback = max(90, min(int(lookback_days), 400))
    return get_portfolio_risk_metrics(db, portfolio_id, lookback_days=bounded_lookback)


def build_risk_metrics_cards(
    metrics: dict[str, Any],
    portfolio_values: dict[str, Any] | None,
) -> dict[str, Any]:
    beta = metrics.get("beta") or {}
    var = metrics.get("var") or {}
    conc = metrics.get("concentration") or {}
    dd = metrics.get("drawdown") or {}
    values = portfolio_values or {}
    return {
        "refreshed_at": metrics.get("refreshed_at"),
        "beta": beta,
        "var": var,
        "concentration": conc,
        "drawdown": dd,
        "portfolio_value": values.get("total_value"),
        "cash_balance": values.get("cash_balance"),
        "market_value": values.get("market_value"),
    }


def build_concentration_rows(db: Session, portfolio_id: int, metrics: dict[str, Any]) -> list[dict[str, Any]]:
    conc = metrics.get("concentration") or {}
    weight_by_symbol = {
        (row.get("symbol") or ""): row for row in (conc.get("holdings") or [])
    }
    values = calculate_portfolio_value(db, portfolio_id)
    holdings = db.scalars(
        select(PortfolioHolding)
        .where(PortfolioHolding.portfolio_id == portfolio_id, PortfolioHolding.quantity > 0)
        .options(joinedload(PortfolioHolding.stock))
    ).all()
    rows: list[dict[str, Any]] = []
    for holding in holdings:
        stock = holding.stock
        symbol = stock.symbol if stock else str(holding.stock_id)
        weight_row = weight_by_symbol.get(symbol, {})
        weight_pct = weight_row.get("weight_pct", 0)
        latest_price = None
        market_value = weight_row.get("market_value")
        for value_row in values.get("holdings") or []:
            if int(value_row.get("stock_id")) == holding.stock_id:
                latest_price = value_row.get("current_price")
                market_value = value_row.get("market_value")
                break
        rows.append(
            {
                "symbol": symbol,
                "company_name": stock.company_name if stock else symbol,
                "sector": stock.sector if stock else "",
                "quantity": holding.quantity,
                "latest_price": latest_price,
                "market_value": market_value or weight_row.get("market_value"),
                "weight_pct": weight_pct,
                "risk_contribution": None,
                "concentration_warning": weight_pct >= 25,
            }
        )
    rows.sort(key=lambda row: _as_float(row.get("weight_pct")) or 0, reverse=True)
    return rows[:50]


def build_allocation_plotly_json(db: Session, portfolio_id: int) -> dict[str, Any] | None:
    from app.models.stock import Stock

    values = calculate_portfolio_value(db, portfolio_id)
    holdings = values.get("holdings") or []
    if not holdings:
        return None
    stock_ids = [int(row["stock_id"]) for row in holdings]
    stocks = {stock.id: stock for stock in db.scalars(select(Stock).where(Stock.id.in_(stock_ids)))}
    by_symbol: list[dict[str, Any]] = []
    sector_totals: dict[str, float] = {}
    for row in holdings:
        stock = stocks.get(int(row["stock_id"]))
        sector = (stock.sector if stock and stock.sector else "Unknown") or "Unknown"
        mv = float(_as_float(row.get("market_value")) or 0)
        by_symbol.append({"label": row.get("symbol"), "value": mv})
        sector_totals[sector] = sector_totals.get(sector, 0.0) + mv
    by_sector = [{"label": key, "value": value} for key, value in sorted(sector_totals.items(), key=lambda item: -item[1])]
    return {
        "by_symbol": {
            "data": [
                {
                    "type": "pie",
                    "labels": [item["label"] for item in by_symbol],
                    "values": [item["value"] for item in by_symbol],
                    "hole": 0.45,
                    "textinfo": "label+percent",
                }
            ],
            "layout": {
                "height": 360,
                "margin": {"l": 10, "r": 10, "t": 10, "b": 10},
                "paper_bgcolor": "#050607",
                "font": {"color": "#f4f7fb"},
                "showlegend": True,
            },
        },
        "by_sector": {
            "data": [
                {
                    "type": "bar",
                    "x": [item["label"] for item in by_sector],
                    "y": [item["value"] for item in by_sector],
                    "marker": {"color": "#ff4d57"},
                }
            ],
            "layout": {
                "height": 360,
                "margin": {"l": 40, "r": 10, "t": 10, "b": 80},
                "paper_bgcolor": "#050607",
                "plot_bgcolor": "#11151b",
                "font": {"color": "#f4f7fb"},
                "yaxis": {"title": "Market value"},
            },
        },
    }


def build_drawdown_plotly_json(db: Session, portfolio_id: int, lookback_days: int) -> dict[str, Any] | None:
    cutoff = datetime.now(UTC).date() - timedelta(days=max(90, min(int(lookback_days), 400)))
    snapshots = list(
        db.scalars(
            select(PortfolioDailySnapshot)
            .where(
                PortfolioDailySnapshot.portfolio_id == portfolio_id,
                PortfolioDailySnapshot.snapshot_date >= cutoff,
            )
            .order_by(PortfolioDailySnapshot.snapshot_date.asc())
        )
    )
    if len(snapshots) < 2:
        return None

    dates = [snapshot.snapshot_date.isoformat() for snapshot in snapshots]
    values = [float(snapshot.total_value) for snapshot in snapshots]
    series = values[:]
    peak = series[0]
    drawdowns: list[float] = []
    for value in series:
        peak = max(peak, value)
        drawdowns.append(((value / peak) - 1) * 100 if peak else 0.0)

    bench_dates: list[str] = []
    bench_values: list[float] = []
    fund = db.scalar(select(IndexFund).where(IndexFund.yahoo_symbol == "^NSEI"))
    if fund:
        prices = list(
            db.scalars(
                select(IndexFundPrice)
                .where(
                    IndexFundPrice.index_fund_id == fund.id,
                    IndexFundPrice.timeframe == DAILY_TIMEFRAME,
                    IndexFundPrice.price_datetime >= datetime.combine(cutoff, datetime.min.time()),
                )
                .order_by(IndexFundPrice.price_datetime.asc())
            )
        )
        if len(prices) >= 2:
            frame = prices_to_dataframe(prices)
            bench_dates = [index.date().isoformat() for index in frame.index]
            bench_values = frame["close"].astype(float).tolist()

    return {
        "data": [
            {
                "type": "scatter",
                "mode": "lines",
                "name": "Portfolio value",
                "x": dates,
                "y": values,
                "line": {"color": "#4f9f68"},
                "yaxis": "y",
            },
            {
                "type": "scatter",
                "mode": "lines",
                "name": "Drawdown %",
                "x": dates,
                "y": drawdowns,
                "line": {"color": "#ff4d57"},
                "yaxis": "y2",
            },
            *(
                [
                    {
                        "type": "scatter",
                        "mode": "lines",
                        "name": "NIFTY 50 (close)",
                        "x": bench_dates,
                        "y": bench_values,
                        "line": {"color": "#7aa2ff", "dash": "dot"},
                        "yaxis": "y3",
                    }
                ]
                if bench_dates
                else []
            ),
        ],
        "layout": {
            "height": 420,
            "margin": {"l": 50, "r": 50, "t": 20, "b": 40},
            "paper_bgcolor": "#050607",
            "plot_bgcolor": "#11151b",
            "font": {"color": "#f4f7fb"},
            "legend": {"orientation": "h"},
            "yaxis": {"title": "Value", "side": "left"},
            "yaxis2": {"title": "Drawdown %", "overlaying": "y", "side": "right"},
            "yaxis3": {"title": "Benchmark", "anchor": "free", "overlaying": "y", "side": "right", "position": 0.98},
        },
    }

