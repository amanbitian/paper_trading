from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.stock import Stock, StockPerformanceSnapshot, StockPrice
from app.services.analytics_refresh_service import refresh_all_analytics
from app.services.exchange_bhavcopy_service import BhavcopyCandle, find_bhavcopy_candle, load_bhavcopy_candles
from app.services.market_data_service import (
    DAILY_TIMEFRAME,
    fetch_stock_history_result,
    save_stock_prices,
    sync_stock_prices,
)

logger = logging.getLogger(__name__)

PRICE_TOLERANCE = Decimal("0.05")
VOLUME_TOLERANCE_RATIO = Decimal("0.01")
SUSPECT_DAILY_CHANGE_PCT = Decimal("50")
SUSPECT_LOOKBACK_DAYS = 730


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _price_changed(left: Any, right: Any) -> bool:
    left_decimal = _decimal_or_none(left)
    right_decimal = _decimal_or_none(right)
    if left_decimal is None and right_decimal is None:
        return False
    if left_decimal is None or right_decimal is None:
        return True
    return abs(left_decimal - right_decimal) > PRICE_TOLERANCE


def _volume_changed(left: Any, right: Any) -> bool:
    if left is None and right is None:
        return False
    if left is None or right is None:
        return True
    try:
        left_int = int(left)
        right_int = int(right)
    except (TypeError, ValueError):
        return True
    if left_int == right_int:
        return False
    tolerance = max(1, int(max(abs(left_int), abs(right_int)) * float(VOLUME_TOLERANCE_RATIO)))
    return abs(left_int - right_int) > tolerance


def _provider_row_for_date(dataframe: pd.DataFrame, target_date: date) -> pd.Series | None:
    if dataframe.empty:
        return None
    date_col = "Datetime" if "Datetime" in dataframe.columns else "Date"
    if date_col not in dataframe.columns:
        return None
    working = dataframe.copy()
    working["_provider_date"] = pd.to_datetime(working[date_col], errors="coerce").dt.date
    rows = working.loc[working["_provider_date"] == target_date]
    if rows.empty:
        return None
    return rows.iloc[-1]


def _bhavcopy_as_comparison_row(candle: BhavcopyCandle) -> dict[str, Any]:
    return {
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "adjusted_close": candle.close,
        "volume": candle.volume,
    }


def _provider_as_comparison_row(row: pd.Series) -> dict[str, Any]:
    return {
        "open": row.get("Open"),
        "high": row.get("High"),
        "low": row.get("Low"),
        "close": row.get("Close"),
        "adjusted_close": row.get("Adj Close", row.get("Close")),
        "volume": row.get("Volume"),
    }


def _mismatched_fields(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    for field_name in ("open", "high", "low", "close", "adjusted_close"):
        if _price_changed(left.get(field_name), right.get(field_name)):
            fields.append(field_name)
    if _volume_changed(left.get("volume"), right.get("volume")):
        fields.append("volume")
    return fields


def _upsert_bhavcopy_candle(db: Session, stock_id: int, candle: BhavcopyCandle) -> int:
    payload = {
        "stock_id": stock_id,
        "price_datetime": datetime.combine(candle.trade_date, datetime.min.time(), tzinfo=UTC),
        "timeframe": DAILY_TIMEFRAME,
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "adjusted_close": candle.close,
        "volume": candle.volume,
        "source": "exchange_bhavcopy",
    }
    stmt = insert(StockPrice).values([payload])
    stmt = stmt.on_conflict_do_update(
        constraint="uq_stock_prices_stock_dt_tf",
        set_={
            "open": stmt.excluded.open,
            "high": stmt.excluded.high,
            "low": stmt.excluded.low,
            "close": stmt.excluded.close,
            "adjusted_close": stmt.excluded.adjusted_close,
            "volume": stmt.excluded.volume,
            "source": stmt.excluded.source,
        },
    )
    db.execute(stmt)
    db.flush()
    return 1


def _count_rows(db: Session, query: str, params: dict[str, Any] | None = None) -> int:
    return int(db.scalar(text(query), params or {}) or 0)


def _detect_summary(db: Session) -> dict[str, int]:
    return {
        "duplicate_daily_candles": _count_rows(
            db,
            """
            WITH daily_dupes AS (
                SELECT stock_id, price_datetime::date, timeframe
                FROM stock_prices
                WHERE timeframe = :timeframe
                GROUP BY stock_id, price_datetime::date, timeframe
                HAVING COUNT(*) > 1
            )
            SELECT COUNT(*) FROM daily_dupes
            """,
            {"timeframe": DAILY_TIMEFRAME},
        ),
        "invalid_ohlc_rows": _count_rows(
            db,
            """
            SELECT COUNT(*)
            FROM stock_prices
            WHERE timeframe = :timeframe
              AND (
                close IS NULL
                OR open IS NULL
                OR high IS NULL
                OR low IS NULL
                OR open < 0
                OR high < 0
                OR low < 0
                OR close < 0
                OR volume < 0
                OR high < GREATEST(open, low, close)
                OR low > LEAST(open, high, close)
              )
            """,
            {"timeframe": DAILY_TIMEFRAME},
        ),
        "missing_price_symbols": _count_rows(
            db,
            """
            SELECT COUNT(*)
            FROM stocks s
            WHERE s.is_active = TRUE
              AND NOT EXISTS (
                  SELECT 1
                  FROM stock_prices sp
                  WHERE sp.stock_id = s.id
                    AND sp.timeframe = :timeframe
              )
            """,
            {"timeframe": DAILY_TIMEFRAME},
        ),
        "stale_symbols": _count_rows(
            db,
            """
            WITH latest AS (
                SELECT MAX(price_datetime::date) AS latest_date
                FROM stock_prices
                WHERE timeframe = :timeframe
            ),
            per_stock AS (
                SELECT s.id, MAX(sp.price_datetime::date) AS latest_symbol_date
                FROM stocks s
                LEFT JOIN stock_prices sp
                    ON sp.stock_id = s.id AND sp.timeframe = :timeframe
                WHERE s.is_active = TRUE
                GROUP BY s.id
            )
            SELECT COUNT(*)
            FROM per_stock, latest
            WHERE latest.latest_date IS NOT NULL
              AND (
                per_stock.latest_symbol_date IS NULL
                OR per_stock.latest_symbol_date < latest.latest_date
              )
            """,
            {"timeframe": DAILY_TIMEFRAME},
        ),
    }


def _to_float(value: Any) -> float | None:
    decimal_value = _decimal_or_none(value)
    return None if decimal_value is None else float(decimal_value)


def _suspect_reasons(row: dict[str, Any]) -> tuple[list[str], str]:
    reasons: list[str] = []
    duplicate_count = int(row.get("duplicate_count") or 0)
    if duplicate_count > 1:
        reasons.append(f"{duplicate_count} candles stored for the same symbol/date")

    open_price = _decimal_or_none(row.get("open"))
    high_price = _decimal_or_none(row.get("high"))
    low_price = _decimal_or_none(row.get("low"))
    close_price = _decimal_or_none(row.get("close"))
    prices = [open_price, high_price, low_price, close_price]

    if any(price is None for price in prices):
        reasons.append("required OHLC value is missing")
    elif any(price < 0 for price in prices if price is not None):
        reasons.append("negative OHLC value")
    elif high_price is not None and low_price is not None and close_price is not None and open_price is not None:
        if high_price < max(open_price, low_price, close_price):
            reasons.append("high is below open/low/close")
        if low_price > min(open_price, high_price, close_price):
            reasons.append("low is above open/high/close")

    volume = row.get("volume")
    if volume is None:
        reasons.append("volume is missing")
    else:
        try:
            if int(volume) <= 0:
                reasons.append("volume is zero or negative")
        except (TypeError, ValueError):
            reasons.append("volume is not numeric")

    daily_change = _decimal_or_none(row.get("daily_change_pct"))
    if daily_change is not None and abs(daily_change) >= SUSPECT_DAILY_CHANGE_PCT:
        reasons.append(f"daily close move is {daily_change:.2f}%")

    if any(reason.startswith("high ") or reason.startswith("low ") or "negative OHLC" in reason for reason in reasons):
        return reasons, "high"
    if duplicate_count > 1 or any("daily close move" in reason for reason in reasons):
        return reasons, "medium"
    return reasons, "low"


def get_suspected_corrupt_data_points(
    db: Session,
    *,
    limit: int = 100,
    lookback_days: int = SUSPECT_LOOKBACK_DAYS,
) -> dict[str, Any]:
    """Return read-only candle-level data quality insights for the Data tab."""
    limit = max(1, min(int(limit or 100), 500))
    lookback_days = max(30, min(int(lookback_days or SUSPECT_LOOKBACK_DAYS), 3650))
    rows = db.execute(
        text(
            """
            WITH bounds AS (
                SELECT MAX(price_datetime)::date AS latest_date
                FROM stock_prices
                WHERE timeframe = :timeframe
            ),
            scoped_prices AS (
                SELECT
                    sp.*,
                    sp.price_datetime::date AS price_date
                FROM stock_prices sp, bounds b
                WHERE sp.timeframe = :timeframe
                  AND (
                    b.latest_date IS NULL
                    OR sp.price_datetime::date >= b.latest_date - (:lookback_days * INTERVAL '1 day')
                  )
            ),
            duplicate_counts AS (
                SELECT
                    stock_id,
                    price_date,
                    COUNT(*)::int AS duplicate_count
                FROM scoped_prices
                GROUP BY stock_id, price_date
            ),
            ordered AS (
                SELECT
                    sp.id AS price_id,
                    sp.stock_id,
                    s.symbol,
                    s.exchange,
                    s.company_name,
                    s.yahoo_symbol,
                    sp.price_date,
                    sp.open,
                    sp.high,
                    sp.low,
                    sp.close,
                    sp.adjusted_close,
                    sp.volume,
                    COALESCE(dc.duplicate_count, 1)::int AS duplicate_count,
                    LAG(sp.close) OVER (
                        PARTITION BY sp.stock_id
                        ORDER BY sp.price_datetime
                    ) AS previous_close,
                    LAG(sp.price_datetime::date) OVER (
                        PARTITION BY sp.stock_id
                        ORDER BY sp.price_datetime
                    ) AS previous_date
                FROM scoped_prices sp
                JOIN stocks s ON s.id = sp.stock_id
                LEFT JOIN duplicate_counts dc
                    ON dc.stock_id = sp.stock_id
                   AND dc.price_date = sp.price_date
                WHERE s.is_active = TRUE
            ),
            scored AS (
                SELECT
                    *,
                    CASE
                        WHEN previous_close IS NULL OR previous_close = 0 OR close IS NULL THEN NULL
                        ELSE ROUND(((close - previous_close) / previous_close * 100)::numeric, 4)
                    END AS daily_change_pct
                FROM ordered
            )
            SELECT *
            FROM scored
            WHERE duplicate_count > 1
               OR close IS NULL
               OR open IS NULL
               OR high IS NULL
               OR low IS NULL
               OR open < 0
               OR high < 0
               OR low < 0
               OR close < 0
               OR volume IS NULL
               OR volume <= 0
               OR high < GREATEST(open, low, close)
               OR low > LEAST(open, high, close)
               OR ABS(COALESCE(daily_change_pct, 0)) >= :change_threshold
            ORDER BY
                CASE
                    WHEN close IS NULL OR open IS NULL OR high IS NULL OR low IS NULL THEN 1
                    WHEN open < 0 OR high < 0 OR low < 0 OR close < 0 THEN 1
                    WHEN high < GREATEST(open, low, close) THEN 1
                    WHEN low > LEAST(open, high, close) THEN 1
                    WHEN duplicate_count > 1 THEN 2
                    WHEN ABS(COALESCE(daily_change_pct, 0)) >= :change_threshold THEN 3
                    ELSE 4
                END,
                price_date DESC,
                symbol
            LIMIT :limit
            """
        ),
        {
            "timeframe": DAILY_TIMEFRAME,
            "lookback_days": lookback_days,
            "change_threshold": float(SUSPECT_DAILY_CHANGE_PCT),
            "limit": limit,
        },
    ).mappings().all()

    points: list[dict[str, Any]] = []
    issue_counts: dict[str, int] = defaultdict(int)
    severity_counts: dict[str, int] = defaultdict(int)
    for raw_row in rows:
        row = dict(raw_row)
        reasons, severity = _suspect_reasons(row)
        for reason in reasons:
            issue_counts[reason] += 1
        severity_counts[severity] += 1
        points.append(
            {
                "symbol": row.get("symbol"),
                "exchange": row.get("exchange"),
                "company_name": row.get("company_name"),
                "provider_ticker": row.get("yahoo_symbol"),
                "price_date": row.get("price_date"),
                "open": _to_float(row.get("open")),
                "high": _to_float(row.get("high")),
                "low": _to_float(row.get("low")),
                "close": _to_float(row.get("close")),
                "previous_close": _to_float(row.get("previous_close")),
                "daily_change_pct": _to_float(row.get("daily_change_pct")),
                "volume": row.get("volume"),
                "duplicate_count": int(row.get("duplicate_count") or 1),
                "severity": severity,
                "reasons": reasons,
            }
        )

    return {
        "as_of": datetime.now(UTC),
        "limit": limit,
        "lookback_days": lookback_days,
        "change_threshold_pct": float(SUSPECT_DAILY_CHANGE_PCT),
        "points": points,
        "issue_counts": dict(issue_counts),
        "severity_counts": dict(severity_counts),
        "summary": _detect_summary(db),
    }


def _delete_duplicate_daily_candles(db: Session) -> int:
    result = db.execute(
        text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY stock_id, price_datetime::date, timeframe
                        ORDER BY price_datetime DESC, id DESC
                    ) AS rn
                FROM stock_prices
                WHERE timeframe = :timeframe
            )
            DELETE FROM stock_prices sp
            USING ranked r
            WHERE sp.id = r.id
              AND r.rn > 1
            """
        ),
        {"timeframe": DAILY_TIMEFRAME},
    )
    return int(result.rowcount or 0)


def _invalid_price_rows(db: Session, limit: int) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT
                sp.id AS price_id,
                sp.stock_id,
                s.symbol,
                s.exchange,
                s.yahoo_symbol,
                sp.price_datetime::date AS price_date,
                sp.open,
                sp.high,
                sp.low,
                sp.close,
                sp.adjusted_close,
                sp.volume
            FROM stock_prices sp
            JOIN stocks s ON s.id = sp.stock_id
            WHERE sp.timeframe = :timeframe
              AND s.is_active = TRUE
              AND (
                sp.close IS NULL
                OR sp.open IS NULL
                OR sp.high IS NULL
                OR sp.low IS NULL
                OR sp.open < 0
                OR sp.high < 0
                OR sp.low < 0
                OR sp.close < 0
                OR sp.volume < 0
                OR sp.high < GREATEST(sp.open, sp.low, sp.close)
                OR sp.low > LEAST(sp.open, sp.high, sp.close)
              )
            ORDER BY sp.price_datetime DESC, s.symbol
            LIMIT :limit
            """
        ),
        {"timeframe": DAILY_TIMEFRAME, "limit": limit},
    ).mappings()
    return [dict(row) for row in rows]


def _stale_or_missing_stocks(db: Session, limit: int) -> list[Stock]:
    stmt = text(
        """
        WITH latest AS (
            SELECT MAX(price_datetime::date) AS latest_date
            FROM stock_prices
            WHERE timeframe = :timeframe
        ),
        ranked AS (
            SELECT
                s.id,
                MAX(sp.price_datetime::date) AS latest_symbol_date,
                CASE WHEN COUNT(sp.id) = 0 THEN 0 ELSE 1 END AS has_prices
            FROM stocks s
            LEFT JOIN stock_prices sp
                ON sp.stock_id = s.id AND sp.timeframe = :timeframe
            WHERE s.is_active = TRUE
            GROUP BY s.id
        )
        SELECT r.id
        FROM ranked r, latest
        WHERE r.latest_symbol_date IS NULL
           OR (
             latest.latest_date IS NOT NULL
             AND r.latest_symbol_date < latest.latest_date
           )
        ORDER BY r.has_prices ASC, r.latest_symbol_date ASC NULLS FIRST, r.id
        LIMIT :limit
        """
    )
    stock_ids = [
        int(row["id"])
        for row in db.execute(stmt, {"timeframe": DAILY_TIMEFRAME, "limit": limit}).mappings()
    ]
    if not stock_ids:
        return []
    return list(db.scalars(select(Stock).where(Stock.id.in_(stock_ids)).order_by(Stock.symbol.asc())))


def _latest_sample_rows(db: Session, limit: int) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT DISTINCT ON (s.id)
                sp.id AS price_id,
                s.id AS stock_id,
                s.symbol,
                s.exchange,
                s.yahoo_symbol,
                sp.price_datetime::date AS price_date,
                sp.open,
                sp.high,
                sp.low,
                sp.close,
                sp.adjusted_close,
                sp.volume
            FROM stocks s
            JOIN stock_prices sp ON sp.stock_id = s.id
            WHERE s.is_active = TRUE
              AND sp.timeframe = :timeframe
              AND sp.close IS NOT NULL
            ORDER BY s.id, sp.price_datetime DESC
            LIMIT :limit
            """
        ),
        {"timeframe": DAILY_TIMEFRAME, "limit": limit},
    ).mappings()
    return [dict(row) for row in rows]


def _cross_check_price_row(db: Session, row: dict[str, Any], *, repair: bool) -> dict[str, Any]:
    target_date = row["price_date"]
    if isinstance(target_date, datetime):
        target_date = target_date.date()
    stored_row = {
        "open": row.get("open"),
        "high": row.get("high"),
        "low": row.get("low"),
        "close": row.get("close"),
        "adjusted_close": row.get("adjusted_close"),
        "volume": row.get("volume"),
    }
    bhavcopy_candle = find_bhavcopy_candle(
        symbol=str(row["symbol"]),
        exchange=str(row["exchange"]),
        trade_date=target_date,
    )
    bhavcopy_row = _bhavcopy_as_comparison_row(bhavcopy_candle) if bhavcopy_candle else None
    fetch_start = target_date - timedelta(days=3)
    fetch_end = target_date + timedelta(days=3)
    provider = fetch_stock_history_result(
        row["yahoo_symbol"],
        start_date=fetch_start,
        end_date=fetch_end,
        interval=DAILY_TIMEFRAME,
    )
    provider_row = None
    provider_comparison_row = None
    if not provider.dataframe.empty:
        provider_row = _provider_row_for_date(provider.dataframe, target_date)
        if provider_row is not None:
            provider_comparison_row = _provider_as_comparison_row(provider_row)

    if bhavcopy_row is not None:
        stored_vs_bhavcopy = _mismatched_fields(stored_row, bhavcopy_row)
        yfinance_vs_bhavcopy = (
            _mismatched_fields(provider_comparison_row, bhavcopy_row)
            if provider_comparison_row is not None
            else []
        )

        if not stored_vs_bhavcopy:
            yfinance_note = (
                "yfinance also matches"
                if provider_comparison_row is not None and not yfinance_vs_bhavcopy
                else "yfinance unavailable or differs"
            )
            return {
                "symbol": row["symbol"],
                "exchange": row["exchange"],
                "date": target_date,
                "status": "verified_exchange",
                "details": f"Matches bhavcopy ({bhavcopy_candle.source_file}); {yfinance_note}.",
            }

        if provider_comparison_row is not None and yfinance_vs_bhavcopy:
            return {
                "symbol": row["symbol"],
                "exchange": row["exchange"],
                "date": target_date,
                "status": "source_conflict",
                "details": (
                    f"Stored differs from bhavcopy on {', '.join(stored_vs_bhavcopy)}; "
                    f"yfinance differs from bhavcopy on {', '.join(yfinance_vs_bhavcopy)}. "
                    f"Bhavcopy: {bhavcopy_candle.source_file}"
                ),
            }

        fixed = False
        if repair:
            fixed = _upsert_bhavcopy_candle(db, int(row["stock_id"]), bhavcopy_candle) > 0
        return {
            "symbol": row["symbol"],
            "exchange": row["exchange"],
            "date": target_date,
            "status": "fixed_exchange_confirmed" if fixed else "mismatch_exchange",
            "details": f"{', '.join(stored_vs_bhavcopy)} via {bhavcopy_candle.source_file}",
        }

    if provider.dataframe.empty:
        return {
            "symbol": row["symbol"],
            "exchange": row["exchange"],
            "date": target_date,
            "status": "source_unavailable",
            "details": provider.error_message or "No yfinance candle returned.",
        }
    if provider_row is None:
        return {
            "symbol": row["symbol"],
            "exchange": row["exchange"],
            "date": target_date,
            "status": "source_date_missing",
            "details": "yfinance returned data, but not for this stored candle date.",
        }

    mismatched_fields = _mismatched_fields(stored_row, provider_comparison_row or {})

    if not mismatched_fields:
        return {
            "symbol": row["symbol"],
            "exchange": row["exchange"],
            "date": target_date,
            "status": "matched",
            "details": "Stored candle matches yfinance within tolerance.",
        }

    fixed = False
    if repair:
        saved = save_stock_prices(db, int(row["stock_id"]), provider.dataframe, DAILY_TIMEFRAME)
        fixed = saved > 0

    return {
        "symbol": row["symbol"],
        "exchange": row["exchange"],
        "date": target_date,
        "status": "fixed" if fixed else "mismatch",
        "details": f"{', '.join(mismatched_fields)}; bhavcopy not available",
    }


def optimize_market_data_quality(
    db: Session,
    *,
    max_symbols: int = 25,
    cross_check_latest: int = 10,
    repair: bool = True,
) -> dict[str, Any]:
    """Audit stored candles, compare priority rows with yfinance, and repair safe drift.

    The operation is intentionally bounded. It cleans structural issues first,
    then uses yfinance for only the highest-priority missing/stale/invalid rows
    so the Data page button stays responsive and repeatable.
    """
    started_at = datetime.now(UTC)
    max_symbols = max(1, min(int(max_symbols or 25), 100))
    cross_check_latest = max(0, min(int(cross_check_latest or 0), 50))

    before = _detect_summary(db)
    repairs: dict[str, int] = defaultdict(int)
    checked_symbols: set[str] = set()
    checked_rows: list[dict[str, Any]] = []
    errors: list[str] = []

    if repair:
        repairs["duplicate_daily_rows_deleted"] = _delete_duplicate_daily_candles(db)
        db.flush()

    remaining_budget = max_symbols
    for row in _invalid_price_rows(db, remaining_budget):
        checked_symbols.add(str(row["yahoo_symbol"]))
        try:
            result = _cross_check_price_row(db, row, repair=repair)
            checked_rows.append(result)
            if result["status"] == "fixed":
                repairs["source_mismatches_fixed"] += 1
            elif result["status"] == "fixed_exchange_confirmed":
                repairs["exchange_confirmed_rows_fixed"] += 1
        except Exception as exc:
            logger.exception("Data quality source repair failed for %s", row.get("yahoo_symbol"))
            errors.append(f"{row.get('yahoo_symbol')}: {exc}")
        remaining_budget -= 1
        if remaining_budget <= 0:
            break

    if remaining_budget > 0:
        for stock in _stale_or_missing_stocks(db, remaining_budget):
            checked_symbols.add(stock.yahoo_symbol)
            try:
                if repair:
                    sync_result = sync_stock_prices(
                        db,
                        stock.id,
                        incremental=True,
                        commit=False,
                        skip_probe=True,
                    )
                    if sync_result.rows_saved > 0:
                        repairs["stale_or_missing_rows_saved"] += sync_result.rows_saved
                    checked_rows.append(
                        {
                            "symbol": stock.symbol,
                            "exchange": stock.exchange,
                            "date": None,
                            "status": sync_result.outcome,
                            "details": f"{sync_result.rows_saved} rows saved",
                        }
                    )
                else:
                    checked_rows.append(
                        {
                            "symbol": stock.symbol,
                            "exchange": stock.exchange,
                            "date": None,
                            "status": "stale_or_missing",
                            "details": "Would run an incremental yfinance sync.",
                        }
                    )
            except Exception as exc:
                logger.exception("Data quality stale repair failed for %s", stock.yahoo_symbol)
                errors.append(f"{stock.yahoo_symbol}: {exc}")
            remaining_budget -= 1
            if remaining_budget <= 0:
                break

    if cross_check_latest > 0:
        for row in _latest_sample_rows(db, cross_check_latest):
            if str(row["yahoo_symbol"]) in checked_symbols:
                continue
            checked_symbols.add(str(row["yahoo_symbol"]))
            try:
                result = _cross_check_price_row(db, row, repair=repair)
                checked_rows.append(result)
                if result["status"] == "fixed":
                    repairs["source_mismatches_fixed"] += 1
                elif result["status"] == "fixed_exchange_confirmed":
                    repairs["exchange_confirmed_rows_fixed"] += 1
            except Exception as exc:
                logger.exception("Data quality latest cross-check failed for %s", row.get("yahoo_symbol"))
                errors.append(f"{row.get('yahoo_symbol')}: {exc}")

    db.commit()
    analytics_payload: dict[str, Any] | None = None
    if repair and any(value > 0 for value in repairs.values()):
        try:
            analytics_payload = refresh_all_analytics(db)
        except Exception as exc:
            logger.exception("Data quality analytics refresh failed")
            errors.append(f"analytics refresh: {exc}")

    after = _detect_summary(db)
    finished_at = datetime.now(UTC)
    status_counts: dict[str, int] = defaultdict(int)
    for row in checked_rows:
        status_counts[str(row.get("status") or "unknown")] += 1

    return {
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": (finished_at - started_at).total_seconds(),
        "repair_enabled": repair,
        "max_symbols": max_symbols,
        "cross_check_latest": cross_check_latest,
        "before": before,
        "after": after,
        "repairs": dict(repairs),
        "checked_symbols": len(checked_symbols),
        "checked_rows": checked_rows[:50],
        "status_counts": dict(status_counts),
        "analytics": analytics_payload,
        "errors": errors[:20],
    }


# ---------------------------------------------------------------------------
# Bhavcopy Audit
# ---------------------------------------------------------------------------

def _clean_sym(value: Any) -> str:
    """Normalise a ticker symbol the same way the bhavcopy parser does."""
    text = str(value or "").strip().upper()
    if "." in text:
        text = text.split(".", 1)[0]
    return " ".join(text.split())


def run_bhavcopy_audit(db: Session, *, sample_stocks: int = 50) -> dict[str, Any]:
    """Cross-reference DB candles against local NSE/BSE bhavcopy files.

    Steps:
    1. Load all local bhavcopy candles (LRU-cached after first call).
    2. Compute coverage: which DB stocks appear in bhavcopy by (exchange, symbol).
    3. Run _detect_summary for duplicate/invalid counts.
    4. For covered stocks (up to sample_stocks), compare the last 10 candles
       within the bhavcopy date window and report matched vs mismatched.
    """
    from app.services.exchange_bhavcopy_service import load_bhavcopy_candles
    import time

    t0 = time.perf_counter()

    # 1. Load bhavcopy index
    candles = load_bhavcopy_candles()
    load_ms = round((time.perf_counter() - t0) * 1000)

    # 2. Coverage stats from bhavcopy keys
    bhavcopy_keys = list(candles.keys())
    bhavcopy_dates = sorted({dt for _, _, dt in bhavcopy_keys})
    bhavcopy_syms_by_exch: dict[str, set[str]] = defaultdict(set)
    for exch, sym, _ in bhavcopy_keys:
        bhavcopy_syms_by_exch[exch].add(sym)

    date_start = bhavcopy_dates[0] if bhavcopy_dates else None
    date_end = bhavcopy_dates[-1] if bhavcopy_dates else None

    # 3. Match every active DB stock against bhavcopy index
    stocks = db.execute(
        text("SELECT id, symbol, exchange FROM stocks WHERE is_active = TRUE ORDER BY symbol")
    ).mappings().all()

    covered: list[dict[str, Any]] = []
    not_covered: list[str] = []
    for s in stocks:
        exch = str(s["exchange"] or "").upper()
        sym = _clean_sym(s["symbol"])
        if sym in bhavcopy_syms_by_exch.get(exch, set()):
            covered.append({"id": s["id"], "symbol": s["symbol"], "exchange": exch})
        else:
            not_covered.append(f"{s['symbol']} ({exch})")

    total_db = len(stocks)
    total_covered = len(covered)
    coverage_pct = round(total_covered / total_db * 100, 1) if total_db else 0.0

    # 4. DB-level issue summary (fast SQL counts)
    issues = _detect_summary(db)

    # 5. Sample cross-check: last 10 candles per covered stock within bhavcopy window
    cross_checked = matched = mismatched = no_entry = 0
    mismatch_examples: list[dict[str, Any]] = []

    if date_start and date_end:
        dt_start = datetime.combine(date_start, datetime.min.time(), tzinfo=UTC)
        dt_end = datetime.combine(date_end, datetime.min.time(), tzinfo=UTC) + timedelta(days=1)

        for stock in covered[:sample_stocks]:
            rows = db.execute(
                text("""
                    SELECT price_datetime, open, high, low, close, volume
                    FROM stock_prices
                    WHERE stock_id = :sid
                      AND timeframe = '1d'
                      AND price_datetime >= :dt_start
                      AND price_datetime <= :dt_end
                    ORDER BY price_datetime DESC
                    LIMIT 10
                """),
                {"sid": stock["id"], "dt_start": dt_start, "dt_end": dt_end},
            ).mappings().all()

            for row in rows:
                raw_dt = row["price_datetime"]
                trade_date = raw_dt.date() if hasattr(raw_dt, "date") else raw_dt
                bhav = candles.get((stock["exchange"], _clean_sym(stock["symbol"]), trade_date))
                cross_checked += 1
                if bhav is None:
                    no_entry += 1
                    continue
                db_row = {
                    "open": row["open"], "high": row["high"],
                    "low": row["low"], "close": row["close"],
                    "adjusted_close": row["close"], "volume": row["volume"],
                }
                diffs = _mismatched_fields(db_row, _bhavcopy_as_comparison_row(bhav))
                if diffs:
                    mismatched += 1
                    if len(mismatch_examples) < 10:
                        mismatch_examples.append({
                            "symbol": stock["symbol"],
                            "exchange": stock["exchange"],
                            "date": str(trade_date),
                            "fields": diffs,
                            "db_close": float(_decimal_or_none(row["close"]) or 0),
                            "bhav_close": float(bhav.close or 0),
                        })
                else:
                    matched += 1

    elapsed_ms = round((time.perf_counter() - t0) * 1000)

    return {
        # bhavcopy index stats
        "bhavcopy_total_candles": len(candles),
        "bhavcopy_date_start": str(date_start) if date_start else None,
        "bhavcopy_date_end": str(date_end) if date_end else None,
        "bhavcopy_trading_days": len(bhavcopy_dates),
        "bhavcopy_nse_symbols": len(bhavcopy_syms_by_exch.get("NSE", set())),
        "bhavcopy_bse_symbols": len(bhavcopy_syms_by_exch.get("BSE", set())),
        # DB vs bhavcopy coverage
        "db_total_stocks": total_db,
        "db_covered_stocks": total_covered,
        "coverage_pct": coverage_pct,
        "not_covered_sample": not_covered[:20],
        # DB-level issues
        "issues": issues,
        # Cross-check results
        "cross_check": {
            "stocks_sampled": min(sample_stocks, total_covered),
            "candles_checked": cross_checked,
            "matched": matched,
            "mismatched": mismatched,
            "no_bhavcopy_entry": no_entry,
            "match_rate_pct": round(matched / cross_checked * 100, 1) if cross_checked else None,
            "mismatch_examples": mismatch_examples,
        },
        "elapsed_ms": elapsed_ms,
        "load_ms": load_ms,
        "ran_at": datetime.now(UTC).isoformat(),
    }


def refresh_bhav_index_membership(db: Session) -> dict[str, Any]:
    """Set is_bhav_index=True for every active stock whose (exchange, symbol) exists in bhavcopy.

    Updates both the stocks table and stock_performance_snapshots.
    Returns summary stats.
    """
    t0 = datetime.now(UTC)
    candles = load_bhavcopy_candles()
    # Build a set of (exchange, symbol) keys present in bhavcopy
    bhav_pairs: set[tuple[str, str]] = {(k[0], k[1]) for k in candles}
    load_ms = round((datetime.now(UTC) - t0).total_seconds() * 1000)

    stocks = db.scalars(select(Stock).where(Stock.is_active.is_(True))).all()
    marked = 0
    unmarked = 0
    for stock in stocks:
        in_bhav = (_clean_sym(stock.exchange), _clean_sym(stock.symbol)) in bhav_pairs
        if stock.is_bhav_index != in_bhav:
            stock.is_bhav_index = in_bhav
        if in_bhav:
            marked += 1
        else:
            unmarked += 1

    # Also update stock_performance_snapshots
    snap_updated = db.execute(
        text(
            """
            UPDATE stock_performance_snapshots sps
            SET is_bhav_index = s.is_bhav_index
            FROM stocks s
            WHERE sps.stock_id = s.id
            """
        )
    ).rowcount

    db.commit()
    elapsed_ms = round((datetime.now(UTC) - t0).total_seconds() * 1000)

    return {
        "status": "ok",
        "bhav_index_stocks": marked,
        "not_covered": unmarked,
        "total_active": len(stocks),
        "snapshots_updated": snap_updated,
        "bhavcopy_pairs_loaded": len(bhav_pairs),
        "load_ms": load_ms,
        "elapsed_ms": elapsed_ms,
        "ran_at": t0.isoformat(),
    }
