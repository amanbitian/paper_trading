from __future__ import annotations

import json
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.backtest import BacktestRun, BacktestTrade
from app.models.strategy import StrategyTemplate
from app.models.user import User
from app.schemas.backtest import BacktestRunRequest
from app.services.backtest_service import run_backtest
from app.services.index_fund_service import search_index_funds
from app.services.market_data_service import get_latest_prices_map
from app.services.stock_performance_service import list_stock_index_filters
from app.services.ticker_service import search_stocks

logger = logging.getLogger(__name__)

PARAMETER_CONFIG: dict[str, dict[str, Any]] = {
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
    "buy_score_above": {"label": "Buy score above", "min": -1.0, "max": 1.0, "step": 0.05, "kind": "float"},
    "sell_score_below": {"label": "Sell score below", "min": -1.0, "max": 1.0, "step": 0.05, "kind": "float"},
    "sequence_window": {"label": "Sequence window", "min": 2, "max": 250, "step": 1, "kind": "int"},
    "ema_fast_span": {"label": "Fast EMA span", "min": 2, "max": 250, "step": 1, "kind": "int"},
    "ema_slow_span": {"label": "Slow EMA span", "min": 3, "max": 400, "step": 1, "kind": "int"},
}

EXECUTION_MODE_OPTIONS = {
    "Next open after close signal": "signal_on_close_execute_next_open",
    "Next close after close signal": "signal_on_close_execute_next_close",
    "Same open after prior history": "signal_on_open_execute_same_open",
}
INTRABAR_OPTIONS = {
    "Conservative": "conservative",
    "Optimistic": "optimistic",
    "Open → High → Low → Close": "open_high_low_close",
    "Open → Low → High → Close": "open_low_high_close",
}
COST_MODEL_OPTIONS = {
    "Zerodha equity delivery": "zerodha_equity_delivery",
    "Zerodha intraday": "zerodha_intraday",
    "Basic": "basic",
    "Zero cost debug": "zero",
}
BENCHMARK_OPTIONS = {
    "Buy and hold same instrument": "buy_and_hold",
    "NIFTY 50": "nifty50",
    "NIFTY 500": "nifty500",
    "Sector benchmark": "sector",
    "Cash / zero return": "cash",
}


def list_strategy_templates(db: Session) -> list[StrategyTemplate]:
    return list(
        db.scalars(
            select(StrategyTemplate)
            .where(StrategyTemplate.is_active.is_(True))
            .order_by(StrategyTemplate.strategy_name.asc())
        )
    )


def get_strategy_template(db: Session, strategy_id: int) -> StrategyTemplate | None:
    return db.get(StrategyTemplate, strategy_id)


def validate_strategy_parameters(strategy_type: str, parameters: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if strategy_type == "rsi" and float(parameters.get("buy_rsi_below", 30)) >= float(
        parameters.get("sell_rsi_above", 70)
    ):
        errors.append("Buy RSI threshold must be lower than sell RSI threshold.")
    if strategy_type == "sma_crossover" and int(parameters.get("short_window", 20)) >= int(
        parameters.get("long_window", 50)
    ):
        errors.append("Short SMA window must be lower than long SMA window.")
    if strategy_type == "breakout" and float(parameters.get("volume_multiplier", 1.5)) <= 0:
        errors.append("Volume multiplier must be greater than zero.")
    if strategy_type in {"vwap", "twap"} and float(parameters.get("buy_below_pct", -1)) >= float(
        parameters.get("sell_above_pct", 1)
    ):
        errors.append("Buy-below threshold must be lower than sell-above threshold.")
    if strategy_type == "implementation_shortfall" and float(
        parameters.get("buy_improvement_pct", -1)
    ) >= float(parameters.get("sell_deterioration_pct", 1)):
        errors.append("Buy improvement must be lower than sell deterioration.")
    if strategy_type == "ou_process" and float(parameters.get("buy_z_below", -1.5)) >= float(
        parameters.get("sell_z_above", 1.5)
    ):
        errors.append("Buy z-score threshold must be lower than sell z-score threshold.")
    if strategy_type == "kalman_filter" and float(parameters.get("residual_buy_below", -1)) >= float(
        parameters.get("residual_sell_above", 1)
    ):
        errors.append("Buy residual threshold must be lower than sell residual threshold.")
    if strategy_type == "sarimax" and float(parameters.get("forecast_sell_below_pct", -0.25)) >= float(
        parameters.get("forecast_buy_above_pct", 0.25)
    ):
        errors.append("Sell forecast threshold must be lower than buy forecast threshold.")
    if strategy_type in {"tree_ensemble", "sequential_deep_learning"} and float(
        parameters.get("sell_score_below", -0.25)
    ) >= float(parameters.get("buy_score_above", 0.25)):
        errors.append("Sell score threshold must be lower than buy score threshold.")
    if strategy_type == "sequential_deep_learning" and int(parameters.get("ema_fast_span", 12)) >= int(
        parameters.get("ema_slow_span", 26)
    ):
        errors.append("Fast EMA span must be lower than slow EMA span.")
    return errors


def parse_basket_json(raw: str | None) -> list[dict[str, Any]]:
    if not raw or not str(raw).strip():
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Basket payload is invalid JSON.") from exc
    if not isinstance(payload, list):
        raise ValueError("Basket payload must be a list.")
    return dedupe_basket_items(payload)


def _basket_item_key(item: dict[str, Any]) -> tuple[str, str, str, int]:
    return (
        str(item.get("instrument_type", "stock")),
        str(item.get("exchange") or ""),
        str(item.get("symbol") or ""),
        int(item["id"]),
    )


def dedupe_basket_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, int]] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or "id" not in item:
            continue
        try:
            key = _basket_item_key(item)
        except (TypeError, ValueError):
            continue
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def parse_basket_from_form(
    *,
    symbols: list[str],
    exchanges: list[str],
    instrument_ids: list[str],
    instrument_types: list[str],
) -> list[dict[str, Any]]:
    if not instrument_ids and not symbols:
        return []

    id_values = [str(value).strip() for value in instrument_ids if str(value).strip()]
    symbol_values = [str(value).strip() for value in symbols if str(value).strip()]
    exchange_values = [str(value).strip() for value in exchanges]
    type_values = [str(value).strip().lower() or "stock" for value in instrument_types]

    row_count = len(id_values) or len(symbol_values)
    if not row_count:
        return []

    if symbol_values and len(symbol_values) != row_count:
        raise ValueError("Basket symbol count does not match instrument count.")
    if exchange_values and len(exchange_values) not in (0, row_count):
        raise ValueError("Basket exchange count does not match instrument count.")
    if type_values and len(type_values) not in (0, row_count):
        raise ValueError("Basket instrument type count does not match instrument count.")

    basket: list[dict[str, Any]] = []
    for index in range(row_count):
        instrument_id = int(id_values[index])
        symbol = symbol_values[index] if symbol_values else str(instrument_id)
        exchange = exchange_values[index] if index < len(exchange_values) else ""
        instrument_type = type_values[index] if index < len(type_values) else "stock"
        basket.append(
            {
                "id": instrument_id,
                "symbol": symbol,
                "exchange": exchange,
                "instrument_type": instrument_type,
            }
        )
    return dedupe_basket_items(basket)


def resolve_run_basket(
    *,
    symbols: list[str],
    exchanges: list[str],
    instrument_ids: list[str],
    instrument_types: list[str],
    basket_json: str | None,
) -> list[dict[str, Any]]:
    basket = parse_basket_from_form(
        symbols=symbols,
        exchanges=exchanges,
        instrument_ids=instrument_ids,
        instrument_types=instrument_types,
    )
    if basket:
        return basket
    return parse_basket_json(basket_json)


def parse_parameters_json(raw: str | None, template: StrategyTemplate) -> dict[str, Any]:
    parameters = dict(template.default_parameters or {})
    if raw and str(raw).strip():
        try:
            overrides = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("Strategy parameters JSON is invalid.") from exc
        if isinstance(overrides, dict):
            parameters.update(overrides)
    return parameters


def validate_run_form(
    *,
    basket: list[dict[str, Any]],
    strategy_id: int | None,
    start_date: date | None,
    end_date: date | None,
    initial_capital: Decimal | None,
    slippage_bps: int,
    strategy_type: str,
    parameters: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if not basket:
        errors.append("Select at least one instrument in the basket.")
    if not strategy_id:
        errors.append("Select a strategy.")
    if start_date is None:
        errors.append("Start date is required.")
    if end_date is None:
        errors.append("End date is required.")
    if start_date and end_date and start_date > end_date:
        errors.append("Start date must be on or before end date.")
    if initial_capital is None or initial_capital <= 0:
        errors.append("Initial capital must be greater than zero.")
    if slippage_bps < 0:
        errors.append("Slippage bps cannot be negative.")
    errors.extend(validate_strategy_parameters(strategy_type, parameters))
    return errors


def build_backtest_request(
    instrument: dict[str, Any],
    *,
    strategy_id: int,
    start_date: date,
    end_date: date,
    initial_capital: Decimal,
    parameters: dict[str, Any],
    walk_forward: bool,
    execution_mode: str,
    intrabar_assumption: str,
    cost_model: str,
    benchmark_code: str,
) -> BacktestRunRequest:
    instrument_type = instrument.get("instrument_type", "stock")
    payload: dict[str, Any] = {
        "instrument_type": instrument_type,
        "strategy_id": strategy_id,
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": initial_capital,
        "parameters": parameters,
        "walk_forward": walk_forward,
        "execution_mode": execution_mode,
        "intrabar_assumption": intrabar_assumption,
        "cost_model": cost_model,
        "benchmark_code": benchmark_code,
    }
    if instrument_type == "index_fund":
        payload["index_fund_id"] = int(instrument["id"])
    else:
        payload["stock_id"] = int(instrument["id"])
    return BacktestRunRequest(**payload)


def instrument_label(instrument: dict[str, Any]) -> str:
    instrument_type = instrument.get("instrument_type", "stock")
    if instrument_type == "index_fund":
        return f"{instrument.get('symbol')} ({instrument.get('base_currency', 'INR')})"
    return f"{instrument.get('symbol')} ({instrument.get('exchange')})"


def run_basket_backtests(
    db: Session,
    user: User,
    *,
    basket: list[dict[str, Any]],
    request_kwargs: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    successful: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for instrument in basket:
        label = instrument_label(instrument)
        try:
            payload = build_backtest_request(instrument, **request_kwargs)
            result = run_backtest(db, user, payload)
            run_model = result["run"]
            view = serialize_backtest_result(
                run_model,
                equity_curve=result.get("equity_curve") or [],
                benchmark_curve=result.get("benchmark_curve") or [],
                instrument=instrument,
                label=label,
                extra=result,
            )
            successful.append(view)
        except HTTPException as exc:
            failed.append({"instrument": label, "reason": exc.detail})
        except Exception as exc:
            logger.exception("Backtest failed for %s", label)
            failed.append({"instrument": label, "reason": str(exc)})
    return successful, failed


def serialize_backtest_result(
    run: BacktestRun,
    *,
    equity_curve: list[dict[str, Any]],
    benchmark_curve: list[dict[str, Any]],
    instrument: dict[str, Any],
    label: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    years = max((run.end_date - run.start_date).days / 365.25, 1 / 365.25)
    final_value = float(run.final_value)
    initial_capital = float(run.initial_capital)
    cagr = None
    if initial_capital > 0 and final_value > 0:
        cagr = ((final_value / initial_capital) ** (1 / years) - 1) * 100
    return {
        "run_id": run.id,
        "label": label,
        "instrument": instrument,
        "total_return_pct": float(run.total_return_pct),
        "net_return_pct": float(run.net_return_pct or run.total_return_pct),
        "cagr_pct": cagr,
        "sharpe_ratio": float(run.sharpe_ratio),
        "max_drawdown_pct": float(run.max_drawdown_pct),
        "win_rate": float(run.win_rate),
        "total_trades": int(run.total_trades),
        "final_value": final_value,
        "initial_capital": initial_capital,
        "gross_pnl": float(run.gross_pnl or 0),
        "net_pnl": float(run.net_pnl or 0),
        "total_charges": float(run.total_charges or 0),
        "slippage_cost": float(run.slippage_cost or 0),
        "benchmark_return": float(run.benchmark_return) if run.benchmark_return is not None else None,
        "benchmark_name": run.benchmark_name,
        "benchmark_symbol": run.benchmark_symbol,
        "excess_return": float(run.excess_return) if run.excess_return is not None else None,
        "beta": float(run.beta) if run.beta is not None else None,
        "information_ratio": float(run.information_ratio) if run.information_ratio is not None else None,
        "execution_mode": run.execution_mode,
        "intrabar_assumption": run.intrabar_assumption,
        "cost_model": run.cost_model,
        "benchmark_code": run.benchmark_code,
        "start_date": run.start_date,
        "end_date": run.end_date,
        "walk_forward_enabled": bool(run.walk_forward_enabled),
        "equity_curve": equity_curve,
        "benchmark_curve": benchmark_curve,
        "drawdown_curve": build_drawdown_curve(equity_curve),
        "monthly_returns": compute_monthly_returns(equity_curve),
        "assumptions": {
            "execution_mode": run.execution_mode,
            "intrabar_assumption": run.intrabar_assumption,
            "cost_model": run.cost_model,
            "benchmark_code": run.benchmark_code,
            "start_date": run.start_date.isoformat(),
            "end_date": run.end_date.isoformat(),
            "initial_capital": str(run.initial_capital),
        },
        "walk_forward": (
            {
                "is_total_return_pct": float(extra.get("is_total_return_pct"))
                if extra and extra.get("is_total_return_pct") is not None
                else None,
                "oos_total_return_pct": float(extra.get("oos_total_return_pct"))
                if extra and extra.get("oos_total_return_pct") is not None
                else None,
                "overfitting_score": float(extra.get("overfitting_score"))
                if extra and extra.get("overfitting_score") is not None
                else None,
            }
            if extra
            else {}
        ),
        "benchmark_warnings": list(run.benchmark_warnings or []),
    }


def build_drawdown_curve(equity_curve: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not equity_curve:
        return []
    series = pd.Series(
        [float(point.get("equity", 0)) for point in equity_curve],
        index=[point.get("date") for point in equity_curve],
        dtype=float,
    )
    drawdown = (series / series.cummax() - 1.0) * 100
    return [
        {"date": str(index), "drawdown_pct": float(value)}
        for index, value in drawdown.items()
    ]


def compute_monthly_returns(equity_curve: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(equity_curve) < 2:
        return []
    frame = pd.DataFrame(equity_curve)
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values("date")
    frame["month"] = frame["date"].dt.to_period("M")
    monthly = frame.groupby("month")["equity"].agg(["first", "last"])
    rows: list[dict[str, Any]] = []
    for period, row in monthly.iterrows():
        start_val = float(row["first"])
        end_val = float(row["last"])
        ret = ((end_val / start_val) - 1) * 100 if start_val else 0.0
        rows.append(
            {
                "month": str(period),
                "return_pct": round(ret, 4),
                "trades": None,
                "win_rate": None,
                "net_pnl": None,
            }
        )
    return rows


def build_equity_plotly_json(
    equity_curve: list[dict[str, Any]],
    benchmark_curve: list[dict[str, Any]] | None = None,
    *,
    title: str = "Equity curve",
) -> dict[str, Any] | None:
    if not equity_curve:
        return None
    traces: list[dict[str, Any]] = [
        {
            "type": "scatter",
            "mode": "lines",
            "name": "Strategy",
            "x": [point["date"] for point in equity_curve],
            "y": [point["equity"] for point in equity_curve],
            "line": {"color": "#4f9f68"},
        }
    ]
    if benchmark_curve:
        traces.append(
            {
                "type": "scatter",
                "mode": "lines",
                "name": "Benchmark",
                "x": [point["date"] for point in benchmark_curve],
                "y": [point["equity"] for point in benchmark_curve],
                "line": {"color": "#7aa2ff", "dash": "dot"},
            }
        )
    return {
        "data": traces,
        "layout": {
            "title": title,
            "autosize": True,
            "height": 420,
            "paper_bgcolor": "#050607",
            "plot_bgcolor": "#11151b",
            "font": {"color": "#f4f7fb", "size": 12},
            "margin": {"l": 50, "r": 20, "t": 40, "b": 40},
            "yaxis": {"title": "Equity"},
        },
    }


def build_drawdown_plotly_json(drawdown_curve: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not drawdown_curve:
        return None
    return {
        "data": [
            {
                "type": "scatter",
                "mode": "lines",
                "name": "Drawdown %",
                "x": [point["date"] for point in drawdown_curve],
                "y": [point["drawdown_pct"] for point in drawdown_curve],
                "fill": "tozeroy",
                "line": {"color": "#ff4d57"},
            }
        ],
        "layout": {
            "title": "Drawdown",
            "autosize": True,
            "height": 360,
            "paper_bgcolor": "#050607",
            "plot_bgcolor": "#11151b",
            "font": {"color": "#f4f7fb", "size": 12},
            "margin": {"l": 50, "r": 20, "t": 40, "b": 40},
            "yaxis": {"title": "Drawdown %"},
        },
    }


def load_backtest_run(db: Session, user_id: int, run_id: int) -> BacktestRun:
    run = db.scalar(
        select(BacktestRun)
        .where(BacktestRun.id == run_id, BacktestRun.user_id == user_id)
        .options(selectinload(BacktestRun.trades))
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest run not found")
    return run


def serialize_trades(trades: list[BacktestTrade], *, limit: int = 500) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    ordered = sorted(trades, key=lambda trade: (trade.trade_date, trade.id))[:limit]
    open_positions: dict[str, dict[str, Any]] = {}
    for trade in ordered:
        side = (trade.side or "").upper()
        row = {
            "entry_date": None,
            "exit_date": trade.trade_date,
            "symbol": str(trade.stock_id or trade.index_fund_id or ""),
            "side": side,
            "entry_price": None,
            "exit_price": float(trade.price),
            "quantity": float(trade.quantity),
            "gross_pnl": float(trade.gross_pnl or 0),
            "net_pnl": float(trade.net_pnl or trade.pnl or 0),
            "return_pct": None,
            "fees": float(trade.charges or 0),
            "slippage": float(trade.slippage_cost or 0),
            "reason": trade.reason,
        }
        if side == "BUY":
            open_positions[row["symbol"]] = {
                "entry_date": trade.trade_date,
                "entry_price": float(trade.price),
                "quantity": float(trade.quantity),
            }
        elif side == "SELL" and row["symbol"] in open_positions:
            entry = open_positions.pop(row["symbol"])
            row["entry_date"] = entry["entry_date"]
            row["entry_price"] = entry["entry_price"]
            invested = entry["entry_price"] * entry["quantity"]
            if invested:
                row["return_pct"] = round((row["net_pnl"] / invested) * 100, 4)
        rows.append(row)
    return rows


def search_backtest_instruments(
    db: Session,
    *,
    query: str,
    exchange: str | None,
    universe_type: str,
    index_membership: str | None,
    category: str | None = None,
    limit: int = 12,
) -> tuple[list[Any], str, dict[int, Decimal]]:
    clean_query = (query or "").strip()
    if not clean_query:
        return [], "empty_query", {}

    universe = (universe_type or "stock").strip().lower()
    if universe == "index_fund":
        funds = search_index_funds(
            db,
            clean_query,
            category=category.strip().lower() if category else None,
            limit=limit,
        )
        return funds, "index_fund", {}

    exchange_value = exchange.strip().upper() if exchange else None
    index_code = index_membership.strip() if index_membership else None
    stocks = search_stocks(
        db,
        clean_query,
        exchange_value,
        index_code=index_code or None,
        limit=limit,
        require_active=True,
    )
    search_mode = "active"
    if not stocks:
        stocks = search_stocks(
            db,
            clean_query,
            exchange_value,
            index_code=index_code or None,
            limit=limit,
            require_active=False,
        )
        search_mode = "with_prices"
    price_map = get_latest_prices_map(db, [stock.id for stock in stocks]) if stocks else {}
    return stocks, search_mode, price_map


def get_index_filter_options(db: Session | None = None) -> list[dict[str, str]]:
    options = [{"label": "All indexes", "value": ""}]
    for option in list_stock_index_filters():
        options.append({"label": option["label"], "value": option["value"]})
    return options


def coerce_decimal(value: str | None) -> Decimal:
    if value is None or not str(value).strip():
        raise ValueError("Initial capital is required.")
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Initial capital must be a valid number.") from exc
    return parsed


def coerce_date(value: str | None, *, field: str) -> date:
    if not value or not str(value).strip():
        raise ValueError(f"{field} is required.")
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{field} must be YYYY-MM-DD.") from exc
