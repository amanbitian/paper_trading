from __future__ import annotations

import math

import pandas as pd

from app.strategies.base import BaseStrategy, SignalResult
from app.strategies.risk_management import enrich_signal_with_atr


def _clean_float(value, decimals: int = 4) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, decimals)


def _clip(value: float, lower: float = -1.0, upper: float = 1.0) -> float:
    if not math.isfinite(value):
        return 0.0
    return max(lower, min(upper, value))


def _prepare_prices(prices: pd.DataFrame) -> pd.DataFrame:
    if prices.empty:
        return prices
    frame = prices.copy()
    for column in ["open", "high", "low", "close", "volume"]:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["close"]).loc[lambda data: data["close"] > 0]


class QualityMomentumStrategy(BaseStrategy):
    name = "Quality Momentum"
    strategy_type = "quality_momentum"
    default_parameters = {
        "momentum_long_window": 252,
        "momentum_skip_window": 21,
        "momentum_short_window": 63,
        "trend_window": 200,
        "volatility_window": 63,
        "min_bars": 275,
        "buy_score_above": 0.35,
        "sell_score_below": -0.20,
        "trend_exit_below_pct": -4.0,
        "max_annualized_vol_pct": 45.0,
        "min_average_volume": 100000,
        "fundamental_weight": 0.30,
        "atr_period": 14,
        "atr_multiplier": 2.5,
        "max_position_size_pct": 10,
        "stop_loss_pct": 6,
    }

    def generate_signal(self, prices: pd.DataFrame, parameters: dict | None = None) -> SignalResult:
        params = self.merged_parameters(parameters)
        frame = _prepare_prices(prices)
        long_window = int(params["momentum_long_window"])
        skip_window = int(params["momentum_skip_window"])
        short_window = int(params["momentum_short_window"])
        trend_window = int(params["trend_window"])
        volatility_window = int(params["volatility_window"])
        required = max(
            int(params.get("min_bars", 0)),
            long_window + skip_window + 1,
            trend_window + 1,
            volatility_window + 1,
        )
        if len(frame) < required:
            return SignalResult("HOLD", 0, "Not enough candles for quality momentum", {})

        close = frame["close"].astype(float)
        volume = frame["volume"].fillna(0).astype(float)
        latest_close = float(close.iloc[-1])
        long_ref = float(close.iloc[-long_window - skip_window])
        skip_close = float(close.iloc[-skip_window])
        short_ref = float(close.iloc[-short_window])
        momentum_long_pct = ((skip_close / long_ref) - 1) * 100 if long_ref else 0.0
        momentum_short_pct = ((latest_close / short_ref) - 1) * 100 if short_ref else 0.0

        trend_sma = close.rolling(trend_window).mean()
        latest_trend = float(trend_sma.iloc[-1])
        trend_vs_sma_pct = ((latest_close / latest_trend) - 1) * 100 if latest_trend else 0.0
        returns = close.pct_change().dropna()
        annualized_vol_pct = float(returns.tail(volatility_window).std() * math.sqrt(252) * 100)
        average_volume = float(volume.tail(20).mean())

        fundamental_score = params.get("fundamental_quality_score")
        fundamental_score_float = (
            float(fundamental_score)
            if fundamental_score is not None and str(fundamental_score).strip() != ""
            else None
        )
        fundamental_weight = float(params.get("fundamental_weight", 0.30))
        if fundamental_score_float is None or not math.isfinite(fundamental_score_float):
            fundamental_weight = 0.0
            fundamental_score_float = 0.0

        max_vol = max(float(params["max_annualized_vol_pct"]), 1.0)
        momentum_score = (
            0.55 * _clip(momentum_long_pct / 35.0)
            + 0.20 * _clip(momentum_short_pct / 15.0)
            + 0.15 * _clip(trend_vs_sma_pct / 8.0)
            - 0.10 * _clip((annualized_vol_pct - max_vol) / max_vol, 0.0, 1.0)
        )
        score = (1.0 - fundamental_weight) * momentum_score + fundamental_weight * _clip(fundamental_score_float)
        score = round(score, 4)

        buy_threshold = float(params["buy_score_above"])
        sell_threshold = float(params["sell_score_below"])
        trend_exit_below_pct = float(params["trend_exit_below_pct"])
        min_average_volume = float(params["min_average_volume"])

        if average_volume < min_average_volume:
            action = "HOLD"
            reason = f"Average volume is {average_volume:,.0f}, below the liquidity floor."
        elif trend_vs_sma_pct <= trend_exit_below_pct or score <= sell_threshold:
            action = "SELL"
            reason = f"Quality momentum score is {score:.2f}; trend is {trend_vs_sma_pct:.2f}% versus SMA."
        elif score >= buy_threshold and trend_vs_sma_pct > 0 and momentum_long_pct > 0:
            action = "BUY"
            reason = f"Quality momentum score is {score:.2f} with positive 12-1 month momentum."
        else:
            action = "HOLD"
            reason = f"Quality momentum score is {score:.2f}; no threshold crossed."

        confidence = round(min(95, max(0, 45 + abs(score) * 70)), 2) if action != "HOLD" else 0
        result = SignalResult(
            action,
            confidence,
            reason,
            {
                "quality_momentum_score": _clean_float(score),
                "momentum_long_pct": _clean_float(momentum_long_pct),
                "momentum_short_pct": _clean_float(momentum_short_pct),
                "trend_vs_sma_pct": _clean_float(trend_vs_sma_pct),
                "annualized_vol_pct": _clean_float(annualized_vol_pct),
                "average_volume": _clean_float(average_volume, decimals=0),
                "fundamental_quality_score": _clean_float(fundamental_score_float),
                "fundamental_source": params.get("fundamental_source"),
                "fundamental_as_of": params.get("fundamental_as_of"),
                "fundamental_quality_fields": params.get("fundamental_quality_fields", {}),
            },
        )
        return enrich_signal_with_atr(prices, result, params, self.strategy_type)
