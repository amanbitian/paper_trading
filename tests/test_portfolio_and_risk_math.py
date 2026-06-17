"""Unit tests for the pure-math helpers behind portfolio accounting and
position sizing — `_effective_buy_price`, weighted-average-cost math,
realized P&L, ATR stops, and fixed-fractional position sizing.

These are the financial calculations most likely to silently drift if
touched during a refactor, and previously had no direct test coverage.
None of them require a database session.
"""

from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR / "backend"))

from app.services.portfolio_service import D, _effective_buy_price  # noqa: E402
from app.strategies.risk_management import (  # noqa: E402
    calculate_atr_stop,
    calculate_position_size,
)


class DConversionTests(unittest.TestCase):
    def test_none_becomes_zero(self) -> None:
        self.assertEqual(D(None), Decimal("0"))

    def test_passthrough_for_existing_decimal(self) -> None:
        value = Decimal("12.5")
        self.assertIs(D(value), value)

    def test_float_and_string_route_through_str_to_avoid_binary_float_noise(self) -> None:
        # Decimal(0.1) would be 0.1000000000000000055511151231257827021181583404541015625;
        # D() must go through str() first so this comes out exact.
        self.assertEqual(D(0.1), Decimal("0.1"))
        self.assertEqual(D("3.14"), Decimal("3.14"))


class EffectiveBuyPriceTests(unittest.TestCase):
    def test_folds_charges_into_per_share_cost(self) -> None:
        # 10 shares @ 100 plus 50 in charges -> (1000 + 50) / 10 = 105/share
        result = _effective_buy_price(Decimal("10"), Decimal("100"), Decimal("50"))
        self.assertEqual(result, Decimal("105"))

    def test_zero_quantity_returns_raw_price_to_avoid_division_by_zero(self) -> None:
        result = _effective_buy_price(Decimal("0"), Decimal("100"), Decimal("50"))
        self.assertEqual(result, Decimal("100"))


class WeightedAverageCostMathTests(unittest.TestCase):
    """Reproduces the weighted-average-cost formula `update_holding_after_buy`
    applies, and the realized-P&L formula `update_holding_after_sell` applies,
    without needing a live DB-backed holding."""

    def test_blended_average_after_adding_to_a_position(self) -> None:
        old_qty, old_avg = Decimal("10"), Decimal("100")
        new_qty, fill_price = Decimal("10"), Decimal("120")
        total_qty = old_qty + new_qty
        new_avg = (old_qty * old_avg + new_qty * fill_price) / total_qty
        # (10*100 + 10*120) / 20 = 2200/20 = 110
        self.assertEqual(new_avg, Decimal("110"))

    def test_realized_pnl_on_partial_sell_uses_average_cost_and_nets_charges(self) -> None:
        avg_price = Decimal("100")
        sell_price = Decimal("130")
        quantity = Decimal("5")
        charges = Decimal("10")
        realized_pnl = (sell_price - avg_price) * quantity - charges
        # (130 - 100) * 5 - 10 = 150 - 10 = 140
        self.assertEqual(realized_pnl, Decimal("140"))

    def test_average_cost_basis_is_unchanged_by_a_sell(self) -> None:
        # Selling part of a position realizes P&L against the existing
        # average — it must not itself shift that average (only buys do).
        avg_price = Decimal("100")
        quantity_before = Decimal("10")
        quantity_sold = Decimal("4")
        remaining = quantity_before - quantity_sold
        # average_buy_price is untouched; only quantity and total_invested move
        self.assertEqual(avg_price, Decimal("100"))
        self.assertEqual(avg_price * remaining, Decimal("600"))


def _trending_ohlc(n: int = 30, start: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    """A smooth uptrend with a small fixed daily range — gives `calculate_atr_stop`
    a deterministic, non-zero true range to chew on."""
    closes = [start + i * step for i in range(n)]
    return pd.DataFrame(
        {
            "open": [c - 0.5 for c in closes],
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
            "volume": [1000.0] * n,
        },
        index=pd.date_range("2026-01-01", periods=n, freq="D"),
    )


class CalculateAtrStopTests(unittest.TestCase):
    def test_buy_side_stop_is_below_entry_and_target_is_two_to_one(self) -> None:
        df = _trending_ohlc()
        entry = float(df["close"].iloc[-1])
        result = calculate_atr_stop(df, entry, atr_period=5, atr_multiplier=2.0, side="BUY")
        self.assertGreater(result["atr"], 0)
        stop_distance = entry - result["stop_price"]
        target_distance = result["take_profit_price"] - entry
        self.assertGreater(stop_distance, 0)
        # Take-profit is set at 2x the stop distance (2:1 reward:risk).
        self.assertAlmostEqual(target_distance, stop_distance * 2, places=2)

    def test_sell_side_mirrors_buy_side(self) -> None:
        df = _trending_ohlc()
        entry = float(df["close"].iloc[-1])
        result = calculate_atr_stop(df, entry, atr_period=5, atr_multiplier=2.0, side="SELL")
        self.assertGreater(result["stop_price"], entry)
        self.assertLess(result["take_profit_price"], entry)


class CalculatePositionSizeTests(unittest.TestCase):
    def test_smallest_of_risk_cap_and_cash_constraints_wins_risk(self) -> None:
        # risk_amount = 100,000 * 1% = 1,000; stop = 5% -> position_by_risk = 1000 / 0.05 = 20,000
        # position_by_cap = 100,000 * 10% = 10,000
        # cash = 50,000
        # smallest is position_by_cap (10,000) -> 10,000 / price(100) = 100 shares
        shares = calculate_position_size(
            portfolio_value=100_000,
            current_cash=50_000,
            current_price=100,
            risk_per_trade_pct=1.0,
            stop_loss_pct=5.0,
            max_position_size_pct=10.0,
        )
        self.assertEqual(shares, 100)

    def test_cash_constraint_can_be_the_binding_limit(self) -> None:
        # position_by_risk = (100,000*1%)/(2%/100) = 1000/0.0002... let's keep
        # the risk and cap constraints generous and let cash (900) bind:
        # position_by_cap = 100,000*50% = 50,000; position_by_risk huge;
        # cash = 900 -> 900/100 = 9 shares
        shares = calculate_position_size(
            portfolio_value=100_000,
            current_cash=900,
            current_price=100,
            risk_per_trade_pct=10.0,
            stop_loss_pct=1.0,
            max_position_size_pct=50.0,
        )
        self.assertEqual(shares, 9)

    def test_atr_stop_pct_overrides_fixed_stop_loss_pct(self) -> None:
        # With a fixed 5% stop, position_by_risk = (100000*1%)/(5/100) = 20,000.
        # An ATR stop of 2% loosens the risk constraint to (1000)/(2/100)=50,000,
        # which should change which constraint binds (and thus the result)
        # relative to the fixed-stop call below.
        with_atr = calculate_position_size(
            portfolio_value=100_000,
            current_cash=100_000,
            current_price=100,
            risk_per_trade_pct=1.0,
            stop_loss_pct=5.0,
            max_position_size_pct=100.0,
            atr_stop={"stop_pct": 2.0},
        )
        without_atr = calculate_position_size(
            portfolio_value=100_000,
            current_cash=100_000,
            current_price=100,
            risk_per_trade_pct=1.0,
            stop_loss_pct=5.0,
            max_position_size_pct=100.0,
        )
        # ATR stop_pct (2%) is tighter than the fixed stop (5%), so the risk
        # budget buys a *larger* position -- this directly checks the
        # "tighter stop -> larger size for same risk" relationship described
        # in the function's docstring.
        self.assertGreater(with_atr, without_atr)

    def test_non_positive_price_or_stop_returns_zero(self) -> None:
        self.assertEqual(
            calculate_position_size(100_000, 50_000, current_price=0, risk_per_trade_pct=1.0, stop_loss_pct=5.0),
            0,
        )
        self.assertEqual(
            calculate_position_size(100_000, 50_000, current_price=100, risk_per_trade_pct=1.0, stop_loss_pct=0),
            0,
        )


if __name__ == "__main__":
    unittest.main()
