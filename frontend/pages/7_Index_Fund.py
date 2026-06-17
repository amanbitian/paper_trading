from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from api_client import (
    format_compact_indian_number,
    format_inr,
    get,
    get_index_fund_suggestions,
    log_page_load,
    post,
    require_login,
    start_timer,
)
from ui import empty_state, info_banner, load_global_css, metric_grid, page_header, status_badge


PAGE_STARTED_AT = start_timer()
PRICE_HISTORY_LIMIT = 10000

st.set_page_config(page_title="Index Fund", page_icon="IF", layout="wide")
load_global_css()
page_header(
    "Index Fund",
    "Explore stored index and commodity instruments, compare returns, and review strategy context.",
    right_badge=status_badge("Indexes", "info"),
)

if not require_login():
    st.stop()


def _display_price(value, currency: str = "INR") -> str:
    if value is None or pd.isna(value):
        return "-"
    if currency == "INR":
        return format_inr(value)
    return f"{float(value):,.2f} {currency}"


def _edit_parameter(name: str, value, key_prefix: str):
    label = name.replace("_", " ").title()
    if isinstance(value, bool):
        return st.checkbox(label, value=bool(value), key=f"{key_prefix}_{name}")
    if isinstance(value, int) and not isinstance(value, bool):
        return int(
            st.number_input(
                label,
                min_value=1,
                max_value=10000,
                value=int(value),
                step=1,
                key=f"{key_prefix}_{name}",
            )
        )
    if isinstance(value, float):
        return float(
            st.number_input(
                label,
                min_value=-1000.0,
                max_value=1000.0,
                value=float(value),
                step=0.05,
                key=f"{key_prefix}_{name}",
            )
        )
    return st.text_input(label, value=str(value), key=f"{key_prefix}_{name}")


def _load_performance() -> list[dict]:
    return get("/index-funds/performance", params={"limit": 5000}) or []


def render_index_table(performance_rows: list[dict]) -> None:
    st.subheader("All index and commodity instruments")
    if not performance_rows:
        empty_state("No index instruments found", "Load the CSV first, then run price ingestion.")
        st.code(
            "python scripts/load_index_funds.py --csv-path data/indexes_commodities_prepared.csv\n"
            "python scripts/ingest_index_funds.py --start-date 2010-01-01 --chunk-days 365 --sleep-seconds 1",
            language="powershell",
        )
        return

    frame = pd.DataFrame(performance_rows)
    display = frame[
        [
            "symbol",
            "yahoo_symbol",
            "category",
            "base_currency",
            "latest_price_datetime",
            "latest_price",
            "change_1m_pct",
            "change_3m_pct",
            "change_6m_pct",
            "change_1y_pct",
            "latest_volume",
        ]
    ].rename(
        columns={
            "symbol": "Symbol",
            "yahoo_symbol": "Yahoo ticker",
            "category": "Category",
            "base_currency": "Currency",
            "latest_price_datetime": "Latest date",
            "latest_price": "Latest price",
            "change_1m_pct": "1M change",
            "change_3m_pct": "3M change",
            "change_6m_pct": "6M change",
            "change_1y_pct": "1Y change",
            "latest_volume": "Volume",
        }
    )
    with st.container(border=True):
        st.dataframe(
            display,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Latest price": st.column_config.NumberColumn("Latest price", format="%.2f"),
                "1M change": st.column_config.NumberColumn("1M change", format="%.2f%%"),
                "3M change": st.column_config.NumberColumn("3M change", format="%.2f%%"),
                "6M change": st.column_config.NumberColumn("6M change", format="%.2f%%"),
                "1Y change": st.column_config.NumberColumn("1Y change", format="%.2f%%"),
                "Volume": st.column_config.NumberColumn("Volume", format="%d"),
            },
        )


def render_return_plot(performance_rows: list[dict]) -> None:
    st.subheader("Return comparison")
    if not performance_rows:
        empty_state("No index data", "Load index funds before plotting returns.")
        return

    labels = {
        f"{row['symbol']} [{row['yahoo_symbol']}]": row
        for row in sorted(performance_rows, key=lambda item: item["symbol"])
    }
    selected_labels = st.multiselect(
        "Select indexes or commodities",
        list(labels.keys()),
        default=list(labels.keys())[: min(3, len(labels))],
        help="Each selected instrument is normalized to 0% at the selected start date.",
    )
    col_start, col_end = st.columns(2)
    start_date = col_start.date_input("Start date", date.today() - timedelta(days=365 * 5))
    end_date = col_end.date_input("End date", date.today())

    if st.button("Generate return plot"):
        selected_ids = [labels[label]["id"] for label in selected_labels]
        if not selected_ids:
            st.error("Select at least one index.")
            return
        series = get(
            "/index-funds/returns",
            params={"ids": selected_ids, "start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        ) or []
        rows = []
        for item in series:
            for point in item.get("points", []):
                rows.append(
                    {
                        "date": point["date"],
                        "return_pct": point["return_pct"],
                        "close": point["close"],
                        "instrument": f"{item['symbol']} [{item['yahoo_symbol']}]",
                    }
                )
        if not rows:
            empty_state("No stored price data", "No stored price data found for the selected period.")
            return
        plot_df = pd.DataFrame(rows)
        fig = px.line(
            plot_df,
            x="date",
            y="return_pct",
            color="instrument",
            title="Return comparison",
            labels={"return_pct": "Return %", "date": "Date", "instrument": "Instrument"},
        )
        fig.update_layout(height=520, margin={"l": 0, "r": 0, "t": 42, "b": 0})
        st.plotly_chart(fig, use_container_width=True)
        with st.container(border=True):
            st.dataframe(plot_df, use_container_width=True, hide_index=True)


def render_strategy_preview(index_fund: dict) -> None:
    st.subheader("Strategy preview")
    templates = get("/strategies/templates") or []
    if not templates:
        empty_state("No strategy templates", "Seed strategy templates before previewing strategies.")
        return

    options = {template["strategy_name"]: template for template in templates}
    template_name = st.selectbox("Strategy", list(options.keys()), key=f"index_strategy_{index_fund['id']}")
    template = options[template_name]
    parameters = dict(template.get("default_parameters") or {})
    advanced = st.toggle("Advanced user", key=f"index_strategy_advanced_{index_fund['id']}_{template['id']}")

    with st.form(f"index_strategy_form_{index_fund['id']}_{template['id']}"):
        if advanced:
            columns = st.columns(3)
            for index, (name, value) in enumerate(parameters.items()):
                with columns[index % 3]:
                    parameters[name] = _edit_parameter(
                        name,
                        value,
                        f"index_strategy_param_{index_fund['id']}_{template['id']}",
                    )
        else:
            st.caption("Using default parameters. Enable Advanced user to edit them.")
            st.json(parameters, expanded=False)
        submitted = st.form_submit_button("Preview strategy")

    if not submitted:
        return
    preview = post(
        "/strategies/preview-signal",
        {
            "instrument_type": "index_fund",
            "index_fund_id": index_fund["id"],
            "strategy_template_id": template["id"],
            "parameters": parameters,
        },
    )
    if not preview:
        return
    metric_grid(
        [
            {"label": "Signal", "value": preview["signal_type"]},
            {"label": "Confidence", "value": f"{float(preview['confidence_score']):.2f}"},
            {
                "label": "Latest price",
                "value": _display_price(preview.get("suggested_price"), index_fund.get("base_currency", "INR")),
            },
            {"label": "Strategy", "value": preview["strategy_name"]},
        ],
        columns=4,
    )
    st.write(preview.get("reason") or "")
    if preview.get("indicators"):
        st.json(preview["indicators"])


def render_algo_findings(index_fund_id: int) -> None:
    findings = get(f"/index-funds/{index_fund_id}/algo-findings", params={"limit": PRICE_HISTORY_LIMIT}) or []
    st.subheader("Algorithm findings")
    if not findings:
        empty_state("No algorithm findings", "No algorithm findings are available yet.")
        return
    with st.container(border=True):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Algorithm": item["algorithm_name"],
                        "Category": item["category"],
                        "Action": item["action"],
                        "Confidence": float(item["confidence_score"]),
                        "Status": item["status"],
                        "Reason": item["reason"],
                    }
                    for item in findings
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
    for item in findings:
        with st.expander(f"{item['algorithm_name']} | {item['action']} | {float(item['confidence_score']):.2f}"):
            st.markdown(f"**Logic:** {item['logic']}")
            st.markdown(f"**Data needed:** {item['data_requirements']}")
            st.markdown(f"**Finding:** {item['reason']}")
            if item.get("indicators"):
                st.json(item["indicators"])
            chart = item.get("chart")
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
                fig.update_layout(height=360, title=chart.get("title"), margin={"l": 0, "r": 0, "t": 42, "b": 0})
                st.plotly_chart(fig, use_container_width=True)


def render_single_index_history() -> None:
    st.subheader("Open one index")
    query = st.text_input("Search index", placeholder="Try NIFTY 50, BANK, IT, GOLD...")
    suggestions = get_index_fund_suggestions(query, limit=25) if query.strip() else []
    if not suggestions:
        st.caption("Search to open stored history, strategy previews, and algorithm findings.")
        return

    labels = {f"{item['symbol']} [{item['yahoo_symbol']}]": item for item in suggestions}
    selected_label = st.selectbox("Select index", list(labels.keys()))
    index_fund = labels[selected_label]

    prices = get(
        f"/index-funds/{index_fund['id']}/prices",
        params={"timeframe": "1d", "limit": PRICE_HISTORY_LIMIT},
    ) or []
    if not prices:
        empty_state("No daily candles", "No stored daily candles found for this index. Run index ingestion first.")
        st.code(
            f"python scripts/ingest_index_funds.py --start-date 2010-01-01 --limit 1 --chunk-days 365 --sleep-seconds 1",
            language="powershell",
        )
        return

    price_df = pd.DataFrame(prices)
    price_df["price_datetime"] = pd.to_datetime(price_df["price_datetime"]).dt.date
    for column in ["open", "high", "low", "close", "adjusted_close", "volume"]:
        if column in price_df.columns:
            price_df[column] = pd.to_numeric(price_df[column], errors="coerce")
    price_df = price_df.sort_values("price_datetime")
    latest = price_df.iloc[-1]

    metric_grid(
        [
            {"label": "Rows in DB", "value": format_compact_indian_number(len(price_df), decimals=0)},
            {"label": "From", "value": str(price_df["price_datetime"].iloc[0])},
            {"label": "To", "value": str(price_df["price_datetime"].iloc[-1])},
            {"label": "Latest close", "value": _display_price(latest.get("close"), index_fund.get("base_currency", "INR"))},
        ],
        columns=4,
    )

    fig = go.Figure(
        data=[
            go.Candlestick(
                x=price_df["price_datetime"],
                open=price_df["open"],
                high=price_df["high"],
                low=price_df["low"],
                close=price_df["close"],
                name=index_fund["symbol"],
            )
        ]
    )
    fig.update_layout(
        title=f"{index_fund['symbol']} daily OHLC",
        height=500,
        xaxis_rangeslider_visible=False,
        margin={"l": 0, "r": 0, "t": 42, "b": 0},
    )
    with st.container(border=True):
        st.plotly_chart(fig, use_container_width=True)

    render_strategy_preview(index_fund)
    render_algo_findings(index_fund["id"])

    st.subheader("Stored daily candles")
    with st.container(border=True):
        st.dataframe(
            price_df.rename(columns={"price_datetime": "date", "adjusted_close": "adjusted close"}),
            use_container_width=True,
            hide_index=True,
        )


performance_rows = _load_performance()
priced_count = sum(1 for row in performance_rows if row.get("latest_price") is not None)
metric_grid(
    [
        {"label": "Items in DB", "value": len(performance_rows)},
        {"label": "With prices", "value": priced_count, "status": "success"},
        {"label": "Without prices", "value": max(0, len(performance_rows) - priced_count), "status": "warning"},
    ],
    columns=3,
)

table_tab, plot_tab, history_tab = st.tabs(["Index universe", "Return plots", "History & strategy"])
with table_tab:
    render_index_table(performance_rows)
with plot_tab:
    render_return_plot(performance_rows)
with history_tab:
    render_single_index_history()

log_page_load("Index Fund", PAGE_STARTED_AT)
