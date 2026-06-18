from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR / "backend"))

from app.services.strategy_explainer_service import (  # noqa: E402
    _headline,
    _rsi_reasons,
    _sma_reasons,
)


class StrategyExplainerServiceTests(unittest.TestCase):
    def test_rsi_reason_marks_oversold_as_positive(self) -> None:
        reasons = _rsi_reasons({"rsi": 28.5}, {"oversold": 35, "overbought": 65})
        self.assertEqual(reasons[0]["label"], "RSI")
        self.assertEqual(reasons[0]["status"], "positive")
        self.assertIn("buy zone", reasons[0]["message"])

    def test_rsi_reason_marks_overbought_as_negative(self) -> None:
        reasons = _rsi_reasons({"rsi": 72.0}, {"oversold": 35, "overbought": 65})
        self.assertEqual(reasons[0]["status"], "negative")
        self.assertIn("overbought", reasons[0]["message"])

    def test_sma_reason_marks_short_above_long_as_positive(self) -> None:
        reasons = _sma_reasons({"short_sma": 105.0, "long_sma": 100.0})
        self.assertEqual(reasons[0]["label"], "SMA spread")
        self.assertEqual(reasons[0]["status"], "positive")
        self.assertEqual(reasons[0]["threshold"], "Short SMA > Long SMA")

    def test_headline_uses_supportive_positive_reason_labels(self) -> None:
        headline = _headline(
            "BUY",
            "Quality Momentum",
            [
                {"label": "12-1 month momentum", "status": "positive"},
                {"label": "200-DMA trend", "status": "positive"},
            ],
        )
        self.assertEqual(headline, "BUY: 12-1 month momentum, 200-DMA trend supportive")


if __name__ == "__main__":
    unittest.main()
