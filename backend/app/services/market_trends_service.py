from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.constants.market_indices import (
    NSE_CSV_INDEX_LABELS,
    NSE_CSV_TREND_FILTER_OPTIONS,
    STOCK_INDEX_FILTER_OPTIONS,
    build_nse_csv_symbol_exists_sql,
)
from app.services.market_data_service import DAILY_TIMEFRAME
from app.utils.observability import timed


TREND_PERIODS: dict[str, tuple[int, str]] = {
    "daily": (1, "Daily"),
    "weekly": (7, "Weekly"),
    "monthly": (30, "Monthly"),
    "quarterly": (90, "Quarterly"),
    "six_month": (180, "6 Month"),
    "annual": (365, "Annual"),
}
BASE_MARKET_FILTERS: dict[str, str] = {
    "all": "All instruments",
    "stocks": "All stocks",
    "nse": "NSE stocks",
    "bse": "BSE stocks",
    "index_funds": "Index funds",
    "commodities": "Commodities",
}
INDEX_MARKET_FILTERS: dict[str, str] = {
    option["value"].lower(): option["label"] for option in STOCK_INDEX_FILTER_OPTIONS
}
MARKET_FILTERS: dict[str, str] = {**BASE_MARKET_FILTERS, **INDEX_MARKET_FILTERS}
PERIOD_ALIASES = {
    "day": "daily",
    "1d": "daily",
    "week": "weekly",
    "1w": "weekly",
    "month": "monthly",
    "1m": "monthly",
    "quarter": "quarterly",
    "qtr": "quarterly",
    "quternally": "quarterly",
    "3m": "quarterly",
    "6m": "six_month",
    "six-month": "six_month",
    "year": "annual",
    "1y": "annual",
}
MARKET_ALIASES = {
    "stock": "stocks",
    "stocks": "stocks",
    "equity": "stocks",
    "equities": "stocks",
    "nse": "nse",
    "bse": "bse",
    "index": "index_funds",
    "indices": "index_funds",
    "index_fund": "index_funds",
    "index_funds": "index_funds",
    "commodity": "commodities",
    "commodities": "commodities",
    "all": "all",
}
for option in STOCK_INDEX_FILTER_OPTIONS:
    value = option["value"].lower()
    label_alias = option["label"].lower().replace(" ", "_").replace("-", "_")
    MARKET_ALIASES[value] = value
    MARKET_ALIASES[label_alias] = value

NSE_CSV_INDEX_FILTERS: dict[str, str] = {
    option["value"]: option["table_name"] for option in NSE_CSV_TREND_FILTER_OPTIONS
}
NSE_CSV_INDEX_ALIASES = {
    "nifty_50": "nifty50",
    "nifty_100": "nifty100",
    "nifty_200": "nifty200",
    "nifty_500": "nifty500",
    "bank_nifty": "banknifty",
    "fin_nifty": "finnifty",
    "midcap_nifty": "midcpnifty",
    "midcp_nifty": "midcpnifty",
}
for option in NSE_CSV_TREND_FILTER_OPTIONS:
    NSE_CSV_INDEX_ALIASES[option["value"]] = option["value"]
    NSE_CSV_INDEX_ALIASES[option["label"].lower()] = option["value"]

_STOCK_INDEX_MARKET_FILTER_SQL = "\n".join(
    f"          OR (:market_filter = '{option['value'].lower()}' AND s.{option['flag_column']} IS TRUE)"
    for option in STOCK_INDEX_FILTER_OPTIONS
)

_NSE_CSV_INDEX_FILTER_SQL = build_nse_csv_symbol_exists_sql()


_STOCK_INDUSTRY_GROUP_SQL = """
CASE
    WHEN UPPER(COALESCE(s.industry, '')) LIKE '%BANK%' THEN 'Banking'
    WHEN UPPER(COALESCE(s.sector, '')) = 'FINANCIAL SERVICES'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%FINANCE%'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%INSURANCE%'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%CAPITAL MARKETS%'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%ASSET MANAGEMENT%'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%CREDIT%' THEN 'Finance'
    WHEN UPPER(COALESCE(s.sector, '')) = 'TECHNOLOGY'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%SOFTWARE%'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%INFORMATION%'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%COMPUTER%'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%SEMICONDUCTOR%' THEN 'IT'
    WHEN UPPER(COALESCE(s.industry, '')) LIKE '%AUTO%'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%VEHICLE%'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%MOTOR%'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%PARTS%' THEN 'Automobile'
    WHEN UPPER(COALESCE(s.sector, '')) IN ('INDUSTRIALS', 'BASIC MATERIALS')
      OR UPPER(COALESCE(s.industry, '')) LIKE '%MANUFACTUR%'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%MACHIN%'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%ENGINEER%'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%METAL%'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%STEEL%'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%CEMENT%'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%CHEMICAL%' THEN 'Manufacturing'
    WHEN UPPER(COALESCE(s.sector, '')) = 'HEALTHCARE'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%PHARMA%'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%HEALTH%'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%BIOTECH%' THEN 'Healthcare'
    WHEN UPPER(COALESCE(s.sector, '')) = 'ENERGY'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%OIL%'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%GAS%'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%COAL%'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%POWER%' THEN 'Energy'
    WHEN UPPER(COALESCE(s.sector, '')) = 'CONSUMER CYCLICAL'
      OR UPPER(COALESCE(s.sector, '')) = 'CONSUMER DEFENSIVE'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%RETAIL%'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%FOOD%'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%TEXTILE%' THEN 'Consumer'
    WHEN UPPER(COALESCE(s.sector, '')) = 'COMMUNICATION SERVICES'
      OR UPPER(COALESCE(s.industry, '')) LIKE '%TELECOM%' THEN 'Telecom'
    WHEN UPPER(COALESCE(s.sector, '')) = 'UTILITIES' THEN 'Utilities'
    WHEN UPPER(COALESCE(s.sector, '')) = 'REAL ESTATE' THEN 'Real Estate'
    ELSE COALESCE(NULLIF(s.sector, ''), 'Unknown')
END
"""


def normalize_trend_period(period: str) -> str:
    normalized = (period or "daily").strip().lower().replace("-", "_").replace(" ", "_")
    normalized = PERIOD_ALIASES.get(normalized, normalized)
    if normalized not in TREND_PERIODS:
        allowed = ", ".join(TREND_PERIODS)
        raise HTTPException(status_code=400, detail=f"Unsupported trend period. Use one of: {allowed}")
    return normalized


def normalize_market_filter(market_filter: str | None) -> str:
    normalized = (market_filter or "stocks").strip().lower().replace("-", "_").replace(" ", "_")
    normalized = MARKET_ALIASES.get(normalized, normalized)
    if normalized not in MARKET_FILTERS:
        allowed = ", ".join(MARKET_FILTERS)
        raise HTTPException(status_code=400, detail=f"Unsupported market filter. Use one of: {allowed}")
    return normalized


def normalize_nifty_index_filter(nifty_index: str | None) -> str | None:
    if nifty_index is None:
        return None
    normalized = nifty_index.strip().lower().replace("-", "_").replace(" ", "_")
    if not normalized or normalized in {"all", "none", "all_indices", "all_nifty"}:
        return None
    normalized = NSE_CSV_INDEX_ALIASES.get(normalized, normalized)
    if normalized not in NSE_CSV_INDEX_FILTERS:
        allowed = ", ".join(NSE_CSV_INDEX_FILTERS)
        raise HTTPException(status_code=400, detail=f"Unsupported NIFTY index filter. Use one of: {allowed}")
    return normalized


TREND_SORT_OPTIONS: dict[str, str] = {
    "size": "Traded value (price x volume)",
    "price": "Market price",
    "volume": "Volume",
    "change": "Change %",
}
TREND_SORT_ORDER_SQL: dict[str, str] = {
    "size": "size_value DESC, symbol ASC",
    "price": "latest_price DESC NULLS LAST, symbol ASC",
    "volume": "latest_volume DESC NULLS LAST, symbol ASC",
    "change": "change_pct DESC NULLS LAST, symbol ASC",
}
DEFAULT_TREND_LIMIT = 1000
MAX_TREND_LIMIT = 5000


def normalize_trend_sort_by(sort_by: str | None) -> str:
    normalized = (sort_by or "size").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "traded_value": "size",
        "market_price": "price",
        "change_pct": "change",
        "trend": "change",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in TREND_SORT_ORDER_SQL:
        allowed = ", ".join(TREND_SORT_OPTIONS)
        raise HTTPException(status_code=400, detail=f"Unsupported sort_by. Use one of: {allowed}")
    return normalized


def _optional_filter(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return None if not cleaned or cleaned.lower() in {"all", "all industries"} else cleaned


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


_TREND_SQL = """
WITH stock_rows AS (
    SELECT
        'stock' AS instrument_type,
        s.id AS instrument_id,
        s.id AS stock_id,
        NULL::integer AS index_fund_id,
        s.symbol,
        s.yahoo_symbol,
        s.exchange,
        CASE
            WHEN UPPER(COALESCE(s.exchange, '')) = 'NSE' THEN 'NSE stocks'
            WHEN UPPER(COALESCE(s.exchange, '')) = 'BSE' THEN 'BSE stocks'
            ELSE 'Stocks'
        END AS market_bucket,
        COALESCE(NULLIF(s.company_name, ''), s.symbol) AS company_name,
        COALESCE(NULLIF(s.sector, ''), 'Unknown Sector') AS sector,
        COALESCE(NULLIF(s.industry, ''), 'Unknown Industry') AS industry,
        {stock_industry_group_sql} AS industry_group,
        latest.price_datetime AS latest_price_datetime,
        latest.close AS latest_price,
        latest.return_price AS latest_return_price,
        latest.volume AS latest_volume,
        baseline.price_datetime AS baseline_price_datetime,
        baseline.close AS baseline_price,
        baseline.return_price AS baseline_return_price,
        CASE
            WHEN baseline.return_price IS NULL OR baseline.return_price = 0 OR latest.return_price IS NULL THEN NULL
            ELSE ((latest.return_price - baseline.return_price) / baseline.return_price) * 100
        END AS change_pct,
        latest.return_price - baseline.return_price AS change_amount,
        GREATEST(
            COALESCE(ABS(latest.close * NULLIF(latest.volume, 0)), 0),
            COALESCE(ABS(latest.close), 0),
            1
        ) AS size_value
    FROM stocks s
    JOIN LATERAL (
        SELECT
            sp.price_datetime,
            sp.close,
            COALESCE(NULLIF(sp.adjusted_close, 0), sp.close) AS return_price,
            sp.volume
        FROM stock_prices sp
        WHERE sp.stock_id = s.id
          AND sp.timeframe = :timeframe
          AND sp.close IS NOT NULL
          AND COALESCE(NULLIF(sp.adjusted_close, 0), sp.close) IS NOT NULL
        ORDER BY sp.price_datetime DESC
        LIMIT 1
    ) latest ON TRUE
    JOIN LATERAL (
        SELECT
            sp.price_datetime,
            sp.close,
            COALESCE(NULLIF(sp.adjusted_close, 0), sp.close) AS return_price
        FROM stock_prices sp
        WHERE sp.stock_id = s.id
          AND sp.timeframe = :timeframe
          AND sp.close IS NOT NULL
          AND COALESCE(NULLIF(sp.adjusted_close, 0), sp.close) IS NOT NULL
          AND (
              (:lookback_days = 1 AND sp.price_datetime < latest.price_datetime)
              OR (
                  :lookback_days <> 1
                  AND sp.price_datetime <= latest.price_datetime - (:lookback_days * INTERVAL '1 day')
              )
          )
        ORDER BY sp.price_datetime DESC
        LIMIT 1
    ) baseline ON TRUE
    WHERE latest.close IS NOT NULL
      AND baseline.close IS NOT NULL
      AND (
          :market_filter IN ('all', 'stocks')
          OR (:market_filter = 'nse' AND UPPER(COALESCE(s.exchange, '')) = 'NSE')
          OR (:market_filter = 'bse' AND UPPER(COALESCE(s.exchange, '')) = 'BSE')
{stock_index_filter_sql}
      )
      AND (
          :nifty_index IS NULL
{nse_csv_index_filter_sql}
      )
),
index_rows AS (
    SELECT
        'index_fund' AS instrument_type,
        f.id AS instrument_id,
        NULL::integer AS stock_id,
        f.id AS index_fund_id,
        f.symbol,
        f.yahoo_symbol,
        UPPER(COALESCE(NULLIF(f.category, ''), 'index')) AS exchange,
        CASE
            WHEN LOWER(COALESCE(f.category, 'index')) = 'commodity' THEN 'Commodities'
            ELSE 'Index funds'
        END AS market_bucket,
        f.symbol AS company_name,
        CASE
            WHEN LOWER(COALESCE(f.category, 'index')) = 'commodity' THEN 'Commodity'
            ELSE 'Index'
        END AS sector,
        CASE
            WHEN LOWER(COALESCE(f.category, 'index')) = 'commodity' THEN 'Commodity Basket'
            ELSE 'Index Basket'
        END AS industry,
        CASE
            WHEN LOWER(COALESCE(f.category, 'index')) = 'commodity' THEN 'Commodity'
            ELSE 'Index'
        END AS industry_group,
        latest.price_datetime AS latest_price_datetime,
        latest.close AS latest_price,
        latest.return_price AS latest_return_price,
        latest.volume AS latest_volume,
        baseline.price_datetime AS baseline_price_datetime,
        baseline.close AS baseline_price,
        baseline.return_price AS baseline_return_price,
        CASE
            WHEN baseline.return_price IS NULL OR baseline.return_price = 0 OR latest.return_price IS NULL THEN NULL
            ELSE ((latest.return_price - baseline.return_price) / baseline.return_price) * 100
        END AS change_pct,
        latest.return_price - baseline.return_price AS change_amount,
        GREATEST(
            COALESCE(ABS(latest.close * NULLIF(latest.volume, 0)), 0),
            COALESCE(ABS(latest.close), 0),
            1
        ) AS size_value
    FROM index_funds f
    JOIN LATERAL (
        SELECT
            fp.price_datetime,
            fp.close,
            COALESCE(NULLIF(fp.adjusted_close, 0), fp.close) AS return_price,
            fp.volume
        FROM index_fund_prices fp
        WHERE fp.index_fund_id = f.id
          AND fp.timeframe = :timeframe
          AND fp.close IS NOT NULL
          AND COALESCE(NULLIF(fp.adjusted_close, 0), fp.close) IS NOT NULL
        ORDER BY fp.price_datetime DESC
        LIMIT 1
    ) latest ON TRUE
    JOIN LATERAL (
        SELECT
            fp.price_datetime,
            fp.close,
            COALESCE(NULLIF(fp.adjusted_close, 0), fp.close) AS return_price
        FROM index_fund_prices fp
        WHERE fp.index_fund_id = f.id
          AND fp.timeframe = :timeframe
          AND fp.close IS NOT NULL
          AND COALESCE(NULLIF(fp.adjusted_close, 0), fp.close) IS NOT NULL
          AND (
              (:lookback_days = 1 AND fp.price_datetime < latest.price_datetime)
              OR (
                  :lookback_days <> 1
                  AND fp.price_datetime <= latest.price_datetime - (:lookback_days * INTERVAL '1 day')
              )
          )
        ORDER BY fp.price_datetime DESC
        LIMIT 1
    ) baseline ON TRUE
    WHERE f.is_active IS TRUE
      AND latest.close IS NOT NULL
      AND baseline.close IS NOT NULL
      AND (
          :market_filter = 'all'
          OR (:market_filter = 'index_funds' AND LOWER(COALESCE(f.category, 'index')) <> 'commodity')
          OR (:market_filter = 'commodities' AND LOWER(COALESCE(f.category, 'index')) = 'commodity')
      )
),
trend_rows AS (
    SELECT * FROM stock_rows
    UNION ALL
    SELECT * FROM index_rows
)
SELECT *
FROM trend_rows
WHERE change_pct IS NOT NULL
  AND ABS(change_pct) <= :max_abs_change_pct
  AND (:industry_group IS NULL OR industry_group = :industry_group)
ORDER BY {order_by_clause}
LIMIT :limit
""".format(
    stock_industry_group_sql=_STOCK_INDUSTRY_GROUP_SQL,
    stock_index_filter_sql=_STOCK_INDEX_MARKET_FILTER_SQL,
    nse_csv_index_filter_sql=_NSE_CSV_INDEX_FILTER_SQL,
    order_by_clause="size_value DESC, symbol ASC",
)

_TREND_COUNT_SQL = """
WITH stock_rows AS (
    SELECT s.id AS instrument_id
    FROM stocks s
    JOIN LATERAL (
        SELECT sp.price_datetime, sp.close
        FROM stock_prices sp
        WHERE sp.stock_id = s.id
          AND sp.timeframe = :timeframe
          AND sp.close IS NOT NULL
          AND COALESCE(NULLIF(sp.adjusted_close, 0), sp.close) IS NOT NULL
        ORDER BY sp.price_datetime DESC
        LIMIT 1
    ) latest ON TRUE
    JOIN LATERAL (
        SELECT sp.close
        FROM stock_prices sp
        WHERE sp.stock_id = s.id
          AND sp.timeframe = :timeframe
          AND sp.close IS NOT NULL
          AND COALESCE(NULLIF(sp.adjusted_close, 0), sp.close) IS NOT NULL
          AND (
              (:lookback_days = 1 AND sp.price_datetime < latest.price_datetime)
              OR (
                  :lookback_days <> 1
                  AND sp.price_datetime <= latest.price_datetime - (:lookback_days * INTERVAL '1 day')
              )
          )
        ORDER BY sp.price_datetime DESC
        LIMIT 1
    ) baseline ON TRUE
    WHERE latest.close IS NOT NULL
      AND baseline.close IS NOT NULL
      AND (
          :market_filter IN ('all', 'stocks')
          OR (:market_filter = 'nse' AND UPPER(COALESCE(s.exchange, '')) = 'NSE')
          OR (:market_filter = 'bse' AND UPPER(COALESCE(s.exchange, '')) = 'BSE')
{stock_index_filter_sql}
      )
      AND (
          :nifty_index IS NULL
{nse_csv_index_filter_sql}
      )
),
index_rows AS (
    SELECT f.id AS instrument_id
    FROM index_funds f
    JOIN LATERAL (
        SELECT fp.price_datetime, fp.close
        FROM index_fund_prices fp
        WHERE fp.index_fund_id = f.id
          AND fp.timeframe = :timeframe
          AND fp.close IS NOT NULL
          AND COALESCE(NULLIF(fp.adjusted_close, 0), fp.close) IS NOT NULL
        ORDER BY fp.price_datetime DESC
        LIMIT 1
    ) latest ON TRUE
    JOIN LATERAL (
        SELECT fp.close
        FROM index_fund_prices fp
        WHERE fp.index_fund_id = f.id
          AND fp.timeframe = :timeframe
          AND fp.close IS NOT NULL
          AND COALESCE(NULLIF(fp.adjusted_close, 0), fp.close) IS NOT NULL
          AND (
              (:lookback_days = 1 AND fp.price_datetime < latest.price_datetime)
              OR (
                  :lookback_days <> 1
                  AND fp.price_datetime <= latest.price_datetime - (:lookback_days * INTERVAL '1 day')
              )
          )
        ORDER BY fp.price_datetime DESC
        LIMIT 1
    ) baseline ON TRUE
    WHERE f.is_active IS TRUE
      AND latest.close IS NOT NULL
      AND baseline.close IS NOT NULL
      AND (
          :market_filter = 'all'
          OR (:market_filter = 'index_funds' AND LOWER(COALESCE(f.category, 'index')) <> 'commodity')
          OR (:market_filter = 'commodities' AND LOWER(COALESCE(f.category, 'index')) = 'commodity')
      )
)
SELECT COUNT(*) AS eligible_count
FROM (
    SELECT instrument_id FROM stock_rows
    UNION ALL
    SELECT instrument_id FROM index_rows
) trend_rows
""".format(
    stock_index_filter_sql=_STOCK_INDEX_MARKET_FILTER_SQL,
    nse_csv_index_filter_sql=_NSE_CSV_INDEX_FILTER_SQL,
)

_ALL_STOCKS_ELIGIBLE_MAX_SQL = """
SELECT COUNT(DISTINCT sp.stock_id)
FROM stock_prices sp
INNER JOIN stocks s ON s.id = sp.stock_id
WHERE sp.timeframe = :timeframe
  AND sp.close IS NOT NULL
"""

_FILTERS_SQL = """
WITH stock_groups AS (
    SELECT DISTINCT {stock_industry_group_sql} AS industry_group
    FROM stocks s
    WHERE s.is_active IS TRUE
),
index_groups AS (
    SELECT DISTINCT
        CASE
            WHEN LOWER(COALESCE(f.category, 'index')) = 'commodity' THEN 'Commodity'
            ELSE 'Index'
        END AS industry_group
    FROM index_funds f
    WHERE f.is_active IS TRUE
)
SELECT DISTINCT industry_group
FROM (
    SELECT industry_group FROM stock_groups
    UNION ALL
    SELECT industry_group FROM index_groups
) grouped
WHERE industry_group IS NOT NULL
ORDER BY industry_group
""".format(stock_industry_group_sql=_STOCK_INDUSTRY_GROUP_SQL)


def _build_trend_sql(sort_by: str) -> str:
    order_by_clause = TREND_SORT_ORDER_SQL[sort_by]
    return _TREND_SQL.replace("size_value DESC, symbol ASC", order_by_clause)


def _trend_query_params(
    *,
    lookback_days: int,
    limit: int,
    market_filter: str,
    nifty_index: str | None,
    industry_group: str | None,
    max_abs_change_pct: float,
) -> dict[str, Any]:
    return {
        "timeframe": DAILY_TIMEFRAME,
        "lookback_days": lookback_days,
        "limit": limit,
        "market_filter": market_filter,
        "nifty_index": nifty_index,
        "industry_group": industry_group,
        "max_abs_change_pct": max_abs_change_pct,
    }


def _count_nse_csv_constituents(db: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    for option in NSE_CSV_TREND_FILTER_OPTIONS:
        table_name = option["table_name"]
        count = db.scalar(text(f"SELECT COUNT(*) FROM {table_name}"))
        counts[option["value"]] = int(count or 0)
    return counts


def _build_trend_count_sql() -> str:
    return _TREND_SQL.rsplit("ORDER BY", 1)[0].replace(
        "SELECT *\nFROM trend_rows",
        "SELECT COUNT(*) FROM trend_rows",
    )


def _count_trend_universe(
    db: Session,
    *,
    lookback_days: int,
    market_filter: str,
    nifty_index: str | None,
    industry_group: str | None,
    max_abs_change_pct: float,
) -> int:
    params = _trend_query_params(
        lookback_days=lookback_days,
        limit=MAX_TREND_LIMIT,
        market_filter=market_filter,
        nifty_index=nifty_index,
        industry_group=industry_group,
        max_abs_change_pct=max_abs_change_pct,
    )
    eligible = db.scalar(text(_build_trend_count_sql()), params)
    return int(eligible or 0)


def _trend_row_to_dict(row: Any) -> dict[str, Any]:
    mapping = row._mapping if hasattr(row, "_mapping") else row
    return {
        "instrument_type": mapping["instrument_type"],
        "instrument_id": mapping["instrument_id"],
        "stock_id": mapping["stock_id"],
        "index_fund_id": mapping["index_fund_id"],
        "symbol": mapping["symbol"],
        "yahoo_symbol": mapping["yahoo_symbol"],
        "exchange": mapping["exchange"],
        "market_bucket": mapping["market_bucket"],
        "company_name": mapping["company_name"],
        "sector": mapping["sector"],
        "industry": mapping["industry"],
        "industry_group": mapping["industry_group"],
        "latest_price_datetime": mapping["latest_price_datetime"],
        "latest_price": _float_or_none(mapping["latest_price"]),
        "latest_return_price": _float_or_none(mapping["latest_return_price"]),
        "latest_volume": mapping["latest_volume"],
        "baseline_price_datetime": mapping["baseline_price_datetime"],
        "baseline_price": _float_or_none(mapping["baseline_price"]),
        "baseline_return_price": _float_or_none(mapping["baseline_return_price"]),
        "change_pct": _float_or_none(mapping["change_pct"]),
        "change_amount": _float_or_none(mapping["change_amount"]),
        "size_value": _float_or_none(mapping["size_value"]) or 1,
        "calculation_basis": "adjusted_close",
    }


@timed("market.trends")
def get_market_trends(
    db: Session,
    *,
    period: str = "daily",
    limit: int = DEFAULT_TREND_LIMIT,
    market_filter: str = "stocks",
    nifty_index: str | None = None,
    industry_group: str | None = None,
    sort_by: str = "size",
    max_abs_change_pct: float = 300.0,
) -> dict[str, Any]:
    normalized_period = normalize_trend_period(period)
    normalized_market = normalize_market_filter(market_filter)
    normalized_nifty_index = normalize_nifty_index_filter(nifty_index)
    normalized_sort_by = normalize_trend_sort_by(sort_by)
    selected_industry_group = _optional_filter(industry_group)
    lookback_days, label = TREND_PERIODS[normalized_period]

    constituent_counts = _count_nse_csv_constituents(db)
    if normalized_nifty_index is not None:
        universe_cap = constituent_counts.get(normalized_nifty_index, MAX_TREND_LIMIT)
    else:
        all_stocks_cap = int(
            db.scalar(text(_ALL_STOCKS_ELIGIBLE_MAX_SQL), {"timeframe": DAILY_TIMEFRAME}) or MAX_TREND_LIMIT
        )
        universe_cap = min(MAX_TREND_LIMIT, all_stocks_cap)

    bounded_limit = min(max(limit, 1), universe_cap)
    universe_eligible_count = _count_trend_universe(
        db,
        lookback_days=lookback_days,
        market_filter=normalized_market,
        nifty_index=normalized_nifty_index,
        industry_group=selected_industry_group,
        max_abs_change_pct=max_abs_change_pct,
    )

    query_params = _trend_query_params(
        lookback_days=lookback_days,
        limit=bounded_limit,
        market_filter=normalized_market,
        nifty_index=normalized_nifty_index,
        industry_group=selected_industry_group,
        max_abs_change_pct=max_abs_change_pct,
    )
    rows = [
        _trend_row_to_dict(row)
        for row in db.execute(text(_build_trend_sql(normalized_sort_by)), query_params)
    ]
    record_dates = [row["latest_price_datetime"] for row in rows if row.get("latest_price_datetime")]
    baseline_dates = [row["baseline_price_datetime"] for row in rows if row.get("baseline_price_datetime")]
    market_label = MARKET_FILTERS[normalized_market]
    nifty_index_label = (
        NSE_CSV_INDEX_LABELS.get(normalized_nifty_index) if normalized_nifty_index else None
    )
    if nifty_index_label:
        market_label = nifty_index_label
    return {
        "as_of": datetime.utcnow(),
        "period": normalized_period,
        "period_label": label,
        "lookback_days": lookback_days,
        "market_filter": normalized_market,
        "market_label": market_label,
        "nifty_index": normalized_nifty_index,
        "nifty_index_label": nifty_index_label,
        "industry_group": selected_industry_group,
        "sort_by": normalized_sort_by,
        "limit_requested": bounded_limit,
        "universe_eligible_count": universe_eligible_count,
        "record_date": max(record_dates).date() if record_dates else None,
        "baseline_date": min(baseline_dates).date() if baseline_dates else None,
        "calculation_basis": "adjusted_close_when_available",
        "row_count": len(rows),
        "items": rows,
    }


@timed("market.trend_filters")
def get_market_trend_filters(db: Session) -> dict[str, Any]:
    industry_groups = [row._mapping["industry_group"] for row in db.execute(text(_FILTERS_SQL))]
    constituent_counts = _count_nse_csv_constituents(db)
    all_stocks_cap = int(
        db.scalar(text(_ALL_STOCKS_ELIGIBLE_MAX_SQL), {"timeframe": DAILY_TIMEFRAME}) or MAX_TREND_LIMIT
    )
    return {
        "markets": [{"label": label, "value": value} for value, label in BASE_MARKET_FILTERS.items()],
        "industry_groups": industry_groups,
        "nifty_indices": [
            {
                "label": option["label"],
                "value": option["value"],
                "constituent_count": constituent_counts.get(option["value"], 0),
            }
            for option in NSE_CSV_TREND_FILTER_OPTIONS
        ],
        "all_stocks_eligible_max": min(MAX_TREND_LIMIT, all_stocks_cap),
        "sort_options": [
            {"label": label, "value": value} for value, label in TREND_SORT_OPTIONS.items()
        ],
    }
