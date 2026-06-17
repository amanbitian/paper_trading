from __future__ import annotations

import pandas as pd
import streamlit as st

from api_client import (
    get,
    log_page_load,
    log_think_tank_action,
    portfolio_select,
    post,
    require_login,
    search_stock_widget,
    start_timer,
)
from ui import empty_state, info_banner, load_global_css, metric_grid, page_header, status_badge

PAGE_STARTED_AT = start_timer()
st.set_page_config(page_title="AI Think Tank", page_icon="🧠", layout="wide")
load_global_css()
page_header(
    "AI Think Tank",
    "Educational AI analysis powered by local Ollama models. Not financial advice, trade recommendations, or price predictions.",
    right_badge=status_badge("Local LLM", "info"),
)

if not require_login():
    st.stop()

status = get("/ai/status", show_error=False) or {}
if not status.get("ai_features_enabled"):
    info_banner(
        "AI features are disabled. Set `AI_FEATURES_ENABLED=true` in `backend/.env` "
        "and restart the API.",
        "warning",
    )
    st.stop()

if not status.get("available"):
    st.warning(
        """
        **Ollama not detected** — AI Think Tank requires Ollama running locally.

        Setup:
        1. Install: https://ollama.com/download
        2. Pull model: `ollama pull qwen3:14b`
        3. Start: `ollama serve`

        With an RTX 4080 (16 GB), **qwen3:14b** is recommended (~9 GB VRAM).
        Alternatively: `ollama pull qwen3:8b` for faster responses.

        If the backend runs in Docker, set `OLLAMA_BASE_URL=http://host.docker.internal:11434`.
        """
    )
    st.stop()

available_models = status.get("models") or [status.get("default_model", "qwen3:14b")]
default_model = status.get("default_model", "qwen3:14b")
if "ai_model" not in st.session_state:
    st.session_state.ai_model = default_model if default_model in available_models else available_models[0]

with st.sidebar:
    st.subheader("AI Settings")
    if available_models:
        st.session_state.ai_model = st.selectbox(
            "Model",
            available_models,
            index=available_models.index(st.session_state.ai_model)
            if st.session_state.ai_model in available_models
            else 0,
        )
    st.caption("RTX 4080 recommendations:")
    st.caption("• qwen3:14b — best quality (~9 GB)")
    st.caption("• qwen3:8b — fastest (~5 GB)")
    st.caption("• qwen3:32b:q4_K_M — highest quality (~10 GB)")

model_param = {"model": st.session_state.ai_model}
DISCLAIMER = (
    "Educational analysis only. Not financial advice, not a trade recommendation, "
    "and not a future price prediction."
)


def _show_error(result: dict | None) -> bool:
    if not result:
        info_banner("No response from AI service.", "danger")
        return True
    if result.get("error"):
        info_banner(result["error"], "danger")
        return True
    return False


def _show_disclaimer(result: dict) -> None:
    disclaimer = result.get("disclaimer") or DISCLAIMER
    st.caption(disclaimer)


(
    tab_signal,
    tab_backtest,
    tab_trade,
    tab_screener,
    tab_portfolio,
    tab_journal,
    tab_logs,
) = st.tabs(
    [
        "Signal Synthesizer",
        "Backtest Interpreter",
        "Pre-Trade Advisor",
        "NL Screener",
        "Portfolio Health",
        "Journal Insights",
        "Activity Log",
    ]
)

with tab_signal:
    st.subheader("Signal Synthesizer")
    stock = search_stock_widget("think_tank_signal")
    if stock:
        findings = get(f"/stocks/{stock['id']}/algo-findings", params={"limit": 15}) or []
        with st.expander("Raw algo findings", expanded=False):
            if findings:
                with st.container(border=True):
                    st.dataframe(pd.DataFrame(findings), use_container_width=True, hide_index=True)
            else:
                empty_state("No findings yet", "No algorithm findings exist for this stock yet.")
        if st.button("Synthesise Signals", type="primary"):
            log_think_tank_action("synthesize_signals", symbol=stock["symbol"])
            payload = {
                "symbol": stock["symbol"],
                "findings": findings,
                "model": st.session_state.ai_model,
            }
            result = post("/ai/synthesize-signals", payload, show_error=False)
            if not _show_error(result):
                st.session_state.signal_synthesis = result
        result = st.session_state.get("signal_synthesis")
        if result and not result.get("error"):
            consensus = result.get("consensus", "NEUTRAL")
            color = {"BULLISH": "green", "BEARISH": "red", "MIXED": "orange"}.get(consensus, "gray")
            st.markdown(f"### {result.get('headline', '')}")
            st.markdown(
                f":{color}[**{consensus}**] — strength {result.get('consensus_strength', 0)}/100"
            )
            st.write(result.get("summary", ""))
            st.info(f"**Key risk:** {result.get('key_risk', '—')}")
            info_banner(result.get("educational_note", ""), "success")
            st.write(result.get("agreement_note", ""))
            _show_disclaimer(result)

with tab_backtest:
    st.subheader("Backtest Interpreter")
    runs = get("/ai/backtest-runs") or []
    if not runs:
        empty_state("No backtest runs", "Run a backtest on the Backtesting page first.")
    else:
        labels = [
            f"#{r['id']} {r.get('symbol') or '—'} "
            f"({r.get('total_return_pct', 0):.1f}% / Sharpe {r.get('sharpe_ratio', 0):.2f})"
            for r in runs
        ]
        choice = st.selectbox("Select backtest run", range(len(labels)), format_func=lambda i: labels[i])
        run = runs[choice]
        if st.button("Interpret Results", type="primary"):
            log_think_tank_action("interpret_backtest", backtest_id=run["id"])
            result = post(
                "/ai/interpret-backtest",
                {"backtest_id": run["id"], "model": st.session_state.ai_model},
                show_error=False,
            )
            if not _show_error(result):
                st.session_state.backtest_interp = result
        result = st.session_state.get("backtest_interp")
        if result and not result.get("error"):
            verdict = result.get("verdict", "WEAK")
            vcolor = {
                "STRONG": "green",
                "ACCEPTABLE": "blue",
                "WEAK": "orange",
                "OVERFIT": "red",
            }.get(verdict, "gray")
            st.markdown(f":{vcolor}[**{verdict}**] — {result.get('headline', '')}")
            st.write(result.get("interpretation", ""))
            for flag in result.get("red_flags") or []:
                info_banner(flag, "warning")
            info_banner(result.get("improvement_tip", ""), "info")
            _show_disclaimer(result)
        with st.expander("Raw metrics"):
            st.json(run)

with tab_trade:
    st.warning(
        "Pre-trade check is **educational only** — it does not approve or reject trades. "
        + DISCLAIMER
    )
    st.subheader("Pre-Trade Advisor")
    portfolio = portfolio_select("think_tank_trade")
    stock = search_stock_widget("think_tank_trade_stock")
    if portfolio and stock:
        c1, c2, c3 = st.columns(3)
        with c1:
            action = st.selectbox("Action", ["BUY", "SELL"])
        with c2:
            quantity = st.number_input("Quantity", min_value=1, value=1, step=1)
        with c3:
            price = st.number_input("Price (INR)", min_value=0.01, value=float(stock.get("latest_price") or 100.0))
        notes = st.text_area("Your reasoning", placeholder="Why are you taking this trade?")
        if st.button("Check My Reasoning", type="primary"):
            log_think_tank_action(
                "evaluate_trade",
                symbol=stock["symbol"],
                action=action,
                portfolio_id=portfolio["id"],
            )
            result = post(
                "/ai/evaluate-trade",
                {
                    "symbol": stock["symbol"],
                    "action": action,
                    "quantity": int(quantity),
                    "price": float(price),
                    "notes": notes,
                    "portfolio_id": portfolio["id"],
                    "stock_id": stock["id"],
                    "model": st.session_state.ai_model,
                },
                show_error=False,
            )
            if not _show_error(result):
                st.session_state.trade_advice = result
        result = st.session_state.get("trade_advice")
        if result and not result.get("error"):
            quality = result.get("reasoning_quality", "THIN")
            metric_grid([{"label": "Reasoning quality", "value": quality}], columns=1)
            info_banner(f"What you got right: {result.get('positive_note', '')}", "success")
            for item in result.get("considerations") or []:
                info_banner(item, "warning")
            info_banner(result.get("risk_reward_note", ""), "info")
            st.write(result.get("educational_note", ""))
            _show_disclaimer(result)

with tab_screener:
    st.subheader("Natural Language Screener")
    examples = [
        "IT stocks that fell this year but recovered last month",
        "Banking stocks with high volume",
        "Defensive sectors — pharma or FMCG",
        "Stocks up more than 20% in 3 months",
    ]
    query = st.text_input(
        "Describe the stocks you're looking for",
        placeholder=examples[0],
    )
    chip_cols = st.columns(len(examples))
    for idx, example in enumerate(examples):
        if chip_cols[idx].button(example, key=f"nl_chip_{idx}"):
            st.session_state.nl_query = example
    if st.session_state.get("nl_query"):
        query = st.session_state.nl_query
        st.text_input("Query", value=query, disabled=True)

    pending = st.session_state.get("nl_pending")
    if pending and st.button("Confirm and apply filters"):
        st.session_state.nl_result = pending
        st.session_state.nl_pending = None

    if st.button("Find Stocks", type="primary") and query:
        log_think_tank_action("nl_screener", query=query)
        result = post(
            "/ai/nl-screener",
            {"query": query, "model": st.session_state.ai_model},
            show_error=False,
        )
        if not _show_error(result):
            if result.get("confidence") == "LOW":
                st.session_state.nl_pending = result
                st.warning(f"Low confidence — {result.get('explanation')}")
                info_banner("Click Confirm and apply filters if this looks right.", "info")
            else:
                st.session_state.nl_result = result

    result = st.session_state.get("nl_result")
    if result and not result.get("error"):
        st.write(result.get("explanation", ""))
        stocks = result.get("stocks") or []
        st.caption(f"{result.get('count', len(stocks))} stocks matched")
        if stocks:
            df = pd.DataFrame(stocks)
            show_cols = [
                c
                for c in (
                    "symbol",
                    "exchange",
                    "sector",
                    "latest_price",
                    "change_1m_pct",
                    "change_3m_pct",
                    "change_1y_pct",
                    "latest_volume",
                )
                if c in df.columns
            ]
            with st.container(border=True):
                st.dataframe(df[show_cols], use_container_width=True, hide_index=True)
        _show_disclaimer(result)

with tab_portfolio:
    st.subheader("Portfolio Health")
    portfolio = portfolio_select("think_tank_portfolio")
    if portfolio and st.button("Analyse Portfolio", type="primary"):
        log_think_tank_action("portfolio_narrative", portfolio_id=portfolio["id"])
        result = get(
            f"/ai/portfolio-narrative/{portfolio['id']}",
            params={"model": st.session_state.ai_model},
            show_error=False,
        )
        if not _show_error(result):
            st.session_state.portfolio_narrative = result
    result = st.session_state.get("portfolio_narrative")
    if result and not result.get("error"):
        score = int(result.get("health_score") or 50)
        metric_grid([{"label": "Health score", "value": score, "delta": result.get("health_label", "")}], columns=1)
        st.write(result.get("narrative", ""))
        st.error(f"**Top concern:** {result.get('top_concern', '—')}")
        _show_disclaimer(result)

with tab_journal:
    st.subheader("Journal Insights")
    portfolio = portfolio_select("think_tank_journal")
    if portfolio:
        trades = get("/paper-trades", params={"portfolio_id": portfolio["id"]}) or []
        st.caption(f"{len(trades)} paper trades in this portfolio (10+ recommended for analysis)")
        if len(trades) < 10:
            empty_state("More journal data needed", "Add at least 10 paper trades to unlock journal analysis.")
        elif st.button("Analyse My Trading", type="primary"):
            log_think_tank_action("analyze_journal", portfolio_id=portfolio["id"])
            result = post(
                f"/ai/analyze-journal/{portfolio['id']}",
                {},
                params={"model": st.session_state.ai_model},
                show_error=False,
            )
            if not _show_error(result):
                st.session_state.journal_analysis = result
        result = st.session_state.get("journal_analysis")
        if result and not result.get("error"):
            st.markdown("**Patterns**")
            for item in result.get("patterns_found") or []:
                st.write(f"- {item}")
            st.markdown("**Biases detected**")
            for item in result.get("biases_detected") or []:
                st.warning(item)
            st.markdown("**Strengths**")
            for item in result.get("strengths") or []:
                st.success(item)
            st.markdown("**Areas to improve**")
            for item in result.get("improvement_areas") or []:
                st.info(item)
            st.write(result.get("summary", ""))
            _show_disclaimer(result)

with tab_logs:
    st.subheader("Activity Log")
    st.caption(
        "Stored in `ai_action_logs` and printed to backend logs (`app.ai`) and "
        "frontend logs (`frontend.ai`). View backend: `docker logs paper_trading_backend -f`"
    )
    if st.button("Refresh logs"):
        st.session_state.pop("ai_activity_logs", None)
    logs = st.session_state.get("ai_activity_logs")
    if logs is None:
        logs = get("/ai/logs", params={"limit": 80}) or []
        st.session_state.ai_activity_logs = logs
    if not logs:
        empty_state("No AI actions logged", "Run an action from another tab to populate the activity log.")
    else:
        for row in logs:
            with st.expander(
                f"{row.get('created_at', '')} — {row.get('action_type')} — {row.get('status')} "
                f"({row.get('duration_ms', 0):.0f} ms)",
                expanded=False,
            ):
                c1, c2, c3 = st.columns(3)
                c1.metric("Model", row.get("model_name") or "—")
                c2.metric("Ollama connected", "Yes" if row.get("ollama_connected") else "No")
                c3.metric("Cache hit", "Yes" if row.get("cache_hit") else "No")
                st.write(f"**Endpoint:** `{row.get('endpoint')}` ({row.get('http_method')})")
                st.write(f"**Ollama URL:** `{row.get('ollama_base_url')}`")
                if row.get("error_message"):
                    st.error(row["error_message"])
                if row.get("request_payload"):
                    st.markdown("**Request**")
                    st.json(row["request_payload"])
                if row.get("llm_prompt"):
                    st.markdown("**LLM query**")
                    st.code(row["llm_prompt"], language="text")
                if row.get("llm_response"):
                    st.markdown("**LLM response**")
                    st.code(row["llm_response"], language="text")
                if row.get("response_payload"):
                    st.markdown("**API response**")
                    st.json(row["response_payload"])

log_page_load("AI Think Tank", PAGE_STARTED_AT)
