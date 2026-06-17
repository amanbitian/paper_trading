from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR / "backend"))

from app.services.backtest_service import _simulate_backtest  # noqa: E402
from app.services.benchmark_service import (  # noqa: E402
    build_buy_and_hold_curve,
    calculate_benchmark_metrics,
)
from app.services.cost_model_service import calculate_round_trip_pnl, calculate_trade_cost  # noqa: E402
from app.services.execution_simulator_service import (  # noqa: E402
    simulate_limit_order,
    simulate_long_stop_target,
)
from app.strategies.base import SignalResult  # noqa: E402


class BuyOnFirstSignal:
    def generate_signal(self, prices: pd.DataFrame, parameters: dict | None = None) -> SignalResult:
        if len(prices) == 1:
            return SignalResult("BUY", 100, "test buy", {})
        return SignalResult("HOLD", 0, "hold", {})


class BuyOnSecondSignal:
    def generate_signal(self, prices: pd.DataFrame, parameters: dict | None = None) -> SignalResult:
        if len(prices) == 2:
            return SignalResult("BUY", 100, "last candle buy", {})
        return SignalResult("HOLD", 0, "hold", {})


def sample_ohlc() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1000},
            {"open": 20, "high": 22, "low": 19, "close": 21, "volume": 1000},
            {"open": 30, "high": 32, "low": 29, "close": 31, "volume": 1000},
        ],
        index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
    )


class ExecutionSimulatorTests(unittest.TestCase):
    def test_long_target_only(self) -> None:
        result = simulate_long_stop_target(
            open_price=100, high_price=112, low_price=99, close_price=110, stop_loss_price=95, target_price=110
        )
        self.assertTrue(result.filled)
        self.assertEqual(result.event, "target")
        self.assertEqual(result.price, Decimal("110"))

    def test_long_stop_only(self) -> None:
        result = simulate_long_stop_target(
            open_price=100, high_price=105, low_price=94, close_price=96, stop_loss_price=95, target_price=110
        )
        self.assertTrue(result.filled)
        self.assertEqual(result.event, "stop_loss")
        self.assertEqual(result.price, Decimal("95"))

    def test_both_hit_conservative_uses_stop(self) -> None:
        result = simulate_long_stop_target(
            open_price=100,
            high_price=115,
            low_price=94,
            close_price=108,
            stop_loss_price=95,
            target_price=110,
            assumption="conservative",
        )
        self.assertEqual(result.event, "stop_loss")

    def test_both_hit_optimistic_uses_target(self) -> None:
        result = simulate_long_stop_target(
            open_price=100,
            high_price=115,
            low_price=94,
            close_price=108,
            stop_loss_price=95,
            target_price=110,
            assumption="optimistic",
        )
        self.assertEqual(result.event, "target")

    def test_limit_buy_and_sell_fill_inside_range(self) -> None:
        buy = simulate_limit_order(side="BUY", limit_price=98, open_price=100, high_price=104, low_price=97)
        sell = simulate_limit_order(side="SELL", limit_price=103, open_price=100, high_price=104, low_price=97)
        self.assertTrue(buy.filled)
        self.assertEqual(buy.price, Decimal("98"))
        self.assertTrue(sell.filled)
        self.assertEqual(sell.price, Decimal("103"))


class CostModelTests(unittest.TestCase):
    def test_buy_transaction_cost(self) -> None:
        cost = calculate_trade_cost(trade_value=100000, side="BUY", cost_model="zerodha_equity_delivery")
        self.assertGreater(cost["total_charges"], Decimal("0"))
        self.assertGreater(cost["stamp_duty"], Decimal("0"))

    def test_sell_transaction_cost(self) -> None:
        cost = calculate_trade_cost(trade_value=100000, side="SELL", cost_model="zerodha_equity_delivery")
        self.assertGreater(cost["total_charges"], Decimal("0"))
        self.assertEqual(cost["stamp_duty"], Decimal("0.0000"))

    def test_round_trip_net_pnl_after_cost(self) -> None:
        buy = calculate_trade_cost(trade_value=10000, side="BUY", cost_model="zero", slippage_cost=5)
        sell = calculate_trade_cost(trade_value=11000, side="SELL", cost_model="zero", slippage_cost=5)
        pnl = calculate_round_trip_pnl(
            entry_quoted_price=100,
            exit_quoted_price=110,
            entry_executed_price=100.05,
            exit_executed_price=109.95,
            quantity=100,
            buy_cost=buy,
            sell_cost=sell,
        )
        self.assertEqual(pnl["gross_pnl"], Decimal("1000.0000"))
        self.assertEqual(pnl["slippage_cost"], Decimal("10.0000"))
        self.assertEqual(pnl["net_pnl"], Decimal("990.0000"))

    def test_zero_cost_model_for_debug(self) -> None:
        cost = calculate_trade_cost(trade_value=100000, side="BUY", cost_model="zero")
        self.assertEqual(cost["total_charges"], Decimal("0"))


class BacktestExecutionModeTests(unittest.TestCase):
    def test_signal_on_close_executes_next_open(self) -> None:
        metrics = _simulate_backtest(
            sample_ohlc(),
            BuyOnFirstSignal(),
            {"stop_loss_pct": 5, "max_position_size_pct": 10},
            Decimal("100000"),
            slippage_bps=0,
            execution_mode="signal_on_close_execute_next_open",
            cost_model="zero",
        )
        self.assertEqual(metrics["simulated_trades"][0]["trade_date"].isoformat(), "2026-01-02")
        self.assertEqual(metrics["simulated_trades"][0]["price"], Decimal("20.0000"))

    def test_no_final_candle_trade_for_next_bar_execution(self) -> None:
        metrics = _simulate_backtest(
            sample_ohlc().iloc[:2],
            BuyOnSecondSignal(),
            {"stop_loss_pct": 5, "max_position_size_pct": 10},
            Decimal("100000"),
            slippage_bps=0,
            execution_mode="signal_on_close_execute_next_open",
            cost_model="zero",
        )
        self.assertEqual(metrics["num_trades"], 0)

    def test_same_open_mode_only_when_explicit(self) -> None:
        metrics = _simulate_backtest(
            sample_ohlc().iloc[:2],
            BuyOnFirstSignal(),
            {"stop_loss_pct": 5, "max_position_size_pct": 10},
            Decimal("100000"),
            slippage_bps=0,
            execution_mode="signal_on_open_execute_same_open",
            cost_model="zero",
        )
        self.assertEqual(metrics["simulated_trades"][0]["trade_date"].isoformat(), "2026-01-01")
        self.assertEqual(metrics["simulated_trades"][0]["price"], Decimal("10.0000"))


class BenchmarkServiceTests(unittest.TestCase):
    def test_strategy_return_vs_benchmark_return_and_excess(self) -> None:
        strategy_curve = [
            {"date": "2026-01-01", "equity": 100},
            {"date": "2026-01-02", "equity": 110},
        ]
        benchmark_curve = [
            {"date": "2026-01-01", "equity": 100},
            {"date": "2026-01-02", "equity": 105},
        ]
        metrics = calculate_benchmark_metrics(strategy_curve, benchmark_curve)
        self.assertEqual(metrics["benchmark_return"], Decimal("5.0"))
        self.assertEqual(metrics["excess_return"], Decimal("5.0"))

    def test_beta_uses_aligned_daily_returns(self) -> None:
        strategy_curve = [
            {"date": "2026-01-01", "equity": 100},
            {"date": "2026-01-02", "equity": 110},
            {"date": "2026-01-03", "equity": 99},
            {"date": "2026-01-04", "equity": 118.8},
        ]
        benchmark_curve = [
            {"date": "2026-01-01", "equity": 100},
            {"date": "2026-01-02", "equity": 105},
            {"date": "2026-01-03", "equity": 99.75},
            {"date": "2026-01-04", "equity": 109.725},
        ]
        metrics = calculate_benchmark_metrics(strategy_curve, benchmark_curve)
        self.assertEqual(metrics["beta"], Decimal("2.0"))

    def test_missing_benchmark_returns_none_metrics(self) -> None:
        metrics = calculate_benchmark_metrics(
            [{"date": "2026-01-01", "equity": 100}, {"date": "2026-01-02", "equity": 101}],
            [],
        )
        self.assertIsNone(metrics["benchmark_return"])
        self.assertIsNone(metrics["excess_return"])

    def test_buy_and_hold_curve_aligns_to_strategy_dates(self) -> None:
        dataframe = pd.DataFrame(
            [{"close": 100}, {"close": 110}, {"close": 121}],
            index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
        )
        curve = build_buy_and_hold_curve(
            dataframe,
            [pd.Timestamp("2026-01-02").date(), pd.Timestamp("2026-01-03").date()],
            Decimal("100000"),
        )
        self.assertEqual(curve[0]["equity"], 100000.0)
        self.assertEqual(curve[1]["equity"], 110000.0)


if __name__ == "__main__":
    unittest.main()
