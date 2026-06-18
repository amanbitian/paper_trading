from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.models.index_fund import IndexFundPrice
from app.models.stock import Stock, StockPrice
from app.services.index_fund_service import index_prices_to_dataframe
from app.services.market_data_service import DAILY_TIMEFRAME, prices_to_dataframe
from app.strategies.macd_strategy import compute_macd_indicators, macd_crossover_signal, macd_rsi_composite_signal
from app.strategies.sector_rotation_strategy import SectorRotationStrategy
from app.utils.observability import timed

MACD_DEFAULT_PARAMS = {
    "fast_period": 12,
    "slow_period": 26,
    "signal_period": 9,
    "rsi_period": 14,
    "rsi_buy_max": 65,
    "rsi_sell_min": 55,
    "min_bars": 60,
}


MIN_SIGNAL_ROWS = 80
DEFAULT_LIMIT = 10000


def _clean_float(value: Any, decimals: int = 4) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, decimals)


def _action_from_threshold(value: float, buy_below: float, sell_above: float) -> str:
    if not math.isfinite(value):
        return "HOLD"
    if value <= buy_below:
        return "BUY"
    if value >= sell_above:
        return "SELL"
    return "HOLD"


def _confidence(distance: float, multiplier: float = 12, base: float = 50) -> float:
    if not math.isfinite(distance):
        return 0
    return round(min(95, max(0, base + abs(distance) * multiplier)), 2)


def _chart(dataframe: pd.DataFrame, title: str, series: dict[str, pd.Series], limit: int = 220) -> dict:
    recent = dataframe.tail(limit)
    x_values = [pd.Timestamp(index).date().isoformat() for index in recent.index]
    chart_series = []
    for name, values in series.items():
        aligned = values.reindex(recent.index)
        chart_series.append(
            {
                "name": name,
                "values": [_clean_float(value) for value in aligned.tolist()],
            }
        )
    return {"title": title, "x": x_values, "series": chart_series}


def _finding(
    algorithm_name: str,
    category: str,
    action: str,
    confidence_score: float,
    status: str,
    data_requirements: str,
    reason: str,
    logic: str,
    indicators: dict[str, Any] | None = None,
    chart: dict | None = None,
) -> dict:
    return {
        "algorithm_name": algorithm_name,
        "category": category,
        "action": action,
        "confidence_score": round(float(confidence_score), 2),
        "status": status,
        "data_requirements": data_requirements,
        "reason": reason,
        "logic": logic,
        "indicators": indicators or {},
        "chart": chart,
    }


def _daily_vwap(dataframe: pd.DataFrame) -> dict:
    typical_price = (dataframe["high"] + dataframe["low"] + dataframe["close"]) / 3
    volume = dataframe["volume"].replace(0, np.nan)
    vwap_20 = (typical_price * volume).rolling(20).sum() / volume.rolling(20).sum()
    latest_close = float(dataframe["close"].iloc[-1])
    latest_vwap = float(vwap_20.iloc[-1])
    status = "daily_proxy"
    benchmark_name = "volume-weighted average price"
    requirement = "Daily OHLCV. True VWAP execution needs intraday volume curves and child-order fills."
    if not math.isfinite(latest_vwap):
        # Some Yahoo rows can have missing or zero volume. Use an unweighted typical-price benchmark
        # so the finding stays usable while still flagging that true VWAP was not available.
        vwap_20 = typical_price.rolling(20).mean()
        latest_vwap = float(vwap_20.iloc[-1])
        status = "daily_proxy_volume_fallback"
        benchmark_name = "typical-price fallback benchmark"
        requirement = (
            "Daily OHLCV with reliable volume. Recent volume was missing/zero, so this uses "
            "a 20-session typical-price fallback."
        )
    delta_pct = ((latest_close / latest_vwap) - 1) * 100
    action = _action_from_threshold(delta_pct, buy_below=-1.0, sell_above=1.0)
    return _finding(
        "VWAP",
        "Execution Algorithms",
        action,
        _confidence(delta_pct),
        status,
        requirement,
        f"Close is {delta_pct:.2f}% versus the 20-session {benchmark_name}.",
        "Uses daily typical price weighted by volume as a proxy. BUY means price is meaningfully below the daily VWAP benchmark; SELL means price is meaningfully above it. Production execution would use intraday volume buckets.",
        {"latest_close": _clean_float(latest_close), "daily_vwap_20": _clean_float(latest_vwap), "delta_pct": _clean_float(delta_pct)},
        _chart(dataframe, "Close vs daily VWAP proxy", {"Close": dataframe["close"], "20D VWAP": vwap_20}),
    )


def _twap(dataframe: pd.DataFrame) -> dict:
    twap_20 = dataframe["close"].rolling(20).mean()
    latest_close = float(dataframe["close"].iloc[-1])
    latest_twap = float(twap_20.iloc[-1])
    delta_pct = ((latest_close / latest_twap) - 1) * 100
    action = _action_from_threshold(delta_pct, buy_below=-1.25, sell_above=1.25)
    return _finding(
        "TWAP",
        "Execution Algorithms",
        action,
        _confidence(delta_pct, multiplier=10),
        "daily_proxy",
        "Daily close data. True TWAP execution needs an order horizon and child-order schedule.",
        f"Close is {delta_pct:.2f}% versus the 20-session time-weighted average close.",
        "Uses a simple rolling average as a daily TWAP proxy. BUY means price is below the equal-time benchmark; SELL means price is above it. The execution version would slice a parent order evenly through time.",
        {"latest_close": _clean_float(latest_close), "twap_20": _clean_float(latest_twap), "delta_pct": _clean_float(delta_pct)},
        _chart(dataframe, "Close vs TWAP proxy", {"Close": dataframe["close"], "20D TWAP": twap_20}),
    )


def _implementation_shortfall(dataframe: pd.DataFrame) -> dict:
    close = dataframe["close"]
    arrival = float(close.iloc[-6]) if len(close) >= 6 else float(close.iloc[0])
    latest = float(close.iloc[-1])
    sma_20 = close.rolling(20).mean()
    trend_pct = ((latest / float(sma_20.iloc[-1])) - 1) * 100
    improvement_pct = ((latest / arrival) - 1) * 100
    if improvement_pct <= -1 and trend_pct >= -1:
        action = "BUY"
    elif improvement_pct >= 1 and trend_pct <= 1:
        action = "SELL"
    else:
        action = "HOLD"
    return _finding(
        "Implementation Shortfall",
        "Execution Algorithms",
        action,
        _confidence(improvement_pct, multiplier=8),
        "daily_proxy",
        "Daily OHLCV. True IS needs arrival price, parent side, urgency, expected impact, and live liquidity.",
        f"Latest close is {improvement_pct:.2f}% away from the five-session arrival benchmark.",
        "Compares current close with a recent arrival benchmark and a 20-session trend filter. This is an execution-readiness proxy, not a complete institutional shortfall optimizer.",
        {"arrival_close_5_sessions_ago": _clean_float(arrival), "latest_close": _clean_float(latest), "shortfall_pct": _clean_float(improvement_pct), "trend_vs_sma20_pct": _clean_float(trend_pct)},
        _chart(dataframe, "Close vs arrival benchmark", {"Close": close, "20D SMA": sma_20}),
    )


def _ou_process(dataframe: pd.DataFrame) -> dict:
    close = dataframe["close"]
    mean_60 = close.rolling(60).mean()
    std_60 = close.rolling(60).std()
    z_score = float(((close - mean_60) / std_60).iloc[-1])
    action = _action_from_threshold(z_score, buy_below=-1.5, sell_above=1.5)
    return _finding(
        "Ornstein-Uhlenbeck (OU) Process",
        "Statistical Arbitrage & Mean Reversion",
        action,
        _confidence(z_score, multiplier=14),
        "daily_proxy",
        "Daily close data. OU is stronger on stationary spreads than on raw single-stock prices.",
        f"Latest close has a {z_score:.2f} rolling z-score versus its 60-session mean.",
        "Approximates mean reversion by measuring how far price has moved from a rolling equilibrium. BUY means below the mean-reversion band; SELL means above it.",
        {"z_score_60": _clean_float(z_score), "rolling_mean_60": _clean_float(mean_60.iloc[-1]), "rolling_std_60": _clean_float(std_60.iloc[-1])},
        _chart(
            dataframe,
            "OU proxy: rolling mean and bands",
            {
                "Close": close,
                "60D Mean": mean_60,
                "+1.5 STD": mean_60 + 1.5 * std_60,
                "-1.5 STD": mean_60 - 1.5 * std_60,
            },
        ),
    )


def _kalman_filter(dataframe: pd.DataFrame) -> dict:
    close = dataframe["close"].astype(float)
    values = close.to_numpy()
    measurement_variance = float(np.nanvar(np.diff(values[-120:]))) if len(values) > 120 else float(np.nanvar(np.diff(values)))
    measurement_variance = measurement_variance or 1.0
    process_variance = measurement_variance * 0.03
    estimate = values[0]
    error_estimate = 1.0
    filtered = []
    for value in values:
        error_estimate += process_variance
        kalman_gain = error_estimate / (error_estimate + measurement_variance)
        estimate = estimate + kalman_gain * (value - estimate)
        error_estimate = (1 - kalman_gain) * error_estimate
        filtered.append(estimate)
    filtered_series = pd.Series(filtered, index=dataframe.index)
    residual = close - filtered_series
    residual_std = float(residual.tail(120).std() or residual.std() or 1)
    residual_z = float(residual.iloc[-1] / residual_std)
    action = _action_from_threshold(residual_z, buy_below=-1.0, sell_above=1.0)
    return _finding(
        "Kalman Filtering",
        "Statistical Arbitrage & Mean Reversion",
        action,
        _confidence(residual_z, multiplier=16),
        "daily_proxy",
        "Daily close data. Production Kalman models often estimate hedge ratios on pairs or multi-factor states.",
        f"Close is {residual_z:.2f} residual standard deviations from the adaptive fair-value estimate.",
        "Runs a one-dimensional Kalman smoother over closing price. BUY means price is below adaptive fair value; SELL means price is above it.",
        {"residual_z": _clean_float(residual_z), "kalman_fair_value": _clean_float(filtered_series.iloc[-1]), "latest_close": _clean_float(close.iloc[-1])},
        _chart(dataframe, "Kalman fair-value filter", {"Close": close, "Kalman fair value": filtered_series}),
    )


def _sarimax_proxy(dataframe: pd.DataFrame) -> dict:
    close = dataframe["close"].astype(float)
    returns = close.pct_change()
    weekday_index = [pd.Timestamp(index).weekday() for index in dataframe.index]
    weekday_returns = returns.groupby(weekday_index).mean()
    latest_weekday = pd.Timestamp(dataframe.index[-1]).weekday()
    baseline_return = float(
        0.55 * returns.tail(20).mean()
        + 0.30 * returns.tail(60).mean()
        + 0.15 * weekday_returns.get(latest_weekday, 0)
    )
    forecast_pct = baseline_return * 100
    action = _action_from_threshold(forecast_pct, buy_below=-0.25, sell_above=0.25)
    # Forecast sign is directional: positive forecast is a BUY bias, negative is a SELL bias.
    if forecast_pct > 0.25:
        action = "BUY"
    elif forecast_pct < -0.25:
        action = "SELL"
    else:
        action = "HOLD"
    sma_20 = close.rolling(20).mean()
    return _finding(
        "SARIMAX",
        "Volatility Forecasting & Time Series Baseline",
        action,
        _confidence(forecast_pct, multiplier=30),
        "baseline_proxy",
        "Daily close data. Full SARIMAX requires statsmodels plus validated seasonal/exogenous features.",
        f"Baseline next-session return estimate is {forecast_pct:.2f}%.",
        "Uses a transparent AR/seasonality proxy until a full SARIMAX model and exogenous features are configured. BUY means the baseline forecast is positive; SELL means it is negative.",
        {"forecast_return_pct": _clean_float(forecast_pct), "mean_return_20d_pct": _clean_float(returns.tail(20).mean() * 100), "mean_return_60d_pct": _clean_float(returns.tail(60).mean() * 100)},
        _chart(dataframe, "SARIMAX baseline proxy", {"Close": close, "20D SMA": sma_20}),
    )


def _garch_proxy(dataframe: pd.DataFrame) -> dict:
    close = dataframe["close"].astype(float)
    returns = close.pct_change()
    vol_20 = returns.rolling(20).std() * math.sqrt(252) * 100
    vol_60 = returns.rolling(60).std() * math.sqrt(252) * 100
    latest_vol_20 = float(vol_20.iloc[-1])
    latest_vol_60 = float(vol_60.iloc[-1])
    momentum_20 = float(((close.iloc[-1] / close.iloc[-21]) - 1) * 100)
    if latest_vol_20 > latest_vol_60 * 1.25 and momentum_20 < 0:
        action = "SELL"
    elif latest_vol_20 < latest_vol_60 and momentum_20 > 0:
        action = "BUY"
    else:
        action = "HOLD"
    vol_ratio = latest_vol_20 / latest_vol_60 if latest_vol_60 else 1
    return _finding(
        "GARCH",
        "Volatility Forecasting & Time Series Baseline",
        action,
        _confidence((vol_ratio - 1) * 2 + momentum_20 / 10, multiplier=10),
        "volatility_proxy",
        "Daily returns. Full GARCH needs a fitted volatility model and residual diagnostics.",
        f"20-session annualized volatility is {latest_vol_20:.2f}% versus {latest_vol_60:.2f}% over 60 sessions.",
        "Uses rolling realized volatility as a GARCH-style volatility clustering proxy. BUY requires positive momentum with calmer short-term volatility; SELL requires rising volatility with negative momentum.",
        {"volatility_20d_pct": _clean_float(latest_vol_20), "volatility_60d_pct": _clean_float(latest_vol_60), "momentum_20d_pct": _clean_float(momentum_20)},
        _chart(dataframe, "Volatility clustering proxy", {"20D Volatility": vol_20, "60D Volatility": vol_60}),
    )


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50)


def _macd_finding(dataframe: pd.DataFrame) -> dict:
    result = macd_crossover_signal(dataframe, MACD_DEFAULT_PARAMS)
    data = compute_macd_indicators(dataframe, MACD_DEFAULT_PARAMS) or {}
    close = dataframe["close"].astype(float)
    ema_fast = close.ewm(span=12, adjust=False).mean()
    ema_slow = close.ewm(span=26, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    return _finding(
        "MACD",
        "Momentum Indicators",
        result.signal_type,
        result.confidence_score,
        "ok",
        "Stored daily OHLCV.",
        result.reason,
        "MACD line (EMA12-EMA26) crossover vs signal line (EMA9 of MACD) with RSI(14) filter for Indian large-cap daily data.",
        {
            "macd": _clean_float(data.get("macd")),
            "signal_line": _clean_float(data.get("signal_line")),
            "histogram": _clean_float(data.get("histogram")),
            "rsi": _clean_float(data.get("rsi")),
        },
        _chart(
            dataframe,
            "MACD momentum",
            {"Close": close, "MACD": macd_line, "Signal": signal_line},
        ),
    )


def _macd_rsi_composite_finding(dataframe: pd.DataFrame) -> dict:
    result = macd_rsi_composite_signal(dataframe, MACD_DEFAULT_PARAMS)
    data = compute_macd_indicators(dataframe, MACD_DEFAULT_PARAMS) or {}
    return _finding(
        "MACD + RSI Composite",
        "Momentum Indicators",
        result.signal_type,
        result.confidence_score,
        "ok",
        "Stored daily OHLCV.",
        result.reason,
        "Combines MACD crossover with RSI zones: sweet-spot entries (RSI 40-60), late-trend caution, and overbought/oversold filters.",
        {
            "macd": _clean_float(data.get("macd")),
            "signal_line": _clean_float(data.get("signal_line")),
            "rsi": _clean_float(data.get("rsi")),
        },
    )


def _sector_rotation_finding(db: Session, stock_id: int | None, dataframe: pd.DataFrame) -> dict:
    if stock_id is None:
        return _not_available(
            "Sector Rotation Momentum",
            "Macro & Sector Analysis",
            "Stock sector and performance snapshots.",
            "Index funds do not have sector membership for rotation analysis.",
            "Ranks sectors by 1-month momentum and signals stocks in top/bottom sectors.",
            status="missing_sector_data",
        )
    strategy = SectorRotationStrategy()
    result = strategy.generate_signal(dataframe, strategy.default_parameters, db=db, stock_id=stock_id)
    status = result.indicators.get("status", "ok")
    return _finding(
        "Sector Rotation Momentum",
        "Macro & Sector Analysis",
        result.signal_type,
        result.confidence_score,
        status,
        "stock_performance_snapshots and sector assignments.",
        result.reason,
        "Ranks all sectors by 1-month return momentum. Buys stocks in top sectors, sells stocks in bottom sectors.",
        result.indicators,
    )


def _tree_ensemble_proxy(dataframe: pd.DataFrame) -> dict:
    close = dataframe["close"].astype(float)
    volume = dataframe["volume"].replace(0, np.nan).astype(float)
    returns = close.pct_change()
    sma_50 = close.rolling(50).mean()
    volatility_20 = returns.rolling(20).std()
    volatility_60 = returns.rolling(60).std()
    volume_z = (volume - volume.rolling(60).mean()) / volume.rolling(60).std()
    rsi_14 = _rsi(close)

    momentum_20 = ((close / close.shift(20)) - 1) * 100
    momentum_60 = ((close / close.shift(60)) - 1) * 100
    trend_score = ((close / sma_50) - 1).clip(-0.08, 0.08) / 0.08
    momentum_score = (0.55 * momentum_20.clip(-12, 12) / 12) + (0.45 * momentum_60.clip(-24, 24) / 24)
    rsi_score = ((50 - rsi_14).clip(-25, 25) / 25) * 0.6
    volatility_score = (1 - (volatility_20 / volatility_60.replace(0, np.nan))).clip(-1, 1) * 0.35
    liquidity_score = volume_z.clip(-2, 2).fillna(0) / 10
    feature_score = (0.38 * momentum_score) + (0.28 * trend_score) + (0.18 * rsi_score) + volatility_score + liquidity_score
    latest_score = float(feature_score.iloc[-1])
    if latest_score >= 0.25:
        action = "BUY"
    elif latest_score <= -0.25:
        action = "SELL"
    else:
        action = "HOLD"
    return _finding(
        "Tree-Based Ensembles (XGBoost / LightGBM)",
        "Modern Machine Learning & Alternative Data",
        action,
        _confidence(latest_score, multiplier=90, base=45),
        "daily_feature_proxy",
        "Stored daily OHLCV is available. Production XGBoost/LightGBM still needs a trained model artifact and feature validation.",
        f"Daily feature ensemble score is {latest_score:.2f}.",
        "This MVP uses a deterministic daily feature ensemble over momentum, trend, RSI, volatility, and volume. In production, replace this proxy with a trained, versioned XGBoost/LightGBM model using the same feature contract.",
        {
            "feature_score": _clean_float(latest_score),
            "momentum_20d_pct": _clean_float(momentum_20.iloc[-1]),
            "momentum_60d_pct": _clean_float(momentum_60.iloc[-1]),
            "rsi_14": _clean_float(rsi_14.iloc[-1]),
            "volume_z_60": _clean_float(volume_z.iloc[-1]),
        },
        _chart(dataframe, "Daily feature ensemble proxy", {"Close": close, "Feature Score": feature_score}),
    )


def _sequence_deep_learning_proxy(dataframe: pd.DataFrame) -> dict:
    close = dataframe["close"].astype(float)
    returns = close.pct_change().fillna(0)
    sequence_return = returns.tail(20).sum() * 100
    positive_ratio = float((returns.tail(20) > 0).mean())
    drawdown_20 = ((close / close.rolling(20).max()) - 1) * 100
    downside_vol = returns.where(returns < 0, 0).tail(20).std() * math.sqrt(252) * 100
    ema_fast = close.ewm(span=12, adjust=False).mean()
    ema_slow = close.ewm(span=26, adjust=False).mean()
    positive_ratio_series = returns.rolling(20).apply(lambda values: (values > 0).mean(), raw=True)
    sequence_score = (
        (returns.rolling(20).sum() * 100).clip(-12, 12) / 12 * 0.50
        + ((ema_fast / ema_slow) - 1).clip(-0.06, 0.06) / 0.06 * 0.35
        + ((positive_ratio_series - 0.5) * 2).clip(-1, 1) * 0.15
    )
    latest_score = float(sequence_score.iloc[-1])
    if latest_score >= 0.25:
        action = "BUY"
    elif latest_score <= -0.25:
        action = "SELL"
    else:
        action = "HOLD"
    return _finding(
        "Sequential Deep Learning (LSTMs / Transformers)",
        "Modern Machine Learning & Alternative Data",
        action,
        _confidence(latest_score, multiplier=90, base=45),
        "daily_sequence_proxy",
        "Stored daily OHLCV is available. Production LSTM/Transformer models need trained sequence model artifacts and preferably intraday/order-book event streams.",
        f"Daily sequence proxy score is {latest_score:.2f}; 20-session positive-day ratio is {positive_ratio:.2f}.",
        "This MVP scores the recent daily return sequence, EMA structure, drawdown, and downside volatility. It is a transparent sequence proxy, not a trained neural network.",
        {
            "sequence_score": _clean_float(latest_score),
            "sequence_return_20d_pct": _clean_float(sequence_return),
            "positive_day_ratio_20d": _clean_float(positive_ratio),
            "drawdown_20d_pct": _clean_float(drawdown_20.iloc[-1]),
            "downside_volatility_20d_pct": _clean_float(downside_vol),
        },
        _chart(dataframe, "Daily sequence proxy", {"Close": close, "Sequence Score": sequence_score}),
    )


def _not_available(
    algorithm_name: str,
    category: str,
    requirements: str,
    reason: str,
    logic: str,
    status: str = "requires_additional_data_type",
) -> dict:
    return _finding(
        algorithm_name,
        category,
        "HOLD",
        0,
        status,
        requirements,
        reason,
        logic,
        {},
        None,
    )


def _load_price_dataframe(db: Session, stock_id: int, limit: int) -> pd.DataFrame:
    prices = list(
        db.scalars(
            select(StockPrice)
            .where(StockPrice.stock_id == stock_id, StockPrice.timeframe == DAILY_TIMEFRAME)
            .order_by(desc(StockPrice.price_datetime))
            .limit(limit)
        )
    )
    prices.reverse()
    dataframe = prices_to_dataframe(prices)
    if dataframe.empty:
        return dataframe
    numeric_columns = ["open", "high", "low", "close", "volume"]
    for column in numeric_columns:
        dataframe[column] = pd.to_numeric(dataframe[column], errors="coerce")
    return dataframe.dropna(subset=["close"]).loc[lambda frame: frame["close"] > 0]


def _load_index_price_dataframe(db: Session, index_fund_id: int, limit: int) -> pd.DataFrame:
    prices = list(
        db.scalars(
            select(IndexFundPrice)
            .where(IndexFundPrice.index_fund_id == index_fund_id, IndexFundPrice.timeframe == DAILY_TIMEFRAME)
            .order_by(desc(IndexFundPrice.price_datetime))
            .limit(limit)
        )
    )
    prices.reverse()
    dataframe = index_prices_to_dataframe(prices)
    if dataframe.empty:
        return dataframe
    numeric_columns = ["open", "high", "low", "close", "volume"]
    for column in numeric_columns:
        dataframe[column] = pd.to_numeric(dataframe[column], errors="coerce")
    return dataframe.dropna(subset=["close"]).loc[lambda frame: frame["close"] > 0]


def _generate_algo_findings_from_dataframe(
    dataframe: pd.DataFrame,
    *,
    instrument_label: str,
    db: Session | None = None,
    stock_id: int | None = None,
) -> list[dict]:
    if len(dataframe) < MIN_SIGNAL_ROWS:
        return [
            _not_available(
                "Algorithm findings",
                "Data Quality",
                f"At least {MIN_SIGNAL_ROWS} daily candles.",
                f"Only {len(dataframe)} stored candles are available.",
                f"Run daily price ingestion before evaluating {instrument_label} signals.",
            )
        ]

    return [
        _daily_vwap(dataframe),
        _twap(dataframe),
        _implementation_shortfall(dataframe),
        _not_available(
            "Pairs Trading via Cointegration",
            "Statistical Arbitrage & Mean Reversion",
            "Two historically related assets and a cointegration test window.",
            f"The selected {instrument_label} has daily OHLCV, but this algorithm requires a second asset to build a spread.",
            "Production logic would run Engle-Granger or Johansen tests, compute a stationary spread, then buy the underperformer and sell the outperformer when spread z-score breaches thresholds.",
            status="requires_pair_asset",
        ),
        _ou_process(dataframe),
        _kalman_filter(dataframe),
        _sarimax_proxy(dataframe),
        _garch_proxy(dataframe),
        _not_available(
            "Avellaneda-Stoikov Model",
            "High-Frequency Market Making",
            "Live bid/ask quotes, inventory, volatility, risk aversion, and order-book depth.",
            f"The database has long daily OHLCV history for this {instrument_label}, but market making needs live bid/ask and inventory state.",
            "Production logic would calculate reservation price and optimal bid/ask spread while skewing quotes to manage inventory.",
            status="requires_order_book_data",
        ),
        _not_available(
            "Order Book Imbalance (OBI) Algos",
            "High-Frequency Market Making",
            "Level 2 order book snapshots with bid/ask depth and event timestamps.",
            "Yahoo daily candles do not include limit-order-book pressure.",
            "Production logic would compare bid-side and ask-side depth to detect short-horizon pressure before trades print.",
            status="requires_order_book_data",
        ),
        _tree_ensemble_proxy(dataframe),
        _macd_finding(dataframe),
        _macd_rsi_composite_finding(dataframe),
        _sequence_deep_learning_proxy(dataframe),
        _sector_rotation_finding(db, stock_id, dataframe),
    ]


@timed("algo.generate_stock_findings")
def generate_stock_algo_findings(db: Session, stock_id: int, limit: int = DEFAULT_LIMIT) -> list[dict]:
    dataframe = _load_price_dataframe(db, stock_id, limit)
    return _generate_algo_findings_from_dataframe(
        dataframe, instrument_label="stock", db=db, stock_id=stock_id
    )


@timed("algo.generate_index_fund_findings")
def generate_index_fund_algo_findings(db: Session, index_fund_id: int, limit: int = DEFAULT_LIMIT) -> list[dict]:
    dataframe = _load_index_price_dataframe(db, index_fund_id, limit)
    return _generate_algo_findings_from_dataframe(dataframe, instrument_label="index fund")


@timed("algo.generate_sequential_rankings")
def generate_sequential_rankings(
    db: Session,
    limit: int = 15,
    universe_limit: int | None = None,
    price_limit: int = 220,
) -> dict[str, Any]:
    """Rank active stocks using the stored daily sequence proxy.

    This scans only stocks that already have enough daily candles in the database.
    The score is an MVP proxy for an LSTM/Transformer-style sequence model; the
    response labels it clearly so we can replace it with a trained artifact later.
    """
    active_count = int(
        db.scalar(select(func.count()).select_from(Stock).where(Stock.is_active.is_(True))) or 0
    )
    coverage_stmt = (
        select(Stock.id)
        .join(StockPrice, StockPrice.stock_id == Stock.id)
        .where(
            Stock.is_active.is_(True),
            StockPrice.timeframe == DAILY_TIMEFRAME,
            StockPrice.close.is_not(None),
        )
        .group_by(Stock.id)
        .having(func.count(StockPrice.id) >= MIN_SIGNAL_ROWS)
        .order_by(Stock.symbol.asc())
    )
    if universe_limit:
        coverage_stmt = coverage_stmt.limit(max(1, universe_limit))

    stock_ids = list(db.scalars(coverage_stmt))
    if not stock_ids:
        return {
            "as_of": datetime.now(UTC),
            "rows_scanned": active_count,
            "eligible_count": 0,
            "top_buys": [],
            "top_sells": [],
        }

    stocks = list(db.scalars(select(Stock).where(Stock.id.in_(stock_ids))))
    stocks_by_id = {stock.id: stock for stock in stocks}
    ranked_rows: list[dict[str, Any]] = []

    for stock_id in stock_ids:
        stock = stocks_by_id.get(stock_id)
        if stock is None:
            continue
        dataframe = _load_price_dataframe(db, stock.id, price_limit)
        if len(dataframe) < MIN_SIGNAL_ROWS:
            continue
        finding = _sequence_deep_learning_proxy(dataframe)
        indicators = finding.get("indicators") or {}
        score = float(indicators.get("sequence_score") or 0)
        latest_close = dataframe["close"].iloc[-1] if "close" in dataframe else None
        latest_date = dataframe.index[-1] if len(dataframe.index) else None
        ranked_rows.append(
            {
                "stock_id": stock.id,
                "symbol": stock.symbol,
                "yahoo_symbol": stock.yahoo_symbol,
                "exchange": stock.exchange,
                "company_name": stock.company_name,
                "action": finding["action"],
                "confidence_score": float(finding["confidence_score"]),
                "sequence_score": round(score, 4),
                "latest_close": _clean_float(latest_close),
                "as_of_date": pd.Timestamp(latest_date).date() if latest_date is not None else None,
                "reason": finding["reason"],
            }
        )

    ranked_by_score = sorted(
        ranked_rows,
        key=lambda row: (row["sequence_score"], row["confidence_score"]),
        reverse=True,
    )
    ranked_by_sell_score = sorted(
        ranked_rows,
        key=lambda row: (row["sequence_score"], -row["confidence_score"]),
    )

    return {
        "as_of": datetime.now(UTC),
        "rows_scanned": active_count,
        "eligible_count": len(ranked_rows),
        "top_buys": ranked_by_score[:limit],
        "top_sells": ranked_by_sell_score[:limit],
    }
