import pandas as pd

from app.strategies.base import BaseStrategy, SignalResult
from app.strategies.risk_management import enrich_signal_with_atr


class SMACrossoverStrategy(BaseStrategy):
    """Trend-following strategy based on a fast/slow simple-moving-average cross.

    Buys on a "golden cross" (short SMA crosses above long SMA — momentum
    turning up) and sells on a "death cross" (short SMA crosses below long
    SMA — momentum turning down). Only *fresh* crossovers trigger a signal:
    we compare the previous bar's SMA ordering against the current bar's, so
    an SMA pair that has already crossed and stayed crossed produces HOLD.
    """

    name = "SMA Crossover"
    strategy_type = "sma_crossover"
    default_parameters = {
        "short_window": 20,
        "long_window": 50,
        "min_bars": 60,
        "atr_period": 14,
        "atr_multiplier": 2.5,
    }

    def generate_signal(self, prices: pd.DataFrame, parameters: dict | None = None) -> SignalResult:
        params = self.merged_parameters(parameters)
        short_window = int(params["short_window"])
        long_window = int(params["long_window"])
        required = max(int(params.get("min_bars", max(short_window, long_window) + 1)), long_window + 1)
        if prices.empty or len(prices) < required:
            return SignalResult("HOLD", 0, "Not enough candles for SMA crossover", {})

        close = prices["close"].astype(float)
        short_sma = close.rolling(short_window).mean()
        long_sma = close.rolling(long_window).mean()
        # Compare the last two bars' SMA ordering to detect a *fresh*
        # crossover (the relationship flipped between t-1 and t), rather than
        # just the current ordering — which would also fire on every bar
        # while the SMAs stay crossed.
        previous_short, current_short = short_sma.iloc[-2], short_sma.iloc[-1]
        previous_long, current_long = long_sma.iloc[-2], long_sma.iloc[-1]
        indicators = {
            "short_sma": float(current_short),
            "long_sma": float(current_long),
            "short_window": short_window,
            "long_window": long_window,
        }

        if previous_short <= previous_long and current_short > current_long:
            result = SignalResult("BUY", 80, "Short SMA crossed above long SMA", indicators)
        elif previous_short >= previous_long and current_short < current_long:
            result = SignalResult("SELL", 80, "Short SMA crossed below long SMA", indicators)
        else:
            result = SignalResult("HOLD", 50, "No fresh SMA crossover", indicators)
        return enrich_signal_with_atr(prices, result, params, self.strategy_type)
