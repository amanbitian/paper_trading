from __future__ import annotations

from typing import Any

from sqlalchemy import asc, desc, func, or_, select, text
from sqlalchemy.orm import Session

from app.constants.market_indices import (
    STOCK_INDEX_FILTER_OPTIONS,
    STOCK_INDEX_FLAG_COLUMNS,
    stock_index_flag_for_code,
)
from app.models.stock import Stock, StockPerformanceSnapshot
from app.services.market_data_service import DAILY_TIMEFRAME
from app.services.ticker_service import build_stock_search_tokens
from app.utils.observability import timed


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


INDEX_FLAG_COLUMNS = tuple(STOCK_INDEX_FLAG_COLUMNS.values())

# Maps the sort_by key accepted by list_stock_performance to the ORM column
# used in the snapshot ORDER BY clause.  Missing keys fall back to date order.
_SNAPSHOT_SORT_COLUMNS: dict[str, Any] = {
    "latest_price_datetime": StockPerformanceSnapshot.latest_price_datetime,
    "latest_price": StockPerformanceSnapshot.latest_price,
    "latest_volume": StockPerformanceSnapshot.latest_volume,
    "change_1m_pct": StockPerformanceSnapshot.change_1m_pct,
    "change_3m_pct": StockPerformanceSnapshot.change_3m_pct,
    "change_6m_pct": StockPerformanceSnapshot.change_6m_pct,
    "change_1y_pct": StockPerformanceSnapshot.change_1y_pct,
}

_PERFORMANCE_SQL = """
        SELECT
            s.id AS stock_id,
            s.symbol,
            s.yahoo_symbol,
            s.exchange,
            s.company_name,
            s.sector,
            s.industry,
            s.is_nifty50,
            s.is_nifty100,
            s.is_nifty200,
            s.is_nifty500,
            s.is_banknifty,
            s.is_finnifty,
            s.is_midcpnifty,
            s.is_sensex,
            latest.price_datetime AS latest_price_datetime,
            latest.close AS latest_price,
            latest.volume AS latest_volume,
            p_1m.close AS price_1m,
            p_3m.close AS price_3m,
            p_6m.close AS price_6m,
            p_1y.close AS price_1y,
            CASE
                WHEN p_1m.close IS NULL OR p_1m.close = 0 OR latest.close IS NULL THEN NULL
                ELSE ((latest.close - p_1m.close) / p_1m.close) * 100
            END AS change_1m_pct,
            CASE
                WHEN p_3m.close IS NULL OR p_3m.close = 0 OR latest.close IS NULL THEN NULL
                ELSE ((latest.close - p_3m.close) / p_3m.close) * 100
            END AS change_3m_pct,
            CASE
                WHEN p_6m.close IS NULL OR p_6m.close = 0 OR latest.close IS NULL THEN NULL
                ELSE ((latest.close - p_6m.close) / p_6m.close) * 100
            END AS change_6m_pct,
            CASE
                WHEN p_1y.close IS NULL OR p_1y.close = 0 OR latest.close IS NULL THEN NULL
                ELSE ((latest.close - p_1y.close) / p_1y.close) * 100
            END AS change_1y_pct
        FROM stocks s
        LEFT JOIN LATERAL (
            SELECT sp.price_datetime, sp.close, sp.volume
            FROM stock_prices sp
            WHERE sp.stock_id = s.id
              AND sp.timeframe = :timeframe
              AND sp.close IS NOT NULL
            ORDER BY sp.price_datetime DESC
            LIMIT 1
        ) latest ON TRUE
        LEFT JOIN LATERAL (
            SELECT sp.close
            FROM stock_prices sp
            WHERE sp.stock_id = s.id
              AND sp.timeframe = :timeframe
              AND sp.close IS NOT NULL
              AND latest.price_datetime IS NOT NULL
              AND sp.price_datetime <= latest.price_datetime - INTERVAL '1 month'
            ORDER BY sp.price_datetime DESC
            LIMIT 1
        ) p_1m ON TRUE
        LEFT JOIN LATERAL (
            SELECT sp.close
            FROM stock_prices sp
            WHERE sp.stock_id = s.id
              AND sp.timeframe = :timeframe
              AND sp.close IS NOT NULL
              AND latest.price_datetime IS NOT NULL
              AND sp.price_datetime <= latest.price_datetime - INTERVAL '3 months'
            ORDER BY sp.price_datetime DESC
            LIMIT 1
        ) p_3m ON TRUE
        LEFT JOIN LATERAL (
            SELECT sp.close
            FROM stock_prices sp
            WHERE sp.stock_id = s.id
              AND sp.timeframe = :timeframe
              AND sp.close IS NOT NULL
              AND latest.price_datetime IS NOT NULL
              AND sp.price_datetime <= latest.price_datetime - INTERVAL '6 months'
            ORDER BY sp.price_datetime DESC
            LIMIT 1
        ) p_6m ON TRUE
        LEFT JOIN LATERAL (
            SELECT sp.close
            FROM stock_prices sp
            WHERE sp.stock_id = s.id
              AND sp.timeframe = :timeframe
              AND sp.close IS NOT NULL
              AND latest.price_datetime IS NOT NULL
              AND sp.price_datetime <= latest.price_datetime - INTERVAL '1 year'
            ORDER BY sp.price_datetime DESC
            LIMIT 1
        ) p_1y ON TRUE
        """


def _row_to_dict(row: Any) -> dict[str, Any]:
    mapping = row._mapping if hasattr(row, "_mapping") else row
    payload = {
        "id": mapping["stock_id"],
        "symbol": mapping["symbol"],
        "yahoo_symbol": mapping["yahoo_symbol"],
        "exchange": mapping["exchange"],
        "company_name": mapping["company_name"],
        "sector": mapping["sector"],
        "industry": mapping.get("industry"),
        "latest_price_datetime": mapping["latest_price_datetime"],
        "latest_price": _float_or_none(mapping["latest_price"]),
        "latest_volume": mapping["latest_volume"],
        "price_1m": _float_or_none(mapping.get("price_1m")),
        "price_3m": _float_or_none(mapping.get("price_3m")),
        "price_6m": _float_or_none(mapping.get("price_6m")),
        "price_1y": _float_or_none(mapping.get("price_1y")),
        "change_1m_pct": _float_or_none(mapping["change_1m_pct"]),
        "change_3m_pct": _float_or_none(mapping["change_3m_pct"]),
        "change_6m_pct": _float_or_none(mapping["change_6m_pct"]),
        "change_1y_pct": _float_or_none(mapping["change_1y_pct"]),
    }
    for flag_column in INDEX_FLAG_COLUMNS:
        payload[flag_column] = bool(mapping.get(flag_column))
    return payload


def _snapshot_row_to_dict(snapshot: StockPerformanceSnapshot, *, industry: str | None = None) -> dict[str, Any]:
    payload = {
        "id": snapshot.stock_id,
        "symbol": snapshot.symbol,
        "yahoo_symbol": snapshot.yahoo_symbol,
        "exchange": snapshot.exchange,
        "company_name": snapshot.company_name,
        "sector": snapshot.sector,
        "industry": industry,
        "latest_price_datetime": snapshot.latest_price_datetime,
        "latest_price": _float_or_none(snapshot.latest_price),
        "latest_volume": snapshot.latest_volume,
        "price_1m": _float_or_none(snapshot.price_1m),
        "price_3m": _float_or_none(snapshot.price_3m),
        "price_6m": _float_or_none(snapshot.price_6m),
        "price_1y": _float_or_none(snapshot.price_1y),
        "change_1m_pct": _float_or_none(snapshot.change_1m_pct),
        "change_3m_pct": _float_or_none(snapshot.change_3m_pct),
        "change_6m_pct": _float_or_none(snapshot.change_6m_pct),
        "change_1y_pct": _float_or_none(snapshot.change_1y_pct),
    }
    for flag_column in INDEX_FLAG_COLUMNS:
        payload[flag_column] = bool(getattr(snapshot, flag_column, False))
    return payload


@timed("stocks.compute_stock_performance_rows")
def compute_stock_performance_rows(db: Session) -> list[dict[str, Any]]:
    sql = text(f"{_PERFORMANCE_SQL} WHERE s.is_active IS TRUE")
    result = db.execute(sql, {"timeframe": DAILY_TIMEFRAME})
    return [_row_to_dict(row) for row in result]


def _live_stock_performance(
    db: Session,
    *,
    query: str | None = None,
    exchange: str | None = None,
    industry: str | None = None,
    sector: str | None = None,
    index_code: str | None = None,
    limit: int = 5000,
    offset: int = 0,
) -> list[dict[str, Any]]:
    index_flag = stock_index_flag_for_code(index_code)
    if index_code and not index_flag:
        return []
    filters = ["s.is_active IS TRUE"]
    params: dict[str, Any] = {
        "timeframe": DAILY_TIMEFRAME,
        "limit": limit,
        "offset": offset,
    }
    if exchange:
        filters.append("s.exchange = :exchange")
        params["exchange"] = exchange.upper()
    if industry:
        filters.append("s.industry = :industry")
        params["industry"] = industry.strip()
    if sector:
        filters.append("s.sector = :sector")
        params["sector"] = sector.strip()
    if index_flag:
        filters.append(f"s.{index_flag} IS TRUE")
    if query:
        for index, token in enumerate(build_stock_search_tokens(query)):
            param_name = f"query_{index}"
            filters.append(
                f"(s.symbol ILIKE :{param_name} OR s.yahoo_symbol ILIKE :{param_name} "
                f"OR s.company_name ILIKE :{param_name} OR s.sector ILIKE :{param_name} "
                f"OR s.industry ILIKE :{param_name})"
            )
            params[param_name] = f"%{token}%"

    sql = text(
        f"""
        {_PERFORMANCE_SQL}
        WHERE {" AND ".join(filters)}
        ORDER BY latest.price_datetime DESC NULLS LAST, s.symbol ASC
        LIMIT :limit OFFSET :offset
        """
    )
    result = db.execute(sql, params)
    return [_row_to_dict(row) for row in result]


@timed("stocks.list_stock_industries")
def list_stock_industries(
    db: Session,
    *,
    exchange: str | None = None,
    sector: str | None = None,
    index_code: str | None = None,
    only_with_prices: bool = False,
) -> list[str]:
    index_flag = stock_index_flag_for_code(index_code)
    if index_code and not index_flag:
        return []
    stmt = (
        select(Stock.industry)
        .where(Stock.is_active.is_(True))
        .where(Stock.industry.is_not(None))
        .where(Stock.industry != "")
        .distinct()
    )
    if only_with_prices:
        stmt = stmt.join(
            StockPerformanceSnapshot,
            StockPerformanceSnapshot.stock_id == Stock.id,
        ).where(StockPerformanceSnapshot.latest_price.is_not(None))
    if exchange:
        stmt = stmt.where(Stock.exchange == exchange.upper())
    if sector:
        stmt = stmt.where(Stock.sector == sector.strip())
    if index_flag:
        stmt = stmt.where(getattr(Stock, index_flag).is_(True))
    stmt = stmt.order_by(Stock.industry.asc())
    return [value for value in db.scalars(stmt) if value]


@timed("stocks.list_stock_sectors")
def list_stock_sectors(
    db: Session,
    *,
    exchange: str | None = None,
    index_code: str | None = None,
    only_with_prices: bool = False,
) -> list[str]:
    index_flag = stock_index_flag_for_code(index_code)
    if index_code and not index_flag:
        return []
    stmt = (
        select(Stock.sector)
        .where(Stock.is_active.is_(True))
        .where(Stock.sector.is_not(None))
        .where(Stock.sector != "")
        .distinct()
    )
    if only_with_prices:
        stmt = stmt.join(
            StockPerformanceSnapshot,
            StockPerformanceSnapshot.stock_id == Stock.id,
        ).where(StockPerformanceSnapshot.latest_price.is_not(None))
    if exchange:
        stmt = stmt.where(Stock.exchange == exchange.upper())
    if index_flag:
        stmt = stmt.where(getattr(Stock, index_flag).is_(True))
    stmt = stmt.order_by(Stock.sector.asc())
    return [value for value in db.scalars(stmt) if value]


def list_stock_index_filters() -> list[dict[str, str]]:
    return STOCK_INDEX_FILTER_OPTIONS


def _post_process_performance_rows(
    rows: list[dict[str, Any]],
    *,
    min_change_1m_pct: float | None = None,
    max_change_1m_pct: float | None = None,
    min_change_3m_pct: float | None = None,
    max_change_3m_pct: float | None = None,
    min_change_6m_pct: float | None = None,
    max_change_6m_pct: float | None = None,
    min_change_1y_pct: float | None = None,
    max_change_1y_pct: float | None = None,
    sort_by: str | None = None,
    sort_desc: bool = True,
) -> list[dict[str, Any]]:
    bounds = [
        ("change_1m_pct", min_change_1m_pct, max_change_1m_pct),
        ("change_3m_pct", min_change_3m_pct, max_change_3m_pct),
        ("change_6m_pct", min_change_6m_pct, max_change_6m_pct),
        ("change_1y_pct", min_change_1y_pct, max_change_1y_pct),
    ]
    filtered: list[dict[str, Any]] = []
    for row in rows:
        keep = True
        for column, min_val, max_val in bounds:
            value = row.get(column)
            if value is None:
                if min_val is not None or max_val is not None:
                    keep = False
                continue
            if min_val is not None and float(value) < float(min_val):
                keep = False
                break
            if max_val is not None and float(value) > float(max_val):
                keep = False
                break
        if keep:
            filtered.append(row)

    if sort_by in {"change_1m_pct", "change_1y_pct", "latest_volume", "latest_price"}:
        filtered.sort(
            key=lambda item: (item.get(sort_by) is None, item.get(sort_by) or 0),
            reverse=sort_desc,
        )
    return filtered


@timed("stocks.get_stock_performance_by_ids")
def get_stock_performance_by_ids(db: Session, stock_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Return a {stock_id: row} dict for a small, known set of IDs.

    Uses the snapshot table exclusively.  Falls back to an empty dict for IDs
    that have no snapshot yet — callers should handle the miss gracefully.
    """
    if not stock_ids:
        return {}
    snapshots = db.scalars(
        select(StockPerformanceSnapshot).where(
            StockPerformanceSnapshot.stock_id.in_(stock_ids)
        )
    ).all()
    return {snap.stock_id: _snapshot_row_to_dict(snap) for snap in snapshots}


@timed("stocks.list_stock_performance")
def list_stock_performance(
    db: Session,
    *,
    query: str | None = None,
    exchange: str | None = None,
    industry: str | None = None,
    sector: str | None = None,
    index_code: str | None = None,
    limit: int = 5000,
    offset: int = 0,
    only_with_prices: bool = False,
    refresh: bool = False,
    min_change_1m_pct: float | None = None,
    max_change_1m_pct: float | None = None,
    min_change_3m_pct: float | None = None,
    max_change_3m_pct: float | None = None,
    min_change_6m_pct: float | None = None,
    max_change_6m_pct: float | None = None,
    min_change_1y_pct: float | None = None,
    max_change_1y_pct: float | None = None,
    sort_by: str | None = None,
    sort_desc: bool = True,
) -> list[dict[str, Any]]:
    index_flag = stock_index_flag_for_code(index_code)
    if index_code and not index_flag:
        return []
    if refresh:
        from app.services.analytics_refresh_service import refresh_stock_performance_snapshots

        refresh_stock_performance_snapshots(db)

    snapshot_count = db.scalar(select(func.count()).select_from(StockPerformanceSnapshot)) or 0
    if snapshot_count == 0:
        rows = _live_stock_performance(
            db,
            query=query,
            exchange=exchange,
            industry=industry,
            sector=sector,
            index_code=index_code,
            limit=limit,
            offset=offset,
        )
        return _post_process_performance_rows(
            rows,
            min_change_1m_pct=min_change_1m_pct,
            max_change_1m_pct=max_change_1m_pct,
            min_change_3m_pct=min_change_3m_pct,
            max_change_3m_pct=max_change_3m_pct,
            min_change_6m_pct=min_change_6m_pct,
            max_change_6m_pct=max_change_6m_pct,
            min_change_1y_pct=min_change_1y_pct,
            max_change_1y_pct=max_change_1y_pct,
            sort_by=sort_by,
            sort_desc=sort_desc,
        )

    stmt = (
        select(StockPerformanceSnapshot, Stock.industry, Stock.sector)
        .join(Stock, Stock.id == StockPerformanceSnapshot.stock_id)
        .where(Stock.is_active.is_(True))
    )
    if exchange:
        stmt = stmt.where(StockPerformanceSnapshot.exchange == exchange.upper())
    if industry:
        stmt = stmt.where(Stock.industry == industry.strip())
    if sector:
        stmt = stmt.where(Stock.sector == sector.strip())
    if index_flag:
        stmt = stmt.where(getattr(Stock, index_flag).is_(True))
    if query:
        for token in build_stock_search_tokens(query):
            pattern = f"%{token}%"
            stmt = stmt.where(
                or_(
                    StockPerformanceSnapshot.symbol.ilike(pattern),
                    StockPerformanceSnapshot.yahoo_symbol.ilike(pattern),
                    StockPerformanceSnapshot.company_name.ilike(pattern),
                    StockPerformanceSnapshot.sector.ilike(pattern),
                    Stock.industry.ilike(pattern),
                )
            )
    if only_with_prices:
        stmt = stmt.where(StockPerformanceSnapshot.latest_price.is_not(None))
    sort_col = _SNAPSHOT_SORT_COLUMNS.get(sort_by or "", StockPerformanceSnapshot.latest_price_datetime)
    primary_order = desc(sort_col).nulls_last() if sort_desc else asc(sort_col).nulls_last()
    stmt = (
        stmt.order_by(primary_order, StockPerformanceSnapshot.symbol.asc())
        .offset(offset)
        .limit(limit)
    )
    rows = db.execute(stmt).all()
    result = [
        {
            **_snapshot_row_to_dict(snapshot, industry=industry_value),
            "sector": sector_value or snapshot.sector,
        }
        for snapshot, industry_value, sector_value in rows
    ]
    return _post_process_performance_rows(
        result,
        min_change_1m_pct=min_change_1m_pct,
        max_change_1m_pct=max_change_1m_pct,
        min_change_3m_pct=min_change_3m_pct,
        max_change_3m_pct=max_change_3m_pct,
        min_change_6m_pct=min_change_6m_pct,
        max_change_6m_pct=max_change_6m_pct,
        min_change_1y_pct=min_change_1y_pct,
        max_change_1y_pct=max_change_1y_pct,
        sort_by=sort_by,
        sort_desc=sort_desc,
    )
