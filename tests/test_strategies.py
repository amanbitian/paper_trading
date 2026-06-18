"""Unit tests for strategy signal generation.

These exercise `generate_signal` directly with small, hand-crafted OHLCV
frames where the expected BUY/SELL/HOLD outcome can be computed by hand —
covering a part of the codebase (backend/app/strategies) that previously had
zero test coverage despite driving both live signals and backtests.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR / "backend"))

from app.strategies.breakout_strategy import BreakoutStrategy  # noqa: E402
from app.strategies.quality_momentum_strategy import QualityMomentumStrategy  # noqa: E402
from app.strategies.rsi_strategy import RSIStrategy  # noqa: E402
from app.strategies.sma_crossover_strategy import SMACrossoverStrategy  # noqa: E402


def _ohlcv(closes: list[float], highs: list[float] | None = None,
           lows: list[float] | None = None, volumes: list[float] | None = None) -> pd.DataFrame:
    """Build a minimal OHLCV frame. Defaults high=low=close, volume=1000."""
    n = len(closes)
    highs = highs or list(closes)
    lows = lows or list(closes)
    volumes = volumes if volumes is not None else [1000.0] * n
    opens = [closes[0]] + closes[:-1]
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        },
        index=pd.date_range("2026-01-01", periods=n, freq="D"),
    )


class RSIStrategyTests(unittest.TestCase):
    def test_not_enough_candles_returns_hold_with_zero_confidence(self) -> None:
        strategy = RSIStrategy()
        frame = _ohlcv([100.0, 101.0, 102.0])
        result = strategy.generate_signal(frame, {"min_bars": 30})
        self.assertEqual(result.signal_type, "HOLD")
        self.assertEqual(result.confidence_score, 0)

    def test_steady_decline_drives_rsi_to_zero_and_signals_buy(self) -> None:
        # Every close is 1 lower than the previous one: every delta is
        # negative, so the rolling *gain* average is exactly 0 and RSI
        # collapses to 0 -- deterministically below the default oversold
        # threshold of 35, which should produce a BUY.
        strategy = RSIStrategy()
        closes = [100.0 - i for i in range(35)]
        frame = _ohlcv(closes)
        result = strategy.generate_signal(frame, {"min_bars": 30})
        self.assertEqual(result.signal_type, "BUY")
        self.assertAlmostEqual(result.indicators["rsi"], 0.0, places=6)
        # enrich_signal_with_atr should have attached a protective stop for a
        # BUY signal (long position needs a stop below entry).
        self.assertIn("stop_price", result.indicators)
        self.assertLess(result.indicators["stop_price"], closes[-1])

    def test_steady_rise_is_treated_as_neutral_not_overbought(self) -> None:
        # Every close is 1 higher than the previous one: every delta is
        # positive, so the rolling *loss* average is exactly 0. The strategy
        # guards against the resulting division-by-zero by replacing a zero
        # loss average with NaN and filling the resulting RSI with 50
        # ("neutral"), so a pure uptrend reads as HOLD rather than SELL.
        strategy = RSIStrategy()
        closes = [100.0 + i for i in range(35)]
        frame = _ohlcv(closes)
        result = strategy.generate_signal(frame, {"min_bars": 30})
        self.assertAlmostEqual(result.indicators["rsi"], 50.0, places=6)
        self.assertEqual(result.signal_type, "HOLD")

    def test_trend_filter_suppresses_buy_into_a_confirmed_downtrend(self) -> None:
        # Same steady-decline data as the BUY test above: RSI=0 -> raw signal
        # is BUY, but price is well below its 20-bar SMA (a confirmed
        # downtrend), so the opt-in trend filter should demote it to HOLD --
        # this is exactly the "don't catch a falling knife" guard it exists for.
        strategy = RSIStrategy()
        closes = [100.0 - i for i in range(35)]
        frame = _ohlcv(closes)
        unfiltered = strategy.generate_signal(frame, {"min_bars": 30})
        self.assertEqual(unfiltered.signal_type, "BUY")

        filtered = strategy.generate_signal(
            frame, {"min_bars": 30, "trend_filter_enabled": True, "trend_filter_period": 20}
        )
        self.assertEqual(filtered.signal_type, "HOLD")
        self.assertEqual(filtered.confidence_score, 0)
        self.assertIn("trend_filter_sma", filtered.indicators)
        self.assertIn("suppressed by trend filter", filtered.reason)

    def test_trend_filter_passes_through_when_not_enough_history(self) -> None:
        # trend_filter_period (200) exceeds the available 35 bars, so the
        # filter can't compute its SMA yet and must let the original signal
        # through unchanged rather than blocking trades on too little data.
        strategy = RSIStrategy()
        closes = [100.0 - i for i in range(35)]
        frame = _ohlcv(closes)
        result = strategy.generate_signal(
            frame, {"min_bars": 30, "trend_filter_enabled": True, "trend_filter_period": 200}
        )
        self.assertEqual(result.signal_type, "BUY")
        self.assertNotIn("trend_filter_sma", result.indicators)

    def test_trend_filter_disabled_by_default(self) -> None:
        # Sanity check that the new parameter defaults to off -- existing
        # callers that don't know about it see no behavior change.
        strategy = RSIStrategy()
        closes = [100.0 - i for i in range(35)]
        frame = _ohlcv(closes)
        result = strategy.generate_signal(frame, {"min_bars": 30})
        self.assertEqual(result.signal_type, "BUY")
        self.assertNotIn("trend_filter_sma", result.indicators)


class SMACrossoverStrategyTests(unittest.TestCase):
    PARAMS = {"short_window": 2, "long_window": 3, "min_bars": 4, "atr_period": 2}

    def test_fresh_upward_cross_signals_buy(self) -> None:
        # closes: [10, 10, 10, 1, 1, 20]
        #   SMA2 -> prev=mean(1,1)=1.0     curr=mean(1,20)=10.5
        #   SMA3 -> prev=mean(10,1,1)=4.0  curr=mean(1,1,20)=7.33
        # short crosses from below (1.0 <= 4.0) to above (10.5 > 7.33) -> BUY
        strategy = SMACrossoverStrategy()
        frame = _ohlcv([10.0, 10.0, 10.0, 1.0, 1.0, 20.0])
        result = strategy.generate_signal(frame, self.PARAMS)
        self.assertEqual(result.signal_type, "BUY")
        self.assertEqual(result.confidence_score, 80)

    def test_fresh_downward_cross_signals_sell(self) -> None:
        # closes: [1, 1, 1, 10, 10, 1]
        #   SMA2 -> prev=mean(10,10)=10.0  curr=mean(10,1)=5.5
        #   SMA3 -> prev=mean(1,10,10)=7.0 curr=mean(10,10,1)=7.0
        # short crosses from above (10.0 >= 7.0) to below (5.5 < 7.0) -> SELL
        strategy = SMACrossoverStrategy()
        frame = _ohlcv([1.0, 1.0, 1.0, 10.0, 10.0, 1.0])
        result = strategy.generate_signal(frame, self.PARAMS)
        self.assertEqual(result.signal_type, "SELL")
        self.assertEqual(result.confidence_score, 80)

    def test_flat_series_has_no_fresh_cross_and_holds(self) -> None:
        strategy = SMACrossoverStrategy()
        frame = _ohlcv([5.0] * 6)
        result = strategy.generate_signal(frame, self.PARAMS)
        self.assertEqual(result.signal_type, "HOLD")


class BreakoutStrategyTests(unittest.TestCase):
    PARAMS = {"lookback_period": 3, "min_bars": 4, "volume_multiplier": 1.5, "atr_period": 2}

    def test_close_above_lookback_high_with_volume_confirms_breakout(self) -> None:
        # Lookback window (bars 0-2): high max = 10, average volume = 100.
        # Required volume = 100 * 1.5 = 150. The latest bar closes at 15
        # (> 10) on volume 200 (> 150) -> confirmed breakout -> BUY.
        strategy = BreakoutStrategy()
        frame = _ohlcv(
            closes=[9.0, 9.5, 9.0, 15.0],
            highs=[10.0, 10.0, 10.0, 16.0],
            lows=[8.0, 8.5, 8.0, 14.0],
            volumes=[100.0, 100.0, 100.0, 200.0],
        )
        result = strategy.generate_signal(frame, self.PARAMS)
        self.assertEqual(result.signal_type, "BUY")
        self.assertEqual(result.indicators["previous_high"], 10.0)
        self.assertEqual(result.indicators["average_volume"], 100.0)

    def test_close_within_range_does_not_confirm_breakout(self) -> None:
        # Latest close (9.0) does not exceed the prior lookback high (10.0),
        # so even with strong volume this should remain a HOLD.
        strategy = BreakoutStrategy()
        frame = _ohlcv(
            closes=[9.0, 9.5, 9.0, 9.0],
            highs=[10.0, 10.0, 10.0, 9.5],
            lows=[8.0, 8.5, 8.0, 8.5],
            volumes=[100.0, 100.0, 100.0, 500.0],
        )
        result = strategy.generate_signal(frame, self.PARAMS)
        self.assertEqual(result.signal_type, "HOLD")

    def test_breakout_without_volume_confirmation_holds(self) -> None:
        # Close breaks above the lookback high (15 > 10) but volume (110)
        # does not clear the 1.5x average-volume bar (150) -> HOLD.
        strategy = BreakoutStrategy()
        frame = _ohlcv(
            closes=[9.0, 9.5, 9.0, 15.0],
            highs=[10.0, 10.0, 10.0, 16.0],
            lows=[8.0, 8.5, 8.0, 14.0],
            volumes=[100.0, 100.0, 100.0, 110.0],
        )
        result = strategy.generate_signal(frame, self.PARAMS)
        self.assertEqual(result.signal_type, "HOLD")

    def test_atr_buffer_off_by_default_reproduces_original_behavior(self) -> None:
        # Close clears the raw lookback high (10.5 > 10.0) by only a hair,
        # with confirming volume -- the original rule fires BUY, and the new
        # `breakout_buffer`/`required_close` indicators report "no buffer".
        strategy = BreakoutStrategy()
        frame = _ohlcv(
            closes=[9.0, 9.5, 9.0, 10.5],
            highs=[10.0, 10.0, 10.0, 10.6],
            lows=[8.0, 8.5, 8.0, 10.4],
            volumes=[100.0, 100.0, 100.0, 200.0],
        )
        result = strategy.generate_signal(frame, self.PARAMS)
        self.assertEqual(result.signal_type, "BUY")
        self.assertEqual(result.indicators["breakout_buffer"], 0.0)
        self.assertEqual(result.indicators["required_close"], result.indicators["previous_high"])

    def test_atr_buffer_suppresses_a_marginal_breakout(self) -> None:
        # Same data as the test above, but with the ATR confirmation buffer
        # enabled: clearing the high by only ~0.5 isn't enough once the close
        # must also clear it by a multiple of recent volatility, so the
        # marginal breakout is suppressed to HOLD instead of firing BUY.
        strategy = BreakoutStrategy()
        frame = _ohlcv(
            closes=[9.0, 9.5, 9.0, 10.5],
            highs=[10.0, 10.0, 10.0, 10.6],
            lows=[8.0, 8.5, 8.0, 10.4],
            volumes=[100.0, 100.0, 100.0, 200.0],
        )
        buffered_params = {**self.PARAMS, "breakout_buffer_atr_multiplier": 1.0}
        result = strategy.generate_signal(frame, buffered_params)
        self.assertEqual(result.signal_type, "HOLD")
        self.assertGreater(result.indicators["breakout_buffer"], 0.0)
        self.assertGreater(result.indicators["required_close"], result.indicators["previous_high"])
        self.assertLess(result.indicators["latest_close"], result.indicators["required_close"])


class QualityMomentumStrategyTests(unittest.TestCase):
    def test_clear_long_term_uptrend_with_quality_filter_signals_buy(self) -> None:
        strategy = QualityMomentumStrategy()
        closes = [100.0 + i * 0.25 for i in range(320)]
        frame = _ohlcv(closes, volumes=[200000.0] * len(closes))
        result = strategy.generate_signal(
            frame,
            {
                "min_bars": 275,
                "fundamental_quality_score": 0.5,
                "fundamental_source": "stock_financials",
            },
        )
        self.assertEqual(result.signal_type, "BUY")
        self.assertGreater(result.indicators["quality_momentum_score"], 0.35)
        self.assertEqual(result.indicators["fundamental_source"], "stock_financials")
        self.assertIn("stop_price", result.indicators)

    def test_broken_trend_signals_sell(self) -> None:
        strategy = QualityMomentumStrategy()
        closes = [200.0 - i * 0.30 for i in range(320)]
        frame = _ohlcv(closes, volumes=[200000.0] * len(closes))
        result = strategy.generate_signal(frame, {"min_bars": 275, "fundamental_quality_score": -0.5})
        self.assertEqual(result.signal_type, "SELL")
        self.assertLess(result.indicators["trend_vs_sma_pct"], 0)

    def test_liquidity_floor_suppresses_signal(self) -> None:
        strategy = QualityMomentumStrategy()
        closes = [100.0 + i * 0.25 for i in range(320)]
        frame = _ohlcv(closes, volumes=[1000.0] * len(closes))
        result = strategy.generate_signal(frame, {"min_bars": 275, "min_average_volume": 100000})
        self.assertEqual(result.signal_type, "HOLD")
        self.assertIn("liquidity floor", result.reason)


if __name__ == "__main__":
    unittest.main()
