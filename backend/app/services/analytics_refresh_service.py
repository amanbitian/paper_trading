from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.constants.market_indices import STOCK_INDEX_FLAG_COLUMNS
from app.models.stock import MarketAnalyticsCache, Stock, StockPerformanceSnapshot
from app.services.algo_finding_service import generate_sequential_rankings
from app.services.market_movers_service import (
    DEFAULT_MOVER_LIMIT,
    MARKET_MOVERS_CACHE_KEY,
    compute_market_movers_from_db,
    sanitize_market_movers_payload,
)
from app.services.stock_performance_service import compute_stock_performance_rows
from app.services.signal_outcome_service import evaluate_pending_outcomes
from app.utils.observability import timed

SEQUENTIAL_RANKINGS_KEY = "sequential_rankings_v1"


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


@timed("analytics.refresh_stock_performance_snapshots")
def refresh_stock_performance_snapshots(db: Session) -> int:
    rows = compute_stock_performance_rows(db)
    if not rows:
        return 0

    now = datetime.now(UTC)
    payload = []
    for row in rows:
        item = {
            "stock_id": row["id"],
            "symbol": row["symbol"],
            "yahoo_symbol": row["yahoo_symbol"],
            "exchange": row["exchange"],
            "company_name": row["company_name"],
            "sector": row["sector"],
            "latest_price_datetime": row["latest_price_datetime"],
            "latest_price": row["latest_price"],
            "latest_volume": row["latest_volume"],
            "price_1m": row.get("price_1m"),
            "price_3m": row.get("price_3m"),
            "price_6m": row.get("price_6m"),
            "price_1y": row.get("price_1y"),
            "change_1m_pct": row.get("change_1m_pct"),
            "change_3m_pct": row.get("change_3m_pct"),
            "change_6m_pct": row.get("change_6m_pct"),
            "change_1y_pct": row.get("change_1y_pct"),
            "refreshed_at": now,
        }
        for flag_column in STOCK_INDEX_FLAG_COLUMNS.values():
            item[flag_column] = bool(row.get(flag_column))
        payload.append(item)

    stmt = insert(StockPerformanceSnapshot).values(payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=["stock_id"],
        set_={
            "symbol": stmt.excluded.symbol,
            "yahoo_symbol": stmt.excluded.yahoo_symbol,
            "exchange": stmt.excluded.exchange,
            "company_name": stmt.excluded.company_name,
            "sector": stmt.excluded.sector,
            "latest_price_datetime": stmt.excluded.latest_price_datetime,
            "latest_price": stmt.excluded.latest_price,
            "latest_volume": stmt.excluded.latest_volume,
            "price_1m": stmt.excluded.price_1m,
            "price_3m": stmt.excluded.price_3m,
            "price_6m": stmt.excluded.price_6m,
            "price_1y": stmt.excluded.price_1y,
            "change_1m_pct": stmt.excluded.change_1m_pct,
            "change_3m_pct": stmt.excluded.change_3m_pct,
            "change_6m_pct": stmt.excluded.change_6m_pct,
            "change_1y_pct": stmt.excluded.change_1y_pct,
            "refreshed_at": stmt.excluded.refreshed_at,
            **{
                flag_column: getattr(stmt.excluded, flag_column)
                for flag_column in STOCK_INDEX_FLAG_COLUMNS.values()
            },
        },
    )
    db.execute(stmt)
    db.commit()
    return len(rows)


@timed("analytics.refresh_sequential_rankings_cache")
def refresh_sequential_rankings_cache(
    db: Session,
    *,
    limit: int = 15,
    universe_limit: int | None = 2000,
) -> dict[str, Any]:
    payload = _json_safe(
        generate_sequential_rankings(
            db,
            limit=limit,
            universe_limit=universe_limit,
        )
    )
    stmt = insert(MarketAnalyticsCache).values(
        cache_key=SEQUENTIAL_RANKINGS_KEY,
        payload=payload,
        refreshed_at=datetime.now(UTC),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["cache_key"],
        set_={
            "payload": stmt.excluded.payload,
            "refreshed_at": stmt.excluded.refreshed_at,
        },
    )
    db.execute(stmt)
    db.commit()
    return payload


def get_cached_sequential_rankings(db: Session) -> dict[str, Any] | None:
    row = db.get(MarketAnalyticsCache, SEQUENTIAL_RANKINGS_KEY)
    if row is None:
        return None
    return row.payload


@timed("analytics.refresh_market_movers_cache")
def refresh_market_movers_cache(db: Session, *, limit: int = DEFAULT_MOVER_LIMIT) -> dict[str, Any]:
    payload = compute_market_movers_from_db(db, limit=limit)
    return store_market_movers_cache(db, payload)


def store_market_movers_cache(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    safe_payload = _json_safe(payload)
    stmt = insert(MarketAnalyticsCache).values(
        cache_key=MARKET_MOVERS_CACHE_KEY,
        payload=safe_payload,
        refreshed_at=datetime.now(UTC),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["cache_key"],
        set_={
            "payload": stmt.excluded.payload,
            "refreshed_at": stmt.excluded.refreshed_at,
        },
    )
    db.execute(stmt)
    db.commit()
    return safe_payload


def get_cached_market_movers(db: Session) -> dict[str, Any] | None:
    row = db.get(MarketAnalyticsCache, MARKET_MOVERS_CACHE_KEY)
    if row is None:
        return None
    return sanitize_market_movers_payload(row.payload)


@timed("analytics.refresh_all")
def refresh_all_analytics(db: Session) -> dict[str, int]:
    performance_rows = refresh_stock_performance_snapshots(db)
    outcomes_evaluated = evaluate_pending_outcomes(db)
    refresh_sequential_rankings_cache(db)
    movers_payload = refresh_market_movers_cache(db)
    stocks_with_prices = int(
        db.scalar(
            select(func.count()).select_from(StockPerformanceSnapshot).where(
                StockPerformanceSnapshot.latest_price.is_not(None)
            )
        )
        or 0
    )
    return {
        "performance_rows": performance_rows,
        "outcomes_evaluated": outcomes_evaluated,
        "stocks_with_prices": stocks_with_prices,
        "movers_universe_count": int(movers_payload.get("eligible_count") or 0),
    }
