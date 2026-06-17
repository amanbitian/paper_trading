from __future__ import annotations

import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import yfinance as yf
from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.fundamentals import StockFundamentalsLatest
from app.models.stock import MarketAnalyticsCache, Stock, StockPrice
from app.utils.json_safe import to_json_safe
from app.utils.observability import timed

logger = logging.getLogger(__name__)

TABLE_NAME = "stock_fundamentals_latest"
SOURCE = "yfinance"
LATEST_SYNC_CACHE_KEY = "fundamentals_sync_latest_v1"
DAILY_TIMEFRAME = "1d"
FUNDAMENTAL_METRICS: tuple[str, ...] = (
    "market_cap",
    "trailing_pe",
    "roe",
    "debt_to_equity",
    "sales_growth",
    "earnings_growth",
    "promoter_holding",
    "dividend_yield",
    "price_to_book",
    "average_volume",
)
YFINANCE_FIELD_MAP: dict[str, str] = {
    "market_cap": "marketCap",
    "trailing_pe": "trailingPE",
    "roe": "returnOnEquity",
    "debt_to_equity": "debtToEquity",
    "sales_growth": "revenueGrowth",
    "earnings_growth": "earningsGrowth",
    "promoter_holding": "heldPercentInsiders",
    "dividend_yield": "dividendYield",
    "price_to_book": "priceToBook",
    "average_volume": "averageVolume",
}
MISSING_TEXT_VALUES = {"", "n/a", "na", "none", "null", "-", "--", "nan"}


def fetch_yfinance_fundamentals(
    yahoo_ticker: str,
    *,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    ticker = (yahoo_ticker or "").strip()
    if not ticker:
        raise ValueError("Yahoo ticker is required for fundamentals sync.")

    def _load_info() -> dict[str, Any]:
        info = yf.Ticker(ticker).info
        return info if isinstance(info, dict) else {}

    if timeout_seconds <= 0:
        return _load_info()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_load_info)
        try:
            return future.result(timeout=timeout_seconds)
        except FuturesTimeoutError as exc:
            raise TimeoutError(
                f"yfinance fundamentals timed out after {timeout_seconds:.0f}s for {ticker}"
            ) from exc


def normalize_fundamental_value(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return Decimal(str(value))
    if hasattr(value, "item"):
        return normalize_fundamental_value(value.item())
    if isinstance(value, str):
        clean = value.strip().replace(",", "")
        if clean.lower() in MISSING_TEXT_VALUES:
            return None
        try:
            parsed = Decimal(clean)
        except InvalidOperation:
            return None
        return parsed if parsed.is_finite() else None
    return None


def _compact_number(value: Any, *, decimals: int = 2) -> str:
    number = _as_float(value)
    if number is None:
        return "-"
    sign = "-" if number < 0 else ""
    absolute = abs(number)
    if absolute >= 10_000_000:
        return f"{sign}{absolute / 10_000_000:,.{decimals}f} Cr"
    if absolute >= 100_000:
        return f"{sign}{absolute / 100_000:,.{decimals}f} L"
    return f"{number:,.0f}"


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _ratio_label(value: Any) -> str:
    number = _as_float(value)
    if number is None:
        return "-"
    return f"{number:,.2f}"


def _percent_label(value: Any) -> str:
    number = _as_float(value)
    if number is None:
        return "-"
    return f"{number * 100:,.2f}%"


def serialize_stock_fundamentals(row: StockFundamentalsLatest | None) -> dict[str, Any] | None:
    if row is None:
        return None
    payload = {
        "id": row.id,
        "stock_id": row.stock_id,
        "symbol": row.symbol,
        "exchange": row.exchange,
        "yahoo_ticker": row.yahoo_ticker,
        "market_cap": row.market_cap,
        "trailing_pe": row.trailing_pe,
        "roe": row.roe,
        "debt_to_equity": row.debt_to_equity,
        "sales_growth": row.sales_growth,
        "earnings_growth": row.earnings_growth,
        "promoter_holding": row.promoter_holding,
        "dividend_yield": row.dividend_yield,
        "price_to_book": row.price_to_book,
        "average_volume": row.average_volume,
        "currency": row.currency,
        "source": row.source,
        "status": row.status,
        "error_message": row.error_message,
        "fetched_at": row.fetched_at,
        "updated_at": row.updated_at,
    }
    safe = to_json_safe(payload)
    safe["display"] = {
        "market_cap": _compact_number(row.market_cap),
        "trailing_pe": _ratio_label(row.trailing_pe),
        "roe": _percent_label(row.roe),
        "debt_to_equity": _ratio_label(row.debt_to_equity),
        "sales_growth": _percent_label(row.sales_growth),
        "earnings_growth": _percent_label(row.earnings_growth),
        "promoter_holding": _percent_label(row.promoter_holding),
        "dividend_yield": _percent_label(row.dividend_yield),
        "price_to_book": _ratio_label(row.price_to_book),
        "average_volume": _compact_number(row.average_volume, decimals=2),
    }
    return safe


def get_stock_fundamentals(db: Session, stock_id: int) -> StockFundamentalsLatest | None:
    return db.scalar(
        select(StockFundamentalsLatest).where(StockFundamentalsLatest.stock_id == stock_id)
    )


def _stock_payload(
    stock: Stock,
    *,
    info: dict[str, Any] | None,
    status: str,
    source: str,
    error_message: str | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    metrics = {
        metric: normalize_fundamental_value((info or {}).get(yfinance_key))
        for metric, yfinance_key in YFINANCE_FIELD_MAP.items()
    }
    return {
        "stock_id": stock.id,
        "symbol": stock.symbol,
        "exchange": stock.exchange,
        "yahoo_ticker": stock.yahoo_symbol,
        **metrics,
        "currency": (info or {}).get("currency") or stock.currency,
        "source": source,
        "status": status,
        "error_message": error_message,
        "raw_json": to_json_safe(info or {}),
        "fetched_at": now,
        "updated_at": now,
    }


def sync_stock_fundamentals(
    db: Session,
    stock: Stock,
    source: str = SOURCE,
    *,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    ticker = (stock.yahoo_symbol or "").strip().upper()
    logger.info(
        "operation=fundamentals_sync_symbol symbol=%s exchange=%s ticker=%s status=started",
        stock.symbol,
        stock.exchange,
        ticker,
    )
    existing_id = db.scalar(
        select(StockFundamentalsLatest.id).where(StockFundamentalsLatest.stock_id == stock.id)
    )
    row_action = "updated" if existing_id else "inserted"

    try:
        info = fetch_yfinance_fundamentals(ticker, timeout_seconds=timeout_seconds)
        metric_values = {
            metric: normalize_fundamental_value(info.get(yfinance_key))
            for metric, yfinance_key in YFINANCE_FIELD_MAP.items()
        }
        metrics_present = sum(1 for value in metric_values.values() if value is not None)
        metrics_missing = len(FUNDAMENTAL_METRICS) - metrics_present
        status = "success" if metrics_present == len(FUNDAMENTAL_METRICS) else "partial"
        if metrics_present == 0:
            status = "failed"
        error_message = None if status != "failed" else "No yfinance fundamental metrics were returned."
        payload = _stock_payload(stock, info=info, status=status, source=source, error_message=error_message)
    except Exception as exc:
        metrics_present = 0
        metrics_missing = len(FUNDAMENTAL_METRICS)
        status = "failed"
        error_message = str(exc)
        payload = _stock_payload(stock, info=None, status=status, source=source, error_message=error_message)
        duration_ms = (time.perf_counter() - started_at) * 1000
        logger.info(
            "operation=fundamentals_sync_symbol symbol=%s exchange=%s ticker=%s "
            "status=failed error_type=%s error_message=%s duration_ms=%.2f",
            stock.symbol,
            stock.exchange,
            ticker,
            type(exc).__name__,
            error_message,
            duration_ms,
        )
        _upsert_fundamentals(db, payload)
        return {
            "symbol": stock.symbol,
            "exchange": stock.exchange,
            "ticker": ticker,
            "status": status,
            "row_action": row_action,
            "metrics_present": metrics_present,
            "metrics_missing": metrics_missing,
            "error_type": type(exc).__name__,
            "error_message": error_message,
            "duration_ms": duration_ms,
        }

    _upsert_fundamentals(db, payload)
    duration_ms = (time.perf_counter() - started_at) * 1000
    if status == "failed":
        logger.info(
            "operation=fundamentals_sync_symbol symbol=%s exchange=%s ticker=%s "
            "status=failed error_type=%s error_message=%s duration_ms=%.2f",
            stock.symbol,
            stock.exchange,
            ticker,
            "NoMetrics",
            error_message,
            duration_ms,
        )
    else:
        logger.info(
            "operation=fundamentals_sync_symbol symbol=%s exchange=%s ticker=%s "
            "status=%s metrics_present=%s metrics_missing=%s row_action=%s duration_ms=%.2f",
            stock.symbol,
            stock.exchange,
            ticker,
            status,
            metrics_present,
            metrics_missing,
            row_action,
            duration_ms,
        )
    return {
        "symbol": stock.symbol,
        "exchange": stock.exchange,
        "ticker": ticker,
        "status": status,
        "row_action": row_action,
        "metrics_present": metrics_present,
        "metrics_missing": metrics_missing,
        "error_type": "NoMetrics" if status == "failed" else None,
        "error_message": error_message,
        "duration_ms": duration_ms,
    }


def _upsert_fundamentals(db: Session, payload: dict[str, Any]) -> None:
    stmt = insert(StockFundamentalsLatest).values(payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=["stock_id"],
        set_={
            "symbol": stmt.excluded.symbol,
            "exchange": stmt.excluded.exchange,
            "yahoo_ticker": stmt.excluded.yahoo_ticker,
            "market_cap": stmt.excluded.market_cap,
            "trailing_pe": stmt.excluded.trailing_pe,
            "roe": stmt.excluded.roe,
            "debt_to_equity": stmt.excluded.debt_to_equity,
            "sales_growth": stmt.excluded.sales_growth,
            "earnings_growth": stmt.excluded.earnings_growth,
            "promoter_holding": stmt.excluded.promoter_holding,
            "dividend_yield": stmt.excluded.dividend_yield,
            "price_to_book": stmt.excluded.price_to_book,
            "average_volume": stmt.excluded.average_volume,
            "currency": stmt.excluded.currency,
            "source": stmt.excluded.source,
            "status": stmt.excluded.status,
            "error_message": stmt.excluded.error_message,
            "raw_json": stmt.excluded.raw_json,
            "fetched_at": stmt.excluded.fetched_at,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    db.execute(stmt)


def _eligible_stocks_query(active_only: bool, limit: int | None):
    has_daily_prices = exists().where(
        StockPrice.stock_id == Stock.id,
        StockPrice.timeframe == DAILY_TIMEFRAME,
    )
    stmt = (
        select(Stock)
        .where(
            Stock.yahoo_symbol.is_not(None),
            Stock.yahoo_symbol != "",
            Stock.exchange.in_(("NSE", "BSE")),
            has_daily_prices,
        )
        .order_by(Stock.exchange.asc(), Stock.symbol.asc())
    )
    if active_only:
        stmt = stmt.where(Stock.is_active.is_(True))
    if limit is not None:
        stmt = stmt.limit(limit)
    return stmt


def store_fundamentals_sync_result(db: Session, result: dict[str, Any]) -> dict[str, Any]:
    safe_payload = to_json_safe(result)
    stmt = insert(MarketAnalyticsCache).values(
        cache_key=LATEST_SYNC_CACHE_KEY,
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


def get_latest_fundamentals_sync_result(db: Session) -> dict[str, Any] | None:
    row = db.get(MarketAnalyticsCache, LATEST_SYNC_CACHE_KEY)
    if row is None:
        return None
    return row.payload


@timed("fundamentals.sync_all")
def sync_all_stock_fundamentals(
    db: Session,
    active_only: bool = True,
    limit: int | None = None,
    sleep_seconds: float = 0.15,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    started = datetime.now(UTC)
    started_perf = time.perf_counter()
    stocks = list(db.scalars(_eligible_stocks_query(active_only, limit)).all())
    selected = len(stocks)
    logger.info(
        "operation=fundamentals_sync status=started selected_stocks=%s source=%s "
        "active_only=%s limit=%s table=%s columns=%s sleep_seconds=%s timeout_seconds=%s",
        selected,
        SOURCE,
        active_only,
        limit,
        TABLE_NAME,
        len(FUNDAMENTAL_METRICS),
        sleep_seconds,
        timeout_seconds,
    )

    succeeded = 0
    failed = 0
    rows_inserted = 0
    rows_updated = 0
    failed_symbols: list[dict[str, Any]] = []
    sample_success_symbols: list[str] = []

    for index, stock in enumerate(stocks):
        if sleep_seconds > 0 and index > 0:
            time.sleep(sleep_seconds)
        try:
            result = sync_stock_fundamentals(db, stock, timeout_seconds=timeout_seconds)
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.exception(
                "operation=fundamentals_sync_symbol symbol=%s exchange=%s ticker=%s "
                "status=failed error_type=%s error_message=%s",
                stock.symbol,
                stock.exchange,
                stock.yahoo_symbol,
                type(exc).__name__,
                str(exc),
            )
            result = {
                "symbol": stock.symbol,
                "exchange": stock.exchange,
                "ticker": stock.yahoo_symbol,
                "status": "failed",
                "row_action": "none",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }

        if result.get("status") in {"success", "partial"}:
            succeeded += 1
            if len(sample_success_symbols) < 10:
                sample_success_symbols.append(str(result.get("ticker") or result.get("symbol")))
        else:
            failed += 1
            failed_symbols.append(
                {
                    "symbol": result.get("symbol"),
                    "exchange": result.get("exchange"),
                    "ticker": result.get("ticker"),
                    "error_type": result.get("error_type"),
                    "error_message": result.get("error_message"),
                }
            )

        if result.get("row_action") == "inserted":
            rows_inserted += 1
        elif result.get("row_action") == "updated":
            rows_updated += 1

    finished = datetime.now(UTC)
    duration_seconds = time.perf_counter() - started_perf
    rows_upserted = rows_inserted + rows_updated
    if selected == 0:
        status = "warning"
    elif failed == 0:
        status = "success"
    elif succeeded > 0:
        status = "partial"
    else:
        status = "failed"

    result_payload = {
        "status": status,
        "table_name": TABLE_NAME,
        "columns_ingested": len(FUNDAMENTAL_METRICS),
        "metrics": list(FUNDAMENTAL_METRICS),
        "selected_stocks": selected,
        "succeeded": succeeded,
        "failed": failed,
        "rows_inserted": rows_inserted,
        "rows_updated": rows_updated,
        "rows_upserted": rows_upserted,
        "started_at": started,
        "finished_at": finished,
        "duration_seconds": duration_seconds,
        "source": SOURCE,
        "failed_symbols": failed_symbols,
        "sample_success_symbols": sample_success_symbols,
        "active_only": active_only,
        "limit": limit,
    }
    logger.info(
        "operation=fundamentals_sync status=finished selected=%s succeeded=%s failed=%s "
        "rows_inserted=%s rows_updated=%s rows_upserted=%s columns=%s duration_ms=%.2f table=%s",
        selected,
        succeeded,
        failed,
        rows_inserted,
        rows_updated,
        rows_upserted,
        len(FUNDAMENTAL_METRICS),
        duration_seconds * 1000,
        TABLE_NAME,
    )
    return store_fundamentals_sync_result(db, result_payload)


def _all_metrics_null_clause():
    return (
        StockFundamentalsLatest.market_cap.is_(None),
        StockFundamentalsLatest.trailing_pe.is_(None),
        StockFundamentalsLatest.roe.is_(None),
        StockFundamentalsLatest.debt_to_equity.is_(None),
        StockFundamentalsLatest.sales_growth.is_(None),
        StockFundamentalsLatest.earnings_growth.is_(None),
        StockFundamentalsLatest.promoter_holding.is_(None),
        StockFundamentalsLatest.dividend_yield.is_(None),
        StockFundamentalsLatest.price_to_book.is_(None),
        StockFundamentalsLatest.average_volume.is_(None),
    )


def audit_fundamentals_table(db: Session) -> dict[str, Any]:
    """Read-only checks for stock_fundamentals_latest before full-universe sync."""
    total_rows = int(db.scalar(select(func.count()).select_from(StockFundamentalsLatest)) or 0)
    status_rows = db.execute(
        select(StockFundamentalsLatest.status, func.count())
        .group_by(StockFundamentalsLatest.status)
        .order_by(StockFundamentalsLatest.status.asc())
    ).all()
    status_counts = {str(status): int(count) for status, count in status_rows}
    all_null_rows = int(
        db.scalar(
            select(func.count())
            .select_from(StockFundamentalsLatest)
            .where(*_all_metrics_null_clause())
        )
        or 0
    )

    def _metric_count(column) -> int:
        return int(
            db.scalar(
                select(func.count())
                .select_from(StockFundamentalsLatest)
                .where(column.is_not(None))
            )
            or 0
        )

    return {
        "table_name": TABLE_NAME,
        "total_rows": total_rows,
        "status_counts": status_counts,
        "last_fetched_at": db.scalar(select(func.max(StockFundamentalsLatest.fetched_at))),
        "all_metrics_null_rows": all_null_rows,
        "market_cap_not_null": _metric_count(StockFundamentalsLatest.market_cap),
        "trailing_pe_not_null": _metric_count(StockFundamentalsLatest.trailing_pe),
        "roe_not_null": _metric_count(StockFundamentalsLatest.roe),
        "average_volume_not_null": _metric_count(StockFundamentalsLatest.average_volume),
    }


def get_fundamentals_status(db: Session) -> dict[str, Any]:
    total_rows = int(db.scalar(select(func.count()).select_from(StockFundamentalsLatest)) or 0)
    success_only_rows = int(
        db.scalar(
            select(func.count())
            .select_from(StockFundamentalsLatest)
            .where(StockFundamentalsLatest.status == "success")
        )
        or 0
    )
    partial_rows = int(
        db.scalar(
            select(func.count())
            .select_from(StockFundamentalsLatest)
            .where(StockFundamentalsLatest.status == "partial")
        )
        or 0
    )
    success_rows = success_only_rows + partial_rows
    failed_rows = int(
        db.scalar(
            select(func.count())
            .select_from(StockFundamentalsLatest)
            .where(StockFundamentalsLatest.status == "failed")
        )
        or 0
    )
    last_fetched_at = db.scalar(select(func.max(StockFundamentalsLatest.fetched_at)))
    audit = audit_fundamentals_table(db)
    return {
        "table_name": TABLE_NAME,
        "columns_ingested": len(FUNDAMENTAL_METRICS),
        "metrics": list(FUNDAMENTAL_METRICS),
        "total_rows": total_rows,
        "success_rows": success_rows,
        "success_only_rows": success_only_rows,
        "partial_rows": partial_rows,
        "failed_rows": failed_rows,
        "status_counts": audit.get("status_counts") or {},
        "all_metrics_null_rows": audit.get("all_metrics_null_rows") or 0,
        "last_fetched_at": last_fetched_at,
        "latest_result": get_latest_fundamentals_sync_result(db),
    }
