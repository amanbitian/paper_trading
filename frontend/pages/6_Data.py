from __future__ import annotations

import pandas as pd
import streamlit as st

from api_client import (
    format_duration,
    format_indian_number,
    format_time_ago,
    get,
    log_page_load,
    require_login,
    start_timer,
)
from ui import empty_state, info_banner, load_global_css, page_header, status_badge


PAGE_STARTED_AT = start_timer()
st.set_page_config(page_title="Data", page_icon="DB", layout="wide")
load_global_css()
page_header(
    "Data Operations",
    "Database health, ingestion freshness, search latency, and market data coverage.",
    right_badge=status_badge("Read-only", "info"),
)


def format_ms(value) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):,.1f} ms"


if not require_login():
    st.stop()

dashboard = get("/data/ingestion-dashboard", params={"runs_limit": 30}) or {}
if not dashboard:
    info_banner("Could not load ingestion dashboard. Check that the backend is running.", "danger")
    st.stop()

st.caption(
    f"Snapshot as of {dashboard.get('as_of', '—')} · "
    "Use **Explore → Sync now** to fetch new prices; this page is read-only."
)

if dashboard.get("sync_is_running"):
    info_banner("A market sync is currently running. Refresh this page to see updated stats when it finishes.", "warning")

# --- Database identity ---
st.subheader("Database")
database_info = dashboard.get("database_info") or {}
db_cols = st.columns(4)
db_cols[0].metric("Database", database_info.get("database_name") or "N/A")
db_cols[1].metric("Schema", database_info.get("schema_name") or "N/A")
db_cols[2].metric("DB user", database_info.get("database_user") or "N/A")
db_cols[3].metric("Tables", format_indian_number(database_info.get("table_count") or 0, decimals=0))
st.caption(database_info.get("postgres_version") or "")

table_rows = database_info.get("tables") or []
if table_rows:
    with st.expander("Show database tables"):
        tables_df = pd.DataFrame(table_rows).rename(
            columns={
                "schema_name": "Schema",
                "table_name": "Table",
                "row_estimate": "Estimated rows",
            }
        )
        st.dataframe(tables_df, use_container_width=True, hide_index=True)

# --- Search response times ---
st.subheader("Search response times")
search_latency = dashboard.get("search_latency") or {}
latency_cols = st.columns(5)
latency_cols[0].metric(
    "Total searches",
    format_indian_number(search_latency.get("total_searches") or 0, decimals=0),
)
latency_cols[1].metric("Avg response", format_ms(search_latency.get("avg_response_ms")))
latency_cols[2].metric("P95 response", format_ms(search_latency.get("p95_response_ms")))
latency_cols[3].metric("Max response", format_ms(search_latency.get("max_response_ms")))
latency_cols[4].metric(
    "Latest search",
    format_time_ago(search_latency.get("latest_search_at"))
    if search_latency.get("latest_search_at")
    else "Never",
)

recent_searches = search_latency.get("recent_searches") or []
avg_searches = search_latency.get("average_by_query") or []
search_tabs = st.tabs(["Recent searches", "Average by query"])
with search_tabs[0]:
    if recent_searches:
        recent_df = pd.DataFrame(recent_searches)
        recent_df["duration"] = recent_df["duration_ms"].apply(format_ms)
        recent_df["filter"] = recent_df.apply(
            lambda row: (
                f"{row['filter_name']}={row['filter_value']}"
                if row.get("filter_name") and row.get("filter_value")
                else ""
            ),
            axis=1,
        )
        st.dataframe(
            recent_df[
                [
                    "created_at",
                    "search_type",
                    "query_text",
                    "filter",
                    "result_count",
                    "duration",
                    "status",
                ]
            ].rename(
                columns={
                    "created_at": "Time",
                    "search_type": "Search",
                    "query_text": "Query",
                    "filter": "Filter",
                    "result_count": "Results",
                    "duration": "Response time",
                    "status": "Status",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )
    else:
        empty_state("No recent searches", "Use stock or index search once, then refresh this dashboard.")

with search_tabs[1]:
    if avg_searches:
        average_df = pd.DataFrame(avg_searches)
        average_df["avg_response"] = average_df["avg_response_ms"].apply(format_ms)
        average_df["max_response"] = average_df["max_response_ms"].apply(format_ms)
        average_df["filter"] = average_df.apply(
            lambda row: (
                f"{row['filter_name']}={row['filter_value']}"
                if row.get("filter_name") and row.get("filter_value")
                else ""
            ),
            axis=1,
        )
        st.dataframe(
            average_df[
                [
                    "search_type",
                    "query_text",
                    "filter",
                    "search_count",
                    "avg_response",
                    "max_response",
                    "latest_search_at",
                ]
            ].rename(
                columns={
                    "search_type": "Search",
                    "query_text": "Query",
                    "filter": "Filter",
                    "search_count": "Count",
                    "avg_response": "Avg response",
                    "max_response": "Max response",
                    "latest_search_at": "Latest",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )
    else:
        empty_state("No response averages", "Average response timings will appear after a few searches.")

# --- Sync & freshness ---
st.subheader("Sync & freshness")
sync_cols = st.columns(4)
sync_cols[0].metric(
    "Last sync",
    format_time_ago(dashboard.get("last_synced_at")) if dashboard.get("last_synced_at") else "Never",
)
sync_cols[1].metric("Last sync status", dashboard.get("last_sync_status") or "—")
sync_cols[2].metric("Last sync duration", format_duration(dashboard.get("last_sync_duration_seconds")))
sync_cols[3].metric("Latest price date", str(dashboard.get("latest_price_date") or "—"))

detail_cols = st.columns(4)
detail_cols[0].metric("Sync mode", dashboard.get("last_sync_mode") or "—")
detail_cols[1].metric(
    "Symbols (ok / fail)",
    (
        f"{dashboard.get('last_sync_symbols_succeeded', 0):,} / "
        f"{dashboard.get('last_sync_symbols_failed', 0):,}"
        if dashboard.get("last_sync_symbols_attempted")
        else "—"
    ),
)
detail_cols[2].metric(
    "Rows saved (last sync)",
    format_indian_number(dashboard.get("last_sync_rows_saved") or 0, decimals=0),
)
detail_cols[3].metric(
    "Analytics refreshed",
    format_time_ago(dashboard.get("analytics_refreshed_at"))
    if dashboard.get("analytics_refreshed_at")
    else "—",
)

# --- Universe coverage ---
st.subheader("Universe & coverage")
cov_cols = st.columns(4)
cov_cols[0].metric("Active tickers", format_indian_number(dashboard.get("active_stocks"), decimals=0))
cov_cols[1].metric(
    "With daily prices",
    format_indian_number(dashboard.get("stocks_with_daily_prices"), decimals=0),
)
cov_cols[2].metric("Price coverage", f"{dashboard.get('price_coverage_pct', 0):.1f}%")
cov_cols[3].metric(
    "Movers universe",
    format_indian_number(dashboard.get("movers_universe_count") or 0, decimals=0),
)

data_cols = st.columns(4)
data_cols[0].metric(
    "Daily price rows",
    format_indian_number(dashboard.get("total_daily_price_rows"), decimals=0),
)
data_cols[1].metric("Earliest price date", str(dashboard.get("earliest_price_date") or "—"))
data_cols[2].metric("Latest price date", str(dashboard.get("latest_price_date") or "—"))
data_cols[3].metric(
    "Performance snapshots",
    format_indian_number(dashboard.get("performance_snapshots"), decimals=0),
)

meta_cols = st.columns(4)
meta_cols[0].metric("Total tickers (all)", format_indian_number(dashboard.get("total_stocks"), decimals=0))
meta_cols[1].metric(
    "Sector metadata",
    f"{format_indian_number(dashboard.get('stocks_with_sector'), decimals=0)} "
    f"({format_indian_number(dashboard.get('distinct_sectors'), decimals=0)} sectors)",
)
meta_cols[2].metric(
    "Industry metadata",
    f"{format_indian_number(dashboard.get('stocks_with_industry'), decimals=0)} "
    f"({format_indian_number(dashboard.get('distinct_industries'), decimals=0)} industries)",
)
meta_cols[3].metric("Sync running now", "Yes" if dashboard.get("sync_is_running") else "No")

# --- Exchange breakdown ---
st.subheader("Exchange breakdown")
exchange_rows = dashboard.get("exchange_breakdown") or []
if exchange_rows:
    exchange_df = pd.DataFrame(exchange_rows)
    exchange_df["coverage_pct"] = exchange_df.apply(
        lambda row: round(100.0 * row["stocks_with_prices"] / row["total_stocks"], 1)
        if row["total_stocks"]
        else 0.0,
        axis=1,
    )
    exchange_df = exchange_df.rename(
        columns={
            "exchange": "Exchange",
            "total_stocks": "Active tickers",
            "stocks_with_prices": "With daily prices",
            "coverage_pct": "Coverage %",
        }
    )
    st.dataframe(exchange_df, use_container_width=True, hide_index=True)
else:
    empty_state("No exchange breakdown", "Run ticker and price ingestion to populate exchange coverage.")

# --- Recent ingestion runs ---
st.subheader("Recent ingestion runs")
recent_runs = dashboard.get("recent_runs") or []
if recent_runs:
    runs_df = pd.DataFrame(recent_runs)
    runs_df["duration"] = runs_df["duration_seconds"].apply(format_duration)
    display_df = runs_df[
        [
            "id",
            "started_at",
            "finished_at",
            "duration",
            "status",
            "ingestion_mode",
            "exchange",
            "total_symbols",
            "success_count",
            "failed_count",
            "rows_saved",
        ]
    ].rename(
        columns={
            "id": "Run ID",
            "started_at": "Started",
            "finished_at": "Finished",
            "duration": "Duration",
            "status": "Status",
            "ingestion_mode": "Mode",
            "exchange": "Exchange",
            "total_symbols": "Symbols",
            "success_count": "OK",
            "failed_count": "Failed",
            "rows_saved": "Rows saved",
        }
    )
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    with st.expander("Error details (latest runs)"):
        for run in recent_runs[:10]:
            if run.get("error_message"):
                st.markdown(f"**Run #{run['id']}** ({run.get('status')})")
                st.code(run["error_message"][:2000])
else:
    empty_state("No ingestion runs", "Run an ingestion script to populate operational run history.")

if st.button("Refresh dashboard"):
    st.rerun()

log_page_load("Data", PAGE_STARTED_AT)
