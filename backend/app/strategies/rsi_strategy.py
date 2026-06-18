import pandas as pd

from app.strategies.base import BaseStrategy, SignalResult
from app.strategies.risk_management import enrich_signal_with_atr


class RSIStrategy(BaseStrategy):
    """Mean-reversion strategy driven by the Relative Strength Index (RSI).

    RSI measures the speed/magnitude of recent price moves on a 0-100 scale.
    This strategy buys when RSI drops below `oversold` (price has fallen
    sharply and may be due for a bounce) and sells when RSI rises above
    `overbought` (price has rallied sharply and may be due for a pullback).
    Confidence scales with how far RSI is past its threshold, capped at 100.
    """

    name = "RSI Mean Reversion"
    strategy_type = "rsi"
    default_parameters = {
        "rsi_period": 14,
        "oversold": 35,
        "overbought": 65,
        "min_bars": 30,
        "atr_period": 14,
        "atr_multiplier": 1.5,
        # Opt-in trend filter (see `_apply_trend_filter`). Off by default so
        # existing backtests/signals are unaffected unless a caller enables it.
        "trend_filter_enabled": False,
        "trend_filter_period": 50,
    }

    def generate_signal(self, prices: pd.DataFrame, parameters: dict | None = None) -> SignalResult:
        params = self.merged_parameters(parameters)
        period = int(params["rsi_period"])
        buy_below = float(params.get("oversold", params.get("buy_rsi_below", 35)))
        sell_above = float(params.get("overbought", params.get("sell_rsi_above", 65)))
        min_bars = int(params.get("min_bars", period + 1))
        if prices.empty or len(prices) < min_bars:
            return SignalResult("HOLD", 0, "Not enough candles for RSI", {})

        # Classic Wilder-style RSI computed with a simple rolling mean of
        # gains/losses (an approximation of Wilder's smoothing, but the one
        # this codebase uses consistently — see macd_strategy._rsi_series):
        #   RS  = avg(gain over `period`) / avg(loss over `period`)
        #   RSI = 100 - 100 / (1 + RS)
        # `loss.mask(loss == 0)` avoids a divide-by-zero when there were no
        # down-bars in the window; `fillna(50)` treats that as "neutral".
        close = prices["close"].astype(float)
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss.mask(loss == 0)
        rsi = (100 - (100 / (1 + rs))).fillna(50)
        latest_rsi = float(rsi.iloc[-1])

        if latest_rsi < buy_below:
            confidence = min(100, (buy_below - latest_rsi) * 3)
            result = SignalResult(
                "BUY",
                confidence,
                f"RSI {latest_rsi:.2f} is below buy threshold {buy_below}",
                {"rsi": latest_rsi},
            )
        elif latest_rsi > sell_above:
            confidence = min(100, (latest_rsi - sell_above) * 3)
            result = SignalResult(
                "SELL",
                confidence,
                f"RSI {latest_rsi:.2f} is above sell threshold {sell_above}",
                {"rsi": latest_rsi},
            )
        else:
            result = SignalResult("HOLD", 50, f"RSI {latest_rsi:.2f} is neutral", {"rsi": latest_rsi})

        if params.get("trend_filter_enabled") and result.signal_type in {"BUY", "SELL"}:
            result = _apply_trend_filter(prices, result, params)

        return enrich_signal_with_atr(prices, result, params, self.strategy_type)


def _apply_trend_filter(prices: pd.DataFrame, result: SignalResult, params: dict) -> SignalResult:
    """Suppress mean-reversion signals that fight a strong prevailing trend.

    Buying "oversold" into a confirmed downtrend (or selling "overbought"
    into a confirmed uptrend) is the classic falling-knife / runaway-rocket
    mistake: RSI can stay pinned near an extreme for a long stretch while the
    underlying trend just keeps going. When `trend_filter_period` bars of
    history are available, this compares the latest close to its long-period
    SMA -- a close below the SMA defines a downtrend (BUY signals are
    demoted to HOLD) and a close above defines an uptrend (SELL signals are
    demoted). If there isn't yet enough history to compute the trend SMA,
    the original signal passes through unchanged rather than blocking trades
    on too little data.
    """
    period = int(params.get("trend_filter_period", 50))
    if len(prices) < period:
        return result

    close = prices["close"].astype(float)
    trend_sma = float(close.rolling(period).mean().iloc[-1])
    latest_close = float(close.iloc[-1])
    indicators = dict(result.indicators)
    indicators["trend_filter_sma"] = round(trend_sma, 4)
    indicators["trend_filter_period"] = period

    if result.signal_type == "BUY" and latest_close < trend_sma:
        return SignalResult(
            "HOLD",
            0,
            f"BUY suppressed by trend filter: price {latest_close:.2f} is below "
            f"its {period}-bar SMA {trend_sma:.2f} (downtrend) — {result.reason}",
            indicators,
        )
    if result.signal_type == "SELL" and latest_close > trend_sma:
        return SignalResult(
            "HOLD",
            0,
            f"SELL suppressed by trend filter: price {latest_close:.2f} is above "
            f"its {period}-bar SMA {trend_sma:.2f} (uptrend) — {result.reason}",
            indicators,
        )
    return SignalResult(result.signal_type, result.confidence_score, result.reason, indicators)
