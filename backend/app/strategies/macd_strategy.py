from __future__ import annotations

import math

import pandas as pd

from app.strategies.base import BaseStrategy, SignalResult
from app.strategies.risk_management import enrich_signal_with_atr


def _wilder_atr(df: pd.DataFrame, period: int = 14) -> float:
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
    return float(tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean().iloc[-1])


def _rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    return (100 - (100 / (1 + rs))).fillna(50)


def compute_macd_indicators(
    prices: pd.DataFrame,
    params: dict,
) -> dict | None:
    fast_period = int(params.get("fast_period", 12))
    slow_period = int(params.get("slow_period", 26))
    signal_period = int(params.get("signal_period", 9))
    rsi_period = int(params.get("rsi_period", 14))
    min_bars = int(params.get("min_bars", 60))

    if prices.empty or len(prices) < min_bars:
        return None

    close = prices["close"].astype(float)
    ema_fast = close.ewm(span=fast_period, adjust=False).mean()
    ema_slow = close.ewm(span=slow_period, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
    histogram = macd_line - signal_line
    rsi = _rsi_series(close, rsi_period)

    return {
        "macd": float(macd_line.iloc[-1]),
        "signal_line": float(signal_line.iloc[-1]),
        "histogram": float(histogram.iloc[-1]),
        "rsi": float(rsi.iloc[-1]),
        "ema_fast": float(ema_fast.iloc[-1]),
        "ema_slow": float(ema_slow.iloc[-1]),
        "macd_prev": float(macd_line.iloc[-2]),
        "signal_prev": float(signal_line.iloc[-2]),
        "histogram_prev": float(histogram.iloc[-2]),
        "histogram_now": float(histogram.iloc[-1]),
        "atr": _wilder_atr(prices, int(params.get("atr_period", 14))),
    }


def macd_crossover_signal(prices: pd.DataFrame, params: dict) -> SignalResult:
    """MACD crossover with RSI filter (shared by strategy and algo findings)."""
    data = compute_macd_indicators(prices, params)
    if data is None:
        return SignalResult("HOLD", 0, "Not enough candles for MACD", {})

    rsi_buy_max = float(params.get("rsi_buy_max", 65))
    rsi_sell_min = float(params.get("rsi_sell_min", 55))
    macd, signal = data["macd"], data["signal_line"]
    macd_prev, signal_prev = data["macd_prev"], data["signal_prev"]
    histogram_now = data["histogram_now"]
    histogram_prev = data["histogram_prev"]
    rsi_val = data["rsi"]
    atr = data["atr"]

    crossed_above = macd_prev <= signal_prev and macd > signal
    crossed_below = macd_prev >= signal_prev and macd < signal
    no_recent_cross = not (
        (macd_prev <= signal_prev and macd > signal)
        or (macd_prev >= signal_prev and macd < signal)
    )

    indicators = {
        "macd": data["macd"],
        "signal_line": data["signal_line"],
        "histogram": data["histogram"],
        "rsi": data["rsi"],
        "ema_fast": data["ema_fast"],
        "ema_slow": data["ema_slow"],
        "status": "ok",
    }

    if crossed_above and histogram_now > 0 and 40 <= rsi_val <= rsi_buy_max:
        hist_component = (histogram_now / atr * 30) if atr > 0 else 0
        confidence = min(95, 50 + hist_component)
        reason = (
            f"MACD crossed above signal ({macd:.2f} > {signal:.2f}); "
            f"RSI at {rsi_val:.1f} — not overbought."
        )
        return SignalResult("BUY", confidence, reason, indicators)

    if crossed_below and histogram_now < 0 and rsi_val > rsi_sell_min:
        hist_component = (abs(histogram_now) / atr * 30) if atr > 0 else 0
        confidence = min(95, 50 + hist_component)
        reason = (
            f"MACD crossed below signal ({macd:.2f} < {signal:.2f}); "
            f"RSI at {rsi_val:.1f} elevated."
        )
        return SignalResult("SELL", confidence, reason, indicators)

    if no_recent_cross or (histogram_prev * histogram_now >= 0 and not crossed_above and not crossed_below):
        return SignalResult(
            "HOLD",
            40,
            "No MACD crossover in the last 2 bars",
            indicators,
        )

    return SignalResult("HOLD", 40, "MACD conditions not met for entry", indicators)


def macd_rsi_composite_signal(prices: pd.DataFrame, params: dict) -> SignalResult:
    """MACD + RSI composite scoring for algo findings."""
    base = macd_crossover_signal(prices, params)
    data = compute_macd_indicators(prices, params)
    if data is None:
        return SignalResult("HOLD", 45, "Not enough candles for MACD + RSI composite", {"status": "ok"})

    rsi_val = data["rsi"]
    macd, signal = data["macd"], data["signal_line"]
    macd_prev, signal_prev = data["macd_prev"], data["signal_prev"]
    crossed_above = macd_prev <= signal_prev and macd > signal
    crossed_below = macd_prev >= signal_prev and macd < signal

    indicators = {
        "macd": data["macd"],
        "signal_line": data["signal_line"],
        "histogram": data["histogram"],
        "rsi": rsi_val,
        "status": "ok",
    }

    if crossed_above:
        if 40 <= rsi_val <= 60:
            return SignalResult(
                "BUY",
                92,
                f"MACD bullish crossover; RSI {rsi_val:.1f} in sweet spot (40–60).",
                indicators,
            )
        if 60 < rsi_val <= 70:
            return SignalResult(
                "BUY",
                70,
                f"MACD bullish crossover; RSI {rsi_val:.1f} — late trend entry.",
                indicators,
            )
        if rsi_val > 70:
            return SignalResult(
                "HOLD",
                55,
                "MACD bullish but RSI overbought — wait for pullback",
                indicators,
            )

    if crossed_below:
        if rsi_val > 60:
            return SignalResult(
                "SELL",
                90,
                f"MACD bearish crossover; RSI {rsi_val:.1f} elevated.",
                indicators,
            )
        if rsi_val < 40:
            return SignalResult(
                "HOLD",
                55,
                "MACD bearish but RSI oversold — possible bounce",
                indicators,
            )

    if base.signal_type in {"BUY", "SELL"}:
        return SignalResult(base.signal_type, base.confidence_score, base.reason, indicators)

    return SignalResult("HOLD", 45, "No MACD crossover", indicators)


class MACDStrategy(BaseStrategy):
    name = "MACD + RSI Filter"
    strategy_type = "macd"
    default_parameters = {
        "fast_period": 12,
        "slow_period": 26,
        "signal_period": 9,
        "rsi_period": 14,
        "rsi_buy_max": 65,
        "rsi_sell_min": 55,
        "min_bars": 60,
        "atr_period": 14,
        "atr_multiplier": 2.0,
    }

    def generate_signal(self, prices: pd.DataFrame, parameters: dict | None = None) -> SignalResult:
        params = self.merged_parameters(parameters)
        result = macd_crossover_signal(prices, params)
        if not math.isfinite(result.confidence_score):
            result = SignalResult(result.signal_type, 0, result.reason, result.indicators)
        return enrich_signal_with_atr(prices, result, params, self.strategy_type)
