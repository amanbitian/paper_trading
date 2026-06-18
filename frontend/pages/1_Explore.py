from __future__ import annotations

import time
from datetime import timedelta
from html import escape
from urllib.parse import quote as url_quote

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from api_client import (
    format_compact_indian_number,
    format_indian_number,
    format_inr,
    format_pct,
    format_signed_inr,
    format_time_ago,
    get,
    log_page_load,
    portfolio_select,
    post,
    require_login,
    start_timer,
)
from ui import load_global_css, page_header, status_badge


PAGE_STARTED_AT = start_timer()
PRICE_HISTORY_LIMIT = 10000
PARAMETER_CONFIG = {
    "rsi_period": {"label": "RSI period", "min": 2, "max": 100, "step": 1, "kind": "int"},
    "buy_rsi_below": {"label": "Buy RSI below", "min": 1.0, "max": 50.0, "step": 1.0, "kind": "float"},
    "sell_rsi_above": {"label": "Sell RSI above", "min": 50.0, "max": 99.0, "step": 1.0, "kind": "float"},
    "short_window": {"label": "Short SMA window", "min": 2, "max": 250, "step": 1, "kind": "int"},
    "long_window": {"label": "Long SMA window", "min": 3, "max": 400, "step": 1, "kind": "int"},
    "lookback_period": {"label": "Lookback period", "min": 2, "max": 250, "step": 1, "kind": "int"},
    "volume_multiplier": {"label": "Volume multiplier", "min": 0.1, "max": 10.0, "step": 0.1, "kind": "float"},
    "max_position_size_pct": {"label": "Max position size %", "min": 0.1, "max": 100.0, "step": 0.5, "kind": "float"},
    "stop_loss_pct": {"label": "Stop loss %", "min": 0.1, "max": 80.0, "step": 0.5, "kind": "float"},
    "take_profit_pct": {"label": "Take profit %", "min": 0.1, "max": 200.0, "step": 0.5, "kind": "float"},
}
PARAMETER_CONFIG.update(
    {
        "vwap_window": {"label": "VWAP window", "min": 2, "max": 250, "step": 1, "kind": "int"},
        "buy_below_pct": {"label": "Buy below benchmark %", "min": -20.0, "max": 0.0, "step": 0.25, "kind": "float"},
        "sell_above_pct": {"label": "Sell above benchmark %", "min": 0.0, "max": 20.0, "step": 0.25, "kind": "float"},
        "twap_window": {"label": "TWAP window", "min": 2, "max": 250, "step": 1, "kind": "int"},
        "arrival_window": {"label": "Arrival benchmark window", "min": 1, "max": 60, "step": 1, "kind": "int"},
        "buy_improvement_pct": {"label": "Buy improvement %", "min": -20.0, "max": 0.0, "step": 0.25, "kind": "float"},
        "sell_deterioration_pct": {"label": "Sell deterioration %", "min": 0.0, "max": 20.0, "step": 0.25, "kind": "float"},
        "trend_window": {"label": "Trend window", "min": 2, "max": 300, "step": 1, "kind": "int"},
        "lookback_window": {"label": "Lookback window", "min": 10, "max": 400, "step": 5, "kind": "int"},
        "zscore_entry": {"label": "Spread z-score entry", "min": 0.5, "max": 5.0, "step": 0.25, "kind": "float"},
        "mean_window": {"label": "Mean window", "min": 5, "max": 300, "step": 1, "kind": "int"},
        "buy_z_below": {"label": "Buy z-score below", "min": -5.0, "max": 0.0, "step": 0.25, "kind": "float"},
        "sell_z_above": {"label": "Sell z-score above", "min": 0.0, "max": 5.0, "step": 0.25, "kind": "float"},
        "residual_buy_below": {"label": "Buy residual z below", "min": -5.0, "max": 0.0, "step": 0.25, "kind": "float"},
        "residual_sell_above": {"label": "Sell residual z above", "min": 0.0, "max": 5.0, "step": 0.25, "kind": "float"},
        "short_return_window": {"label": "Short return window", "min": 2, "max": 250, "step": 1, "kind": "int"},
        "long_return_window": {"label": "Long return window", "min": 3, "max": 400, "step": 1, "kind": "int"},
        "forecast_buy_above_pct": {"label": "Buy forecast above %", "min": 0.0, "max": 10.0, "step": 0.05, "kind": "float"},
        "forecast_sell_below_pct": {"label": "Sell forecast below %", "min": -10.0, "max": 0.0, "step": 0.05, "kind": "float"},
        "short_vol_window": {"label": "Short volatility window", "min": 2, "max": 250, "step": 1, "kind": "int"},
        "long_vol_window": {"label": "Long volatility window", "min": 3, "max": 400, "step": 1, "kind": "int"},
        "momentum_window": {"label": "Momentum window", "min": 2, "max": 250, "step": 1, "kind": "int"},
        "vol_ratio_sell_above": {"label": "Sell volatility ratio above", "min": 0.5, "max": 5.0, "step": 0.05, "kind": "float"},
        "risk_aversion": {"label": "Risk aversion", "min": 0.01, "max": 10.0, "step": 0.01, "kind": "float"},
        "inventory_limit": {"label": "Inventory limit", "min": 1, "max": 100000, "step": 1, "kind": "int"},
        "max_spread_pct": {"label": "Max spread %", "min": 0.01, "max": 10.0, "step": 0.05, "kind": "float"},
        "imbalance_buy_above": {"label": "Buy imbalance above", "min": 0.0, "max": 1.0, "step": 0.05, "kind": "float"},
        "imbalance_sell_below": {"label": "Sell imbalance below", "min": -1.0, "max": 0.0, "step": 0.05, "kind": "float"},
        "lookback_events": {"label": "Lookback events", "min": 1, "max": 10000, "step": 1, "kind": "int"},
        "momentum_short_window": {"label": "Short momentum window", "min": 2, "max": 250, "step": 1, "kind": "int"},
        "momentum_long_window": {"label": "Long momentum window", "min": 3, "max": 400, "step": 1, "kind": "int"},
        "momentum_skip_window": {"label": "Skip recent sessions", "min": 0, "max": 63, "step": 1, "kind": "int"},
        "volatility_window": {"label": "Volatility window", "min": 5, "max": 250, "step": 1, "kind": "int"},
        "min_bars": {"label": "Minimum candles", "min": 5, "max": 600, "step": 1, "kind": "int"},
        "trend_exit_below_pct": {"label": "Trend exit below %", "min": -50.0, "max": 0.0, "step": 0.5, "kind": "float"},
        "max_annualized_vol_pct": {"label": "Max annualized vol %", "min": 1.0, "max": 200.0, "step": 1.0, "kind": "float"},
        "min_average_volume": {"label": "Min average volume", "min": 0, "max": 10000000, "step": 1000, "kind": "int"},
        "fundamental_weight": {"label": "Fundamental weight", "min": 0.0, "max": 1.0, "step": 0.05, "kind": "float"},
        "atr_multiplier": {"label": "ATR stop multiplier", "min": 0.1, "max": 10.0, "step": 0.1, "kind": "float"},
        "buy_score_above": {"label": "Buy score above", "min": -1.0, "max": 1.0, "step": 0.05, "kind": "float"},
        "sell_score_below": {"label": "Sell score below", "min": -1.0, "max": 1.0, "step": 0.05, "kind": "float"},
        "sequence_window": {"label": "Sequence window", "min": 2, "max": 250, "step": 1, "kind": "int"},
        "ema_fast_span": {"label": "Fast EMA span", "min": 2, "max": 250, "step": 1, "kind": "int"},
        "ema_slow_span": {"label": "Slow EMA span", "min": 3, "max": 400, "step": 1, "kind": "int"},
    }
)
st.set_page_config(page_title="Explore", page_icon="EX", layout="wide")
load_global_css()
page_header(
    "Explore",
    "Scan Indian market data, open stored stock history, and review educational strategy findings.",
    right_badge=status_badge("Market view", "info"),
)

if not require_login():
    st.stop()

st.markdown(
    """
    <style>
    div[data-testid="stVerticalBlock"] {
        min-width: 0;
    }
    .index-grid,
    .stock-card-grid {
        display: grid;
        gap: 14px;
        width: 100%;
    }
    .index-grid {
        grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
        margin: 12px 0 22px 0;
    }
    .stock-card-grid {
        grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
    }
    .index-card,
    .market-card {
        border: 1px solid rgba(148, 163, 184, 0.24);
        border-radius: 14px;
        padding: 18px;
        background: linear-gradient(180deg, rgba(27, 32, 43, 0.92), rgba(17, 22, 31, 0.92));
        box-shadow: 0 12px 28px rgba(0, 0, 0, 0.14);
        min-width: 0;
        overflow: hidden;
    }
    .market-card {
        min-height: 172px;
    }
    .index-label,
    .card-symbol,
    .muted {
        color: #8e93a6;
    }
    .index-label,
    .card-symbol {
        font-size: 0.84rem;
        font-weight: 700;
        text-transform: uppercase;
    }
    .index-price,
    .card-price {
        font-size: 1.35rem;
        font-weight: 800;
        margin: 6px 0;
        overflow-wrap: anywhere;
    }
    .stock-logo {
        width: 44px;
        height: 44px;
        border: 1px solid rgba(135, 145, 168, 0.28);
        border-radius: 8px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: 700;
        color: #00a884;
        background: #f7fffc;
        margin-bottom: 14px;
    }
    .company-link {
        color: inherit !important;
        display: inline-block;
        max-width: 100%;
        text-decoration: none;
        border: 1px solid rgba(148, 163, 184, 0.3);
        border-radius: 10px;
        padding: 6px 10px;
        font-weight: 700;
        line-height: 1.35;
        overflow-wrap: anywhere;
        word-break: normal;
    }
    .company-link:hover {
        border-color: #ff4b4b;
        color: #ff6b6b !important;
    }
    .gain { color: #00a884; font-weight: 700; }
    .loss { color: #ef4d3d; font-weight: 700; }
    .soft-card {
        border: 1px solid rgba(148, 163, 184, 0.24);
        border-radius: 14px;
        padding: 16px;
        background: rgba(21, 26, 36, 0.68);
    }
    .quote-table,
    .ranking-table {
        border: 1px solid rgba(148, 163, 184, 0.24);
        border-radius: 14px;
        overflow: hidden;
        margin-bottom: 22px;
        background: rgba(14, 18, 26, 0.75);
    }
    .quote-table-scroll {
        max-height: 720px;
        overflow-y: auto;
    }
    .quote-row,
    .ranking-row {
        display: grid;
        gap: 14px;
        align-items: center;
        border-top: 1px solid rgba(135, 145, 168, 0.18);
        padding: 14px 16px;
        min-width: 0;
    }
    .quote-row {
        grid-template-columns: minmax(180px, 1.45fr) minmax(130px, 1fr) minmax(150px, 1fr) minmax(90px, 0.6fr);
    }
    .ranking-row {
        grid-template-columns: minmax(190px, 1.5fr) minmax(90px, 0.65fr) minmax(110px, 0.7fr) minmax(90px, 0.6fr) minmax(240px, 1.8fr);
    }
    .quote-row:first-child,
    .ranking-row:first-child {
        border-top: 0;
    }
    .table-header {
        color: #8e93a6;
        font-size: 0.84rem;
        font-weight: 800;
        text-transform: uppercase;
        background: rgba(255, 255, 255, 0.03);
    }
    .sparkline {
        width: min(150px, 100%);
        height: 42px;
        display: block;
    }
    .badge {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        border-radius: 999px;
        font-weight: 800;
        padding: 5px 10px;
        width: fit-content;
    }
    .badge-buy {
        background: rgba(0, 168, 132, 0.12);
        color: #00a884;
    }
    .badge-sell {
        background: rgba(239, 77, 61, 0.12);
        color: #ef4d3d;
    }
    .badge-hold {
        background: rgba(142, 147, 166, 0.16);
        color: #c8ccd8;
    }
    @media (max-width: 980px) {
        .index-grid {
            grid-template-columns: repeat(auto-fit, minmax(128px, 1fr));
        }
        .stock-card-grid {
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
        }
    }
    @media (max-width: 640px) {
        .index-grid,
        .stock-card-grid {
            grid-template-columns: 1fr;
        }
        .market-card {
            min-height: 0;
        }
        .index-price,
        .card-price {
            font-size: 1.15rem;
        }
        .quote-row,
        .ranking-row {
            grid-template-columns: 1fr;
        }
        .table-header {
            display: none;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def _change_class(value: float) -> str:
    return "gain" if value >= 0 else "loss"


def _price_text(quote: dict) -> str:
    if quote.get("kind") == "index":
        return format_indian_number(quote.get("price"), decimals=2)
    return format_inr(quote.get("price"), decimals=2)


def _change_text(quote: dict) -> str:
    change = quote.get("change", 0)
    if quote.get("kind") == "index":
        amount = format_indian_number(abs(change), decimals=2)
        prefix = "+" if change >= 0 else "-"
        return f"{prefix}{amount} ({format_pct(quote.get('change_pct'), signed=True)})"
    return f"{format_signed_inr(change)} ({format_pct(quote.get('change_pct'), signed=True)})"


def _selected_yahoo_symbol() -> str | None:
    value = st.query_params.get("stock")
    if isinstance(value, list):
        return value[0] if value else None
    return value or None


def _stock_href(yahoo_symbol: str | None) -> str:
    if not yahoo_symbol:
        return "#"
    return f"?stock={url_quote(yahoo_symbol)}"


def _company_link_html(quote: dict, label: str | None = None) -> str:
    yahoo_symbol = str(quote.get("yahoo_symbol") or "")
    link_label = label or str(quote.get("label") or quote.get("symbol") or yahoo_symbol)
    return (
        f'<a class="company-link" href="{_stock_href(yahoo_symbol)}" '
        f'title="View stored historical data for {escape(link_label)}">{escape(link_label)}</a>'
    )


def _volume_text(value) -> str:
    if value is None:
        return "-"
    return format_compact_indian_number(value, decimals=2)


def _sparkline_svg(values: list[float] | None) -> str:
    clean_values = [float(value) for value in values or [] if value is not None]
    if len(clean_values) < 2:
        return '<span class="muted">-</span>'
    width = 150
    height = 42
    minimum = min(clean_values)
    maximum = max(clean_values)
    span = maximum - minimum or 1
    step = width / max(len(clean_values) - 1, 1)
    points = []
    for index, value in enumerate(clean_values):
        x = index * step
        y = height - ((value - minimum) / span * (height - 8)) - 4
        points.append(f"{x:.1f},{y:.1f}")
    color = "#00a884" if clean_values[-1] >= clean_values[0] else "#ef4d3d"
    return (
        f'<svg class="sparkline" viewBox="0 0 {width} {height}" role="img" aria-label="Price trend">'
        f'<polyline fill="none" stroke="{color}" stroke-width="3" stroke-linecap="round" '
        f'stroke-linejoin="round" points="{" ".join(points)}"/></svg>'
    )


def _is_missing(value) -> bool:
    return value is None or pd.isna(value)


def _optional_inr(value) -> str:
    return "-" if _is_missing(value) else format_inr(value, decimals=2)


def _optional_pct(value) -> str:
    return "-" if _is_missing(value) else format_pct(value, signed=True)


def _optional_date(value) -> str:
    if _is_missing(value):
        return "-"
    return str(pd.to_datetime(value).date())


def resolve_selected_stock(yahoo_symbol: str) -> dict | None:
    if yahoo_symbol:
        results = get("/stocks/search", params={"query": yahoo_symbol}) or []
        exact = next(
            (stock for stock in results if stock.get("yahoo_symbol") == yahoo_symbol),
            None,
        )
        if exact:
            return exact
        symbol = yahoo_symbol.replace(".NS", "").replace(".BO", "")
        results = get("/stocks/search", params={"query": symbol}) or []
        exact = next(
            (stock for stock in results if stock.get("symbol") == symbol),
            None,
        )
        if exact:
            return exact
        if results:
            return results[0]
    return None


def render_stock_search() -> None:
    st.subheader("Find a stock")
    query = st.text_input(
        "Search company",
        placeholder="Search ITC, HDFC Bank, Axis Bank, RELIANCE...",
        label_visibility="collapsed",
    ).strip()
    if not query:
        st.caption("Search a company to open its stored historical data and algorithm findings.")
        return

    results = get("/stocks/search", params={"query": query}) or []
    if not results:
        st.info("No matching stocks found in the local stocks table.")
        return

    cards = []
    for stock in results[:10]:
        symbol = str(stock.get("symbol") or "")
        initials = escape(symbol[:2] or "ST")
        label = stock.get("company_name") or symbol
        cards.append(
            f'<div class="market-card">'
            f'<div class="stock-logo">{initials}</div>'
            f'{_company_link_html({"yahoo_symbol": stock.get("yahoo_symbol"), "symbol": symbol, "label": label})}'
            f'<div class="card-symbol" style="margin-top: 12px;">{escape(symbol)} - {escape(str(stock.get("exchange") or ""))}</div>'
            f'<div class="muted">{escape(str(stock.get("yahoo_symbol") or ""))}</div>'
            f"</div>"
        )
    st.markdown(f"<div class='stock-card-grid'>{''.join(cards)}</div>", unsafe_allow_html=True)


def _sort_quotes(quotes: list[dict], sort_by: str, descending: bool) -> list[dict]:
    def sort_key(quote: dict) -> float:
        if sort_by == "price":
            return float(quote.get("price") or 0)
        if sort_by == "volume":
            return float(quote.get("volume") or 0)
        return float(quote.get("change_pct") or 0)

    return sorted(quotes, key=sort_key, reverse=descending)


def render_quote_table(quotes: list[dict], *, sort_by: str = "trend", descending: bool = True) -> None:
    if not quotes:
        st.info("No rows are available for this group yet.")
        return

    sorted_quotes = _sort_quotes(quotes, sort_by, descending)

    rows = [
        "<div class='quote-row table-header'><div>Company</div><div>Trend</div><div>Market price (1D)</div><div>Volume</div></div>"
    ]
    for quote in sorted_quotes:
        css_class = _change_class(float(quote.get("change", 0)))
        label = str(quote.get("label") or quote.get("symbol") or "")
        symbol = str(quote.get("symbol") or "")
        rows.append(
            f"<div class='quote-row'>"
            f"<div>{_company_link_html(quote, label)}<div class='muted' style='margin-top:8px;'>{escape(symbol)}</div></div>"
            f"<div>{_sparkline_svg(quote.get('sparkline'))}</div>"
            f"<div><strong>{escape(_price_text(quote))}</strong><br><span class='{css_class}'>{escape(_change_text(quote))}</span></div>"
            f"<div>{escape(_volume_text(quote.get('volume')))}</div>"
            f"</div>"
        )
    st.markdown(f"<div class='quote-table quote-table-scroll'>{''.join(rows)}</div>", unsafe_allow_html=True)


def _render_movers_sort_controls(session_prefix: str) -> tuple[str, bool]:
    sort_key = f"{session_prefix}_sort_by"
    direction_key = f"{session_prefix}_sort_desc"
    st.session_state.setdefault(sort_key, "trend")
    st.session_state.setdefault(direction_key, True)

    sort_cols = st.columns([1, 1, 1, 2])
    if sort_cols[0].button(
        "Sort: Trend",
        type="primary" if st.session_state[sort_key] == "trend" else "secondary",
        use_container_width=True,
        key=f"{session_prefix}_btn_trend",
    ):
        if st.session_state[sort_key] == "trend":
            st.session_state[direction_key] = not st.session_state[direction_key]
        else:
            st.session_state[sort_key] = "trend"
            st.session_state[direction_key] = True
        st.rerun()
    if sort_cols[1].button(
        "Sort: Market price",
        type="primary" if st.session_state[sort_key] == "price" else "secondary",
        use_container_width=True,
        key=f"{session_prefix}_btn_price",
    ):
        if st.session_state[sort_key] == "price":
            st.session_state[direction_key] = not st.session_state[direction_key]
        else:
            st.session_state[sort_key] = "price"
            st.session_state[direction_key] = True
        st.rerun()
    if sort_cols[2].button(
        "Sort: Volume",
        type="primary" if st.session_state[sort_key] == "volume" else "secondary",
        use_container_width=True,
        key=f"{session_prefix}_btn_volume",
    ):
        if st.session_state[sort_key] == "volume":
            st.session_state[direction_key] = not st.session_state[direction_key]
        else:
            st.session_state[sort_key] = "volume"
            st.session_state[direction_key] = True
        st.rerun()

    direction_label = "high to low" if st.session_state[direction_key] else "low to high"
    sort_cols[3].caption(f"Active sort: {st.session_state[sort_key].replace('_', ' ')} ({direction_label})")
    return st.session_state[sort_key], st.session_state[direction_key]


SYNC_INTERVAL_OPTIONS: dict[str, int] = {
    "Every 15 minutes": 15,
    "Every 30 minutes": 30,
    "Every 1 hour": 60,
    "Every 2 hours": 120,
    "Every 4 hours": 240,
}
SYNC_SCHEDULER_TICK_SECONDS = 30


def _init_market_sync_session() -> None:
    defaults = {
        "market_sync_scheduler_enabled": False,
        "market_sync_interval_minutes": 60,
        "market_sync_interval_label": "Every 1 hour",
        "market_sync_next_due_at": None,
        "market_sync_waiting": False,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _is_sync_active(sync_status: dict) -> bool:
    return bool(sync_status.get("is_running") and st.session_state.get("market_sync_waiting"))


def _trigger_market_sync() -> bool:
    result = post("/market/sync", show_error=True)
    if result and result.get("started"):
        st.session_state["market_sync_waiting"] = True
        return True
    if result and not result.get("started"):
        st.warning(result.get("message") or "A sync is already running.")
    return False


def _scheduler_status_text() -> str | None:
    if not st.session_state.get("market_sync_scheduler_enabled"):
        return None
    next_due = st.session_state.get("market_sync_next_due_at")
    if next_due is None:
        return "Scheduler active for this session · first run after the selected interval"
    remaining = int(next_due - time.time())
    if remaining <= 0:
        return "Scheduler active for this session · sync due"
    if remaining < 60:
        return f"Scheduler active for this session · next sync in {remaining} seconds"
    minutes = remaining // 60
    unit = "minute" if minutes == 1 else "minutes"
    return f"Scheduler active for this session · next sync in {minutes} {unit}"


def _sync_status_caption(sync_status: dict) -> str:
    if _is_sync_active(sync_status):
        current_run = sync_status.get("current_run") or {}
        total = current_run.get("total_symbols")
        if total:
            return f"Syncing market prices from Yahoo Finance ({total:,} symbols). This may take several minutes."
        return "Syncing market prices from Yahoo Finance. This may take several minutes."

    scheduler_text = _scheduler_status_text()
    last_synced_at = sync_status.get("last_synced_at")
    if last_synced_at:
        parts = [f"Synced {format_time_ago(last_synced_at)}"]
        record_date = sync_status.get("record_date")
        if record_date:
            parts.append(f"latest trading session: {record_date}")
        if scheduler_text:
            parts.append(scheduler_text)
        return " · ".join(parts)

    if scheduler_text:
        return scheduler_text
    return "No automatic sync. Click Sync now when you want fresh prices from Yahoo Finance."


@st.fragment(run_every=timedelta(seconds=SYNC_SCHEDULER_TICK_SECONDS))
def _market_sync_scheduler_fragment() -> None:
    sync_status = get("/market/sync-status") or {}
    is_running = bool(sync_status.get("is_running"))

    if st.session_state.get("market_sync_waiting"):
        if is_running:
            return
        st.session_state["market_sync_waiting"] = False
        if st.session_state.get("market_sync_scheduler_enabled"):
            interval_minutes = int(st.session_state.get("market_sync_interval_minutes") or 60)
            st.session_state["market_sync_next_due_at"] = time.time() + interval_minutes * 60
        if sync_status.get("last_sync_status") in {"SUCCEEDED", "PARTIAL"}:
            st.toast("Market data synced")
        st.rerun()
        return

    if not st.session_state.get("market_sync_scheduler_enabled") or is_running:
        return

    next_due = st.session_state.get("market_sync_next_due_at")
    if next_due is None or time.time() < float(next_due):
        return

    result = post("/market/sync", show_error=False)
    if result and result.get("started"):
        st.session_state["market_sync_waiting"] = True
        interval_minutes = int(st.session_state.get("market_sync_interval_minutes") or 60)
        st.session_state["market_sync_next_due_at"] = time.time() + interval_minutes * 60
        st.toast("Scheduled sync started")
        st.rerun()


def _on_scheduler_toggle() -> None:
    if st.session_state.get("market_sync_scheduler_enabled"):
        interval_minutes = int(st.session_state.get("market_sync_interval_minutes") or 60)
        st.session_state["market_sync_next_due_at"] = time.time() + interval_minutes * 60
    else:
        st.session_state["market_sync_next_due_at"] = None


def _on_scheduler_interval_change() -> None:
    selected_label = st.session_state.get("market_sync_interval_select", "Every 1 hour")
    selected_minutes = SYNC_INTERVAL_OPTIONS.get(selected_label, 60)
    st.session_state["market_sync_interval_label"] = selected_label
    st.session_state["market_sync_interval_minutes"] = selected_minutes
    if st.session_state.get("market_sync_scheduler_enabled"):
        st.session_state["market_sync_next_due_at"] = time.time() + selected_minutes * 60


def render_market_sync_controls() -> dict:
    _init_market_sync_session()
    sync_status = get("/market/sync-status") or {}

    sync_col, scheduler_col, status_col = st.columns([1, 2, 4], vertical_alignment="center")

    with sync_col:
        if _is_sync_active(sync_status):
            st.button("Syncing...", disabled=True, use_container_width=True)
        elif st.button("Sync now", type="primary", use_container_width=True, key="market_sync_button"):
            if _trigger_market_sync():
                st.toast("Market sync started")
                st.rerun()

    with scheduler_col:
        st.toggle(
            "Session sync scheduler",
            help="Runs only while this Explore tab stays open. No sync happens in the background after you leave.",
            key="market_sync_scheduler_enabled",
            on_change=_on_scheduler_toggle,
        )
        if st.session_state.get("market_sync_scheduler_enabled"):
            interval_labels = list(SYNC_INTERVAL_OPTIONS.keys())
            current_label = st.session_state.get("market_sync_interval_label", "Every 1 hour")
            if current_label not in interval_labels:
                current_label = "Every 1 hour"
            st.selectbox(
                "Sync interval",
                interval_labels,
                index=interval_labels.index(current_label),
                label_visibility="collapsed",
                key="market_sync_interval_select",
                on_change=_on_scheduler_interval_change,
            )

    with status_col:
        st.caption(_sync_status_caption(sync_status))

    if st.session_state.get("market_sync_scheduler_enabled") or st.session_state.get("market_sync_waiting"):
        _market_sync_scheduler_fragment()

    return sync_status


def render_market_movers(_overview_payload: dict | None = None) -> None:
    st.subheader("Top movers")

    filter_payload = get("/market/trends/filters") or {}
    nifty_index_options = filter_payload.get("nifty_indices") or [
        {"label": "NIFTY 50", "value": "nifty50"},
        {"label": "NIFTY 100", "value": "nifty100"},
        {"label": "NIFTY 200", "value": "nifty200"},
        {"label": "NIFTY 500", "value": "nifty500"},
        {"label": "NIFTY Bank", "value": "banknifty"},
        {"label": "NIFTY Financial Services", "value": "finnifty"},
        {"label": "NIFTY Midcap Select", "value": "midcpnifty"},
    ]
    nifty_by_label = {option["label"]: option["value"] for option in nifty_index_options}
    nifty_labels = list(nifty_by_label)
    nifty_filter_labels = ["All indices"] + nifty_labels

    st.session_state.setdefault("explore_movers_nifty_index", "All indices")
    current_nifty_label = (
        st.session_state.explore_movers_nifty_index
        if st.session_state.explore_movers_nifty_index in nifty_filter_labels
        else "All indices"
    )
    selected_nifty_label = st.selectbox(
        "NIFTY Index",
        nifty_filter_labels,
        index=nifty_filter_labels.index(current_nifty_label),
        help="Filter top movers to constituents from the nse_csv_* index tables.",
    )
    st.session_state.explore_movers_nifty_index = selected_nifty_label

    movers_params: dict[str, str] = {}
    if selected_nifty_label != "All indices":
        movers_params["nifty_index"] = nifty_by_label[selected_nifty_label]

    movers_payload = get("/market/movers", params=movers_params) or {}
    universe = movers_payload.get("eligible_count") or 0
    nifty_label = movers_payload.get("nifty_index_label")

    if nifty_label:
        st.caption(
            f"Top 50 gainers, losers, and volume leaders within {nifty_label} "
            f"({universe:,} eligible stocks with stored daily candles)."
        )
    elif universe:
        st.caption(
            f"Top 50 gainers, losers, and volume leaders across {universe:,} stocks using stored daily candles."
        )
    else:
        st.caption("Top 50 gainers, losers, and volume leaders from stored daily candles.")

    gainers_tab, losers_tab, volume_tab = st.tabs(["Top 50 gainers", "Top 50 losers", "Top 50 volume"])
    with gainers_tab:
        sort_by, descending = _render_movers_sort_controls("explore_movers_gainers")
        render_quote_table(
            movers_payload.get("top_gainers", []),
            sort_by=sort_by,
            descending=descending,
        )
    with losers_tab:
        sort_by, descending = _render_movers_sort_controls("explore_movers_losers")
        render_quote_table(
            movers_payload.get("top_losers", []),
            sort_by=sort_by,
            descending=False if sort_by == "trend" else descending,
        )
    with volume_tab:
        sort_by, descending = _render_movers_sort_controls("explore_movers_volume")
        render_quote_table(
            movers_payload.get("volume_shockers", []),
            sort_by=sort_by if sort_by != "trend" else "volume",
            descending=descending,
        )


def render_ranking_table(rows: list[dict], rank_side: str, empty_message: str) -> None:
    if not rows:
        st.info(empty_message)
        return

    html_rows = [
        "<div class='ranking-row table-header'><div>Company</div><div>Rank side</div><div>Score</div><div>Close</div><div>Finding</div></div>"
    ]
    rank_side = rank_side.upper()
    badge_class = "badge-buy" if rank_side == "BUY" else "badge-sell"
    for row in rows:
        label = str(row.get("company_name") or row.get("symbol") or row.get("yahoo_symbol") or "")
        model_action = str(row.get("action") or "HOLD").upper()
        score = float(row.get("sequence_score") or 0)
        score_class = "gain" if score >= 0 else "loss"
        html_rows.append(
            f"<div class='ranking-row'>"
            f"<div>{_company_link_html(row, label)}<div class='muted' style='margin-top:8px;'>{escape(str(row.get('symbol') or ''))} - {escape(str(row.get('exchange') or ''))}</div></div>"
            f"<div><span class='badge {badge_class}'>{escape(rank_side)}</span><div class='muted' style='margin-top:8px;'>Model: {escape(model_action)} | Conf {float(row.get('confidence_score') or 0):.2f}</div></div>"
            f"<div class='{score_class}'>{score:.4f}</div>"
            f"<div>{escape(format_inr(row.get('latest_close'), decimals=2))}</div>"
            f"<div>{escape(str(row.get('reason') or ''))}<div class='muted' style='margin-top:8px;'>As of {escape(str(row.get('as_of_date') or '-'))}</div></div>"
            f"</div>"
        )
    st.markdown(f"<div class='ranking-table'>{''.join(html_rows)}</div>", unsafe_allow_html=True)


def render_sequential_rankings() -> None:
    rankings = get("/market/sequential-rankings", params={"limit": 15}) or {}
    st.subheader("Sequential Deep Learning rankings")
    st.caption(
        "Top buy and sell candidates are ranked across active stocks that have enough stored daily candles. This MVP uses the daily sequence proxy behind the stock detail page."
    )
    eligible_count = rankings.get("eligible_count", 0)
    rows_scanned = rankings.get("rows_scanned", 0)
    st.caption(f"Eligible stocks: {format_indian_number(eligible_count, decimals=0)} of {format_indian_number(rows_scanned, decimals=0)} active stocks.")
    buy_tab, sell_tab = st.tabs(["Top 15 buy candidates", "Top 15 sell candidates"])
    with buy_tab:
        render_ranking_table(rankings.get("top_buys", []), "BUY", "No buy ranking rows are available yet. Ingest daily prices first.")
    with sell_tab:
        render_ranking_table(rankings.get("top_sells", []), "SELL", "No sell ranking rows are available yet. Ingest daily prices first.")


def render_all_stocks_performance() -> None:
    st.subheader("All stocks in database")
    st.caption(
        "Latest price uses the most recent stored daily candle. Period changes compare that close with the nearest "
        "stored candle at or before 1M, 3M, 6M, and 1Y ago. Filter by sector (e.g. Financial Services) or industry "
        "(e.g. Banks - Regional) where Yahoo metadata is available."
    )
    index_filters = get("/stocks/index-filters") or []
    index_label_to_value = {"All": ""}
    index_flag_labels = {}
    for option in index_filters:
        index_label_to_value[option["label"]] = option["value"]
        index_flag_labels[option["flag_column"]] = option["label"]

    label_cols = st.columns([2, 1, 1, 1, 1])
    label_cols[0].markdown("**Search**")
    label_cols[1].markdown("**Exchange**")
    label_cols[2].markdown("**Index**")
    label_cols[3].markdown("**Sector**")
    label_cols[4].markdown("**Industry**")

    filter_cols = st.columns([2, 1, 1, 1, 1])
    query = filter_cols[0].text_input(
        "Search all stocks",
        key="all_stocks_performance_query",
        placeholder="Search symbol or company...",
        label_visibility="collapsed",
    ).strip()
    exchange = filter_cols[1].selectbox(
        "Exchange",
        ["All", "NSE", "BSE"],
        key="all_stocks_performance_exchange",
        label_visibility="collapsed",
    )
    selected_index_label = filter_cols[2].selectbox(
        "Index",
        list(index_label_to_value.keys()),
        key="all_stocks_performance_index",
        label_visibility="collapsed",
    )
    selected_index = index_label_to_value[selected_index_label]

    meta_params: dict[str, str | bool] = {"only_with_prices": True}
    if exchange != "All":
        meta_params["exchange"] = exchange
    if selected_index:
        meta_params["index_code"] = selected_index

    sector_options = ["All", *(get("/stocks/sectors", params=meta_params) or [])]
    sector = filter_cols[3].selectbox(
        "Sector",
        sector_options,
        key="all_stocks_performance_sector",
        label_visibility="collapsed",
    )

    industry_params = dict(meta_params)
    if sector != "All":
        industry_params["sector"] = sector
    industry_options = ["All", *(get("/stocks/industries", params=industry_params) or [])]
    industry = filter_cols[4].selectbox(
        "Industry",
        industry_options,
        key="all_stocks_performance_industry",
        label_visibility="collapsed",
    )

    if len(sector_options) == 1 and len(industry_options) == 1:
        st.info(
            "Sector/industry filters are empty for most stocks. Run "
            "`python scripts/enrich_stock_metadata.py --only-with-prices` to populate them from Yahoo Finance."
        )

    params: dict[str, str | int | bool] = {"limit": 5000, "only_with_prices": True}
    if query:
        params["query"] = query
    if exchange != "All":
        params["exchange"] = exchange
    if selected_index:
        params["index_code"] = selected_index
    if sector != "All":
        params["sector"] = sector
    if industry != "All":
        params["industry"] = industry

    sort_by = st.selectbox(
        "Sort by",
        [
            "Latest date (newest)",
            "Volume (high to low)",
            "Volume (low to high)",
            "Latest price (high to low)",
            "Latest price (low to high)",
            "1Y change (high to low)",
            "1Y change (low to high)",
            "Symbol (A-Z)",
        ],
        key="all_stocks_performance_sort",
    )

    rows = get("/stocks/performance", params=params) or []
    if not rows:
        st.info("No stocks are available for this filter yet.")
        return

    dataframe = pd.DataFrame(rows)
    dataframe["latest_volume"] = pd.to_numeric(dataframe.get("latest_volume"), errors="coerce")
    dataframe["latest_price"] = pd.to_numeric(dataframe.get("latest_price"), errors="coerce")
    for column in ("change_1m_pct", "change_3m_pct", "change_6m_pct", "change_1y_pct"):
        if column in dataframe.columns:
            dataframe[column] = pd.to_numeric(dataframe[column], errors="coerce")

    sort_specs: dict[str, tuple[str, bool]] = {
        "Latest date (newest)": ("latest_price_datetime", False),
        "Volume (high to low)": ("latest_volume", False),
        "Volume (low to high)": ("latest_volume", True),
        "Latest price (high to low)": ("latest_price", False),
        "Latest price (low to high)": ("latest_price", True),
        "1Y change (high to low)": ("change_1y_pct", False),
        "1Y change (low to high)": ("change_1y_pct", True),
        "Symbol (A-Z)": ("symbol", True),
    }
    sort_column, sort_ascending = sort_specs[sort_by]
    if sort_column in dataframe.columns:
        dataframe = dataframe.sort_values(
            by=sort_column,
            ascending=sort_ascending,
            na_position="last",
            kind="mergesort",
        )

    dataframe["Company"] = dataframe["company_name"].fillna(dataframe["symbol"])
    if index_flag_labels:
        dataframe["Indexes"] = dataframe.apply(
            lambda row: ", ".join(
                label for flag, label in index_flag_labels.items() if bool(row.get(flag))
            )
            or "-",
            axis=1,
        )
    else:
        dataframe["Indexes"] = "-"
    dataframe["Open"] = dataframe["yahoo_symbol"].apply(
        lambda symbol: f"/Explore?stock={url_quote(str(symbol))}"
    )
    display_df = pd.DataFrame(
        {
            "Open": dataframe["Open"],
            "Company": dataframe["Company"],
            "Symbol": dataframe["symbol"],
            "Exchange": dataframe["exchange"],
            "Indexes": dataframe["Indexes"],
            "Sector": dataframe["sector"].fillna("-"),
            "Industry": dataframe["industry"].fillna("-"),
            "Latest Date": dataframe["latest_price_datetime"].apply(_optional_date),
            "Latest Price": dataframe["latest_price"],
            "1M Change": dataframe["change_1m_pct"],
            "3M Change": dataframe["change_3m_pct"],
            "6M Change": dataframe["change_6m_pct"],
            "1Y Change": dataframe["change_1y_pct"],
            "Volume": dataframe["latest_volume"],
        }
    )
    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Open": st.column_config.LinkColumn("Open", display_text="Open"),
            "Latest Price": st.column_config.NumberColumn(
                "Latest Price",
                format="₹ %.2f",
            ),
            "1M Change": st.column_config.NumberColumn("1M Change", format="%.2f%%"),
            "3M Change": st.column_config.NumberColumn("3M Change", format="%.2f%%"),
            "6M Change": st.column_config.NumberColumn("6M Change", format="%.2f%%"),
            "1Y Change": st.column_config.NumberColumn("1Y Change", format="%.2f%%"),
            "Volume": st.column_config.NumberColumn(
                "Volume",
                format="%d",
                help="Latest session traded quantity. Use Sort by or click this column header to sort.",
            ),
        },
    )


def _edit_strategy_parameter(name: str, value, key_prefix: str):
    config = PARAMETER_CONFIG.get(name)
    label = config["label"] if config else name.replace("_", " ").title()
    kind = config.get("kind") if config else None
    if isinstance(value, bool):
        return st.checkbox(label, value=bool(value), key=f"{key_prefix}_{name}")
    if kind == "int" or (kind is None and isinstance(value, int) and not isinstance(value, bool)):
        minimum = int(config.get("min", 0)) if config else 0
        maximum = int(config.get("max", 1000)) if config else 1000
        step = int(config.get("step", 1)) if config else 1
        return int(
            st.number_input(
                label,
                min_value=minimum,
                max_value=maximum,
                value=int(value),
                step=step,
                key=f"{key_prefix}_{name}",
            )
        )
    if kind == "float" or isinstance(value, float):
        minimum = float(config.get("min", 0.0)) if config else 0.0
        maximum = float(config.get("max", 1000.0)) if config else 1000.0
        step = float(config.get("step", 0.1)) if config else 0.1
        return float(
            st.number_input(
                label,
                min_value=minimum,
                max_value=maximum,
                value=float(value),
                step=step,
                key=f"{key_prefix}_{name}",
            )
        )
    return st.text_input(label, value=str(value), key=f"{key_prefix}_{name}")


def _validate_strategy_parameters(strategy_type: str, parameters: dict) -> list[str]:
    errors: list[str] = []
    if strategy_type == "rsi" and float(parameters.get("buy_rsi_below", 30)) >= float(parameters.get("sell_rsi_above", 70)):
        errors.append("Buy RSI threshold must be lower than sell RSI threshold.")
    if strategy_type == "sma_crossover" and int(parameters.get("short_window", 20)) >= int(parameters.get("long_window", 50)):
        errors.append("Short SMA window must be lower than long SMA window.")
    if strategy_type == "breakout" and float(parameters.get("volume_multiplier", 1.5)) <= 0:
        errors.append("Volume multiplier must be greater than zero.")
    if strategy_type in {"vwap", "twap"} and float(parameters.get("buy_below_pct", -1)) >= float(parameters.get("sell_above_pct", 1)):
        errors.append("Buy-below threshold must be lower than sell-above threshold.")
    if strategy_type == "implementation_shortfall" and float(parameters.get("buy_improvement_pct", -1)) >= float(parameters.get("sell_deterioration_pct", 1)):
        errors.append("Buy improvement must be lower than sell deterioration.")
    if strategy_type == "ou_process" and float(parameters.get("buy_z_below", -1.5)) >= float(parameters.get("sell_z_above", 1.5)):
        errors.append("Buy z-score threshold must be lower than sell z-score threshold.")
    if strategy_type == "kalman_filter" and float(parameters.get("residual_buy_below", -1)) >= float(parameters.get("residual_sell_above", 1)):
        errors.append("Buy residual threshold must be lower than sell residual threshold.")
    if strategy_type == "sarimax" and float(parameters.get("forecast_sell_below_pct", -0.25)) >= float(parameters.get("forecast_buy_above_pct", 0.25)):
        errors.append("Sell forecast threshold must be lower than buy forecast threshold.")
    if strategy_type in {"tree_ensemble", "sequential_deep_learning", "quality_momentum"} and float(parameters.get("sell_score_below", -0.25)) >= float(parameters.get("buy_score_above", 0.25)):
        errors.append("Sell score threshold must be lower than buy score threshold.")
    if strategy_type == "sequential_deep_learning" and int(parameters.get("ema_fast_span", 12)) >= int(parameters.get("ema_slow_span", 26)):
        errors.append("Fast EMA span must be lower than slow EMA span.")
    return errors


def render_strategy_playground(stock_id: int) -> None:
    st.subheader("Strategy playground")
    templates = get("/strategies/templates") or []
    if not templates:
        st.info("Seed strategy templates to preview stock strategies.")
        return

    template_options = {
        f"{template['strategy_name']} ({template['strategy_type']})": template
        for template in templates
    }
    selected_label = st.selectbox(
        "Strategy",
        list(template_options.keys()),
        key=f"explore_strategy_template_{stock_id}",
    )
    template = template_options[selected_label]
    if template.get("description"):
        st.caption(template["description"])

    advanced = st.toggle(
        "Advanced user",
        key=f"explore_strategy_advanced_{stock_id}_{template['id']}",
    )
    default_parameters = dict(template.get("default_parameters") or {})
    parameters = dict(default_parameters)
    form_key = f"explore_strategy_preview_{stock_id}_{template['id']}"
    with st.form(form_key):
        if advanced:
            parameter_items = list(default_parameters.items())
            columns = st.columns(3)
            for index, (name, value) in enumerate(parameter_items):
                with columns[index % 3]:
                    parameters[name] = _edit_strategy_parameter(name, value, form_key)
        else:
            st.caption("Using default parameters. Enable Advanced user to edit them.")
            st.json(default_parameters, expanded=False)

        errors = _validate_strategy_parameters(template["strategy_type"], parameters)
        for error in errors:
            st.error(error)
        submitted = st.form_submit_button("Preview strategy", disabled=bool(errors))

    if not submitted:
        return

    preview = post(
        "/strategies/preview-signal",
        {
            "stock_id": stock_id,
            "strategy_template_id": template["id"],
            "parameters": parameters,
        },
    )
    if not preview:
        return

    metric_cols = st.columns(4)
    metric_cols[0].metric("Signal", preview["signal_type"])
    metric_cols[1].metric("Confidence", f"{float(preview['confidence_score']):.2f}")
    metric_cols[2].metric("Latest price", format_inr(preview.get("suggested_price") or 0))
    metric_cols[3].metric("Strategy", preview["strategy_name"])
    st.write(preview.get("reason") or "")
    if preview.get("indicators"):
        st.json(preview["indicators"])
    with st.expander("Parameters used"):
        st.json(preview.get("parameters") or {})


def render_algo_findings(stock_id: int) -> None:
    findings = get(f"/stocks/{stock_id}/algo-findings", params={"limit": PRICE_HISTORY_LIMIT}) or []
    st.subheader("Algorithm findings")
    st.caption(
        "Signals use the stored daily OHLCV history where possible. Some institutional algos require a different data type, such as Level 2 order book events or a second pair asset, so those are labeled explicitly."
    )

    if not findings:
        st.info("No algorithm findings are available for this stock yet.")
        return

    summary_df = pd.DataFrame(
        [
            {
                "Algorithm": finding["algorithm_name"],
                "Category": finding["category"],
                "Action": finding["action"],
                "Confidence": f"{float(finding['confidence_score']):.2f}",
                "Status": finding["status"],
                "Reason": finding["reason"],
            }
            for finding in findings
        ]
    )
    st.dataframe(summary_df, use_container_width=True, hide_index=True)

    for finding in findings:
        title = (
            f"{finding['algorithm_name']} | {finding['action']} | "
            f"{float(finding['confidence_score']):.2f}"
        )
        with st.expander(title):
            st.markdown(f"**Category:** {finding['category']}")
            st.markdown(f"**Status:** `{finding['status']}`")
            st.markdown(f"**Data needed:** {finding['data_requirements']}")
            st.markdown(f"**Logic:** {finding['logic']}")
            st.markdown(f"**Finding:** {finding['reason']}")
            if finding.get("indicators"):
                st.json(finding["indicators"])
            chart = finding.get("chart")
            if chart and chart.get("series"):
                fig = go.Figure()
                for series in chart["series"]:
                    fig.add_trace(
                        go.Scatter(
                            x=chart["x"],
                            y=series["values"],
                            mode="lines",
                            name=series["name"],
                        )
                    )
                fig.update_layout(
                    title=chart.get("title"),
                    height=360,
                    margin={"l": 0, "r": 0, "t": 42, "b": 0},
                    legend={"orientation": "h", "y": -0.2},
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info(
                    "No daily OHLCV chart is rendered for this algorithm because it requires a different data type, not because historical candles are missing."
                )


def render_selected_stock_history() -> None:
    yahoo_symbol = _selected_yahoo_symbol()
    if not yahoo_symbol:
        return

    stock = resolve_selected_stock(yahoo_symbol)
    st.divider()
    title = stock.get("company_name") if stock else yahoo_symbol
    st.subheader(f"{title or yahoo_symbol} historical data")
    st.markdown('<a class="company-link" href="/Explore">Close historical view</a>', unsafe_allow_html=True)

    if not stock:
        st.warning("This company is not available in the local stocks table yet. Load tickers first.")
        return

    prices = get(
        f"/stocks/{stock['id']}/prices",
        params={"timeframe": "1d", "limit": PRICE_HISTORY_LIMIT},
    ) or []
    if not prices:
        st.info(
            "No stored daily candles found for this stock. Run price ingestion, then reopen this company."
        )
        st.code(
            f"python scripts/fetch_prices.py --symbol {stock['symbol']} --years 15 --chunk-days 365 --sleep-seconds 1",
            language="powershell",
        )
        return

    price_df = pd.DataFrame(prices)
    price_df["price_datetime"] = pd.to_datetime(price_df["price_datetime"]).dt.date
    numeric_columns = ["open", "high", "low", "close", "adjusted_close", "volume"]
    for column in numeric_columns:
        if column in price_df.columns:
            price_df[column] = pd.to_numeric(price_df[column], errors="coerce")
    price_df = price_df.sort_values("price_datetime")

    latest = price_df.iloc[-1]
    metric_cols = st.columns(4)
    metric_cols[0].metric("Rows In DB", format_indian_number(len(price_df), decimals=0))
    metric_cols[1].metric("From", str(price_df["price_datetime"].iloc[0]))
    metric_cols[2].metric("To", str(price_df["price_datetime"].iloc[-1]))
    metric_cols[3].metric("Latest Close", format_inr(latest.get("close"), decimals=2))

    fig = go.Figure(
        data=[
            go.Candlestick(
                x=price_df["price_datetime"],
                open=price_df["open"],
                high=price_df["high"],
                low=price_df["low"],
                close=price_df["close"],
                name=stock["symbol"],
            )
        ]
    )
    fig.update_layout(
        title=f"{stock.get('company_name') or stock['symbol']} daily OHLC",
        height=460,
        xaxis_rangeslider_visible=False,
        margin={"l": 0, "r": 0, "t": 42, "b": 0},
    )
    st.plotly_chart(fig, use_container_width=True)

    render_strategy_playground(stock["id"])
    render_algo_findings(stock["id"])

    st.subheader("Stored daily candles")
    display_df = price_df[
        [
            "price_datetime",
            "open",
            "high",
            "low",
            "close",
            "adjusted_close",
            "volume",
            "source",
        ]
    ].rename(
        columns={
            "price_datetime": "date",
            "adjusted_close": "adjusted close",
        }
    )
    st.dataframe(display_df, use_container_width=True, hide_index=True)


def render_index_strip(indices: list[dict]) -> None:
    if not indices:
        st.info("Market index data is unavailable right now. Try refreshing after a minute.")
        return
    cards = []
    for quote in indices:
        css_class = _change_class(float(quote.get("change", 0)))
        arrow = "&uarr;" if float(quote.get("change", 0)) >= 0 else "&darr;"
        cards.append(
            f'<div class="index-card">'
            f'<div class="index-label">{escape(str(quote.get("label", "")))}</div>'
            f'<div class="index-price">{escape(_price_text(quote))}</div>'
            f'<div class="{css_class}">{arrow} {escape(_change_text(quote))}</div>'
            f"</div>"
        )
    st.markdown(f"<div class='index-grid'>{''.join(cards)}</div>", unsafe_allow_html=True)


def render_metric_grid(metrics: list[tuple[str, str, str | None]]) -> None:
    cards = []
    for label, value, delta in metrics:
        delta_class = "loss" if delta and delta.strip().startswith("-") else "gain"
        delta_html = f'<div class="{delta_class}">{escape(delta)}</div>' if delta else ""
        cards.append(
            f'<div class="index-card">'
            f'<div class="index-label">{escape(label)}</div>'
            f'<div class="index-price">{escape(value)}</div>'
            f"{delta_html}</div>"
        )
    st.markdown(f"<div class='index-grid'>{''.join(cards)}</div>", unsafe_allow_html=True)


def render_investments(performance: dict | None) -> None:
    if not performance:
        st.info("Select a portfolio to see your investments.")
        return
    total_return_amount = float(performance.get("realized_pnl", 0)) + float(performance.get("unrealized_pnl", 0))
    total_return_pct = float(performance.get("total_return_pct", 0))
    with st.container(border=True):
        st.caption("Current")
        render_metric_grid(
            [
                ("Portfolio value", format_inr(performance.get("total_value"), compact=True), None),
                (
                    "Total returns",
                    format_signed_inr(total_return_amount, compact=True),
                    format_pct(total_return_pct, signed=True),
                ),
                ("Invested", format_inr(performance.get("invested_value"), compact=True), None),
                ("Cash", format_inr(performance.get("cash_balance"), compact=True), None),
            ]
        )


def render_tools() -> None:
    tools = [
        ("IPO", "2 open"),
        ("Bonds", ""),
        ("ETFs", ""),
        ("Intraday Screener", ""),
        ("Stocks SIP", ""),
        ("Events calendar", ""),
        ("All Stocks screener", ""),
    ]
    with st.container(border=True):
        for name, badge in tools:
            badge_html = f" <span class='gain'>{escape(badge)}</span>" if badge else ""
            st.markdown(f"**+ {name}**{badge_html}", unsafe_allow_html=True)



overview = get("/market/overview") or {}
source = overview.get("source")
sync_status = render_market_sync_controls()
record_date = overview.get("record_date") or sync_status.get("record_date")
if source == "sample_fallback":
    st.warning("Live Yahoo Finance data is unavailable, so this Explore view is using sample market rows for layout.")
elif source == "database":
    universe = overview.get("movers_universe_count")
    if universe:
        st.caption(
            f"Top movers ranked across {universe:,} stocks using stored daily candles (latest close vs prior close)."
        )
    else:
        st.caption("Market overview is using stored daily price candles.")
elif source == "yfinance":
    st.caption("Market data source: Yahoo Finance.")
if record_date and not _is_sync_active(sync_status):
    st.caption(f"Price data through {record_date}")
render_index_strip(overview.get("indices", []))

if _selected_yahoo_symbol():
    render_selected_stock_history()
else:
    render_stock_search()
    movers_tab, all_stocks_tab, ranking_tab = st.tabs(
        ["Market movers", "All stocks", "Sequential rankings"]
    )
    with movers_tab:
        render_market_movers()
    with all_stocks_tab:
        render_all_stocks_performance()
    with ranking_tab:
        render_sequential_rankings()

    st.subheader("Your investments")
    portfolio = portfolio_select("dashboard_portfolio")
    performance = get(f"/portfolios/{portfolio['id']}/performance") if portfolio else None
    render_investments(performance)

    st.subheader("Products & Tools")
    render_tools()

    st.subheader("Trading Screens")
    with st.container(border=True):
        st.markdown("**Resistance breakouts**  \n<span class='muted'>Bullish</span> <span class='gain'>Live</span>", unsafe_allow_html=True)
        st.divider()
        st.markdown("**Volume breakouts**  \n<span class='muted'>High activity</span> <span class='gain'>Live</span>", unsafe_allow_html=True)

    if performance:
        st.subheader("Portfolio summary")
        render_metric_grid(
            [
                ("Total Value", format_inr(performance["total_value"], compact=True), None),
                ("Current Cash", format_inr(performance["cash_balance"], compact=True), None),
                ("Invested", format_inr(performance["invested_value"], compact=True), None),
                ("Market Value", format_inr(performance["market_value"], compact=True), None),
                ("Realized P&L", format_inr(performance["realized_pnl"], compact=True), None),
                ("Unrealized P&L", format_inr(performance["unrealized_pnl"], compact=True), None),
                ("Total Return", format_pct(performance["total_return_pct"]), None),
            ]
        )

        snapshots = performance.get("snapshots", [])
        if snapshots:
            snapshot_df = pd.DataFrame(snapshots)
            fig = px.line(snapshot_df, x="snapshot_date", y="total_value", title="Portfolio Value")
            st.plotly_chart(fig, use_container_width=True)

        holdings = performance.get("holdings", [])
        if holdings:
            holdings_df = pd.DataFrame(holdings)
            money_columns = [
                "average_buy_price",
                "total_invested",
                "current_price",
                "market_value",
                "realized_pnl",
                "unrealized_pnl",
            ]
            for column in money_columns:
                if column in holdings_df.columns:
                    holdings_df[column] = holdings_df[column].apply(lambda value: format_inr(value))
            if "return_pct" in holdings_df.columns:
                holdings_df["return_pct"] = holdings_df["return_pct"].apply(format_pct)
            st.dataframe(holdings_df, use_container_width=True, hide_index=True)
        else:
            st.info("No active holdings yet.")

log_page_load("Explore", PAGE_STARTED_AT)
