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
    normalize_financial_field_name,
    normalize_financial_statement_value,
    normalize_fundamental_value,
    parse_screener_export_rows,
    parse_screener_html_rows,
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

    def test_normalize_financial_statement_values(self) -> None:
        self.assertEqual(normalize_financial_statement_value("1,234.50"), Decimal("1234.50"))
        self.assertEqual(normalize_financial_statement_value("(250)"), Decimal("-250"))
        self.assertEqual(normalize_financial_statement_value("18%"), Decimal("0.18"))
        self.assertIsNone(normalize_financial_statement_value("x,xxx"))

    def test_normalize_financial_field_name(self) -> None:
        self.assertEqual(normalize_financial_field_name("Borrowings +"), "borrowings")
        self.assertEqual(normalize_financial_field_name("ROCE %"), "roce_percent")

    def test_parse_screener_export_rows(self) -> None:
        csv_text = "\n".join(
            [
                "Profit & Loss",
                ",Mar 2023,Mar 2024",
                "Sales +,\"1,000\",\"1,200\"",
                "Net Profit,100,150",
                "Balance Sheet",
                ",Mar 2023,Mar 2024",
                "Borrowings +,400,360",
                "Total Assets,\"2,000\",\"2,400\"",
                "Ratios",
                ",Mar 2023,Mar 2024",
                "ROCE %,15%,18%",
            ]
        )
        rows = parse_screener_export_rows(csv_text, symbol="TEST", exchange="NSE")
        keys = {
            (row["statement_type"], row["period_end"].isoformat(), row["normalized_field"])
            for row in rows
        }
        self.assertIn(("income_statement", "2024-03-31", "sales"), keys)
        self.assertIn(("balance_sheet", "2024-03-31", "borrowings"), keys)
        self.assertIn(("ratios", "2024-03-31", "roce_percent"), keys)
        roce = next(row for row in rows if row["normalized_field"] == "roce_percent" and row["period_end"].year == 2024)
        self.assertEqual(roce["value"], Decimal("0.18"))

    def test_parse_screener_html_rows(self) -> None:
        html = """
        <section id="profit-loss">
            <table>
                <tr><th></th><th>Mar 2023</th><th>Mar 2024</th></tr>
                <tr><td>Sales +</td><td>1,000</td><td>1,200</td></tr>
                <tr><td>Net Profit</td><td>100</td><td>150</td></tr>
            </table>
        </section>
        <section id="balance-sheet">
            <table>
                <tr><th></th><th>Mar 2023</th><th>Mar 2024</th></tr>
                <tr><td>Total Assets</td><td>2,000</td><td>2,400</td></tr>
            </table>
        </section>
        """
        rows = parse_screener_html_rows(html, symbol="TEST", exchange="NSE")
        keys = {
            (row["statement_type"], row["period_end"].isoformat(), row["normalized_field"])
            for row in rows
        }
        self.assertIn(("income_statement", "2024-03-31", "sales"), keys)
        self.assertIn(("balance_sheet", "2024-03-31", "total_assets"), keys)


if __name__ == "__main__":
    unittest.main()
