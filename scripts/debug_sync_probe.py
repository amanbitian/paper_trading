"""Temporary probe for market sync diagnostics."""
from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import text

from app.database import SessionLocal
from app.services.market_data_service import fetch_stock_history_result, previous_business_day


def main() -> None:
    symbols = ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS"]
    start = date(2026, 5, 27)
    end = date(2026, 5, 30)
    print("previous_business_day", previous_business_day())
    for sym in symbols:
        result = fetch_stock_history_result(sym, start_date=start, end_date=end)
        df = result.dataframe
        dates: list[date] = []
        if not df.empty:
            col = "Datetime" if "Datetime" in df.columns else "Date"
            dates = sorted(pd.to_datetime(df[col]).dt.date.unique().tolist())
        print(sym, "rows", len(df), "dates", dates, "err", result.error_message)

    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                """
                SELECT price_datetime::date AS d, COUNT(*) AS c
                FROM stock_prices
                WHERE timeframe = '1d'
                  AND price_datetime::date >= '2026-05-20'
                GROUP BY 1
                ORDER BY 1 DESC
                LIMIT 10
                """
            )
        ).mappings()
        print("counts_by_date", [dict(row) for row in rows])
        active = db.execute(
            text(
                "SELECT COUNT(*) FROM stocks WHERE is_active = true AND is_delisted = false"
            )
        ).scalar()
        print("active_stocks", active)
        cache = db.execute(
            text(
                """
                SELECT payload->>'record_date' AS rd
                FROM market_analytics_cache
                WHERE cache_key = 'market_movers_t1_v1'
                """
            )
        ).scalar()
        print("movers_cache_record_date", cache)
        covered = db.execute(
            text(
                """
                SELECT COUNT(*) FROM (
                    SELECT stock_id, MAX(price_datetime::date) AS last_date
                    FROM stock_prices
                    WHERE timeframe = '1d'
                    GROUP BY stock_id
                ) t
                WHERE last_date >= '2026-05-29'
                """
            )
        ).scalar()
        print("stocks_with_last_date_ge_2026_05_29", covered)
        print("active_stocks_count", active)
        print("total_stocks", db.execute(text("SELECT COUNT(*) FROM stocks")).scalar())
        reliance = db.execute(
            text(
                "SELECT symbol, is_active, is_delisted FROM stocks WHERE yahoo_symbol = 'RELIANCE.NS'"
            )
        ).mappings().first()
        print("RELIANCE", dict(reliance) if reliance else None)
        active_rows = db.execute(
            text(
                """
                SELECT symbol, yahoo_symbol
                FROM stocks
                WHERE is_active = true AND is_delisted = false
                ORDER BY symbol
                """
            )
        ).mappings()
        print("active_symbols", [row["yahoo_symbol"] for row in active_rows])
    finally:
        db.close()


if __name__ == "__main__":
    main()
