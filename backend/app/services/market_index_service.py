from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.constants.market_indices import (
    INDEX_DEFINITIONS,
    STOCK_INDEX_FLAG_COLUMNS,
    normalize_index_code,
)
from app.models.market_index import MarketIndex, StockIndexMembership
from app.models.stock import Stock, StockPerformanceSnapshot
from app.services.ticker_service import normalize_bse_symbol, normalize_nse_symbol, upsert_stock
from app.utils.observability import timed


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text.lower() != "nan" else None


def _decimal_or_none(value: Any) -> Decimal | None:
    text = _clean_text(value)
    if text is None:
        return None
    try:
        return Decimal(text.replace("%", "").replace(",", ""))
    except Exception:
        return None


@timed("market_index.upsert_index")
def upsert_market_index(
    db: Session,
    *,
    index_code: str,
    index_name: str | None = None,
    provider: str | None = None,
    exchange: str | None = None,
    yahoo_symbol: str | None = None,
    is_active: bool = True,
) -> MarketIndex:
    normalized_code = normalize_index_code(index_code)
    defaults = INDEX_DEFINITIONS.get(normalized_code, {})
    values = {
        "index_code": normalized_code,
        "index_name": index_name or defaults.get("index_name") or normalized_code,
        "provider": (provider or defaults.get("provider") or "NSE").upper(),
        "exchange": (exchange or defaults.get("exchange") or "NSE").upper(),
        "yahoo_symbol": yahoo_symbol if yahoo_symbol is not None else defaults.get("yahoo_symbol"),
        "is_active": is_active,
    }
    stmt = insert(MarketIndex).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_market_indices_index_code",
        set_={
            "index_name": stmt.excluded.index_name,
            "provider": stmt.excluded.provider,
            "exchange": stmt.excluded.exchange,
            "yahoo_symbol": stmt.excluded.yahoo_symbol,
            "is_active": stmt.excluded.is_active,
            "updated_at": func.now(),
        },
    ).returning(MarketIndex.id)
    index_id = db.scalar(stmt)
    db.flush()
    market_index = db.get(MarketIndex, index_id)
    if market_index is None:
        raise RuntimeError("Market index upsert failed")
    return market_index


def _resolve_stock_exchange(row_exchange: str | None, index_code: str, symbol: str) -> str:
    if row_exchange:
        return row_exchange.strip().upper()
    if normalize_index_code(index_code) == "SENSEX" and symbol.isdigit():
        return "BSE"
    return "NSE"


def _resolve_stock(db: Session, row: dict[str, Any]) -> Stock:
    symbol = str(row["symbol"]).strip().upper()
    exchange = _resolve_stock_exchange(_clean_text(row.get("stock_exchange")), row["index_code"], symbol)
    existing = db.scalar(
        select(Stock).where(
            Stock.symbol == symbol,
            Stock.exchange == exchange,
        )
    )
    if existing is not None:
        return existing

    yahoo_symbol = normalize_bse_symbol(symbol) if exchange == "BSE" else normalize_nse_symbol(symbol)
    return upsert_stock(
        db,
        symbol=symbol,
        yahoo_symbol=yahoo_symbol,
        exchange=exchange,
        company_name=_clean_text(row.get("company_name")),
        industry=_clean_text(row.get("industry")),
        is_active=True,
    )


@timed("market_index.upsert_membership")
def upsert_stock_index_membership(
    db: Session,
    *,
    market_index: MarketIndex,
    stock: Stock,
    row: dict[str, Any],
    effective_date: date | None = None,
    source: str = "manual",
) -> StockIndexMembership:
    values = {
        "index_id": market_index.id,
        "stock_id": stock.id,
        "symbol": stock.symbol,
        "exchange": stock.exchange,
        "company_name": _clean_text(row.get("company_name")) or stock.company_name,
        "industry": _clean_text(row.get("industry")) or stock.industry,
        "series": _clean_text(row.get("series")),
        "isin": _clean_text(row.get("isin")),
        "weight": _decimal_or_none(row.get("weight")),
        "effective_date": effective_date,
        "source": source,
        "is_active": True,
    }
    stmt = insert(StockIndexMembership).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_stock_index_memberships_index_stock",
        set_={
            "symbol": stmt.excluded.symbol,
            "exchange": stmt.excluded.exchange,
            "company_name": stmt.excluded.company_name,
            "industry": stmt.excluded.industry,
            "series": stmt.excluded.series,
            "isin": stmt.excluded.isin,
            "weight": stmt.excluded.weight,
            "effective_date": stmt.excluded.effective_date,
            "source": stmt.excluded.source,
            "is_active": True,
            "updated_at": func.now(),
        },
    ).returning(StockIndexMembership.id)
    membership_id = db.scalar(stmt)
    db.flush()
    membership = db.get(StockIndexMembership, membership_id)
    if membership is None:
        raise RuntimeError("Stock index membership upsert failed")
    return membership


@timed("market_index.refresh_stock_index_flags")
def refresh_stock_index_flags(db: Session, index_codes: set[str] | None = None) -> int:
    normalized_codes = {
        normalize_index_code(code) for code in (index_codes or set(STOCK_INDEX_FLAG_COLUMNS))
    }
    refreshed = 0
    for index_code in sorted(normalized_codes):
        flag_column = STOCK_INDEX_FLAG_COLUMNS.get(index_code)
        if not flag_column:
            continue
        stock_flag = getattr(Stock, flag_column, None)
        snapshot_flag = getattr(StockPerformanceSnapshot, flag_column, None)
        if stock_flag is None:
            continue

        active_member_stock_ids = (
            select(StockIndexMembership.stock_id)
            .join(MarketIndex, MarketIndex.id == StockIndexMembership.index_id)
            .where(
                MarketIndex.index_code == index_code,
                MarketIndex.is_active.is_(True),
                StockIndexMembership.is_active.is_(True),
            )
        )
        db.query(Stock).update({stock_flag: False}, synchronize_session=False)
        db.query(Stock).filter(Stock.id.in_(active_member_stock_ids)).update(
            {stock_flag: True},
            synchronize_session=False,
        )
        if snapshot_flag is not None:
            db.query(StockPerformanceSnapshot).update(
                {snapshot_flag: False},
                synchronize_session=False,
            )
            db.query(StockPerformanceSnapshot).filter(
                StockPerformanceSnapshot.stock_id.in_(active_member_stock_ids)
            ).update({snapshot_flag: True}, synchronize_session=False)
        refreshed += 1
    db.flush()
    return refreshed


@timed("market_index.load_memberships")
def load_index_membership_rows(
    db: Session,
    rows: list[dict[str, Any]],
    *,
    source: str,
    effective_date: date | None = None,
    deactivate_missing: bool = True,
) -> dict[str, int]:
    upserted = 0
    failed = 0
    seen_index_stock: set[tuple[int, int]] = set()
    seen_index_codes: set[str] = set()

    for row in rows:
        try:
            with db.begin_nested():
                index_code = normalize_index_code(str(row["index_code"]))
                market_index = upsert_market_index(
                    db,
                    index_code=index_code,
                    index_name=_clean_text(row.get("index_name")),
                    provider=_clean_text(row.get("provider")),
                    exchange=_clean_text(row.get("index_exchange")),
                    yahoo_symbol=_clean_text(row.get("index_yahoo_symbol")),
                )
                seen_index_codes.add(index_code)
                stock = _resolve_stock(db, row)
                upsert_stock_index_membership(
                    db,
                    market_index=market_index,
                    stock=stock,
                    row=row,
                    effective_date=effective_date,
                    source=source,
                )
                seen_index_stock.add((market_index.id, stock.id))
                upserted += 1
        except Exception:
            failed += 1

    if deactivate_missing and seen_index_stock:
        index_ids = sorted({index_id for index_id, _ in seen_index_stock})
        for index_id in index_ids:
            active_stock_ids = [stock_id for idx, stock_id in seen_index_stock if idx == index_id]
            db.query(StockIndexMembership).filter(
                StockIndexMembership.index_id == index_id,
                StockIndexMembership.stock_id.notin_(active_stock_ids),
                StockIndexMembership.is_active.is_(True),
            ).update({"is_active": False, "updated_at": func.now()}, synchronize_session=False)

    flags_refreshed = refresh_stock_index_flags(db, seen_index_codes)
    db.commit()
    return {"upserted": upserted, "failed": failed, "flags_refreshed": flags_refreshed}
