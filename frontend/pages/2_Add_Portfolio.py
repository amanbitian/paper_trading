from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import streamlit as st

from api_client import format_inr, log_page_load, portfolio_select, post, require_login, search_stock_widget, start_timer
from ui import info_banner, load_global_css, metric_card, page_header, status_badge


PAGE_STARTED_AT = start_timer()
st.set_page_config(page_title="Add Portfolio", page_icon="AP", layout="wide")
load_global_css()
page_header(
    "Add Portfolio",
    "Create paper portfolios and record manual holdings without changing live brokerage accounts.",
    right_badge=status_badge("Educational", "info"),
)

if not require_login():
    st.stop()

with st.expander("Create Portfolio", expanded=False):
    with st.form("create_portfolio"):
        name = st.text_input("Portfolio name")
        portfolio_type = st.selectbox("Portfolio type", ["manual", "paper", "sip", "algo"])
        starting_value = st.number_input("Starting value", min_value=0.0, value=0.0, step=1000.0)
        metric_card("Starting value", format_inr(starting_value, compact=True))
        submitted = st.form_submit_button("Create")
    if submitted:
        result = post(
            "/portfolios",
            {
                "portfolio_name": name,
                "portfolio_type": portfolio_type,
                "base_currency": "INR",
                "starting_value": str(Decimal(str(starting_value))),
            },
        )
        if result:
            info_banner("Portfolio created.", "success")

st.subheader("Add Manual Holding")
with st.container(border=True):
    portfolio = portfolio_select("add_portfolio_select")
    stock = search_stock_widget("add_stock")

    with st.form("manual_buy_form"):
        col_qty, col_price, col_date, col_charges = st.columns(4)
        quantity = col_qty.number_input("Quantity", min_value=0.0, value=1.0, step=1.0)
        price = col_price.number_input("Buy price", min_value=0.0, value=100.0, step=1.0)
        purchase_date = col_date.date_input("Purchase date")
        charges = col_charges.number_input("Charges", min_value=0.0, value=0.0, step=1.0)
        metric_card("Estimated amount", format_inr(quantity * price + charges, compact=True))
        notes = st.text_area("Notes")
        submitted = st.form_submit_button("Add Buy")

if submitted:
    if not portfolio or not stock:
        st.error("Select a portfolio and stock first.")
    else:
        result = post(
            "/transactions/manual-buy",
            {
                "portfolio_id": portfolio["id"],
                "stock_id": stock["id"],
                "quantity": str(Decimal(str(quantity))),
                "price": str(Decimal(str(price))),
                "transaction_date": datetime.combine(purchase_date, datetime.min.time()).isoformat(),
                "charges": str(Decimal(str(charges))),
                "notes": notes,
            },
        )
        if result:
            info_banner("Manual holding added.", "success")

log_page_load("Add Portfolio", PAGE_STARTED_AT)
