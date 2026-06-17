from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR

import pandas as pd

from app.strategies.base import SignalResult

STRATEGY_ATR_MULTIPLIERS: dict[str, float] = {
    "rsi": 1.5,
    "sma_crossover": 2.5,
    "macd": 2.0,
    "breakout": 1.5,
    "vwap": 1.0,
    "ou_process": 2.0,
    "kalman_filter": 2.0,
    "garch": 3.0,
    "tree_ensemble": 2.0,
    "sequential_deep_learning": 2.0,
}


def calculate_atr(df: pd.DataFrame, atr_period: int = 14) -> float:
    """Average True Range (Wilder smoothing) over the trailing `atr_period` bars.

    True range for each bar is the largest of: high-low, |high - prev_close|,
    |low - prev_close| -- so gaps between bars are captured, not just each
    bar's own intrabar range. `calculate_atr_stop` builds stop-loss/
    take-profit levels on top of this; strategies that need a raw volatility
    reading for their own purposes (e.g. sizing a breakout-confirmation
    buffer so it scales with each instrument's typical daily range rather
    than using one fixed price/percentage for every stock) can call this
    directly instead of duplicating the calculation.
    """
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    tr = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return float(tr.ewm(alpha=1 / atr_period, min_periods=atr_period, adjust=False).mean().iloc[-1])


def calculate_atr_stop(
    df: pd.DataFrame,
    entry_price: float,
    atr_period: int = 14,
    atr_multiplier: float = 2.0,
    side: str = "BUY",
) -> dict:
    """ATR-based stop loss (Wilder) and 2:1 take-profit."""
    atr = calculate_atr(df, atr_period=atr_period)
    stop_distance = atr * atr_multiplier

    if side == "BUY":
        stop_price = entry_price - stop_distance
        take_profit = entry_price + (stop_distance * 2)
    else:
        stop_price = entry_price + stop_distance
        take_profit = entry_price - (stop_distance * 2)

    stop_pct = (stop_distance / entry_price) * 100 if entry_price else 0.0

    return {
        "atr": round(atr, 4),
        "stop_price": round(stop_price, 2),
        "stop_pct": round(stop_pct, 4),
        "take_profit_price": round(take_profit, 2),
    }


def _atr_settings(parameters: dict, strategy_type: str) -> tuple[int, float]:
    period = int(parameters.get("atr_period", 14))
    multiplier = float(
        parameters.get("atr_multiplier")
        or STRATEGY_ATR_MULTIPLIERS.get(strategy_type, 2.0)
    )
    return period, multiplier


def enrich_signal_with_atr(
    prices: pd.DataFrame,
    result: SignalResult,
    parameters: dict,
    strategy_type: str,
) -> SignalResult:
    """Attach ATR-derived stop-loss / take-profit prices to a BUY or SELL signal.

    Strategies call this as the last step of `generate_signal` so the
    backtest engine and live execution have a concrete stop/target to manage
    the position with — the strategy itself only decides direction, not
    risk management. HOLD signals (and signals on data missing OHLC columns)
    pass through untouched.
    """
    if result.signal_type not in {"BUY", "SELL"}:
        return result
    if prices.empty or not {"high", "low", "close"}.issubset(prices.columns):
        return result

    entry_price = float(prices["close"].iloc[-1])
    atr_period, atr_multiplier = _atr_settings(parameters, strategy_type)
    atr_stop = calculate_atr_stop(
        prices,
        entry_price,
        atr_period=atr_period,
        atr_multiplier=atr_multiplier,
        side=result.signal_type,
    )
    indicators = dict(result.indicators)
    indicators.update(
        {
            "atr": atr_stop["atr"],
            "stop_price": atr_stop["stop_price"],
            "stop_pct": atr_stop["stop_pct"],
            "stop_loss_pct": atr_stop["stop_pct"],
            "take_profit_price": atr_stop["take_profit_price"],
        }
    )
    return SignalResult(
        result.signal_type,
        result.confidence_score,
        result.reason,
        indicators,
    )


def calculate_position_size(
    portfolio_value,
    current_cash,
    current_price,
    risk_per_trade_pct,
    stop_loss_pct=None,
    max_position_size_pct=10.0,
    *,
    atr_stop: dict | None = None,
) -> int:
    """Size a position using fixed-fractional risk, capped by exposure and cash.

    Three independent constraints are computed and the *smallest* wins
    (each expressed as a position value, then converted to a whole-share
    quantity, rounded down so we never overspend):

      - position_by_risk: how large a position can be while risking only
        `risk_per_trade_pct` of the portfolio if the stop is hit —
        `risk_amount / stop_distance_pct`. A tighter stop (smaller
        `effective_stop`) allows a *larger* position for the same risk
        budget, and vice versa.
      - position_by_cap: a hard ceiling of `max_position_size_pct` of the
        portfolio, regardless of how favorable the risk math looks — this
        bounds concentration risk in any single name.
      - current_cash: you obviously cannot buy more than you can afford.

    `atr_stop["stop_pct"]`, when supplied, overrides `stop_loss_pct` as the
    basis for the risk calculation (ATR-based stops adapt to each
    instrument's volatility rather than using one fixed percentage for all).
    Returns 0 if the price or stop distance is non-positive (sizing would be
    undefined/infinite).
    """
    portfolio_value = Decimal(str(portfolio_value))
    current_cash = Decimal(str(current_cash))
    current_price = Decimal(str(current_price))
    risk_per_trade_pct = Decimal(str(risk_per_trade_pct))
    effective_stop = (
        Decimal(str(atr_stop["stop_pct"]))
        if atr_stop
        else Decimal(str(stop_loss_pct if stop_loss_pct is not None else 5.0))
    )
    max_position_size_pct = Decimal(str(max_position_size_pct))

    if current_price <= 0 or effective_stop <= 0:
        return 0
    risk_amount = portfolio_value * risk_per_trade_pct / Decimal("100")
    position_by_risk = risk_amount / (effective_stop / Decimal("100"))
    position_by_cap = portfolio_value * max_position_size_pct / Decimal("100")
    final_position_value = min(position_by_risk, position_by_cap, current_cash)
    return int((final_position_value / current_price).to_integral_value(rounding=ROUND_FLOOR))
