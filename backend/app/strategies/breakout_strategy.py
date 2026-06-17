from app.strategies.base import BaseStrategy, SignalResult
from app.strategies.risk_management import calculate_atr, enrich_signal_with_atr


class BreakoutStrategy(BaseStrategy):
    """Momentum strategy that buys confirmed upside breakouts.

    A "breakout" is when the latest close exceeds the highest high of the
    prior `lookback_period` bars — i.e. price pushes into new short-term
    territory. To filter out low-conviction breakouts (and avoid chasing
    noise), the latest bar's volume must also exceed `volume_multiplier`
    times the average volume of that lookback window. Only long/BUY signals
    are generated; there is no symmetric short-breakdown signal.
    """

    name = "Breakout with Volume"
    strategy_type = "breakout"
    default_parameters = {
        "lookback_period": 20,
        "volume_multiplier": 1.5,
        "min_bars": 30,
        "atr_period": 14,
        "atr_multiplier": 1.5,
        # Opt-in ATR-normalized confirmation buffer (see below). Defaults to
        # 0 -- i.e. the original "any close above the lookback high" rule --
        # so existing backtests/signals are unaffected unless enabled.
        "breakout_buffer_atr_multiplier": 0.0,
    }

    def generate_signal(self, prices, parameters: dict | None = None) -> SignalResult:
        params = self.merged_parameters(parameters)
        lookback = int(params["lookback_period"])
        min_bars = int(params.get("min_bars", lookback + 1))
        if prices.empty or len(prices) < min_bars:
            return SignalResult("HOLD", 0, "Not enough candles for breakout", {})

        # `previous` = the `lookback` bars strictly before the latest one;
        # `latest` is the bar the signal is evaluated on. Excluding `latest`
        # from the high/volume baseline prevents the breakout bar from
        # inflating its own confirmation threshold.
        previous = prices.iloc[-lookback - 1 : -1]
        latest = prices.iloc[-1]
        previous_high = float(previous["high"].max())
        average_volume = float(previous["volume"].mean())
        latest_close = float(latest["close"])
        latest_volume = float(latest["volume"])
        required_volume = average_volume * float(params["volume_multiplier"])

        # Optional ATR-normalized confirmation buffer: instead of accepting
        # any close that pokes above the lookback high by even a hair, this
        # requires the close to clear it by some multiple of recent
        # volatility. That does two things: (1) it filters out marginal
        # "broke the high by a penny" prints around round numbers, which are
        # common noise and frequently fail to follow through; (2) it makes
        # the breakout threshold comparable across instruments -- a flat
        # price buffer means very different things for a Rs 50 stock and a
        # Rs 5,000 one, but an ATR-scaled buffer adapts to each instrument's
        # typical daily range. ATR is computed on `previous` (excluding the
        # breakout bar itself) so a single oversized breakout candle can't
        # inflate the very threshold it needs to clear. Defaults to 0 (off),
        # which reproduces the original "any close above the high" behavior.
        buffer_multiplier = float(params.get("breakout_buffer_atr_multiplier", 0.0) or 0.0)
        atr_period = int(params.get("atr_period", 14))
        breakout_buffer = 0.0
        if buffer_multiplier > 0 and len(previous) >= atr_period:
            breakout_buffer = calculate_atr(previous, atr_period=atr_period) * buffer_multiplier
        required_close = previous_high + breakout_buffer

        indicators = {
            "previous_high": previous_high,
            "latest_close": latest_close,
            "average_volume": average_volume,
            "latest_volume": latest_volume,
            "breakout_buffer": round(breakout_buffer, 4),
            "required_close": round(required_close, 4),
        }
        if latest_close > required_close and latest_volume > required_volume:
            reason = (
                "Close broke above lookback high with confirming volume"
                if breakout_buffer == 0
                else (
                    f"Close cleared lookback high by {breakout_buffer:.2f} "
                    "(ATR-buffered) with confirming volume"
                )
            )
            result = SignalResult("BUY", 85, reason, indicators)
        else:
            result = SignalResult("HOLD", 45, "No confirmed breakout", indicators)
        return enrich_signal_with_atr(prices, result, params, self.strategy_type)
