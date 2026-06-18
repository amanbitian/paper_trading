from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.append(str(BACKEND))

from app.database import SessionLocal  # noqa: E402
from app.models.strategy import StrategyTemplate  # noqa: E402

STRATEGY_DEFAULTS: dict[str, dict] = {
    "rsi": {
        "rsi_period": 14,
        "oversold": 35,
        "overbought": 65,
        "min_bars": 30,
        "atr_period": 14,
        "atr_multiplier": 1.5,
    },
    "sma_crossover": {
        "short_window": 20,
        "long_window": 50,
        "min_bars": 60,
        "atr_period": 14,
        "atr_multiplier": 2.5,
    },
    "macd": {
        "fast_period": 12,
        "slow_period": 26,
        "signal_period": 9,
        "rsi_period": 14,
        "rsi_buy_max": 65,
        "rsi_sell_min": 55,
        "min_bars": 60,
        "atr_period": 14,
        "atr_multiplier": 2.0,
    },
    "breakout": {
        "lookback_period": 20,
        "volume_multiplier": 1.5,
        "min_bars": 30,
        "atr_period": 14,
        "atr_multiplier": 1.5,
    },
    "vwap": {
        "window": 20,
        "threshold_pct": 2.0,
        "min_bars": 20,
        "atr_period": 14,
        "atr_multiplier": 1.0,
    },
    "ou_process": {
        "lookback": 60,
        "z_entry": 2.0,
        "z_exit": 0.5,
        "min_bars": 80,
        "atr_period": 14,
        "atr_multiplier": 2.0,
    },
    "kalman_filter": {
        "observation_covariance": 0.1,
        "transition_covariance": 0.01,
        "signal_threshold": 2.5,
        "min_bars": 60,
        "atr_period": 14,
        "atr_multiplier": 2.0,
    },
    "sarimax": {
        "ar_order": 2,
        "diff_order": 1,
        "ma_order": 1,
        "seasonal_period": 5,
        "forecast_horizon": 1,
        "min_bars": 120,
        "atr_period": 14,
        "atr_multiplier": 2.0,
    },
    "garch": {
        "vol_lookback_short": 20,
        "vol_lookback_long": 60,
        "vol_ratio_threshold": 0.7,
        "min_bars": 80,
        "atr_period": 14,
        "atr_multiplier": 3.0,
    },
    "tree_ensemble": {
        "n_estimators": 100,
        "max_depth": 4,
        "feature_window": 20,
        "min_bars": 60,
        "atr_period": 14,
        "atr_multiplier": 2.0,
    },
    "sequential_deep_learning": {
        "sequence_length": 20,
        "min_positive_ratio": 0.6,
        "momentum_threshold": 0.02,
        "min_bars": 80,
        "atr_period": 14,
        "atr_multiplier": 2.0,
    },
    "sector_rotation": {
        "top_n_sectors": 2,
        "bottom_n_sectors": 2,
        "momentum_period": "1m",
        "min_stocks_per_sector": 5,
        "universe": "NIFTY_500",
    },
    "quality_momentum": {
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
    },
}

STRATEGY_ENTRIES = [
    {
        "strategy_name": "RSI Mean Reversion",
        "strategy_type": "rsi",
        "description": "Mean-reversion signal based on oversold and overbought RSI thresholds tuned for Indian large-cap daily data.",
        "category": "Momentum",
        "default_parameters": STRATEGY_DEFAULTS["rsi"],
    },
    {
        "strategy_name": "SMA Crossover",
        "strategy_type": "sma_crossover",
        "description": "Trend-following signal based on short and long moving average crossovers.",
        "category": "Trend",
        "default_parameters": STRATEGY_DEFAULTS["sma_crossover"],
    },
    {
        "strategy_name": "MACD + RSI Filter",
        "strategy_type": "macd",
        "description": "MACD crossover with RSI overbought/oversold filter. Tuned for Indian large-cap daily data. Best for trending markets.",
        "category": "Momentum",
        "default_parameters": STRATEGY_DEFAULTS["macd"],
    },
    {
        "strategy_name": "Breakout with Volume",
        "strategy_type": "breakout",
        "description": "Breakout signal when price clears a recent high with strong volume.",
        "category": "Breakout",
        "default_parameters": STRATEGY_DEFAULTS["breakout"],
    },
    {
        "strategy_name": "VWAP",
        "strategy_type": "vwap",
        "description": "Daily VWAP execution proxy comparing close against a volume-weighted benchmark.",
        "category": "Execution",
        "default_parameters": STRATEGY_DEFAULTS["vwap"],
    },
    {
        "strategy_name": "TWAP",
        "strategy_type": "twap",
        "description": "Daily TWAP execution proxy comparing close against an equal-time benchmark.",
        "category": "Execution",
        "default_parameters": {"twap_window": 20, "buy_below_pct": -1.25, "sell_above_pct": 1.25},
    },
    {
        "strategy_name": "Implementation Shortfall",
        "strategy_type": "implementation_shortfall",
        "description": "Execution proxy that compares the latest close with a recent arrival benchmark.",
        "category": "Execution",
        "default_parameters": {
            "arrival_window": 5,
            "buy_improvement_pct": -1.0,
            "sell_deterioration_pct": 1.0,
            "trend_window": 20,
        },
    },
    {
        "strategy_name": "Pairs Trading via Cointegration",
        "strategy_type": "pairs_cointegration",
        "description": "Pairs-trading placeholder that requires a second asset series before trading.",
        "category": "Stat Arb",
        "default_parameters": {"pair_symbol": "", "lookback_window": 120, "zscore_entry": 2.0},
    },
    {
        "strategy_name": "Ornstein-Uhlenbeck (OU) Process",
        "strategy_type": "ou_process",
        "description": "Mean-reversion proxy using rolling z-score bands inspired by OU dynamics.",
        "category": "Mean Reversion",
        "default_parameters": STRATEGY_DEFAULTS["ou_process"],
    },
    {
        "strategy_name": "Kalman Filtering",
        "strategy_type": "kalman_filter",
        "description": "Adaptive fair-value proxy using a one-dimensional Kalman filter.",
        "category": "Quant",
        "default_parameters": STRATEGY_DEFAULTS["kalman_filter"],
    },
    {
        "strategy_name": "SARIMAX",
        "strategy_type": "sarimax",
        "description": "Transparent seasonal return baseline until a full SARIMAX model is configured.",
        "category": "Time Series",
        "default_parameters": STRATEGY_DEFAULTS["sarimax"],
    },
    {
        "strategy_name": "GARCH",
        "strategy_type": "garch",
        "description": "Volatility-clustering proxy using short and long realized volatility windows.",
        "category": "Volatility",
        "default_parameters": STRATEGY_DEFAULTS["garch"],
    },
    {
        "strategy_name": "Avellaneda-Stoikov Model",
        "strategy_type": "avellaneda_stoikov",
        "description": "Market-making model placeholder that needs live order-book and inventory data.",
        "category": "HFT",
        "default_parameters": {"risk_aversion": 0.10, "inventory_limit": 100, "max_spread_pct": 1.0},
    },
    {
        "strategy_name": "Order Book Imbalance (OBI) Algos",
        "strategy_type": "order_book_imbalance",
        "description": "Order-book imbalance placeholder that needs Level 2 bid/ask depth snapshots.",
        "category": "HFT",
        "default_parameters": {
            "imbalance_buy_above": 0.20,
            "imbalance_sell_below": -0.20,
            "lookback_events": 100,
        },
    },
    {
        "strategy_name": "Tree-Based Ensembles (XGBoost / LightGBM)",
        "strategy_type": "tree_ensemble",
        "description": "Deterministic daily feature proxy for an XGBoost or LightGBM alpha model.",
        "category": "ML",
        "default_parameters": STRATEGY_DEFAULTS["tree_ensemble"],
    },
    {
        "strategy_name": "Sequential Deep Learning (LSTMs / Transformers)",
        "strategy_type": "sequential_deep_learning",
        "description": "Daily sequence proxy for future LSTM or Transformer model integration.",
        "category": "ML",
        "default_parameters": STRATEGY_DEFAULTS["sequential_deep_learning"],
    },
    {
        "strategy_name": "Sector Rotation Momentum",
        "strategy_type": "sector_rotation",
        "description": "Ranks all sectors by 1-month return momentum. Buys stocks in the top 2 sectors, sells stocks in the bottom 2. Particularly effective for NIFTY 500 universe during trending markets.",
        "category": "Macro / Sector",
        "default_parameters": STRATEGY_DEFAULTS["sector_rotation"],
    },
    {
        "strategy_name": "Quality Momentum",
        "strategy_type": "quality_momentum",
        "description": "Explainable 12-1 month momentum with trend, volatility, liquidity, ATR risk controls, and optional historical fundamental quality filters.",
        "category": "Quality / Momentum",
        "default_parameters": STRATEGY_DEFAULTS["quality_momentum"],
    },
]


def main() -> None:
    with SessionLocal() as db:
        for entry in STRATEGY_ENTRIES:
            stmt = insert(StrategyTemplate).values(
                strategy_name=entry["strategy_name"],
                strategy_type=entry["strategy_type"],
                description=entry["description"],
                default_parameters=entry["default_parameters"],
                is_active=True,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["strategy_type"],
                set_={
                    "strategy_name": stmt.excluded.strategy_name,
                    "description": stmt.excluded.description,
                    "default_parameters": stmt.excluded.default_parameters,
                    "is_active": stmt.excluded.is_active,
                },
            )
            db.execute(stmt)
        db.commit()
    print("Strategy templates seeded.")


if __name__ == "__main__":
    main()
