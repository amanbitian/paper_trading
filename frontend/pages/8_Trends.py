from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from api_client import format_indian_number, format_inr, format_pct, get, log_page_load, require_login, start_timer
from ui import empty_state, info_banner, load_global_css, page_header, status_badge


PAGE_STARTED_AT = start_timer()
PERIOD_OPTIONS = [
    ("Daily", "daily"),
    ("Weekly", "weekly"),
    ("Monthly", "monthly"),
    ("Quarterly", "quarterly"),
    ("6 Month", "six_month"),
    ("Annual", "annual"),
]

st.set_page_config(page_title="Trends", page_icon="TR", layout="wide")
load_global_css()
page_header(
    "Trends",
    "Market heatmaps by daily, weekly, monthly, quarterly, six-month, and annual change.",
    right_badge=status_badge("Heatmap", "info"),
)

if not require_login():
    st.stop()

st.caption(
    "Daily compares the latest stored candle with the previous stored trading candle. Other periods compare the latest candle with the closest stored candle on or before the calendar anchor."
)

st.session_state.setdefault("trend_period", "daily")

button_cols = st.columns(len(PERIOD_OPTIONS))
for index, (label, value) in enumerate(PERIOD_OPTIONS):
    button_type = "primary" if st.session_state.trend_period == value else "secondary"
    if button_cols[index].button(label, key=f"trend_period_{value}", type=button_type, use_container_width=True):
        st.session_state.trend_period = value
        st.rerun()

filter_payload = get("/market/trends/filters") or {}
market_options = filter_payload.get("markets") or [
    {"label": "All stocks", "value": "stocks"},
    {"label": "NSE stocks", "value": "nse"},
    {"label": "BSE stocks", "value": "bse"},
    {"label": "Index funds", "value": "index_funds"},
    {"label": "Commodities", "value": "commodities"},
]
industry_groups = ["All industries"] + list(filter_payload.get("industry_groups") or [])
nifty_index_options = filter_payload.get("nifty_indices") or [
    {"label": "NIFTY 50", "value": "nifty50"},
    {"label": "NIFTY 100", "value": "nifty100"},
    {"label": "NIFTY 200", "value": "nifty200"},
    {"label": "NIFTY 500", "value": "nifty500"},
    {"label": "NIFTY Bank", "value": "banknifty"},
    {"label": "NIFTY Financial Services", "value": "finnifty"},
    {"label": "NIFTY Midcap Select", "value": "midcpnifty"},
]
market_by_label = {option["label"]: option["value"] for option in market_options}
market_labels = list(market_by_label)
nifty_by_label = {option["label"]: option["value"] for option in nifty_index_options}
nifty_labels = list(nifty_by_label)
st.session_state.setdefault("trend_market", "stocks")
st.session_state.setdefault("trend_industry_group", "All industries")
st.session_state.setdefault("trend_nifty_index", "All indices")
st.session_state.setdefault("trend_stock_limit", 1000)
st.session_state.setdefault("trend_sort_by", "size")

filter_cols = st.columns([1, 1, 1, 1, 1])
current_market_label = next(
    (label for label, value in market_by_label.items() if value == st.session_state.trend_market),
    market_labels[0],
)
selected_market_label = filter_cols[0].selectbox(
    "Index",
    market_labels,
    index=market_labels.index(current_market_label),
    help="Choose the market universe for the treemap.",
)
selected_market = market_by_label[selected_market_label]
st.session_state.trend_market = selected_market

current_industry = (
    st.session_state.trend_industry_group
    if st.session_state.trend_industry_group in industry_groups
    else "All industries"
)
selected_industry_group = filter_cols[1].selectbox(
    "Industry",
    industry_groups,
    index=industry_groups.index(current_industry),
    help="Broad industry grouping derived from sector and industry metadata.",
)
st.session_state.trend_industry_group = selected_industry_group

nifty_filter_labels = ["All indices"] + nifty_labels
current_nifty_label = (
    st.session_state.trend_nifty_index
    if st.session_state.trend_nifty_index in nifty_filter_labels
    else "All indices"
)
selected_nifty_label = filter_cols[2].selectbox(
    "NIFTY Index",
    nifty_filter_labels,
    index=nifty_filter_labels.index(current_nifty_label),
    help="Filter stocks to NSE index constituents from the nse_csv_* tables.",
)
st.session_state.trend_nifty_index = selected_nifty_label

if selected_nifty_label != "All indices":
    selected_option = next(
        (option for option in nifty_index_options if option["label"] == selected_nifty_label),
        None,
    )
    max_stocks = int((selected_option or {}).get("constituent_count") or 50)
else:
    max_stocks = int(filter_payload.get("all_stocks_eligible_max") or 5000)

max_stocks = max(50, min(max_stocks, 5000))
default_limit = min(st.session_state.trend_stock_limit, max_stocks)
if default_limit < 50:
    default_limit = min(1000, max_stocks)

stock_limit = filter_cols[3].number_input(
    "Stocks to show",
    min_value=50,
    max_value=max_stocks,
    value=default_limit,
    step=50 if max_stocks >= 200 else 10,
    help=(
        "Caps treemap rows after sorting. Previously defaulted to 1000 largest by traded value "
        "(price x volume). Max adjusts to the selected NIFTY index size."
    ),
)
st.session_state.trend_stock_limit = int(stock_limit)

sort_options = filter_payload.get("sort_options") or [
    {"label": "Traded value (price x volume)", "value": "size"},
    {"label": "Market price", "value": "price"},
    {"label": "Volume", "value": "volume"},
    {"label": "Change %", "value": "change"},
]
sort_by_label = {option["label"]: option["value"] for option in sort_options}
sort_labels = list(sort_by_label)
current_sort_label = next(
    (label for label, value in sort_by_label.items() if value == st.session_state.trend_sort_by),
    sort_labels[0],
)
selected_sort_label = filter_cols[4].selectbox(
    "Sort by",
    sort_labels,
    index=sort_labels.index(current_sort_label),
    help="Pick which metric ranks stocks before applying the row cap.",
)
st.session_state.trend_sort_by = sort_by_label[selected_sort_label]

st.caption(
    "Returns use adjusted close when available to reduce split and bonus distortion. "
    "Treemap tile size still uses latest price x volume."
)

trend_params = {
    "period": st.session_state.trend_period,
    "limit": int(stock_limit),
    "market": selected_market,
    "sort_by": st.session_state.trend_sort_by,
}
if selected_industry_group != "All industries":
    trend_params["industry_group"] = selected_industry_group
if selected_nifty_label != "All indices":
    trend_params["nifty_index"] = nifty_by_label[selected_nifty_label]

trend_payload = get(
    "/market/trends",
    params=trend_params,
) or {}
items = trend_payload.get("items", [])

if not items:
    empty_state("No trend data", "Run daily price ingestion first.")
    st.code(
        "python scripts/fetch_prices.py --all --exchange NSE --incremental --sleep-seconds 1",
        language="powershell",
    )
    st.stop()

frame = pd.DataFrame(items)
frame["change_pct"] = pd.to_numeric(frame["change_pct"], errors="coerce")
frame["size_value"] = pd.to_numeric(frame["size_value"], errors="coerce").fillna(1).clip(lower=1)
frame["display_name"] = frame["company_name"].fillna(frame["symbol"])
if "market_bucket" not in frame.columns:
    frame["market_bucket"] = "Stocks"
if "industry_group" not in frame.columns:
    frame["industry_group"] = frame["sector"]
frame["market_bucket"] = frame["market_bucket"].fillna("Stocks")
frame["industry_group"] = frame["industry_group"].fillna("Unknown")
frame["change_label"] = frame["change_pct"].apply(lambda value: format_pct(value, signed=True))
frame["latest_price_label"] = frame["latest_price"].apply(lambda value: format_inr(value))
frame["baseline_price_label"] = frame["baseline_price"].apply(lambda value: format_inr(value))
frame["latest_date"] = pd.to_datetime(frame["latest_price_datetime"], errors="coerce").dt.date
frame["baseline_date"] = pd.to_datetime(frame["baseline_price_datetime"], errors="coerce").dt.date
frame["latest_date_label"] = frame["latest_date"].astype("string").fillna("-")
frame["baseline_date_label"] = frame["baseline_date"].astype("string").fillna("-")
frame["latest_return_price"] = pd.to_numeric(frame.get("latest_return_price"), errors="coerce")
frame["baseline_return_price"] = pd.to_numeric(frame.get("baseline_return_price"), errors="coerce")
frame["latest_return_price_label"] = frame["latest_return_price"].apply(lambda value: format_inr(value))
frame["baseline_return_price_label"] = frame["baseline_return_price"].apply(lambda value: format_inr(value))
color_cap = float(frame["change_pct"].abs().quantile(0.9))
color_cap = max(2.0, min(color_cap, 50.0))

metric_cols = st.columns(7)
metric_cols[0].metric("Period", trend_payload.get("period_label", st.session_state.trend_period.title()))
metric_cols[1].metric("Lookback", f"{trend_payload.get('lookback_days')} days")
metric_cols[2].metric("Index", trend_payload.get("market_label", selected_market_label))
metric_cols[3].metric("From", trend_payload.get("baseline_date") or "-")
metric_cols[4].metric("To", trend_payload.get("record_date") or "-")
metric_cols[5].metric("Rows", format_indian_number(len(frame), decimals=0))
metric_cols[6].metric("Median change", format_pct(frame["change_pct"].median(), signed=True))
eligible = trend_payload.get("universe_eligible_count")
if eligible is not None:
    st.caption(
        f"Showing top {len(frame):,} of {eligible:,} eligible instruments "
        f"(cap {int(stock_limit):,}, max {max_stocks:,}, sort: {selected_sort_label})."
    )
st.caption(f"Industry filter: {selected_industry_group}")
st.caption(f"Calculation basis: {trend_payload.get('calculation_basis', 'adjusted_close_when_available')}")

fig = px.treemap(
    frame,
    path=["market_bucket", "industry_group", "display_name"],
    values="size_value",
    color="change_pct",
    color_continuous_scale=[
        [0.0, "#6f1620"],
        [0.22, "#b73a43"],
        [0.44, "#e26767"],
        [0.5, "#2b3038"],
        [0.56, "#4f9f68"],
        [0.78, "#2e8b57"],
        [1.0, "#1f6f43"],
    ],
    range_color=(-float(color_cap), float(color_cap)),
    custom_data=[
        "symbol",
        "exchange",
        "latest_price_label",
        "baseline_price_label",
        "latest_return_price_label",
        "baseline_return_price_label",
        "change_label",
        "baseline_date_label",
        "latest_date_label",
    ],
)
fig.update_traces(
    texttemplate="<b>%{label}</b><br>%{customdata[6]}",
    hovertemplate=(
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
    marker_line_width=1,
    marker_line_color="rgba(5, 6, 7, 0.88)",
)
fig.update_layout(
    height=760,
    margin={"l": 0, "r": 0, "t": 10, "b": 0},
    paper_bgcolor="#050607",
    plot_bgcolor="#050607",
    font={"color": "#f4f7fb"},
    coloraxis_colorbar={"title": "Change %", "ticksuffix": "%"},
)
with st.container(border=True):
    st.plotly_chart(fig, use_container_width=True)

table = frame[
    [
        "instrument_type",
        "symbol",
        "exchange",
        "market_bucket",
        "company_name",
        "sector",
        "industry",
        "industry_group",
        "baseline_date",
        "latest_date",
        "latest_price",
        "baseline_price",
        "change_amount",
        "change_pct",
        "latest_volume",
    ]
].rename(
    columns={
        "instrument_type": "Type",
        "symbol": "Symbol",
        "exchange": "Exchange",
        "market_bucket": "Index",
        "company_name": "Company",
        "sector": "Sector",
        "industry": "Industry",
        "industry_group": "Industry group",
        "baseline_date": "From date",
        "latest_date": "To date",
        "baseline_price": "From price",
        "latest_price": "To price",
        "change_amount": "Change",
        "change_pct": "Change %",
        "latest_volume": "Volume",
    }
)
st.subheader("Trend rows")
table_sort_col = {
    "size": "Change %",
    "price": "To price",
    "volume": "Volume",
    "change": "Change %",
}.get(st.session_state.trend_sort_by, "Change %")
with st.container(border=True):
    st.dataframe(
        table.sort_values(table_sort_col, ascending=False),
        use_container_width=True,
        hide_index=True,
        column_config={
            "From price": st.column_config.NumberColumn("From price", format="%.2f"),
            "To price": st.column_config.NumberColumn("To price", format="%.2f"),
            "Change": st.column_config.NumberColumn("Change", format="%.2f"),
            "Change %": st.column_config.NumberColumn("Change %", format="%.2f%%"),
            "Volume": st.column_config.NumberColumn("Volume", format="%d"),
        },
    )

log_page_load("Trends", PAGE_STARTED_AT)
