from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR / "backend"))

from app.services.market_movers_service import (  # noqa: E402
    _split_quality_rows,
    sanitize_market_movers_payload,
)


def mover_row(
    *,
    price: float = 110,
    previous_close: float = 100,
    change_pct: float = 10,
    gap_days: int = 1,
    volume: int = 1000,
) -> dict:
    record_datetime = datetime(2026, 6, 15, tzinfo=UTC)
    return {
        "stock_id": 1,
        "price": price,
        "previous_close": previous_close,
        "change_pct": change_pct,
        "volume": volume,
        "record_datetime": record_datetime,
        "previous_datetime": record_datetime - timedelta(days=gap_days),
    }


class MarketMoverQualityTests(unittest.TestCase):
    def test_keeps_reasonable_daily_mover(self) -> None:
        clean, issues = _split_quality_rows([mover_row()])
        self.assertEqual(len(clean), 1)
        self.assertEqual(issues, {})

    def test_excludes_extreme_daily_change(self) -> None:
        clean, issues = _split_quality_rows(
            [mover_row(price=175, previous_close=100, change_pct=75)]
        )
        self.assertEqual(clean, [])
        self.assertEqual(issues, {"extreme_daily_change": 1})

    def test_excludes_zero_volume_mover(self) -> None:
        clean, issues = _split_quality_rows([mover_row(volume=0)])
        self.assertEqual(clean, [])
        self.assertEqual(issues, {"missing_or_zero_volume": 1})

    def test_excludes_stale_previous_candle_for_one_day_movers(self) -> None:
        clean, issues = _split_quality_rows([mover_row(gap_days=19)])
        self.assertEqual(clean, [])
        self.assertEqual(issues, {"stale_previous_candle": 1})

    def test_sanitizes_cached_payload_without_rewriting_cache(self) -> None:
        payload = {
            "eligible_count": 2,
            "top_gainers": [
                {"symbol": "BAD", "price": 175, "change_pct": 75, "volume": 1000},
                {"symbol": "OK", "price": 110, "change_pct": 10, "volume": 1000},
            ],
            "top_losers": [],
            "volume_shockers": [],
            "most_bought": [],
        }

        cleaned = sanitize_market_movers_payload(payload)

        self.assertIsNot(cleaned, payload)
        self.assertEqual([row["symbol"] for row in cleaned["top_gainers"]], ["OK"])
        self.assertEqual(cleaned["quality_excluded_count"], 1)
        self.assertEqual(cleaned["quality_issue_counts"], {"extreme_daily_change": 1})


if __name__ == "__main__":
    unittest.main()
