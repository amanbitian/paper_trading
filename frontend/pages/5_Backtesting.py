from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import plotly.express as px
import streamlit as st

from api_client import (
    format_inr,
    get,
    get_index_fund_suggestions,
    get_stock_suggestions,
    log_page_load,
    post,
    require_login,
    start_timer,
)
from ui import empty_state, info_banner, load_global_css, page_header, section_card, status_badge


PAGE_STARTED_AT = start_timer()
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
st.set_page_config(page_title="Backtesting", page_icon="BT", layout="wide")
load_global_css()
page_header(
    "Backtesting",
    "Run educational strategy tests with explicit execution, cost, and benchmark assumptions.",
    right_badge=status_badge("Research mode", "warning"),
)

if not require_login():
    st.stop()


def edit_strategy_parameter(name: str, value, key_prefix: str):
    config = PARAMETER_CONFIG.get(name)
    label = config["label"] if config else name.replace("_", " ").title()
    kind = config.get("kind") if config else None
    if isinstance(value, bool):
        return st.checkbox(label, value=bool(value), key=f"{key_prefix}_{name}")
    if kind == "int" or (kind is None and isinstance(value, int) and not isinstance(value, bool)):
        return int(
            st.number_input(
                label,
                min_value=int(config.get("min", 0)) if config else 0,
                max_value=int(config.get("max", 1000)) if config else 1000,
                value=int(value),
                step=int(config.get("step", 1)) if config else 1,
                key=f"{key_prefix}_{name}",
            )
        )
    if kind == "float" or isinstance(value, float):
        return float(
            st.number_input(
                label,
                min_value=float(config.get("min", 0.0)) if config else 0.0,
                max_value=float(config.get("max", 1000.0)) if config else 1000.0,
                value=float(value),
                step=float(config.get("step", 0.1)) if config else 0.1,
                key=f"{key_prefix}_{name}",
            )
        )
    return st.text_input(label, value=str(value), key=f"{key_prefix}_{name}")


def validate_strategy_parameters(strategy_type: str, parameters: dict) -> list[str]:
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


def _stock_key(stock: dict) -> str:
    return f"{stock.get('_instrument_type', 'stock')}:{stock.get('exchange', '')}:{stock.get('symbol')}:{stock.get('id')}"


def _stock_label(stock: dict) -> str:
    if stock.get("_instrument_type") == "index_fund":
        return f"{stock.get('symbol')} - {stock.get('yahoo_symbol')}"
    company = stock.get("company_name") or stock.get("yahoo_symbol") or stock.get("symbol")
    return f"{stock.get('symbol')} ({stock.get('exchange')}) - {company}"


def _clear_last_backtest_results() -> None:
    st.session_state.pop("backtest_successful_results", None)
    st.session_state.pop("backtest_failed_results", None)


def _backtest_stock_basket(instrument_type: str) -> list[dict]:
    basket_key = f"backtest_{instrument_type}_basket"
    if basket_key not in st.session_state:
        st.session_state[basket_key] = []
    return st.session_state[basket_key]


def add_stock_to_basket(stock: dict, instrument_type: str) -> None:
    basket = _backtest_stock_basket(instrument_type)
    existing_keys = {_stock_key(item) for item in basket}
    instrument = dict(stock)
    instrument["_instrument_type"] = instrument_type
    if _stock_key(instrument) not in existing_keys:
        basket.append(instrument)
        _clear_last_backtest_results()


def remove_stock_from_basket(stock: dict, instrument_type: str) -> None:
    basket_key = f"backtest_{instrument_type}_basket"
    st.session_state[basket_key] = [
        item for item in _backtest_stock_basket(instrument_type) if _stock_key(item) != _stock_key(stock)
    ]
    _clear_last_backtest_results()


def render_stock_basket_picker() -> list[dict]:
    universe_label = st.radio(
        "Instrument universe",
        ["Stocks", "Index funds"],
        horizontal=True,
        key="backtest_instrument_universe",
    )
    instrument_type = "index_fund" if universe_label == "Index funds" else "stock"
    st.subheader(universe_label)
    search_query = st.text_input(
        "Search instrument",
        key=f"backtest_{instrument_type}_query",
        placeholder="Try OLA cab, HDFC private bank, NIFTY 50, BANKNIFTY...",
        help="Search suggestions are separate from the backtest basket. Add instruments one by one.",
    ).strip()
    exchange = None
    category = None
    index_code = None
    if instrument_type == "stock":
        exchange = st.selectbox("Exchange", ["", "NSE", "BSE"], key="backtest_stock_exchange")
        index_filters = get("/stocks/index-filters") or []
        index_label_to_value = {"All indexes": ""}
        index_label_to_value.update({option["label"]: option["value"] for option in index_filters})
        selected_index_label = st.selectbox(
            "Index membership",
            list(index_label_to_value.keys()),
            key="backtest_stock_index_code",
        )
        index_code = index_label_to_value[selected_index_label] or None
    else:
        category = st.selectbox("Category", ["", "index", "commodity"], key="backtest_index_category")
    basket = _backtest_stock_basket(instrument_type)
    basket_keys = {_stock_key(stock) for stock in basket}

    if search_query:
        suggestions = (
            get_index_fund_suggestions(search_query, category=category or None, limit=12)
            if instrument_type == "index_fund"
            else get_stock_suggestions(
                search_query,
                exchange=exchange or None,
                index_code=index_code,
                limit=12,
            )
        )
        if suggestions:
            st.caption("Ranked suggestions. Click Add to keep an instrument in the backtest basket.")
            for stock in suggestions:
                stock = {**stock, "_instrument_type": instrument_type}
                info_col, action_col = st.columns([5, 1])
                info_col.markdown(
                    f"**{_stock_label(stock)}**  \n"
                    f"`{stock.get('yahoo_symbol')}`"
                )
                if _stock_key(stock) in basket_keys:
                    action_col.button("Added", key=f"backtest_{instrument_type}_added_{stock['id']}", disabled=True)
                elif action_col.button("Add", key=f"backtest_{instrument_type}_add_{stock['id']}"):
                    add_stock_to_basket(stock, instrument_type)
                    st.rerun()
        else:
            empty_state("No matching instruments", "Try fewer words or a direct symbol.")
    else:
        st.caption("Search, then add one or more instruments to the basket below.")

    st.subheader("Backtest basket")
    if not basket:
        empty_state("Empty basket", "Search above, then add one or more instruments to the backtest basket.")
        return []

    for stock in basket:
        info_col, action_col = st.columns([5, 1])
        info_col.markdown(
            f"**{_stock_label(stock)}**  \n"
            f"`{stock.get('yahoo_symbol')}`"
        )
        if action_col.button("Remove", key=f"backtest_{instrument_type}_remove_{stock['id']}"):
            remove_stock_from_basket(stock, instrument_type)
            st.rerun()
    if st.button("Clear basket"):
        st.session_state[f"backtest_{instrument_type}_basket"] = []
        _clear_last_backtest_results()
        st.rerun()
    return list(basket)


section_card("1. Instrument universe and basket", "Search stocks or index funds, then keep one or more instruments in the basket.")
selected_stocks = render_stock_basket_picker()

templates = get("/strategies/templates") or []
if not templates:
    empty_state("No strategy templates", "Seed strategy templates first.")
    st.stop()

section_card("2. Strategy and parameters", "Choose the strategy and optionally tune parameters for this run.")
template_labels = {item["strategy_name"]: item for item in templates}
template_name = st.selectbox("Strategy", list(template_labels.keys()))
template = template_labels[template_name]
parameters = dict(template["default_parameters"])
advanced = st.toggle("Advanced user", key=f"backtest_strategy_advanced_{template['id']}")
if advanced:
    st.caption("Tune strategy parameters for this backtest run.")
    parameter_items = list(parameters.items())
    parameter_columns = st.columns(3)
    key_prefix = f"backtest_param_{template['id']}"
    for index, (name, value) in enumerate(parameter_items):
        with parameter_columns[index % 3]:
            parameters[name] = edit_strategy_parameter(name, value, key_prefix)
else:
    with st.expander("Default parameters", expanded=False):
        st.json(parameters)

parameter_errors = validate_strategy_parameters(template["strategy_type"], parameters)
for parameter_error in parameter_errors:
    info_banner(parameter_error, "danger")

section_card("3. Execution assumptions and date range", "Control fill assumptions, cost model, benchmark, and capital window.")
walk_forward = st.toggle("Walk-Forward Validation", value=False, help="Splits history 70% in-sample / 30% out-of-sample. Slower but helps detect overfitting.")

execution_mode_options = {
    "Next open after close signal": "signal_on_close_execute_next_open",
    "Next close after close signal": "signal_on_close_execute_next_close",
    "Same open after prior history": "signal_on_open_execute_same_open",
}
intrabar_options = {
    "Conservative": "conservative",
    "Optimistic": "optimistic",
    "Open -> High -> Low -> Close": "open_high_low_close",
    "Open -> Low -> High -> Close": "open_low_high_close",
}
cost_model_options = {
    "Zerodha equity delivery": "zerodha_equity_delivery",
    "Zerodha intraday": "zerodha_intraday",
    "Basic": "basic",
    "Zero cost debug": "zero",
}
benchmark_options = {
    "Buy and hold same instrument": "buy_and_hold",
    "NIFTY 50": "nifty50",
    "NIFTY 500": "nifty500",
    "Sector benchmark": "sector",
    "Cash / zero return": "cash",
}
mode_col, intrabar_col, cost_col, bench_col, slip_col = st.columns(5)
execution_mode = execution_mode_options[
    mode_col.selectbox("Execution mode", list(execution_mode_options.keys()))
]
intrabar_assumption = intrabar_options[
    intrabar_col.selectbox("Intrabar assumption", list(intrabar_options.keys()))
]
cost_model = cost_model_options[cost_col.selectbox("Cost model", list(cost_model_options.keys()))]
benchmark_code = benchmark_options[bench_col.selectbox("Benchmark", list(benchmark_options.keys()))]
slippage_bps = int(
    slip_col.number_input("Slippage bps", min_value=0, max_value=500, value=10, step=1)
)
parameters["slippage_bps"] = slippage_bps

col1, col2, col3 = st.columns(3)
start_date = col1.date_input("Start date", date.today() - timedelta(days=365))
end_date = col2.date_input("End date", date.today())
initial_capital = col3.number_input("Initial capital", min_value=1000.0, value=100000.0, step=10000.0)
col3.caption(f"Selected: {format_inr(initial_capital, compact=True)}")


def render_backtest_results(successful_results: list[dict], failed_results: list[dict]) -> None:
    if failed_results:
        info_banner("Some backtests could not be completed.", "warning")
        with st.container(border=True):
            st.dataframe(pd.DataFrame(failed_results), use_container_width=True, hide_index=True)

    if not successful_results:
        return

    st.caption("Showing last completed backtest run.")
    comparison_rows = [
        {
            "Instrument": result["_label"],
            "Name": result["_stock"].get("company_name") or result["_stock"].get("yahoo_symbol"),
            "Total Return %": float(result["total_return_pct"]),
            "Max Drawdown %": float(result["max_drawdown_pct"]),
            "Sharpe": float(result["sharpe_ratio"]),
            "Win Rate %": float(result["win_rate"]),
            "Trades": int(result["total_trades"]),
            "Final Value": float(result["final_value"]),
            "Net Return %": float(result.get("net_return_pct", result["total_return_pct"])),
            "Benchmark": result.get("benchmark_name") or result.get("benchmark_symbol"),
            "Benchmark Return %": float(result["benchmark_return"]) if result.get("benchmark_return") is not None else None,
            "Excess Return %": float(result["excess_return"]) if result.get("excess_return") is not None else None,
            "Charges": float(result.get("total_charges", 0)),
            "Slippage": float(result.get("slippage_cost", 0)),
        }
        for result in successful_results
    ]
    comparison_df = pd.DataFrame(comparison_rows).sort_values(
        "Total Return %",
        ascending=False,
    )

    if len(successful_results) == 1:
        result = successful_results[0]
        if result.get("walk_forward_enabled"):
            st.subheader("Walk-Forward Validation")
            is_col, oos_col = st.columns(2)
            with is_col:
                st.markdown("**In-Sample (70%)**")
                st.metric("Total Return", f"{float(result.get('is_total_return_pct', 0)):.2f}%")
                st.metric("Sharpe", f"{float(result.get('is_sharpe_ratio', 0)):.2f}")
                st.metric("Max Drawdown", f"{float(result.get('is_max_drawdown_pct', 0)):.2f}%")
                st.metric("Win Rate", f"{float(result.get('is_win_rate', 0)):.2f}%")
                st.metric("Trades", int(result.get("is_num_trades", 0)))
            with oos_col:
                st.markdown("**Out-of-Sample (30%)**")
                st.metric("Total Return", f"{float(result.get('oos_total_return_pct', 0)):.2f}%")
                st.metric("Sharpe", f"{float(result.get('oos_sharpe_ratio', 0)):.2f}")
                st.metric("Max Drawdown", f"{float(result.get('oos_max_drawdown_pct', 0)):.2f}%")
                st.metric("Win Rate", f"{float(result.get('oos_win_rate', 0)):.2f}%")
                st.metric("Trades", int(result.get("oos_num_trades", 0)))
            overfit = float(result.get("overfitting_score") or 0)
            if overfit > 0.7:
                st.success(f"Overfitting score: {overfit:.2f} — strategy likely generalizes")
            elif overfit >= 0.4:
                st.warning(f"Overfitting score: {overfit:.2f} — moderate overfitting risk")
            else:
                st.error(f"Overfitting score: {overfit:.2f} — likely overfitted to training period")
            if int(result.get("oos_num_trades", 0)) < 5:
                st.warning("Too few OOS trades for reliable estimate.")
        else:
            metric_cols = st.columns(5)
            metric_cols[0].metric("Total Return", f"{float(result['total_return_pct']):.2f}%")
            metric_cols[1].metric("Max Drawdown", f"{float(result['max_drawdown_pct']):.2f}%")
            metric_cols[2].metric("Sharpe", f"{float(result['sharpe_ratio']):.2f}")
            metric_cols[3].metric("Win Rate", f"{float(result['win_rate']):.2f}%")
            metric_cols[4].metric("Trades", result["total_trades"])
            cost_cols = st.columns(4)
            cost_cols[0].metric("Gross PnL", format_inr(float(result.get("gross_pnl", 0))))
            cost_cols[1].metric("Net PnL", format_inr(float(result.get("net_pnl", 0))))
            cost_cols[2].metric("Charges", format_inr(float(result.get("total_charges", 0))))
            cost_cols[3].metric("Slippage", format_inr(float(result.get("slippage_cost", 0))))
            benchmark_cols = st.columns(4)
            benchmark_cols[0].metric(
                "Benchmark Return",
                "n/a" if result.get("benchmark_return") is None else f"{float(result['benchmark_return']):.2f}%",
            )
            benchmark_cols[1].metric(
                "Excess Return",
                "n/a" if result.get("excess_return") is None else f"{float(result['excess_return']):.2f}%",
            )
            benchmark_cols[2].metric(
                "Beta",
                "n/a" if result.get("beta") is None else f"{float(result['beta']):.2f}",
            )
            benchmark_cols[3].metric(
                "Information Ratio",
                "n/a" if result.get("information_ratio") is None else f"{float(result['information_ratio']):.2f}",
            )
            if result.get("benchmark_warnings"):
                for warning in result["benchmark_warnings"]:
                    info_banner(warning, "warning")
            st.caption(
                "Execution: "
                f"{result.get('execution_mode')} | Intrabar: {result.get('intrabar_assumption')} | "
                f"Cost model: {result.get('cost_model')} | "
                f"Benchmark: {result.get('benchmark_name') or result.get('benchmark_symbol') or result.get('benchmark_code')}"
            )
    else:
        st.subheader("Backtest comparison")
        with st.container(border=True):
            st.dataframe(
                comparison_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Total Return %": st.column_config.NumberColumn("Total Return %", format="%.2f%%"),
                    "Max Drawdown %": st.column_config.NumberColumn("Max Drawdown %", format="%.2f%%"),
                    "Sharpe": st.column_config.NumberColumn("Sharpe", format="%.2f"),
                    "Win Rate %": st.column_config.NumberColumn("Win Rate %", format="%.2f%%"),
                    "Final Value": st.column_config.NumberColumn("Final Value", format="INR %.2f"),
                    "Net Return %": st.column_config.NumberColumn("Net Return %", format="%.2f%%"),
                    "Benchmark Return %": st.column_config.NumberColumn("Benchmark Return %", format="%.2f%%"),
                    "Excess Return %": st.column_config.NumberColumn("Excess Return %", format="%.2f%%"),
                    "Charges": st.column_config.NumberColumn("Charges", format="INR %.2f"),
                    "Slippage": st.column_config.NumberColumn("Slippage", format="INR %.2f"),
                },
            )

    equity_frames = []
    for result in successful_results:
        curve = result.get("equity_curve", [])
        if not curve:
            continue
        frame = pd.DataFrame(curve)
        frame["Series"] = f"{result['_label']} strategy"
        equity_frames.append(frame)
        benchmark_curve = result.get("benchmark_curve") or []
        if benchmark_curve:
            benchmark_frame = pd.DataFrame(benchmark_curve)
            benchmark_name = result.get("benchmark_name") or result.get("benchmark_symbol") or "benchmark"
            benchmark_frame["Series"] = f"{result['_label']} benchmark ({benchmark_name})"
            equity_frames.append(benchmark_frame)
    if equity_frames:
        equity_df = pd.concat(equity_frames, ignore_index=True)
        fig = px.line(
            equity_df,
            x="date",
            y="equity",
            color="Series",
            title="Strategy vs Benchmark Equity Curve"
            if len(successful_results) == 1
            else "Strategy vs Benchmark Equity Curve Comparison",
        )
        with st.container(border=True):
            st.plotly_chart(fig, use_container_width=True)

    result_labels = {result["_label"]: result for result in successful_results}
    detail_label = st.selectbox("Trade details", list(result_labels.keys()))
    trades = result_labels[detail_label].get("trades", [])
    if trades:
        with st.container(border=True):
            st.dataframe(pd.DataFrame(trades), use_container_width=True, hide_index=True)
    else:
        empty_state("No generated trades", "No trades were generated by this strategy in the selected period.")


if st.button("Run Backtest", disabled=bool(parameter_errors)):
    if not selected_stocks:
        info_banner("Select at least one instrument.", "danger")
    else:
        successful_results = []
        failed_results = []
        progress = st.progress(0, text="Running backtests...")
        for index, stock in enumerate(selected_stocks, start=1):
            instrument_type = stock.get("_instrument_type", "stock")
            label = (
                f"{stock['symbol']} ({stock['exchange']})"
                if instrument_type == "stock"
                else f"{stock['symbol']} ({stock['base_currency']})"
            )
            payload = {
                "instrument_type": instrument_type,
                "strategy_id": template["id"],
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "initial_capital": str(Decimal(str(initial_capital))),
                "parameters": parameters,
                "walk_forward": walk_forward,
                "execution_mode": execution_mode,
                "intrabar_assumption": intrabar_assumption,
                "cost_model": cost_model,
                "benchmark_code": benchmark_code,
            }
            if instrument_type == "index_fund":
                payload["index_fund_id"] = stock["id"]
            else:
                payload["stock_id"] = stock["id"]
            result = post(
                "/backtest/run",
                payload,
                return_error=True,
                show_error=False,
            )
            if result and not result.get("error"):
                result["_stock"] = stock
                result["_label"] = label
                successful_results.append(result)
            else:
                failed_results.append(
                    {
                        "Instrument": label,
                        "Reason": (result or {}).get("message", "Backtest failed"),
                    }
                )
            progress.progress(index / len(selected_stocks), text=f"Backtested {label}")
        progress.empty()
        st.session_state.backtest_successful_results = successful_results
        st.session_state.backtest_failed_results = failed_results

render_backtest_results(
    st.session_state.get("backtest_successful_results", []),
    st.session_state.get("backtest_failed_results", []),
)

log_page_load("Backtesting", PAGE_STARTED_AT)
