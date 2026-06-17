from __future__ import annotations

import logging
import math
import time as time_module
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, NamedTuple

import pandas as pd
from fastapi import HTTPException
from sqlalchemy import desc, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings
from app.models.index_fund import IndexFund, IndexFundPrice
from app.services.market_data_service import DAILY_TIMEFRAME, ensure_daily_interval, fetch_stock_history, previous_business_day
from app.utils.observability import timed


logger = logging.getLogger(__name__)
ProgressCallback = Callable[[dict[str, Any]], None]


class IndexFundSyncResult(NamedTuple):
    rows_saved: int
    outcome: str


def _clean_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _safe_decimal(value: Any) -> Decimal | None:
    if value is None or pd.isna(value):
        return None
    try:
        number = Decimal(str(value))
    except Exception:
        return None
    if not number.is_finite():
        return None
    return number


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def infer_index_category(symbol: str, yahoo_symbol: str) -> str:
    text_value = f"{symbol} {yahoo_symbol}".upper()
    commodity_terms = {
        "GOLD",
        "SILVER",
        "CRUDE",
        "OIL",
        "GAS",
        "COPPER",
        "ZINC",
        "ALUMIN",
        "COMMOD",
    }
    return "commodity" if any(term in text_value for term in commodity_terms) else "index"


def _date_windows(start_date: date, end_date: date, chunk_days: int | None) -> list[tuple[date, date]]:
    if not chunk_days:
        return [(start_date, end_date)]
    windows: list[tuple[date, date]] = []
    current_start = start_date
    while current_start <= end_date:
        current_end = min(current_start + timedelta(days=chunk_days - 1), end_date)
        windows.append((current_start, current_end))
        current_start = current_end + timedelta(days=1)
    return windows


def _price_datetime_from_row(row: pd.Series) -> datetime | None:
    value = row.get("Date") or row.get("Datetime") or row.get("index")
    if value is None:
        return None
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        return None
    if timestamp.tzinfo is None:
        return datetime.combine(timestamp.date(), time.min, tzinfo=UTC)
    return timestamp.to_pydatetime().astimezone(UTC)


def _dataframe_to_index_price_payload(
    index_fund_id: int,
    dataframe: pd.DataFrame,
    timeframe: str,
) -> list[dict[str, Any]]:
    if dataframe.empty:
        return []
    if "Date" not in dataframe.columns and "Datetime" not in dataframe.columns:
        dataframe = dataframe.reset_index()
    payload: list[dict[str, Any]] = []
    for _, row in dataframe.iterrows():
        price_datetime = _price_datetime_from_row(row)
        close = row.get("Close")
        if price_datetime is None or pd.isna(close):
            continue
        payload.append(
            {
                "index_fund_id": index_fund_id,
                "price_datetime": price_datetime,
                "timeframe": timeframe,
                "open": _safe_decimal(row.get("Open")),
                "high": _safe_decimal(row.get("High")),
                "low": _safe_decimal(row.get("Low")),
                "close": _safe_decimal(close),
                "adjusted_close": _safe_decimal(row.get("Adj Close", close)),
                "volume": None if pd.isna(row.get("Volume")) else int(row.get("Volume") or 0),
                "source": "yfinance",
            }
        )
    return payload


def index_prices_to_dataframe(prices: list[IndexFundPrice]) -> pd.DataFrame:
    rows = [
        {
            "date": price.price_datetime,
            "open": float(price.open or 0),
            "high": float(price.high or 0),
            "low": float(price.low or 0),
            "close": float(price.close or 0),
            "volume": int(price.volume or 0),
        }
        for price in prices
    ]
    dataframe = pd.DataFrame(rows)
    if not dataframe.empty:
        dataframe = dataframe.set_index("date").sort_index()
    return dataframe


@timed("index_funds.upsert_index_fund")
def upsert_index_fund(
    db: Session,
    *,
    symbol: str,
    yahoo_symbol: str,
    base_currency: str = "INR",
    latest_price: Any = None,
    value_in_inr: Any = None,
    category: str | None = None,
    is_active: bool = True,
) -> IndexFund:
    symbol = _clean_text(symbol).upper()
    yahoo_symbol = _clean_text(yahoo_symbol)
    base_currency = (_clean_text(base_currency) or "INR").upper()
    if not symbol or not yahoo_symbol:
        raise ValueError("symbol and yahoo_symbol are required")

    stmt = insert(IndexFund).values(
        symbol=symbol,
        yahoo_symbol=yahoo_symbol,
        base_currency=base_currency,
        latest_price=_safe_decimal(latest_price),
        value_in_inr=_safe_decimal(value_in_inr),
        category=category or infer_index_category(symbol, yahoo_symbol),
        is_active=is_active,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_index_funds_yahoo_symbol",
        set_={
            "symbol": stmt.excluded.symbol,
            "base_currency": stmt.excluded.base_currency,
            "latest_price": stmt.excluded.latest_price,
            "value_in_inr": stmt.excluded.value_in_inr,
            "category": stmt.excluded.category,
            "is_active": stmt.excluded.is_active,
            "updated_at": func.now(),
        },
    ).returning(IndexFund.id)
    index_fund_id = db.scalar(stmt)
    db.flush()
    index_fund = db.get(IndexFund, index_fund_id)
    if index_fund is None:
        raise RuntimeError("Index fund upsert failed")
    return index_fund


@timed("index_funds.load_from_csv")
def load_index_funds_from_csv(db: Session, csv_path: str | Path) -> dict[str, int | list[str]]:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Index fund CSV not found: {path}")
    dataframe = pd.read_csv(path)
    required_columns = {"Symbol", "Yahoo_Ticker"}
    missing_columns = required_columns - set(dataframe.columns)
    if missing_columns:
        raise ValueError(f"CSV missing required columns: {sorted(missing_columns)}")

    inserted_or_updated = 0
    failed: list[str] = []
    for _, row in dataframe.iterrows():
        try:
            upsert_index_fund(
                db,
                symbol=row["Symbol"],
                yahoo_symbol=row["Yahoo_Ticker"],
                base_currency=row.get("Base_Currency") or "INR",
                latest_price=row.get("Latest_Price"),
                value_in_inr=row.get("Value_in_INR"),
            )
            inserted_or_updated += 1
        except Exception as exc:
            failed.append(f"{row.to_dict()}: {exc}")
            logger.warning("Skipping index fund CSV row: %s", exc)
    db.commit()
    return {"upserted": inserted_or_updated, "failed_count": len(failed), "failed": failed[:25]}


def build_index_search_tokens(query: str) -> list[str]:
    aliases = {
        "nifty50": "nifty 50",
        "banknifty": "nifty bank",
        "sensex": "sensex",
        "vix": "india vix",
        "smallcap": "small cap",
        "midcap": "mid cap",
    }
    normalized = query.strip().lower()
    normalized = aliases.get(normalized, normalized)
    raw_tokens = normalized.replace("^", " ").replace("-", " ").replace("_", " ").split()
    return [token for token in raw_tokens if token]


@timed("index_funds.search")
def search_index_funds(
    db: Session,
    query: str,
    *,
    category: str | None = None,
    limit: int = 50,
) -> list[IndexFund]:
    tokens = build_index_search_tokens(query)
    stmt = select(IndexFund).where(IndexFund.is_active.is_(True))
    if category:
        stmt = stmt.where(IndexFund.category == category.strip().lower())
    if tokens:
        for token in tokens:
            pattern = f"%{token}%"
            stmt = stmt.where(
                or_(
                    IndexFund.symbol.ilike(pattern),
                    IndexFund.yahoo_symbol.ilike(pattern),
                    IndexFund.base_currency.ilike(pattern),
                    IndexFund.category.ilike(pattern),
                )
            )
    rows = list(db.scalars(stmt.order_by(IndexFund.symbol.asc()).limit(limit)))
    if rows or not tokens:
        return rows
    broad_pattern = f"%{query.strip()}%"
    return list(
        db.scalars(
            select(IndexFund)
            .where(
                IndexFund.is_active.is_(True),
                or_(IndexFund.symbol.ilike(broad_pattern), IndexFund.yahoo_symbol.ilike(broad_pattern)),
            )
            .order_by(IndexFund.symbol.asc())
            .limit(limit)
        )
    )


@timed("index_funds.save_prices")
def save_index_fund_prices(db: Session, index_fund_id: int, dataframe: pd.DataFrame, timeframe: str) -> int:
    payload = _dataframe_to_index_price_payload(index_fund_id, dataframe, timeframe)
    if not payload:
        return 0
    stmt = insert(IndexFundPrice).values(payload)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_index_fund_prices_fund_dt_tf",
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
    return len(payload)


def get_last_index_price_date(db: Session, index_fund_id: int) -> date | None:
    latest_datetime = db.scalar(
        select(IndexFundPrice.price_datetime)
        .where(IndexFundPrice.index_fund_id == index_fund_id, IndexFundPrice.timeframe == DAILY_TIMEFRAME)
        .order_by(desc(IndexFundPrice.price_datetime))
        .limit(1)
    )
    return latest_datetime.date() if latest_datetime else None


def get_first_index_price_date(db: Session, index_fund_id: int) -> date | None:
    earliest_datetime = db.scalar(
        select(IndexFundPrice.price_datetime)
        .where(IndexFundPrice.index_fund_id == index_fund_id, IndexFundPrice.timeframe == DAILY_TIMEFRAME)
        .order_by(IndexFundPrice.price_datetime.asc())
        .limit(1)
    )
    return earliest_datetime.date() if earliest_datetime else None


def get_latest_index_price(db: Session, index_fund_id: int) -> Decimal | None:
    latest = db.scalar(
        select(IndexFundPrice)
        .where(IndexFundPrice.index_fund_id == index_fund_id, IndexFundPrice.timeframe == DAILY_TIMEFRAME)
        .order_by(desc(IndexFundPrice.price_datetime))
        .limit(1)
    )
    return Decimal(latest.close) if latest and latest.close is not None else None


def _history_covers_requested_range(db: Session, index_fund_id: int, start_date: date, end_date: date) -> bool:
    first_date = get_first_index_price_date(db, index_fund_id)
    last_date = get_last_index_price_date(db, index_fund_id)
    if not first_date or not last_date:
        return False
    return first_date <= start_date + timedelta(days=7) and last_date >= end_date - timedelta(days=7)


def _emit_progress(progress_callback: ProgressCallback | None, **payload: Any) -> None:
    if progress_callback:
        progress_callback(payload)


@timed("index_funds.sync_prices")
def sync_index_fund_prices(
    db: Session,
    index_fund_id: int,
    *,
    period: str | None = None,
    interval: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    chunk_days: int | None = 365,
    sleep_seconds: float = 0,
    incremental: bool = False,
    progress_callback: ProgressCallback | None = None,
    item_index: int | None = None,
    total_items: int | None = None,
    commit: bool = True,
) -> IndexFundSyncResult:
    index_fund = db.get(IndexFund, index_fund_id)
    if index_fund is None:
        raise LookupError("Index fund not found")
    timeframe = ensure_daily_interval(interval or settings.yfinance_default_interval)
    if chunk_days is not None and chunk_days < 1:
        raise ValueError("chunk_days must be at least 1 when provided")
    if sleep_seconds < 0:
        raise ValueError("sleep_seconds cannot be negative")

    resolved_end_date = end_date or previous_business_day()
    resolved_start_date = start_date
    if incremental:
        last_available_date = get_last_index_price_date(db, index_fund_id)
        if last_available_date and last_available_date >= resolved_end_date:
            _emit_progress(
                progress_callback,
                event="index_fund_skipped",
                symbol=index_fund.yahoo_symbol,
                item_index=item_index,
                total_items=total_items,
                reason="already_up_to_date",
                last_available_date=last_available_date,
                end_date=resolved_end_date,
            )
            return IndexFundSyncResult(0, "skipped_up_to_date")
        if last_available_date:
            resolved_start_date = max(resolved_start_date or last_available_date, last_available_date + timedelta(days=1))
    if resolved_start_date is None:
        if period:
            resolved_start_date = resolved_end_date - timedelta(days=365)
        else:
            resolved_start_date = date(2010, 1, 1)

    if resolved_start_date > resolved_end_date:
        return IndexFundSyncResult(0, "skipped_empty_range")
    if not incremental and _history_covers_requested_range(db, index_fund_id, resolved_start_date, resolved_end_date):
        _emit_progress(
            progress_callback,
            event="index_fund_skipped",
            symbol=index_fund.yahoo_symbol,
            item_index=item_index,
            total_items=total_items,
            reason="history_already_present",
            start_date=resolved_start_date,
            end_date=resolved_end_date,
        )
        return IndexFundSyncResult(0, "skipped_covered")

    rows_saved = 0
    windows = _date_windows(resolved_start_date, resolved_end_date, chunk_days)
    for chunk_index, (window_start, window_end) in enumerate(windows, start=1):
        _emit_progress(
            progress_callback,
            event="chunk_started",
            symbol=index_fund.yahoo_symbol,
            item_index=item_index,
            total_items=total_items,
            chunk_index=chunk_index,
            total_chunks=len(windows),
            start_date=window_start,
            end_date=window_end,
        )
        dataframe = fetch_stock_history(
            index_fund.yahoo_symbol,
            period=period or settings.yfinance_default_period,
            interval=timeframe,
            start_date=window_start,
            end_date=window_end,
        )
        chunk_rows = save_index_fund_prices(db, index_fund_id, dataframe, timeframe)
        rows_saved += chunk_rows
        _emit_progress(
            progress_callback,
            event="chunk_finished",
            symbol=index_fund.yahoo_symbol,
            item_index=item_index,
            total_items=total_items,
            chunk_index=chunk_index,
            total_chunks=len(windows),
            rows_saved=chunk_rows,
            rows_saved_total=rows_saved,
        )
        if sleep_seconds:
            time_module.sleep(sleep_seconds)
    if commit:
        db.commit()
    return IndexFundSyncResult(rows_saved, "synced")


@timed("index_funds.sync_all")
def sync_all_active_index_funds(
    db: Session,
    *,
    limit: int | None = None,
    offset: int = 0,
    category: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    chunk_days: int | None = 365,
    sleep_seconds: float = 0,
    incremental: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> dict[int, int]:
    stmt = select(IndexFund).where(IndexFund.is_active.is_(True)).order_by(IndexFund.symbol.asc()).offset(offset)
    if category:
        stmt = stmt.where(IndexFund.category == category.strip().lower())
    if limit:
        stmt = stmt.limit(limit)
    funds = list(db.scalars(stmt))
    result: dict[int, int] = {}
    for item_index, index_fund in enumerate(funds, start=1):
        try:
            sync_result = sync_index_fund_prices(
                db,
                index_fund.id,
                start_date=start_date,
                end_date=end_date,
                chunk_days=chunk_days,
                sleep_seconds=sleep_seconds,
                incremental=incremental,
                progress_callback=progress_callback,
                item_index=item_index,
                total_items=len(funds),
                commit=False,
            )
            result[index_fund.id] = sync_result.rows_saved
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Failed syncing index fund %s", index_fund.yahoo_symbol)
            result[index_fund.id] = 0
    return result


_INDEX_PERFORMANCE_SQL = """
    SELECT
        f.id,
        f.symbol,
        f.yahoo_symbol,
        f.base_currency,
        f.category,
        latest.price_datetime AS latest_price_datetime,
        latest.close AS latest_price,
        latest.volume AS latest_volume,
        p_1m.close AS price_1m,
        p_3m.close AS price_3m,
        p_6m.close AS price_6m,
        p_1y.close AS price_1y,
        CASE WHEN p_1m.close IS NULL OR p_1m.close = 0 OR latest.close IS NULL THEN NULL ELSE ((latest.close - p_1m.close) / p_1m.close) * 100 END AS change_1m_pct,
        CASE WHEN p_3m.close IS NULL OR p_3m.close = 0 OR latest.close IS NULL THEN NULL ELSE ((latest.close - p_3m.close) / p_3m.close) * 100 END AS change_3m_pct,
        CASE WHEN p_6m.close IS NULL OR p_6m.close = 0 OR latest.close IS NULL THEN NULL ELSE ((latest.close - p_6m.close) / p_6m.close) * 100 END AS change_6m_pct,
        CASE WHEN p_1y.close IS NULL OR p_1y.close = 0 OR latest.close IS NULL THEN NULL ELSE ((latest.close - p_1y.close) / p_1y.close) * 100 END AS change_1y_pct
    FROM index_funds f
    LEFT JOIN LATERAL (
        SELECT p.price_datetime, p.close, p.volume
        FROM index_fund_prices p
        WHERE p.index_fund_id = f.id AND p.timeframe = :timeframe AND p.close IS NOT NULL
        ORDER BY p.price_datetime DESC
        LIMIT 1
    ) latest ON TRUE
    LEFT JOIN LATERAL (
        SELECT p.close
        FROM index_fund_prices p
        WHERE p.index_fund_id = f.id AND p.timeframe = :timeframe AND p.close IS NOT NULL
          AND latest.price_datetime IS NOT NULL
          AND p.price_datetime <= latest.price_datetime - INTERVAL '1 month'
        ORDER BY p.price_datetime DESC
        LIMIT 1
    ) p_1m ON TRUE
    LEFT JOIN LATERAL (
        SELECT p.close
        FROM index_fund_prices p
        WHERE p.index_fund_id = f.id AND p.timeframe = :timeframe AND p.close IS NOT NULL
          AND latest.price_datetime IS NOT NULL
          AND p.price_datetime <= latest.price_datetime - INTERVAL '3 months'
        ORDER BY p.price_datetime DESC
        LIMIT 1
    ) p_3m ON TRUE
    LEFT JOIN LATERAL (
        SELECT p.close
        FROM index_fund_prices p
        WHERE p.index_fund_id = f.id AND p.timeframe = :timeframe AND p.close IS NOT NULL
          AND latest.price_datetime IS NOT NULL
          AND p.price_datetime <= latest.price_datetime - INTERVAL '6 months'
        ORDER BY p.price_datetime DESC
        LIMIT 1
    ) p_6m ON TRUE
    LEFT JOIN LATERAL (
        SELECT p.close
        FROM index_fund_prices p
        WHERE p.index_fund_id = f.id AND p.timeframe = :timeframe AND p.close IS NOT NULL
          AND latest.price_datetime IS NOT NULL
          AND p.price_datetime <= latest.price_datetime - INTERVAL '1 year'
        ORDER BY p.price_datetime DESC
        LIMIT 1
    ) p_1y ON TRUE
"""


def _performance_row_to_dict(row: Any) -> dict[str, Any]:
    mapping = row._mapping if hasattr(row, "_mapping") else row
    return {
        "id": mapping["id"],
        "symbol": mapping["symbol"],
        "yahoo_symbol": mapping["yahoo_symbol"],
        "base_currency": mapping["base_currency"],
        "category": mapping["category"],
        "latest_price_datetime": mapping["latest_price_datetime"],
        "latest_price": _float_or_none(mapping["latest_price"]),
        "latest_volume": mapping["latest_volume"],
        "price_1m": _float_or_none(mapping["price_1m"]),
        "price_3m": _float_or_none(mapping["price_3m"]),
        "price_6m": _float_or_none(mapping["price_6m"]),
        "price_1y": _float_or_none(mapping["price_1y"]),
        "change_1m_pct": _float_or_none(mapping["change_1m_pct"]),
        "change_3m_pct": _float_or_none(mapping["change_3m_pct"]),
        "change_6m_pct": _float_or_none(mapping["change_6m_pct"]),
        "change_1y_pct": _float_or_none(mapping["change_1y_pct"]),
    }


@timed("index_funds.performance")
def list_index_fund_performance(
    db: Session,
    *,
    query: str | None = None,
    category: str | None = None,
    limit: int = 5000,
    offset: int = 0,
    only_with_prices: bool = False,
) -> list[dict[str, Any]]:
    filters = ["f.is_active IS TRUE"]
    params: dict[str, Any] = {"timeframe": DAILY_TIMEFRAME, "limit": limit, "offset": offset}
    if category:
        filters.append("f.category = :category")
        params["category"] = category.strip().lower()
    if query:
        for index, token in enumerate(build_index_search_tokens(query)):
            param_name = f"query_{index}"
            filters.append(f"(f.symbol ILIKE :{param_name} OR f.yahoo_symbol ILIKE :{param_name})")
            params[param_name] = f"%{token}%"
    if only_with_prices:
        filters.append("latest.close IS NOT NULL")
    sql = text(
        f"""
        {_INDEX_PERFORMANCE_SQL}
        WHERE {" AND ".join(filters)}
        ORDER BY latest.price_datetime DESC NULLS LAST, f.symbol ASC
        LIMIT :limit OFFSET :offset
        """
    )
    return [_performance_row_to_dict(row) for row in db.execute(sql, params)]


@timed("index_funds.return_series")
def calculate_index_return_series(
    db: Session,
    *,
    index_fund_ids: list[int],
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    if not index_fund_ids:
        return []
    start_dt = datetime.combine(start_date, time.min, tzinfo=UTC)
    end_dt = datetime.combine(end_date, time.max, tzinfo=UTC)
    funds = list(db.scalars(select(IndexFund).where(IndexFund.id.in_(index_fund_ids))))
    funds_by_id = {fund.id: fund for fund in funds}
    response: list[dict[str, Any]] = []
    for fund_id in index_fund_ids:
        fund = funds_by_id.get(fund_id)
        if fund is None:
            continue
        prices = list(
            db.scalars(
                select(IndexFundPrice)
                .where(
                    IndexFundPrice.index_fund_id == fund_id,
                    IndexFundPrice.timeframe == DAILY_TIMEFRAME,
                    IndexFundPrice.price_datetime >= start_dt,
                    IndexFundPrice.price_datetime <= end_dt,
                    IndexFundPrice.close.is_not(None),
                )
                .order_by(IndexFundPrice.price_datetime.asc())
            )
        )
        dataframe = index_prices_to_dataframe(prices)
        if dataframe.empty:
            response.append(
                {
                    "id": fund.id,
                    "symbol": fund.symbol,
                    "yahoo_symbol": fund.yahoo_symbol,
                    "base_currency": fund.base_currency,
                    "points": [],
                }
            )
            continue
        close = dataframe["close"].astype(float)
        base = close.iloc[0]
        points = [
            {
                "date": pd.Timestamp(index).date().isoformat(),
                "close": float(value),
                "return_pct": round(((float(value) / base) - 1) * 100, 4) if base else 0,
            }
            for index, value in close.items()
        ]
        response.append(
            {
                "id": fund.id,
                "symbol": fund.symbol,
                "yahoo_symbol": fund.yahoo_symbol,
                "base_currency": fund.base_currency,
                "points": points,
            }
        )
    return response


def load_index_price_dataframe_for_range(
    db: Session,
    index_fund_id: int,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    start_dt = datetime.combine(start_date, time.min, tzinfo=UTC)
    end_dt = datetime.combine(end_date, time.max, tzinfo=UTC)
    prices = list(
        db.scalars(
            select(IndexFundPrice)
            .where(
                IndexFundPrice.index_fund_id == index_fund_id,
                IndexFundPrice.timeframe == DAILY_TIMEFRAME,
                IndexFundPrice.price_datetime >= start_dt,
                IndexFundPrice.price_datetime <= end_dt,
            )
            .order_by(IndexFundPrice.price_datetime.asc())
        )
    )
    if not prices:
        sync_index_fund_prices(
            db,
            index_fund_id,
            start_date=start_date,
            end_date=end_date,
            chunk_days=365,
            sleep_seconds=0,
            commit=False,
        )
        prices = list(
            db.scalars(
                select(IndexFundPrice)
                .where(
                    IndexFundPrice.index_fund_id == index_fund_id,
                    IndexFundPrice.timeframe == DAILY_TIMEFRAME,
                    IndexFundPrice.price_datetime >= start_dt,
                    IndexFundPrice.price_datetime <= end_dt,
                )
                .order_by(IndexFundPrice.price_datetime.asc())
            )
        )
    dataframe = index_prices_to_dataframe(prices)
    if dataframe.empty:
        raise HTTPException(status_code=400, detail="No historical index fund prices available")
    return dataframe
