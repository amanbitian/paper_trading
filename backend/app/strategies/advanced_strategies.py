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


def _prepare_prices(prices: pd.DataFrame) -> pd.DataFrame:
    if prices.empty:
        return prices
    frame = prices.copy()
    for column in ["open", "high", "low", "close", "volume"]:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["close"]).loc[lambda data: data["close"] > 0]


def _threshold_action(value: float, buy_below: float, sell_above: float) -> str:
    if not math.isfinite(value):
        return "HOLD"
    if value <= buy_below:
        return "BUY"
    if value >= sell_above:
        return "SELL"
    return "HOLD"


def _directional_action(value: float, sell_below: float, buy_above: float) -> str:
    if not math.isfinite(value):
        return "HOLD"
    if value >= buy_above:
        return "BUY"
    if value <= sell_below:
        return "SELL"
    return "HOLD"


def _confidence(distance: float, multiplier: float = 12, base: float = 45) -> float:
    if not math.isfinite(distance):
        return 0
    return round(min(95, max(0, base + abs(distance) * multiplier)), 2)


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    return (100 - (100 / (1 + rs))).fillna(50)


class VWAPStrategy(BaseStrategy):
    name = "VWAP"
    strategy_type = "vwap"
    default_parameters = {
        "window": 20,
        "threshold_pct": 2.0,
        "min_bars": 20,
        "atr_period": 14,
        "atr_multiplier": 1.0,
    }

    def generate_signal(self, prices: pd.DataFrame, parameters: dict | None = None) -> SignalResult:
        params = self.merged_parameters(parameters)
        frame = _prepare_prices(prices)
        window = int(params.get("window", params.get("vwap_window", 20)))
        threshold = float(params.get("threshold_pct", 2.0))
        min_bars = int(params.get("min_bars", window))
        if len(frame) < min_bars:
            return SignalResult("HOLD", 0, "Not enough candles for VWAP proxy", {})

        high = frame["high"].fillna(frame["close"])
        low = frame["low"].fillna(frame["close"])
        typical_price = (high + low + frame["close"]) / 3
        volume = frame["volume"].fillna(0).replace(0, pd.NA)
        benchmark = (typical_price * volume).rolling(window).sum() / volume.rolling(window).sum()
        status = "daily_vwap_proxy"
        if pd.isna(benchmark.iloc[-1]):
            benchmark = typical_price.rolling(window).mean()
            status = "typical_price_fallback"

        latest_close = float(frame["close"].iloc[-1])
        latest_vwap = float(benchmark.iloc[-1])
        delta_pct = ((latest_close / latest_vwap) - 1) * 100
        action = _threshold_action(delta_pct, -threshold, threshold)
        result = SignalResult(
            action,
            _confidence(delta_pct),
            f"Close is {delta_pct:.2f}% versus the {window}-session VWAP proxy.",
            {
                "latest_close": _clean_float(latest_close),
                "vwap": _clean_float(latest_vwap),
                "delta_pct": _clean_float(delta_pct),
                "status": status,
            },
        )
        return enrich_signal_with_atr(prices, result, params, self.strategy_type)


class TWAPStrategy(BaseStrategy):
    name = "TWAP"
    strategy_type = "twap"
    default_parameters = {
        "twap_window": 20,
        "buy_below_pct": -1.25,
        "sell_above_pct": 1.25,
        "max_position_size_pct": 10,
        "stop_loss_pct": 5,
    }

    def generate_signal(self, prices: pd.DataFrame, parameters: dict | None = None) -> SignalResult:
        params = self.merged_parameters(parameters)
        frame = _prepare_prices(prices)
        window = int(params["twap_window"])
        if len(frame) < window:
            return SignalResult("HOLD", 0, "Not enough candles for TWAP proxy", {})

        twap = frame["close"].rolling(window).mean()
        latest_close = float(frame["close"].iloc[-1])
        latest_twap = float(twap.iloc[-1])
        delta_pct = ((latest_close / latest_twap) - 1) * 100
        action = _threshold_action(
            delta_pct,
            float(params["buy_below_pct"]),
            float(params["sell_above_pct"]),
        )
        return SignalResult(
            action,
            _confidence(delta_pct, multiplier=10),
            f"Close is {delta_pct:.2f}% versus the {window}-session time-weighted average.",
            {
                "latest_close": _clean_float(latest_close),
                "twap": _clean_float(latest_twap),
                "delta_pct": _clean_float(delta_pct),
            },
        )


class ImplementationShortfallStrategy(BaseStrategy):
    name = "Implementation Shortfall"
    strategy_type = "implementation_shortfall"
    default_parameters = {
        "arrival_window": 5,
        "buy_improvement_pct": -1.0,
        "sell_deterioration_pct": 1.0,
        "trend_window": 20,
        "max_position_size_pct": 8,
        "stop_loss_pct": 5,
    }

    def generate_signal(self, prices: pd.DataFrame, parameters: dict | None = None) -> SignalResult:
        params = self.merged_parameters(parameters)
        frame = _prepare_prices(prices)
        arrival_window = int(params["arrival_window"])
        trend_window = int(params["trend_window"])
        required = max(arrival_window + 1, trend_window)
        if len(frame) < required:
            return SignalResult("HOLD", 0, "Not enough candles for implementation shortfall proxy", {})

        close = frame["close"]
        arrival = float(close.iloc[-arrival_window - 1])
        latest = float(close.iloc[-1])
        trend = close.rolling(trend_window).mean()
        trend_pct = ((latest / float(trend.iloc[-1])) - 1) * 100
        shortfall_pct = ((latest / arrival) - 1) * 100
        if shortfall_pct <= float(params["buy_improvement_pct"]) and trend_pct >= -2:
            action = "BUY"
        elif shortfall_pct >= float(params["sell_deterioration_pct"]) and trend_pct <= 2:
            action = "SELL"
        else:
            action = "HOLD"
        return SignalResult(
            action,
            _confidence(shortfall_pct, multiplier=8),
            f"Latest close is {shortfall_pct:.2f}% from the {arrival_window}-session arrival benchmark.",
            {
                "arrival_close": _clean_float(arrival),
                "latest_close": _clean_float(latest),
                "shortfall_pct": _clean_float(shortfall_pct),
                "trend_vs_sma_pct": _clean_float(trend_pct),
            },
        )


class PairsCointegrationStrategy(BaseStrategy):
    name = "Pairs Trading via Cointegration"
    strategy_type = "pairs_cointegration"
    default_parameters = {
        "pair_symbol": "",
        "lookback_window": 120,
        "zscore_entry": 2.0,
        "max_position_size_pct": 8,
        "stop_loss_pct": 5,
    }

    def generate_signal(self, prices: pd.DataFrame, parameters: dict | None = None) -> SignalResult:
        params = self.merged_parameters(parameters)
        pair_symbol = str(params.get("pair_symbol") or "").strip().upper()
        reason = "Pairs trading needs a second asset price series and a cointegration test."
        if pair_symbol:
            reason = f"Pair symbol {pair_symbol} is configured, but pair price history is not available to this runner yet."
        return SignalResult(
            "HOLD",
            0,
            reason,
            {"status": "requires_pair_asset", "pair_symbol": pair_symbol},
        )


class OUProcessStrategy(BaseStrategy):
    name = "Ornstein-Uhlenbeck (OU) Process"
    strategy_type = "ou_process"
    default_parameters = {
        "lookback": 60,
        "z_entry": 2.0,
        "z_exit": 0.5,
        "min_bars": 80,
        "atr_period": 14,
        "atr_multiplier": 2.0,
    }

    def generate_signal(self, prices: pd.DataFrame, parameters: dict | None = None) -> SignalResult:
        params = self.merged_parameters(parameters)
        frame = _prepare_prices(prices)
        window = int(params.get("lookback", params.get("mean_window", 60)))
        z_entry = float(params.get("z_entry", 2.0))
        min_bars = int(params.get("min_bars", window))
        if len(frame) < min_bars:
            return SignalResult("HOLD", 0, "Not enough candles for OU mean-reversion proxy", {})

        close = frame["close"]
        mean = close.rolling(window).mean()
        std = close.rolling(window).std()
        z_score = float(((close - mean) / std.replace(0, pd.NA)).iloc[-1])
        if z_score <= -z_entry:
            action = "BUY"
        elif z_score >= z_entry:
            action = "SELL"
        else:
            action = "HOLD"
        result = SignalResult(
            action,
            _confidence(z_score, multiplier=14),
            f"Latest close has a {z_score:.2f} z-score versus its {window}-session mean.",
            {
                "z_score": _clean_float(z_score),
                "rolling_mean": _clean_float(mean.iloc[-1]),
                "rolling_std": _clean_float(std.iloc[-1]),
            },
        )
        return enrich_signal_with_atr(prices, result, params, self.strategy_type)


class KalmanFilterStrategy(BaseStrategy):
    name = "Kalman Filtering"
    strategy_type = "kalman_filter"
    default_parameters = {
        "observation_covariance": 0.1,
        "transition_covariance": 0.01,
        "signal_threshold": 2.5,
        "min_bars": 60,
        "atr_period": 14,
        "atr_multiplier": 2.0,
    }

    def generate_signal(self, prices: pd.DataFrame, parameters: dict | None = None) -> SignalResult:
        params = self.merged_parameters(parameters)
        frame = _prepare_prices(prices)
        window = int(params.get("lookback_window", 120))
        min_bars = int(params.get("min_bars", 60))
        signal_threshold = float(params.get("signal_threshold", 2.5))
        if len(frame) < max(min_bars, 20, window // 2):
            return SignalResult("HOLD", 0, "Not enough candles for Kalman filter proxy", {})

        close = frame["close"].astype(float)
        values = close.to_list()
        recent_diff = pd.Series(values[-window:]).diff().dropna()
        measurement_variance = float(
            recent_diff.var()
            or close.diff().var()
            or float(params.get("observation_covariance", 0.1))
        )
        process_variance = measurement_variance * float(params.get("transition_covariance", 0.01)) * 3
        estimate = values[0]
        error_estimate = 1.0
        filtered: list[float] = []
        for value in values:
            error_estimate += process_variance
            kalman_gain = error_estimate / (error_estimate + measurement_variance)
            estimate = estimate + kalman_gain * (value - estimate)
            error_estimate = (1 - kalman_gain) * error_estimate
            filtered.append(estimate)

        filtered_series = pd.Series(filtered, index=frame.index)
        residual = close - filtered_series
        residual_std = float(residual.tail(window).std() or residual.std() or 1.0)
        residual_z = float(residual.iloc[-1] / residual_std)
        action = _threshold_action(residual_z, -signal_threshold, signal_threshold)
        result = SignalResult(
            action,
            _confidence(residual_z, multiplier=16),
            f"Close is {residual_z:.2f} residual standard deviations from adaptive fair value.",
            {
                "residual_z": _clean_float(residual_z),
                "kalman_fair_value": _clean_float(filtered_series.iloc[-1]),
                "latest_close": _clean_float(close.iloc[-1]),
            },
        )
        return enrich_signal_with_atr(prices, result, params, self.strategy_type)


class SARIMAXBaselineStrategy(BaseStrategy):
    name = "SARIMAX"
    strategy_type = "sarimax"
    default_parameters = {
        "short_return_window": 20,
        "long_return_window": 60,
        "forecast_buy_above_pct": 0.25,
        "forecast_sell_below_pct": -0.25,
        "max_position_size_pct": 8,
        "stop_loss_pct": 5,
    }

    def generate_signal(self, prices: pd.DataFrame, parameters: dict | None = None) -> SignalResult:
        params = self.merged_parameters(parameters)
        frame = _prepare_prices(prices)
        short_window = int(params["short_return_window"])
        long_window = int(params["long_return_window"])
        if len(frame) < long_window + 1:
            return SignalResult("HOLD", 0, "Not enough candles for SARIMAX baseline proxy", {})

        close = frame["close"].astype(float)
        returns = close.pct_change()
        weekday_index = [pd.Timestamp(index).weekday() for index in frame.index]
        weekday_returns = returns.groupby(weekday_index).mean()
        latest_weekday = pd.Timestamp(frame.index[-1]).weekday()
        forecast_pct = float(
            (
                0.55 * returns.tail(short_window).mean()
                + 0.30 * returns.tail(long_window).mean()
                + 0.15 * weekday_returns.get(latest_weekday, 0)
            )
            * 100
        )
        action = _directional_action(
            forecast_pct,
            float(params["forecast_sell_below_pct"]),
            float(params["forecast_buy_above_pct"]),
        )
        return SignalResult(
            action,
            _confidence(forecast_pct, multiplier=30),
            f"Baseline next-session return estimate is {forecast_pct:.2f}%.",
            {
                "forecast_return_pct": _clean_float(forecast_pct),
                "mean_return_short_pct": _clean_float(returns.tail(short_window).mean() * 100),
                "mean_return_long_pct": _clean_float(returns.tail(long_window).mean() * 100),
            },
        )


class GARCHVolatilityStrategy(BaseStrategy):
    name = "GARCH"
    strategy_type = "garch"
    default_parameters = {
        "vol_lookback_short": 20,
        "vol_lookback_long": 60,
        "vol_ratio_threshold": 0.7,
        "min_bars": 80,
        "atr_period": 14,
        "atr_multiplier": 3.0,
    }

    def generate_signal(self, prices: pd.DataFrame, parameters: dict | None = None) -> SignalResult:
        params = self.merged_parameters(parameters)
        frame = _prepare_prices(prices)
        short_window = int(params.get("vol_lookback_short", params.get("short_vol_window", 20)))
        long_window = int(params.get("vol_lookback_long", params.get("long_vol_window", 60)))
        vol_threshold = float(params.get("vol_ratio_threshold", 0.7))
        min_bars = int(params.get("min_bars", long_window + 1))
        if len(frame) < min_bars:
            return SignalResult("HOLD", 0, "Not enough candles for GARCH volatility proxy", {})

        close = frame["close"].astype(float)
        returns = close.pct_change()
        vol_short = returns.rolling(short_window).std() * math.sqrt(252) * 100
        vol_long = returns.rolling(long_window).std() * math.sqrt(252) * 100
        latest_short = float(vol_short.iloc[-1])
        latest_long = float(vol_long.iloc[-1])
        momentum_pct = float(((close.iloc[-1] / close.iloc[-short_window - 1]) - 1) * 100)
        vol_ratio = latest_short / latest_long if latest_long else 1.0
        if vol_ratio > 1.25 and momentum_pct < 0:
            action = "SELL"
        elif vol_ratio < vol_threshold and momentum_pct > 0:
            action = "BUY"
        else:
            action = "HOLD"
        result = SignalResult(
            action,
            _confidence((vol_ratio - 1) * 2 + momentum_pct / 10, multiplier=10),
            f"{short_window}-session annualized volatility is {latest_short:.2f}% versus {latest_long:.2f}% over {long_window} sessions.",
            {
                "volatility_short_pct": _clean_float(latest_short),
                "volatility_long_pct": _clean_float(latest_long),
                "vol_ratio": _clean_float(vol_ratio),
                "momentum_pct": _clean_float(momentum_pct),
            },
        )
        return enrich_signal_with_atr(prices, result, params, self.strategy_type)


class AvellanedaStoikovStrategy(BaseStrategy):
    name = "Avellaneda-Stoikov Model"
    strategy_type = "avellaneda_stoikov"
    default_parameters = {
        "risk_aversion": 0.10,
        "inventory_limit": 100,
        "max_spread_pct": 1.0,
    }

    def generate_signal(self, prices: pd.DataFrame, parameters: dict | None = None) -> SignalResult:
        params = self.merged_parameters(parameters)
        return SignalResult(
            "HOLD",
            0,
            "Avellaneda-Stoikov needs live bid/ask quotes, order-book depth, and inventory state.",
            {
                "status": "requires_order_book_data",
                "risk_aversion": _clean_float(params.get("risk_aversion")),
                "inventory_limit": _clean_float(params.get("inventory_limit")),
            },
        )


class OrderBookImbalanceStrategy(BaseStrategy):
    name = "Order Book Imbalance (OBI) Algos"
    strategy_type = "order_book_imbalance"
    default_parameters = {
        "imbalance_buy_above": 0.20,
        "imbalance_sell_below": -0.20,
        "lookback_events": 100,
    }

    def generate_signal(self, prices: pd.DataFrame, parameters: dict | None = None) -> SignalResult:
        params = self.merged_parameters(parameters)
        return SignalResult(
            "HOLD",
            0,
            "OBI needs Level 2 bid/ask depth snapshots; Yahoo daily candles do not include order-book pressure.",
            {
                "status": "requires_order_book_data",
                "imbalance_buy_above": _clean_float(params.get("imbalance_buy_above")),
                "imbalance_sell_below": _clean_float(params.get("imbalance_sell_below")),
            },
        )


class TreeEnsembleProxyStrategy(BaseStrategy):
    name = "Tree-Based Ensembles (XGBoost / LightGBM)"
    strategy_type = "tree_ensemble"
    default_parameters = {
        "n_estimators": 100,
        "max_depth": 4,
        "feature_window": 20,
        "min_bars": 60,
        "atr_period": 14,
        "atr_multiplier": 2.0,
        "buy_score_above": 0.25,
        "sell_score_below": -0.25,
    }

    def generate_signal(self, prices: pd.DataFrame, parameters: dict | None = None) -> SignalResult:
        params = self.merged_parameters(parameters)
        frame = _prepare_prices(prices)
        feature_window = int(params.get("feature_window", 20))
        short_window = int(params.get("momentum_short_window", feature_window))
        long_window = int(params.get("momentum_long_window", 60))
        trend_window = int(params.get("trend_window", 50))
        required = int(params.get("min_bars", max(short_window, long_window, trend_window, 60) + 1))
        if len(frame) < required:
            return SignalResult("HOLD", 0, "Not enough candles for tree-ensemble feature proxy", {})

        close = frame["close"].astype(float)
        volume = frame["volume"].fillna(0).replace(0, pd.NA).astype(float)
        returns = close.pct_change()
        sma = close.rolling(trend_window).mean()
        volatility_short = returns.rolling(20).std()
        volatility_long = returns.rolling(60).std()
        volume_z = (volume - volume.rolling(60).mean()) / volume.rolling(60).std()
        rsi = _rsi(close)
        momentum_short = ((close / close.shift(short_window)) - 1) * 100
        momentum_long = ((close / close.shift(long_window)) - 1) * 100
        trend_score = ((close / sma) - 1).clip(-0.08, 0.08) / 0.08
        momentum_score = (
            0.55 * momentum_short.clip(-12, 12) / 12
            + 0.45 * momentum_long.clip(-24, 24) / 24
        )
        rsi_score = ((50 - rsi).clip(-25, 25) / 25) * 0.6
        volatility_score = (1 - (volatility_short / volatility_long.replace(0, pd.NA))).clip(-1, 1) * 0.35
        liquidity_score = volume_z.clip(-2, 2).fillna(0) / 10
        feature_score = (
            0.38 * momentum_score
            + 0.28 * trend_score
            + 0.18 * rsi_score
            + volatility_score
            + liquidity_score
        )
        latest_score = float(feature_score.iloc[-1])
        action = _directional_action(
            latest_score,
            float(params["sell_score_below"]),
            float(params["buy_score_above"]),
        )
        result = SignalResult(
            action,
            _confidence(latest_score, multiplier=90),
            f"Daily feature ensemble proxy score is {latest_score:.2f}.",
            {
                "feature_score": _clean_float(latest_score),
                "momentum_short_pct": _clean_float(momentum_short.iloc[-1]),
                "momentum_long_pct": _clean_float(momentum_long.iloc[-1]),
                "rsi": _clean_float(rsi.iloc[-1]),
                "volume_z": _clean_float(volume_z.iloc[-1]),
            },
        )
        return enrich_signal_with_atr(prices, result, params, self.strategy_type)


class SequentialDeepLearningProxyStrategy(BaseStrategy):
    name = "Sequential Deep Learning (LSTMs / Transformers)"
    strategy_type = "sequential_deep_learning"
    default_parameters = {
        "sequence_length": 20,
        "min_positive_ratio": 0.6,
        "momentum_threshold": 0.02,
        "min_bars": 80,
        "atr_period": 14,
        "atr_multiplier": 2.0,
        "buy_score_above": 0.25,
        "sell_score_below": -0.25,
    }

    def generate_signal(self, prices: pd.DataFrame, parameters: dict | None = None) -> SignalResult:
        params = self.merged_parameters(parameters)
        frame = _prepare_prices(prices)
        sequence_window = int(params.get("sequence_length", params.get("sequence_window", 20)))
        ema_fast_span = int(params.get("ema_fast_span", 12))
        ema_slow_span = int(params.get("ema_slow_span", 26))
        min_bars = int(params.get("min_bars", max(sequence_window, ema_slow_span) + 1))
        if len(frame) < min_bars:
            return SignalResult("HOLD", 0, "Not enough candles for sequence proxy", {})

        close = frame["close"].astype(float)
        returns = close.pct_change().fillna(0)
        ema_fast = close.ewm(span=ema_fast_span, adjust=False).mean()
        ema_slow = close.ewm(span=ema_slow_span, adjust=False).mean()
        positive_ratio_series = returns.rolling(sequence_window).apply(
            lambda values: (values > 0).mean(),
            raw=True,
        )
        sequence_score = (
            (returns.rolling(sequence_window).sum() * 100).clip(-12, 12) / 12 * 0.50
            + ((ema_fast / ema_slow) - 1).clip(-0.06, 0.06) / 0.06 * 0.35
            + ((positive_ratio_series - 0.5) * 2).clip(-1, 1) * 0.15
        )
        latest_score = float(sequence_score.iloc[-1])
        positive_ratio = float((returns.tail(sequence_window) > 0).mean())
        sequence_return = float(returns.tail(sequence_window).sum() * 100)
        action = _directional_action(
            latest_score,
            float(params["sell_score_below"]),
            float(params["buy_score_above"]),
        )
        result = SignalResult(
            action,
            _confidence(latest_score, multiplier=90),
            f"Daily sequence proxy score is {latest_score:.2f}; positive-day ratio is {positive_ratio:.2f}.",
            {
                "sequence_score": _clean_float(latest_score),
                "sequence_return_pct": _clean_float(sequence_return),
                "positive_day_ratio": _clean_float(positive_ratio),
                "ema_fast": _clean_float(ema_fast.iloc[-1]),
                "ema_slow": _clean_float(ema_slow.iloc[-1]),
            },
        )
        return enrich_signal_with_atr(prices, result, params, self.strategy_type)
