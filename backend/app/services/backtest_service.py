"""Bar-by-bar backtest engine: replays historical candles through a strategy.

High-level flow of `run_backtest`:
  1. Load the requested strategy and the OHLCV history for the chosen
     instrument/date range (`_load_prices_for_range` /
     `_load_instrument_prices_for_range`).
  2. Optionally split the range into in-sample/out-of-sample windows for a
     walk-forward check (`_walk_forward_split`).
  3. Feed the price history bar-by-bar into `_simulate_backtest`, which asks
     the strategy for a signal on each bar and simulates fills, slippage,
     brokerage/STT/other charges, and stop-loss/target exits.
  4. Persist the resulting `BacktestRun`/`BacktestTrade` rows and compute
     summary metrics + a benchmark comparison.

The engine is careful to avoid lookahead bias: `_execution_events` only ever
shows the strategy bars up to and including the signal bar, and trades fill
on a *later* bar's price (e.g. "signal on close, execute on next open") so a
strategy can never act on information from the future.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Callable

import pandas as pd
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.backtest import BacktestRun, BacktestTrade
from app.models.stock import Stock, StockPrice
from app.models.strategy import StrategyTemplate, UserStrategy
from app.models.user import User
from app.schemas.backtest import BacktestRunRequest
from app.services.benchmark_service import compare_to_benchmark
from app.services.cost_model_service import calculate_round_trip_pnl, calculate_trade_cost
from app.services.execution_simulator_service import simulate_long_stop_target
from app.services.index_fund_service import load_index_price_dataframe_for_range
from app.services.market_data_service import DAILY_TIMEFRAME, prices_to_dataframe, sync_stock_prices
from app.services.portfolio_service import D
from app.services.execution_service import apply_slippage
from app.services.strategy_service import get_strategy_instance, parameters_with_point_in_time_context
from app.strategies.risk_management import calculate_position_size
from app.utils.observability import timed


def _strategy_from_request(db: Session, user: User, strategy_id: int):
    user_strategy = db.scalar(
        select(UserStrategy).where(UserStrategy.id == strategy_id, UserStrategy.user_id == user.id)
    )
    if user_strategy:
        template = db.get(StrategyTemplate, user_strategy.strategy_template_id)
        return user_strategy, template, dict(user_strategy.parameters or {})
    template = db.get(StrategyTemplate, strategy_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return None, template, dict(template.default_parameters or {})


def _load_prices_for_range(db: Session, stock_id: int, start_date: date, end_date: date) -> pd.DataFrame:
    start_dt = datetime.combine(start_date, time.min, tzinfo=UTC)
    end_dt = datetime.combine(end_date, time.max, tzinfo=UTC)
    prices = list(
        db.scalars(
            select(StockPrice)
            .where(
                StockPrice.stock_id == stock_id,
                StockPrice.timeframe == DAILY_TIMEFRAME,
                StockPrice.price_datetime >= start_dt,
                StockPrice.price_datetime <= end_dt,
            )
            .order_by(StockPrice.price_datetime.asc())
        )
    )
    if not prices:
        sync_stock_prices(db, stock_id, period="5y", interval="1d", commit=False)
        prices = list(
            db.scalars(
                select(StockPrice)
                .where(
                    StockPrice.stock_id == stock_id,
                    StockPrice.timeframe == DAILY_TIMEFRAME,
                    StockPrice.price_datetime >= start_dt,
                    StockPrice.price_datetime <= end_dt,
                )
                .order_by(StockPrice.price_datetime.asc())
            )
        )
    dataframe = prices_to_dataframe(prices)
    if dataframe.empty:
        raise HTTPException(status_code=400, detail="No historical prices available for backtest")
    return dataframe


def _load_instrument_prices_for_range(db: Session, payload: BacktestRunRequest) -> pd.DataFrame:
    if payload.instrument_type == "index_fund":
        if payload.index_fund_id is None:
            raise HTTPException(status_code=400, detail="index_fund_id is required")
        return load_index_price_dataframe_for_range(
            db,
            payload.index_fund_id,
            payload.start_date,
            payload.end_date,
        )
    if payload.stock_id is None:
        raise HTTPException(status_code=400, detail="stock_id is required")
    return _load_prices_for_range(db, payload.stock_id, payload.start_date, payload.end_date)


def _slice_by_dates(dataframe: pd.DataFrame, start_date: date, end_date: date) -> pd.DataFrame:
    index_dates = pd.to_datetime(dataframe.index).date
    mask = (index_dates >= start_date) & (index_dates <= end_date)
    return dataframe.loc[mask]


def _compute_metrics(
    equity_curve: list[dict],
    simulated_trades: list[dict],
    initial_capital: Decimal,
    final_value: Decimal,
) -> dict:
    total_return_pct = (final_value - initial_capital) / initial_capital * 100
    equity_series = pd.Series([point["equity"] for point in equity_curve], dtype=float)
    drawdown = (equity_series / equity_series.cummax() - 1.0) * 100
    max_drawdown_pct = Decimal(str(round(abs(float(drawdown.min() or 0)), 4)))
    returns = equity_series.pct_change().dropna()
    sharpe = 0.0
    if not returns.empty and returns.std() != 0:
        sharpe = float((returns.mean() / returns.std()) * (252**0.5))
    sell_trades = [trade for trade in simulated_trades if trade["side"] == "SELL"]
    wins = [trade for trade in sell_trades if D(trade["pnl"]) > 0]
    win_rate = Decimal(str(round((len(wins) / len(sell_trades) * 100) if sell_trades else 0, 4)))
    return {
        "final_value": final_value,
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "sharpe_ratio": Decimal(str(round(sharpe, 4))),
        "win_rate": win_rate,
        "num_trades": len(simulated_trades),
        "equity_curve": equity_curve,
        "simulated_trades": simulated_trades,
        "sharpe_float": sharpe,
    }


def _execution_events(dataframe: pd.DataFrame, execution_mode: str):
    """Yield (signal_slice, execution_row, execution_field) for each bar.

    This is the anti-lookahead heart of the engine:
      - `signal_slice` is the price history the strategy is allowed to see —
        always a prefix of `dataframe` ending at the signal bar, never
        including any future bar.
      - `execution_row` is the bar whose price the resulting trade actually
        fills at, and `execution_field` ("open"/"close") says which price
        on that bar is used.

    Three execution modes are supported, controlling the gap between
    "when the strategy decides" and "when the trade fills":
      - signal_on_open_execute_same_open: the strategy sees the *opening*
        print of the current bar (high/low/close are masked to the open so
        it cannot peek at where the bar ends) and fills at that same open.
        This models intraday/opening-auction style execution.
      - signal_on_close_execute_next_open / *_next_close: the strategy sees
        a bar's full OHLCV only after it has closed, and the trade fills on
        the *next* bar's open or close respectively — the standard
        "decide on today's close, trade tomorrow" backtest convention.
    """
    if execution_mode == "signal_on_open_execute_same_open":
        for execution_idx in range(0, len(dataframe)):
            signal_slice = dataframe.iloc[: execution_idx + 1].copy()
            current_label = signal_slice.index[-1]
            current_open = signal_slice.loc[current_label, "open"]
            # At the opening print, high/low/close are unknown. Replacing them
            # with open prevents hidden same-candle lookahead in strategy code.
            for field in ("high", "low", "close"):
                signal_slice.loc[current_label, field] = current_open
            yield signal_slice, dataframe.iloc[execution_idx], "open"
        return

    execution_field = (
        "close" if execution_mode == "signal_on_close_execute_next_close" else "open"
    )
    for signal_idx in range(0, max(len(dataframe) - 1, 0)):
        yield dataframe.iloc[: signal_idx + 1], dataframe.iloc[signal_idx + 1], execution_field


def _execution_price(row: pd.Series, field: str) -> Decimal:
    return D(row[field])


def _pct_price(price: Decimal, pct: Decimal, side: str) -> Decimal:
    direction = Decimal("-1") if side == "stop" else Decimal("1")
    return price * (Decimal("1") + direction * pct / Decimal("100"))


def _cost(
    *,
    quoted_price: Decimal,
    executed_price: Decimal,
    quantity: Decimal,
    side: str,
    cost_model: str,
    exchange: str,
) -> dict:
    trade_value = executed_price * quantity
    slippage_cost = abs(executed_price - quoted_price) * quantity
    return calculate_trade_cost(
        trade_value=trade_value,
        side=side,
        cost_model=cost_model,
        exchange=exchange,
        slippage_cost=slippage_cost,
    )


def _json_cost(cost: dict) -> dict:
    return {key: float(value) if isinstance(value, Decimal) else value for key, value in cost.items()}


def _simulate_backtest(
    dataframe: pd.DataFrame,
    strategy,
    parameters: dict,
    initial_capital: Decimal,
    *,
    slippage_bps: int = 10,
    execution_mode: str = "signal_on_close_execute_next_open",
    intrabar_assumption: str = "conservative",
    cost_model: str = "zerodha_equity_delivery",
    exchange: str = "NSE",
    parameter_context_builder: Callable[[date], dict] | None = None,
) -> dict:
    """Walk `dataframe` bar-by-bar, simulating a single-position long-only book.

    On each step:
      1. If currently holding a position, first check whether its stop-loss
         or take-profit would have been hit intrabar
         (`simulate_long_stop_target`); if so, exit immediately at that
         price ("protective_exit") before consulting the strategy again —
         this mirrors how a real broker-side stop order behaves.
      2. Otherwise ask the strategy for a fresh signal on the visible slice.
         A BUY opens a new position sized by `calculate_position_size`
         (capital, risk-per-trade, and stop distance all factor in); a SELL
         closes the existing one.
      3. Every fill applies slippage (`apply_slippage`) to the quoted price
         to get an executed price, then charges (`_cost` /
         `calculate_trade_cost`) are deducted from cash — so the simulation
         reflects realistic, not idealized, P&L.
      4. Mark-to-market equity (cash + position value at a slipped exit
         price) is recorded each bar to build the equity curve used for
         drawdown/Sharpe/other metrics.

    Returns a dict of aggregate metrics plus gross/net P&L, total charges,
    and slippage cost — `simulated_trades` carries the full trade log.
    """
    cash = initial_capital
    quantity = Decimal("0")
    entry_executed_price = Decimal("0")
    entry_quoted_price = Decimal("0")
    entry_cost: dict | None = None
    stop_loss_price: Decimal | None = None
    target_price: Decimal | None = None
    equity_curve: list[dict] = []
    simulated_trades: list[dict] = []
    total_charges = Decimal("0")
    total_slippage = Decimal("0")
    realized_gross_pnl = Decimal("0")
    realized_net_pnl = Decimal("0")

    for current_slice, execution_row, execution_field in _execution_events(dataframe, execution_mode):
        execution_date = pd.to_datetime(execution_row.name).date()
        protective_exit = False

        if quantity > 0:
            stop_result = simulate_long_stop_target(
                open_price=execution_row["open"],
                high_price=execution_row["high"],
                low_price=execution_row["low"],
                close_price=execution_row["close"],
                stop_loss_price=stop_loss_price,
                target_price=target_price,
                assumption=intrabar_assumption,  # type: ignore[arg-type]
            )
            if stop_result.filled and stop_result.price is not None:
                quoted = stop_result.price
                exec_price = D(round(apply_slippage(float(quoted), "SELL", slippage_bps), 4))
                sell_cost = _cost(
                    quoted_price=quoted,
                    executed_price=exec_price,
                    quantity=quantity,
                    side="SELL",
                    cost_model=cost_model,
                    exchange=exchange,
                )
                round_trip = calculate_round_trip_pnl(
                    entry_quoted_price=entry_quoted_price,
                    exit_quoted_price=quoted,
                    entry_executed_price=entry_executed_price,
                    exit_executed_price=exec_price,
                    quantity=quantity,
                    buy_cost=entry_cost or {},
                    sell_cost=sell_cost,
                )
                cash += quantity * exec_price - D(sell_cost["total_charges"])
                total_charges += D(sell_cost["total_charges"])
                total_slippage += D(sell_cost["slippage_cost"])
                realized_gross_pnl += round_trip["gross_pnl"]
                realized_net_pnl += round_trip["net_pnl"]
                simulated_trades.append(
                    {
                        "side": "SELL",
                        "quantity": quantity,
                        "price": exec_price,
                        "quoted_price": quoted,
                        "trade_date": execution_date,
                        "signal_date": pd.to_datetime(current_slice.index[-1]).date(),
                        "pnl": round_trip["net_pnl"],
                        "gross_pnl": round_trip["gross_pnl"],
                        "charges": D(sell_cost["total_charges"]),
                        "slippage_cost": round_trip["slippage_cost"],
                        "net_pnl": round_trip["net_pnl"],
                        "charges_breakdown": _json_cost(sell_cost),
                        "reason": stop_result.reason,
                    }
                )
                quantity = Decimal("0")
                entry_executed_price = Decimal("0")
                entry_quoted_price = Decimal("0")
                entry_cost = None
                stop_loss_price = None
                target_price = None
                protective_exit = True

        if protective_exit:
            equity_curve.append({"date": execution_date.isoformat(), "equity": float(cash)})
            continue

        signal_date = pd.to_datetime(current_slice.index[-1]).date()
        signal_parameters = parameters
        if parameter_context_builder is not None:
            signal_parameters = parameter_context_builder(signal_date)
        signal = strategy.generate_signal(current_slice, signal_parameters)
        quoted = _execution_price(execution_row, execution_field)

        if signal.signal_type == "BUY" and quantity == 0 and cash > quoted:
            exec_price = D(round(apply_slippage(float(quoted), "BUY", slippage_bps), 4))
            stop_loss_pct = D(parameters.get("stop_loss_pct", signal.indicators.get("stop_loss_pct", 5)))
            take_profit_pct = D(parameters.get("take_profit_pct", 0) or 0)
            max_position_size_pct = parameters.get("max_position_size_pct", 10)
            risk_per_trade_pct = parameters.get("risk_per_trade_pct", 1)
            atr_stop = {"stop_pct": signal.indicators["stop_pct"]} if signal.indicators.get("stop_pct") else None
            buy_quantity = calculate_position_size(
                cash,
                cash,
                exec_price,
                risk_per_trade_pct,
                stop_loss_pct,
                max_position_size_pct,
                atr_stop=atr_stop,
            )
            if buy_quantity > 0:
                buy_qty = D(buy_quantity)
                buy_cost = _cost(
                    quoted_price=quoted,
                    executed_price=exec_price,
                    quantity=buy_qty,
                    side="BUY",
                    cost_model=cost_model,
                    exchange=exchange,
                )
                trade_value = buy_qty * exec_price
                total_cash_needed = trade_value + D(buy_cost["total_charges"])
                if total_cash_needed <= cash:
                    cash -= total_cash_needed
                    quantity = buy_qty
                    entry_executed_price = exec_price
                    entry_quoted_price = quoted
                    entry_cost = buy_cost
                    stop_loss_price = D(signal.indicators.get("stop_price")) if signal.indicators.get("stop_price") else _pct_price(exec_price, stop_loss_pct, "stop")
                    target_price = (
                        D(signal.indicators.get("take_profit_price"))
                        if signal.indicators.get("take_profit_price")
                        else (_pct_price(exec_price, take_profit_pct, "target") if take_profit_pct > 0 else None)
                    )
                    total_charges += D(buy_cost["total_charges"])
                    total_slippage += D(buy_cost["slippage_cost"])
                    simulated_trades.append(
                        {
                            "side": "BUY",
                            "quantity": buy_qty,
                            "price": exec_price,
                            "quoted_price": quoted,
                            "trade_date": execution_date,
                            "signal_date": signal_date,
                            "pnl": Decimal("0"),
                            "gross_pnl": Decimal("0"),
                            "charges": D(buy_cost["total_charges"]),
                            "slippage_cost": D(buy_cost["slippage_cost"]),
                            "net_pnl": Decimal("0"),
                            "charges_breakdown": _json_cost(buy_cost),
                            "reason": signal.reason,
                        }
                    )
        elif signal.signal_type == "SELL" and quantity > 0:
            exec_price = D(round(apply_slippage(float(quoted), "SELL", slippage_bps), 4))
            sell_cost = _cost(
                quoted_price=quoted,
                executed_price=exec_price,
                quantity=quantity,
                side="SELL",
                cost_model=cost_model,
                exchange=exchange,
            )
            round_trip = calculate_round_trip_pnl(
                entry_quoted_price=entry_quoted_price,
                exit_quoted_price=quoted,
                entry_executed_price=entry_executed_price,
                exit_executed_price=exec_price,
                quantity=quantity,
                buy_cost=entry_cost or {},
                sell_cost=sell_cost,
            )
            cash += quantity * exec_price - D(sell_cost["total_charges"])
            total_charges += D(sell_cost["total_charges"])
            total_slippage += D(sell_cost["slippage_cost"])
            realized_gross_pnl += round_trip["gross_pnl"]
            realized_net_pnl += round_trip["net_pnl"]
            simulated_trades.append(
                {
                    "side": "SELL",
                    "quantity": quantity,
                    "price": exec_price,
                    "quoted_price": quoted,
                    "trade_date": execution_date,
                    "signal_date": signal_date,
                    "pnl": round_trip["net_pnl"],
                    "gross_pnl": round_trip["gross_pnl"],
                    "charges": D(sell_cost["total_charges"]),
                    "slippage_cost": round_trip["slippage_cost"],
                    "net_pnl": round_trip["net_pnl"],
                    "charges_breakdown": _json_cost(sell_cost),
                    "reason": signal.reason,
                }
            )
            quantity = Decimal("0")
            entry_executed_price = Decimal("0")
            entry_quoted_price = Decimal("0")
            entry_cost = None
            stop_loss_price = None
            target_price = None

        mark_quoted = D(execution_row["close"])
        mark_price = D(round(apply_slippage(float(mark_quoted), "SELL", slippage_bps), 4))
        equity = cash + quantity * mark_price
        equity_curve.append({"date": execution_date.isoformat(), "equity": float(equity)})

    if not equity_curve:
        raise HTTPException(status_code=400, detail="Not enough candles for selected execution mode")

    final_quoted = D(dataframe.iloc[-1]["close"])
    final_price = D(round(apply_slippage(float(final_quoted), "SELL", slippage_bps), 4))
    final_value = cash + quantity * final_price
    gross_pnl = final_value - initial_capital + total_charges + total_slippage
    net_pnl = final_value - initial_capital
    metrics = _compute_metrics(equity_curve, simulated_trades, initial_capital, final_value)
    metrics["gross_pnl"] = gross_pnl
    metrics["total_charges"] = total_charges
    metrics["slippage_cost"] = total_slippage
    metrics["net_pnl"] = net_pnl
    metrics["gross_return_pct"] = (gross_pnl / initial_capital * 100) if initial_capital else Decimal("0")
    metrics["net_return_pct"] = (net_pnl / initial_capital * 100) if initial_capital else Decimal("0")
    metrics["realized_gross_pnl"] = realized_gross_pnl
    metrics["realized_net_pnl"] = realized_net_pnl
    metrics["total_charges_pct"] = (
        (total_charges / initial_capital * 100) if initial_capital else Decimal("0")
    )
    return metrics


def _walk_forward_split(start_date: date, end_date: date) -> tuple[date, date, date, date]:
    total_days = (end_date - start_date).days
    if total_days < 30:
        raise HTTPException(status_code=400, detail="Date range too short for walk-forward validation")
    is_days = int(total_days * 0.7)
    is_end_date = start_date + timedelta(days=is_days)
    oos_start_date = is_end_date + timedelta(days=1)
    return start_date, is_end_date, oos_start_date, end_date


def _parameter_context_builder(
    db: Session,
    strategy_type: str,
    base_parameters: dict,
    *,
    stock_id: int | None,
) -> Callable[[date], dict] | None:
    if stock_id is None or strategy_type != "quality_momentum":
        return None
    cache: dict[date, dict] = {}

    def build(signal_date: date) -> dict:
        if signal_date not in cache:
            cache[signal_date] = parameters_with_point_in_time_context(
                db,
                strategy_type,
                base_parameters,
                stock_id=stock_id,
                as_of_date=signal_date,
            )
        return cache[signal_date]

    return build


@timed("backtest.run_backtest")
def run_backtest(db: Session, user: User, payload: BacktestRunRequest) -> dict:
    user_strategy, template, base_parameters = _strategy_from_request(db, user, payload.strategy_id)
    parameters = base_parameters
    parameters.update(payload.parameters or {})
    slippage_bps = int(payload.parameters.get("slippage_bps", 10))
    strategy = get_strategy_instance(template.strategy_type)
    dataframe = _load_instrument_prices_for_range(db, payload)
    initial_capital = D(payload.initial_capital)
    exchange = "NSE"
    stock: Stock | None = None
    if payload.instrument_type == "stock" and payload.stock_id is not None:
        stock = db.get(Stock, payload.stock_id)
        exchange = stock.exchange if stock and stock.exchange else "NSE"
    parameter_builder = _parameter_context_builder(
        db,
        template.strategy_type,
        parameters,
        stock_id=payload.stock_id if payload.instrument_type == "stock" else None,
    )

    walk_forward_extra: dict = {}
    benchmark_dataframe = dataframe
    benchmark_start_date = payload.start_date
    benchmark_end_date = payload.end_date
    if payload.walk_forward:
        is_start, is_end, oos_start, oos_end = _walk_forward_split(payload.start_date, payload.end_date)
        is_frame = _slice_by_dates(dataframe, is_start, is_end)
        oos_frame = _slice_by_dates(dataframe, oos_start, oos_end)
        if is_frame.empty or oos_frame.empty:
            raise HTTPException(status_code=400, detail="Insufficient data for walk-forward split")

        is_metrics = _simulate_backtest(
            is_frame,
            strategy,
            parameters,
            initial_capital,
            slippage_bps=slippage_bps,
            execution_mode=payload.execution_mode,
            intrabar_assumption=payload.intrabar_assumption,
            cost_model=payload.cost_model,
            exchange=exchange,
            parameter_context_builder=parameter_builder,
        )
        oos_metrics = _simulate_backtest(
            oos_frame,
            strategy,
            parameters,
            initial_capital,
            slippage_bps=slippage_bps,
            execution_mode=payload.execution_mode,
            intrabar_assumption=payload.intrabar_assumption,
            cost_model=payload.cost_model,
            exchange=exchange,
            parameter_context_builder=parameter_builder,
        )
        is_sharpe = float(is_metrics["sharpe_float"])
        oos_sharpe = float(oos_metrics["sharpe_float"])
        overfitting = oos_sharpe / is_sharpe if is_sharpe else 0.0

        walk_forward_extra = {
            "walk_forward_enabled": True,
            "is_start_date": is_start,
            "is_end_date": is_end,
            "is_total_return_pct": is_metrics["total_return_pct"],
            "is_sharpe_ratio": is_metrics["sharpe_ratio"],
            "is_max_drawdown_pct": is_metrics["max_drawdown_pct"],
            "is_win_rate": is_metrics["win_rate"],
            "is_num_trades": is_metrics["num_trades"],
            "oos_start_date": oos_start,
            "oos_end_date": oos_end,
            "oos_total_return_pct": oos_metrics["total_return_pct"],
            "oos_sharpe_ratio": oos_metrics["sharpe_ratio"],
            "oos_max_drawdown_pct": oos_metrics["max_drawdown_pct"],
            "oos_win_rate": oos_metrics["win_rate"],
            "oos_num_trades": oos_metrics["num_trades"],
            "overfitting_score": Decimal(str(round(overfitting, 4))),
        }
        metrics = is_metrics
        equity_curve = is_metrics["equity_curve"]
        simulated_trades = is_metrics["simulated_trades"]
        benchmark_dataframe = is_frame
        benchmark_start_date = is_start
        benchmark_end_date = is_end
    else:
        metrics = _simulate_backtest(
            dataframe,
            strategy,
            parameters,
            initial_capital,
            slippage_bps=slippage_bps,
            execution_mode=payload.execution_mode,
            intrabar_assumption=payload.intrabar_assumption,
            cost_model=payload.cost_model,
            exchange=exchange,
            parameter_context_builder=parameter_builder,
        )
        equity_curve = metrics["equity_curve"]
        simulated_trades = metrics["simulated_trades"]

    benchmark = compare_to_benchmark(
        db,
        benchmark_code=payload.benchmark_code,
        strategy_equity_curve=equity_curve,
        instrument_dataframe=benchmark_dataframe,
        initial_capital=initial_capital,
        start_date=benchmark_start_date,
        end_date=benchmark_end_date,
        stock=stock,
    )

    run = BacktestRun(
        user_id=user.id,
        user_strategy_id=user_strategy.id if user_strategy else None,
        stock_id=payload.stock_id if payload.instrument_type == "stock" else None,
        index_fund_id=payload.index_fund_id if payload.instrument_type == "index_fund" else None,
        start_date=payload.start_date,
        end_date=payload.end_date,
        initial_capital=payload.initial_capital,
        final_value=metrics["final_value"],
        total_return_pct=metrics["total_return_pct"],
        max_drawdown_pct=metrics["max_drawdown_pct"],
        sharpe_ratio=metrics["sharpe_ratio"],
        win_rate=metrics["win_rate"],
        total_trades=metrics["num_trades"],
        execution_mode=payload.execution_mode,
        intrabar_assumption=payload.intrabar_assumption,
        cost_model=payload.cost_model,
        gross_pnl=metrics["gross_pnl"],
        total_charges=metrics["total_charges"],
        slippage_cost=metrics["slippage_cost"],
        net_pnl=metrics["net_pnl"],
        gross_return_pct=metrics["gross_return_pct"],
        net_return_pct=metrics["net_return_pct"],
        benchmark_code=benchmark["benchmark_code"],
        benchmark_symbol=benchmark["benchmark_symbol"],
        benchmark_name=benchmark["benchmark_name"],
        benchmark_return=benchmark["benchmark_return"],
        excess_return=benchmark["excess_return"],
        alpha=benchmark["alpha"],
        beta=benchmark["beta"],
        tracking_error=benchmark["tracking_error"],
        information_ratio=benchmark["information_ratio"],
        upside_capture=benchmark["upside_capture"],
        downside_capture=benchmark["downside_capture"],
        benchmark_warnings=benchmark["benchmark_warnings"],
        walk_forward_enabled=payload.walk_forward,
        is_sharpe_ratio=walk_forward_extra.get("is_sharpe_ratio") if payload.walk_forward else None,
        oos_sharpe_ratio=walk_forward_extra.get("oos_sharpe_ratio") if payload.walk_forward else None,
        oos_total_return_pct=walk_forward_extra.get("oos_total_return_pct") if payload.walk_forward else None,
        oos_max_drawdown_pct=walk_forward_extra.get("oos_max_drawdown_pct") if payload.walk_forward else None,
        overfitting_score=walk_forward_extra.get("overfitting_score") if payload.walk_forward else None,
    )
    db.add(run)
    db.flush()
    for trade in simulated_trades:
        db.add(
            BacktestTrade(
                backtest_id=run.id,
                stock_id=payload.stock_id if payload.instrument_type == "stock" else None,
                index_fund_id=payload.index_fund_id if payload.instrument_type == "index_fund" else None,
                side=trade["side"],
                quantity=trade["quantity"],
                price=trade["price"],
                trade_date=trade["trade_date"],
                pnl=trade["pnl"],
                signal_date=trade.get("signal_date"),
                quoted_price=trade.get("quoted_price"),
                gross_pnl=trade.get("gross_pnl", Decimal("0")),
                charges=trade.get("charges", Decimal("0")),
                slippage_cost=trade.get("slippage_cost", Decimal("0")),
                net_pnl=trade.get("net_pnl", trade["pnl"]),
                charges_breakdown=trade.get("charges_breakdown"),
                reason=trade["reason"],
            )
        )
    db.commit()
    db.refresh(run)
    response = {
        "run": run,
        "equity_curve": equity_curve,
        "benchmark_curve": benchmark["benchmark_curve"],
        "total_charges": metrics.get("total_charges", Decimal("0")),
        "total_charges_pct": metrics.get("total_charges_pct", Decimal("0")),
    }
    response.update(walk_forward_extra)
    return response
