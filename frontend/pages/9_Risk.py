from __future__ import annotations

import streamlit as st

from api_client import format_inr, format_pct, get, log_page_load, portfolio_select, require_login, start_timer
from ui import empty_state, info_banner, load_global_css, metric_grid, page_header, status_badge


PAGE_STARTED_AT = start_timer()
st.set_page_config(page_title="Risk Dashboard", page_icon="RD", layout="wide")
load_global_css()
page_header(
    "Risk Dashboard",
    "Monitor portfolio risk, concentration, drawdown, and benchmark sensitivity.",
    right_badge=status_badge("Portfolio risk", "warning"),
)

if not require_login():
    st.stop()

portfolio = portfolio_select("risk_portfolio")
if not portfolio:
    empty_state("No portfolio selected", "Create a portfolio first, then return here to review risk metrics.")
    st.stop()

lookback = st.slider("Lookback days", 90, 400, 252)
if st.button("Refresh risk metrics"):
    st.session_state.pop("risk_metrics", None)

metrics = st.session_state.get("risk_metrics")
if metrics is None:
    metrics = get(f"/portfolios/{portfolio['id']}/risk-metrics", params={"lookback_days": lookback})
    if metrics:
        st.session_state.risk_metrics = metrics

if not metrics:
    info_banner("Risk metrics unavailable. Ensure portfolio snapshots and NIFTY 50 index prices exist.", "warning")
    st.stop()

st.caption(f"Last computed: {metrics.get('refreshed_at', '-')}")

beta = metrics.get("beta", {})
var = metrics.get("var", {})
conc = metrics.get("concentration", {})
dd = metrics.get("drawdown", {})

c1, c2 = st.columns(2)
with c1:
    st.subheader("Portfolio Beta")
    if beta.get("portfolio_beta") is not None:
        metric_grid([{"label": "Beta vs NIFTY 50", "value": f"{beta['portfolio_beta']:.2f}"}], columns=1)
        st.caption(beta.get("interpretation", ""))
    else:
        info_banner(beta.get("interpretation", "Insufficient data"), "warning")
with c2:
    st.subheader("Value at Risk (95%, 1-day)")
    if var.get("var_95_1d") is not None:
        metric_grid(
            [
                {
                    "label": "VaR",
                    "value": format_inr(var["var_95_1d"]),
                    "delta": f"{var.get('var_95_1d_pct', 0):.2f}% of portfolio",
                    "status": "warning",
                }
            ],
            columns=1,
        )
        st.caption("Historical method")
    else:
        info_banner("Need at least 30 daily snapshots.", "warning")

c3, c4 = st.columns(2)
with c3:
    st.subheader("Concentration")
    metric_grid(
        [
            {"label": "Top holding", "value": f"{conc.get('top_holding_pct', 0):.1f}%"},
            {"label": "Top 3", "value": f"{conc.get('top_3_pct', 0):.1f}%"},
            {"label": "HHI", "value": f"{conc.get('hhi', 0):.0f}", "delta": conc.get("concentration_level", "")},
        ],
        columns=1,
    )
with c4:
    st.subheader("Max Drawdown vs NIFTY 50")
    if dd.get("portfolio_max_drawdown_pct") is not None:
        drawdown_cards = [{"label": "Portfolio", "value": format_pct(-dd["portfolio_max_drawdown_pct"])}]
        if dd.get("benchmark_max_drawdown_pct") is not None:
            drawdown_cards.append({"label": "NIFTY 50", "value": format_pct(-dd["benchmark_max_drawdown_pct"])})
        metric_grid(drawdown_cards, columns=1)
    else:
        info_banner("Insufficient snapshot history.", "warning")

if conc.get("holdings"):
    st.subheader("Holdings by weight")
    with st.container(border=True):
        st.dataframe(conc["holdings"], use_container_width=True, hide_index=True)

log_page_load("Risk Dashboard", PAGE_STARTED_AT)
