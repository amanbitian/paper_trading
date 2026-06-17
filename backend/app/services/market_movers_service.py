from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.constants.market_indices import NSE_CSV_INDEX_LABELS, build_nse_csv_symbol_exists_sql
from app.services.market_trends_service import normalize_nifty_index_filter
from app.utils.observability import timed

MARKET_MOVERS_CACHE_KEY = "market_movers_t1_v1"
DEFAULT_MOVER_LIMIT = 50
SPARKLINE_DAYS = 14

_NSE_CSV_INDEX_FILTER_SQL = build_nse_csv_symbol_exists_sql()

_T1_DAILY_CHANGE_SQL = f"""
WITH ranked AS (
    SELECT
        sp.stock_id,
        sp.close,
        sp.volume,
        sp.price_datetime,
        ROW_NUMBER() OVER (
            PARTITION BY sp.stock_id
            ORDER BY sp.price_datetime DESC
        ) AS rn
    FROM stock_prices sp
    INNER JOIN stocks s ON s.id = sp.stock_id
        AND (
            :nifty_index IS NOT NULL
            OR s.is_active IS TRUE
        )
    WHERE sp.timeframe = :timeframe
      AND sp.close IS NOT NULL
      AND sp.price_datetime < :cutoff
),
paired AS (
    SELECT
        r1.stock_id,
        r1.close AS latest_close,
        r1.volume AS latest_volume,
        r1.price_datetime AS record_datetime,
        r2.close AS previous_close
    FROM ranked r1
    INNER JOIN ranked r2 ON r1.stock_id = r2.stock_id AND r2.rn = 2
    WHERE r1.rn = 1
      AND r2.close IS NOT NULL
      AND r2.close <> 0
)
SELECT
    p.stock_id,
    s.symbol,
    s.yahoo_symbol,
    s.company_name,
    p.latest_close AS price,
    p.previous_close,
    p.latest_volume AS volume,
    p.record_datetime,
    ((p.latest_close - p.previous_close) / p.previous_close * 100) AS change_pct
FROM paired p
INNER JOIN stocks s ON s.id = p.stock_id
WHERE (
    :nifty_index IS NULL
{_NSE_CSV_INDEX_FILTER_SQL}
)
"""

_SPARKLINE_SQL = """
SELECT sp.stock_id, sp.close
FROM stock_prices sp
WHERE sp.stock_id = ANY(:stock_ids)
  AND sp.timeframe = :timeframe
  AND sp.close IS NOT NULL
  AND sp.price_datetime < :cutoff
ORDER BY sp.stock_id ASC, sp.price_datetime DESC
"""


def _today_cutoff() -> datetime:
    return datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)


def _float(value: Any) -> float:
    return float(value)


def _fetch_sparklines(
    db: Session,
    stock_ids: list[int],
    *,
    cutoff: datetime,
    days: int = SPARKLINE_DAYS,
) -> dict[int, list[float]]:
    if not stock_ids:
        return {}
    rows = db.execute(
        text(_SPARKLINE_SQL),
        {"stock_ids": stock_ids, "timeframe": "1d", "cutoff": cutoff},
    ).mappings()
    grouped: dict[int, list[float]] = {}
    for row in rows:
        stock_id = int(row["stock_id"])
        if stock_id not in grouped:
            grouped[stock_id] = []
        if len(grouped[stock_id]) < days:
            grouped[stock_id].append(_float(row["close"]))
    return {
        stock_id: list(reversed(closes))
        for stock_id, closes in grouped.items()
        if len(closes) >= 2
    }


def _row_to_quote(row: dict[str, Any], sparklines: dict[int, list[float]]) -> dict[str, Any]:
    price = _float(row["price"])
    previous = _float(row["previous_close"])
    change = price - previous
    change_pct = _float(row["change_pct"])
    record_datetime = row["record_datetime"]
    record_date = record_datetime.date() if isinstance(record_datetime, datetime) else record_datetime
    stock_id = int(row["stock_id"])
    symbol = str(row["symbol"])
    company_name = row.get("company_name")
    sparkline = sparklines.get(stock_id)
    if not sparkline:
        sparkline = [round(previous + (change * step / 13), 2) for step in range(14)]
    return {
        "label": str(company_name or symbol),
        "symbol": symbol,
        "yahoo_symbol": str(row["yahoo_symbol"]),
        "kind": "stock",
        "price": round(price, 2),
        "change": round(change, 2),
        "change_pct": round(change_pct, 2),
        "volume": int(row["volume"]) if row.get("volume") is not None else None,
        "record_date": record_date,
        "sparkline": [round(value, 2) for value in sparkline],
    }


@timed("market.compute_t1_mover_rows")
def compute_t1_mover_rows(db: Session, *, nifty_index: str | None = None) -> list[dict[str, Any]]:
    cutoff = _today_cutoff()
    normalized_nifty_index = normalize_nifty_index_filter(nifty_index)
    result = db.execute(
        text(_T1_DAILY_CHANGE_SQL),
        {"timeframe": "1d", "cutoff": cutoff, "nifty_index": normalized_nifty_index},
    )
    return [dict(row._mapping) for row in result]


@timed("market.compute_market_movers_from_db")
def compute_market_movers_from_db(
    db: Session,
    *,
    limit: int = DEFAULT_MOVER_LIMIT,
    nifty_index: str | None = None,
) -> dict[str, Any]:
    normalized_nifty_index = normalize_nifty_index_filter(nifty_index)
    rows = compute_t1_mover_rows(db, nifty_index=normalized_nifty_index)
    if not rows:
        return {
            "record_date": None,
            "eligible_count": 0,
            "nifty_index": normalized_nifty_index,
            "nifty_index_label": (
                NSE_CSV_INDEX_LABELS.get(normalized_nifty_index) if normalized_nifty_index else None
            ),
            "top_gainers": [],
            "top_losers": [],
            "volume_shockers": [],
            "most_bought": [],
        }

    sorted_by_gain = sorted(rows, key=lambda row: _float(row["change_pct"]), reverse=True)
    sorted_by_loss = sorted(rows, key=lambda row: _float(row["change_pct"]))
    sorted_by_volume = sorted(
        rows,
        key=lambda row: int(row["volume"] or 0),
        reverse=True,
    )

    top_gainers_rows = [row for row in sorted_by_gain if _float(row["change_pct"]) > 0][:limit]
    top_losers_rows = [row for row in sorted_by_loss if _float(row["change_pct"]) < 0][:limit]
    volume_rows = sorted_by_volume[:limit]
    most_bought_rows = sorted_by_volume[:4]

    selected_ids = {
        int(row["stock_id"])
        for row in (*top_gainers_rows, *top_losers_rows, *volume_rows, *most_bought_rows)
    }
    sparklines = _fetch_sparklines(db, list(selected_ids), cutoff=_today_cutoff())

    record_dates = [
        row["record_datetime"].date()
        for row in rows
        if isinstance(row.get("record_datetime"), datetime)
    ]
    record_date: date | None = max(record_dates) if record_dates else None

    return {
        "record_date": record_date,
        "eligible_count": len(rows),
        "nifty_index": normalized_nifty_index,
        "nifty_index_label": (
            NSE_CSV_INDEX_LABELS.get(normalized_nifty_index) if normalized_nifty_index else None
        ),
        "top_gainers": [_row_to_quote(row, sparklines) for row in top_gainers_rows],
        "top_losers": [_row_to_quote(row, sparklines) for row in top_losers_rows],
        "volume_shockers": [_row_to_quote(row, sparklines) for row in volume_rows],
        "most_bought": [_row_to_quote(row, sparklines) for row in most_bought_rows],
    }
