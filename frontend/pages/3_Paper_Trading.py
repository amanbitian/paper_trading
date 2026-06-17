from __future__ import annotations

from decimal import Decimal

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from api_client import (
    format_inr,
    get,
    log_page_load,
    portfolio_select,
    post,
    require_login,
    search_stock_widget,
    start_timer,
)
from ui import empty_state, info_banner, load_global_css, metric_card, page_header, status_badge


PAGE_STARTED_AT = start_timer()
st.set_page_config(page_title="Paper Trading", page_icon="PT", layout="wide")
load_global_css()
page_header(
    "Paper Trading",
    "Place simulated orders against stored market data. No real trades are sent.",
    right_badge=status_badge("Paper only", "warning"),
)

if not require_login():
    st.stop()

with st.container(border=True):
    st.subheader("Instrument")
    portfolio = portfolio_select("paper_portfolio")
    stock = search_stock_widget("paper_stock")

latest_price = None
if stock:
    col_sync, col_info = st.columns([1, 3])
    if col_sync.button("Sync Prices"):
        post(
            f"/stocks/{stock['id']}/sync-prices",
            params={"period": "1y", "interval": "1d", "incremental": True},
        )
    prices = get(f"/stocks/{stock['id']}/prices", params={"limit": 250}) or []
    if prices:
        df = pd.DataFrame(prices)
        latest_price = float(df.iloc[-1]["close"])
        with col_info:
            metric_card("Latest Close", format_inr(latest_price))
        fig = go.Figure(
            data=[
                go.Candlestick(
                    x=df["price_datetime"],
                    open=df["open"],
                    high=df["high"],
                    low=df["low"],
                    close=df["close"],
                )
            ]
        )
        fig.update_layout(title=f"{stock['yahoo_symbol']} Price", xaxis_rangeslider_visible=False)
        with st.container(border=True):
            st.plotly_chart(fig, use_container_width=True)
    else:
        empty_state("No synced price history", "Sync prices to see a chart and place market orders.")

st.subheader("Order Ticket")
with st.container(border=True):
    with st.form("paper_order_form"):
        side = st.radio("Side", ["BUY", "SELL"], horizontal=True)
        col_type, col_qty = st.columns(2)
        order_type = col_type.selectbox("Order type", ["MARKET", "LIMIT", "STOP_LOSS"])
        quantity = col_qty.number_input("Quantity", min_value=0.0, value=1.0, step=1.0)
        limit_price = None
        stop_price = None
        if order_type == "LIMIT":
            limit_price = st.number_input("Limit price", min_value=0.0, value=float(latest_price or 0), step=1.0)
        if order_type == "STOP_LOSS":
            stop_price = st.number_input("Stop price", min_value=0.0, value=float(latest_price or 0), step=1.0)
        estimated = (latest_price or 0) * quantity
        metric_card("Estimated order value", format_inr(estimated, compact=True))
        submitted = st.form_submit_button("Submit Paper Order")

if submitted:
    if not portfolio or not stock:
        st.error("Select a portfolio and stock first.")
    else:
        payload = {
            "portfolio_id": portfolio["id"],
            "stock_id": stock["id"],
            "order_type": order_type,
            "side": side,
            "quantity": str(Decimal(str(quantity))),
            "limit_price": str(Decimal(str(limit_price))) if limit_price else None,
            "stop_price": str(Decimal(str(stop_price))) if stop_price else None,
        }
        result = post("/paper-orders", payload)
        if result:
            info_banner(f"Order {result['status']}: {result.get('reason') or ''}", "success")

orders = get("/paper-orders", params={"portfolio_id": portfolio["id"]}) if portfolio else []
if orders:
    st.subheader("Recent Orders")
    with st.container(border=True):
        st.dataframe(pd.DataFrame(orders), use_container_width=True, hide_index=True)

log_page_load("Paper Trading", PAGE_STARTED_AT)
