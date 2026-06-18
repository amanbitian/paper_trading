from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR / "backend"))

from app.services.exchange_bhavcopy_service import clear_bhavcopy_cache, find_bhavcopy_candle  # noqa: E402
from app.services.data_quality_service import _price_changed, _volume_changed  # noqa: E402
from app.services.data_quality_service import _suspect_reasons  # noqa: E402


class DataQualityServiceTests(unittest.TestCase):
    def test_price_tolerance_ignores_small_rounding_drift(self) -> None:
        self.assertFalse(_price_changed("100.0000", "100.03"))
        self.assertTrue(_price_changed("100.0000", "100.08"))

    def test_volume_tolerance_ignores_small_provider_drift(self) -> None:
        self.assertFalse(_volume_changed(1000, 1007))
        self.assertTrue(_volume_changed(1000, 1020))

    def test_missing_values_count_as_changes(self) -> None:
        self.assertFalse(_price_changed(None, None))
        self.assertTrue(_price_changed(None, "1.0"))
        self.assertFalse(_volume_changed(None, None))
        self.assertTrue(_volume_changed(None, 1))

    def test_suspect_reasons_flags_impossible_ohlc_as_high_severity(self) -> None:
        reasons, severity = _suspect_reasons(
            {
                "open": "100",
                "high": "90",
                "low": "95",
                "close": "98",
                "volume": 1000,
                "duplicate_count": 1,
                "daily_change_pct": "1.0",
            }
        )

        self.assertEqual(severity, "high")
        self.assertIn("high is below open/low/close", reasons)

    def test_suspect_reasons_flags_extreme_move_as_medium_severity(self) -> None:
        reasons, severity = _suspect_reasons(
            {
                "open": "100",
                "high": "151",
                "low": "99",
                "close": "150",
                "volume": 1000,
                "duplicate_count": 1,
                "daily_change_pct": "50.0",
            }
        )

        self.assertEqual(severity, "medium")
        self.assertIn("daily close move is 50.00%", reasons)

    def test_bhavcopy_loader_reads_nse_style_csv(self) -> None:
        clear_bhavcopy_cache()
        with tempfile.TemporaryDirectory(dir=ROOT_DIR) as temp_dir:
            bhavcopy_dir = Path(temp_dir)
            csv_path = bhavcopy_dir / "nse_cm_20260615.csv"
            csv_path.write_text(
                "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,TOTTRDQTY,TIMESTAMP\n"
                "RELIANCE,EQ,100,110,99,108,12345,15-Jun-2026\n",
                encoding="utf-8",
            )

            candle = find_bhavcopy_candle(
                symbol="RELIANCE.NS",
                exchange="NSE",
                trade_date=date(2026, 6, 15),
                root=str(bhavcopy_dir),
            )

        self.assertIsNotNone(candle)
        self.assertEqual(str(candle.close), "108")
        self.assertEqual(candle.volume, 12345)


if __name__ == "__main__":
    unittest.main()
