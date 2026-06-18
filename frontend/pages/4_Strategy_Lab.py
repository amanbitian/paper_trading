from __future__ import annotations

import json

import pandas as pd
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
from ui import empty_state, info_banner, load_global_css, metric_grid, page_header, status_badge


PAGE_STARTED_AT = start_timer()
st.set_page_config(page_title="Strategy Lab", page_icon="SL", layout="wide")
load_global_css()
page_header(
    "Strategy Lab",
    "Configure strategy templates, generate educational signals, and optionally convert them into paper orders.",
    right_badge=status_badge("Educational", "info"),
)

if not require_login():
    st.stop()

templates = get("/strategies/templates") or []
if not templates:
    empty_state("No strategy templates", "Seed strategy templates before using the lab.")
    st.stop()

with st.container(border=True):
    portfolio = portfolio_select("strategy_portfolio")
    stock = search_stock_widget("strategy_stock")

template_labels = {item["strategy_name"]: item for item in templates}
with st.container(border=True):
    st.subheader("Strategy parameters")
    template_name = st.selectbox("Strategy", list(template_labels.keys()))
    template = template_labels[template_name]
    default_json = json.dumps(template["default_parameters"], indent=2)
    parameters_text = st.text_area("Parameters", value=default_json, height=220)
    risk_per_trade = st.number_input("Risk per trade %", min_value=0.1, max_value=10.0, value=1.0, step=0.1)

col_create, col_signal = st.columns(2)
if col_create.button("Create User Strategy"):
    if not portfolio:
        st.error("Select a portfolio.")
    else:
        try:
            params = json.loads(parameters_text)
        except json.JSONDecodeError as exc:
            st.error(f"Invalid JSON: {exc}")
            st.stop()
        result = post(
            "/strategies/user-strategy",
            {
                "portfolio_id": portfolio["id"],
                "strategy_template_id": template["id"],
                "strategy_name": template["strategy_name"],
                "parameters": params,
                "risk_settings": {"risk_per_trade_pct": risk_per_trade},
                "is_enabled": True,
            },
        )
        if result:
            info_banner("User strategy created.", "success")

user_strategies = get("/strategies/user-strategy") or []
strategy_options = {
    f"{item['strategy_name']} #{item['id']}": item for item in user_strategies if not portfolio or item["portfolio_id"] == portfolio["id"]
}

if strategy_options:
    selected_label = st.selectbox("User strategy", list(strategy_options.keys()))
    selected_strategy = strategy_options[selected_label]
    if col_signal.button("Generate Signal"):
        if not stock:
            st.error("Select a stock.")
        else:
            signal = post(
                "/strategies/generate-signal",
                {"user_strategy_id": selected_strategy["id"], "stock_id": stock["id"]},
            )
            if signal:
                st.session_state.last_signal_id = signal["id"]
                st.subheader(signal["signal_type"])
                metric_grid(
                    [
                        {"label": "Confidence", "value": f"{float(signal['confidence_score']):.2f}"},
                        {"label": "Suggested Qty", "value": f"{float(signal['suggested_quantity']):.0f}"},
                        {"label": "Suggested Price", "value": format_inr(signal["suggested_price"] or 0)},
                    ],
                    columns=3,
                )
                st.write(signal["reason"])
                st.json(signal["indicators"])
else:
    empty_state("No user strategy yet", "Create a user strategy to generate signals.")

if st.session_state.get("last_signal_id") and st.button("Execute Last Signal as Paper Order"):
    order = post(f"/strategies/signals/{st.session_state.last_signal_id}/execute-paper-order")
    if order:
        info_banner(f"Order {order['status']}: {order.get('reason') or ''}", "success")

signals = get("/strategies/signals") or []
if signals:
    st.subheader("Recent Signals")
    with st.container(border=True):
        st.dataframe(pd.DataFrame(signals), use_container_width=True, hide_index=True)

log_page_load("Strategy Lab", PAGE_STARTED_AT)
