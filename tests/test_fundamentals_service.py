from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR / "backend"))

from app.services.fundamentals_service import (  # noqa: E402
    FUNDAMENTAL_METRICS,
    TABLE_NAME,
    normalize_fundamental_value,
)


class FundamentalsServiceTests(unittest.TestCase):
    def test_table_name_is_exact_latest_snapshot_table(self) -> None:
        self.assertEqual(TABLE_NAME, "stock_fundamentals_latest")

    def test_core_metric_count_is_ten(self) -> None:
        self.assertEqual(len(FUNDAMENTAL_METRICS), 10)

    def test_normalize_missing_values_to_none(self) -> None:
        for value in (None, "", "N/A", "nan", "-", "--", float("nan"), float("inf"), True):
            with self.subTest(value=value):
                self.assertIsNone(normalize_fundamental_value(value))

    def test_normalize_numeric_strings_and_numbers(self) -> None:
        self.assertEqual(normalize_fundamental_value("1,234.50"), Decimal("1234.50"))
        self.assertEqual(normalize_fundamental_value(42), Decimal("42"))
        self.assertEqual(normalize_fundamental_value(1.25), Decimal("1.25"))


if __name__ == "__main__":
    unittest.main()
