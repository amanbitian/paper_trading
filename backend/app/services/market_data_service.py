from __future__ import annotations

import logging
import os
import time as time_module
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, NamedTuple, TypedDict

import pandas as pd
import yfinance as yf
import yfinance.shared as yf_shared
from sqlalchemy import desc, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.stock import IngestionRun, Stock, StockPrice
from app.services.delisted_registry_service import is_yfinance_delisted_error, mark_stock_delisted
from app.utils.observability import timed

logger = logging.getLogger(__name__)


DAILY_TIMEFRAME = "1d"
MARKET_TIMEZONE = timezone(timedelta(hours=5, minutes=30), "Asia/Kolkata")
ProgressCallback = Callable[[dict[str, Any]], None]

SKIPPED_SYNC_OUTCOMES = frozenset(
    {
        "skipped_covered",
        "skipped_no_recent",
        "skipped_no_new_data",
        "skipped_delisted",
        "skipped_up_to_date",
        "skipped_empty_range",
        "marked_delisted",
    }
)


class StockSyncResult(NamedTuple):
    rows_saved: int
    outcome: str


class HistoryFetchResult(NamedTuple):
    dataframe: pd.DataFrame
    error_message: str | None


class SyncAllActiveStocksResult(TypedDict):
    status: str
    message: str
    symbol_results: dict[str, int]
    symbols_selected: int
    symbols_attempted: int
    symbols_synced: int
    symbols_skipped: int
    symbols_success: int
    symbols_failed: int
    rows_saved: int
    rows_fetched: int
    rows_inserted: int
    rows_updated: int
    latest_stored_date_before: date | None
    latest_stored_date_after: date | None
    provider_latest_date: date | None
    effective_end_date: date | None
    effective_start_date: date | None
    provider: str
    skip_breakdown: dict[str, int]
    sample_symbols: list[str]
    exchange_breakdown: dict[str, int]
    run_id: int | None


def default_ingestion_workers() -> int:
    cpu_count = os.cpu_count() or 4
    return max(4, min(32, cpu_count * 2))


def _symbol_frame(dataframe: pd.DataFrame, yahoo_symbol: str) -> pd.DataFrame:
    if dataframe.empty:
        return pd.DataFrame()
    if isinstance(dataframe.columns, pd.MultiIndex):
        first_level = set(dataframe.columns.get_level_values(0))
        if yahoo_symbol in first_level:
            return dataframe[yahoo_symbol].dropna(how="all")
        try:
            return dataframe.xs(yahoo_symbol, axis=1, level=1).dropna(how="all")
        except (KeyError, ValueError):
            return pd.DataFrame()
    if len(dataframe.columns) > 0 and yahoo_symbol not in str(dataframe.columns[0]):
        return dataframe.dropna(how="all")
    return dataframe.dropna(how="all")


def _history_frame_to_rows(dataframe: pd.DataFrame) -> pd.DataFrame:
    if dataframe.empty:
        return pd.DataFrame()
    normalized = _normalize_yfinance_columns(dataframe.copy())
    if "Close" not in normalized.columns:
        return pd.DataFrame()
    return normalized.reset_index()


def get_latest_prices_map(db: Session, stock_ids: list[int]) -> dict[int, Decimal]:
    if not stock_ids:
        return {}
    stmt = text(
        """
        SELECT DISTINCT ON (sp.stock_id)
            sp.stock_id,
            sp.close
        FROM stock_prices sp
        WHERE sp.stock_id = ANY(:stock_ids)
          AND sp.timeframe = :timeframe
          AND sp.close IS NOT NULL
        ORDER BY sp.stock_id, sp.price_datetime DESC
        """
    )
    rows = db.execute(
        stmt,
        {"stock_ids": stock_ids, "timeframe": DAILY_TIMEFRAME},
    ).mappings()
    return {int(row["stock_id"]): Decimal(str(row["close"])) for row in rows}


def ensure_daily_interval(interval: str | None) -> str:
    selected = (interval or DAILY_TIMEFRAME).strip().lower()
    if selected != DAILY_TIMEFRAME:
        raise ValueError("Only daily OHLCV ingestion is supported. Use interval='1d'.")
    return DAILY_TIMEFRAME


def previous_business_day(reference_date: date | None = None) -> date:
    candidate = reference_date or datetime.now(MARKET_TIMEZONE).date()
    candidate -= timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def get_latest_stored_daily_date(db: Session) -> date | None:
    latest = db.scalar(
        text(
            """
            SELECT MAX(price_datetime)::date
            FROM stock_prices
            WHERE timeframe = :timeframe
            """
        ),
        {"timeframe": DAILY_TIMEFRAME},
    )
    return latest


def probe_provider_latest_date(
    symbol: str = "RELIANCE.NS",
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[date | None, list[date], str | None]:
    resolved_end = end_date or previous_business_day()
    resolved_start = start_date or (resolved_end - timedelta(days=5))
    result = fetch_stock_history_result(
        symbol,
        start_date=resolved_start,
        end_date=resolved_end,
    )
    if result.dataframe.empty:
        return None, [], result.error_message
    date_col = "Datetime" if "Datetime" in result.dataframe.columns else "Date"
    dates = sorted(pd.to_datetime(result.dataframe[date_col]).dt.date.unique().tolist())
    return (dates[-1] if dates else None), dates, result.error_message


def _exchange_breakdown(stocks: list[Stock]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for stock in stocks:
        counts[stock.exchange or "UNKNOWN"] += 1
    return dict(counts)


def _finalize_sync_run_status(
    *,
    rows_saved_total: int,
    success_count: int,
    failed_count: int,
    symbols_selected: int,
    symbols_skipped: int,
    effective_end_date: date | None,
) -> tuple[str, str]:
    if failed_count > 0 and rows_saved_total == 0:
        return "failed", f"Market sync failed for all attempted symbols ({failed_count} failures)."
    if rows_saved_total > 0:
        if failed_count > 0:
            return "warning", (
                f"Market sync saved {rows_saved_total} rows with {failed_count} symbol failures."
            )
        return "success", f"Market sync saved {rows_saved_total} daily rows through {effective_end_date}."
    if symbols_selected == 0:
        return "warning", "No active stocks were selected for sync."
    if symbols_skipped >= symbols_selected:
        return (
            "warning",
            f"All {symbols_selected} active symbols were skipped (already up to date through {effective_end_date}).",
        )
    return "warning", "Market sync completed but saved 0 rows."


def _emit_progress(progress_callback: ProgressCallback | None, **payload: Any) -> None:
    if not progress_callback:
        return
    try:
        progress_callback(payload)
    except Exception:
        logger.exception("Price ingestion progress callback failed")


def _date_windows(
    start_date: date | None,
    end_date: date | None,
    chunk_days: int | None,
) -> list[tuple[date | None, date | None]]:
    if start_date is None:
        return [(None, None)]
    if end_date is None or not chunk_days:
        return [(start_date, end_date)]
    windows: list[tuple[date | None, date | None]] = []
    current_start = start_date
    while current_start <= end_date:
        current_end = min(current_start + timedelta(days=chunk_days - 1), end_date)
        windows.append((current_start, current_end))
        current_start = current_end + timedelta(days=1)
    return windows


def _period_to_start_date(period: str, end_date: date) -> date:
    normalized = (period or settings.yfinance_default_period).strip().lower()
    try:
        amount = int(normalized[:-1])
    except (TypeError, ValueError):
        return end_date - timedelta(days=365)
    unit = normalized[-1:]
    if unit == "d":
        return end_date - timedelta(days=amount)
    if unit == "w":
        return end_date - timedelta(weeks=amount)
    if unit == "m":
        return end_date - timedelta(days=amount * 31)
    if unit == "y":
        return end_date - timedelta(days=amount * 365 + amount // 4)
    return end_date - timedelta(days=365)


def _inclusive_yfinance_end(end_date: date | None) -> str | None:
    # yfinance treats end as exclusive; add one calendar day so CLI/API end_date is intuitive.
    return (end_date + timedelta(days=1)).isoformat() if end_date else None


def _yfinance_error_keys(yahoo_symbol: str) -> set[str]:
    return {yahoo_symbol, yahoo_symbol.upper(), yahoo_symbol.lower()}


def _clear_yfinance_errors(symbols: list[str]) -> None:
    for symbol in symbols:
        for key in _yfinance_error_keys(symbol):
            yf_shared._ERRORS.pop(key, None)


def _collect_yfinance_errors(symbols: list[str]) -> dict[str, str]:
    errors: dict[str, str] = {}
    for symbol in symbols:
        for key in _yfinance_error_keys(symbol):
            message = yf_shared._ERRORS.get(key)
            if message:
                errors[symbol] = str(message)
                break
    return errors


def _normalize_yfinance_columns(dataframe: pd.DataFrame) -> pd.DataFrame:
    if isinstance(dataframe.columns, pd.MultiIndex):
        dataframe = dataframe.copy()
        dataframe.columns = [column[0] for column in dataframe.columns]
    if dataframe.columns.has_duplicates:
        dataframe = dataframe.loc[:, ~dataframe.columns.duplicated()].copy()
    return dataframe


def _download_history_result(
    yahoo_symbol: str,
    period: str,
    interval: str,
    start_date: date | None,
    end_date: date | None,
) -> HistoryFetchResult:
    kwargs: dict[str, Any] = {
        "tickers": yahoo_symbol,
        "interval": interval,
        "auto_adjust": False,
        "repair": False,
        "threads": True,
        "progress": False,
        "timeout": 30,
        "multi_level_index": False,
    }
    if start_date:
        kwargs["start"] = start_date.isoformat()
        kwargs["end"] = _inclusive_yfinance_end(end_date)
    else:
        kwargs["period"] = period
    _clear_yfinance_errors([yahoo_symbol])
    try:
        dataframe = yf.download(**kwargs)
    except Exception as exc:
        errors = _collect_yfinance_errors([yahoo_symbol])
        return HistoryFetchResult(pd.DataFrame(), errors.get(yahoo_symbol) or str(exc))
    errors = _collect_yfinance_errors([yahoo_symbol])
    return HistoryFetchResult(
        dataframe if isinstance(dataframe, pd.DataFrame) else pd.DataFrame(),
        errors.get(yahoo_symbol),
    )


def _download_history(
    yahoo_symbol: str,
    period: str,
    interval: str,
    start_date: date | None,
    end_date: date | None,
) -> pd.DataFrame:
    return _download_history_result(
        yahoo_symbol=yahoo_symbol,
        period=period,
        interval=interval,
        start_date=start_date,
        end_date=end_date,
    ).dataframe


def _ticker_history_result(
    yahoo_symbol: str,
    period: str,
    interval: str,
    start_date: date | None,
    end_date: date | None,
) -> HistoryFetchResult:
    ticker = yf.Ticker(yahoo_symbol)
    _clear_yfinance_errors([yahoo_symbol])
    try:
        if start_date:
            dataframe = ticker.history(
                start=start_date.isoformat(),
                end=_inclusive_yfinance_end(end_date),
                interval=interval,
                auto_adjust=False,
                timeout=30,
            )
        else:
            dataframe = ticker.history(period=period, interval=interval, auto_adjust=False, timeout=30)
    except Exception as exc:
        errors = _collect_yfinance_errors([yahoo_symbol])
        return HistoryFetchResult(pd.DataFrame(), errors.get(yahoo_symbol) or str(exc))
    errors = _collect_yfinance_errors([yahoo_symbol])
    return HistoryFetchResult(
        dataframe if isinstance(dataframe, pd.DataFrame) else pd.DataFrame(),
        errors.get(yahoo_symbol),
    )


def _ticker_history(
    yahoo_symbol: str,
    period: str,
    interval: str,
    start_date: date | None,
    end_date: date | None,
) -> pd.DataFrame:
    return _ticker_history_result(
        yahoo_symbol=yahoo_symbol,
        period=period,
        interval=interval,
        start_date=start_date,
        end_date=end_date,
    ).dataframe


@timed("market_data.fetch_stock_history")
def fetch_stock_history(
    yahoo_symbol: str,
    period: str = "1y",
    interval: str = "1d",
    start_date: date | None = None,
    end_date: date | None = None,
) -> pd.DataFrame:
    return fetch_stock_history_result(
        yahoo_symbol,
        period=period,
        interval=interval,
        start_date=start_date,
        end_date=end_date,
    ).dataframe


def fetch_stock_history_result(
    yahoo_symbol: str,
    period: str = "1y",
    interval: str = "1d",
    start_date: date | None = None,
    end_date: date | None = None,
) -> HistoryFetchResult:
    interval = ensure_daily_interval(interval)
    download_result = _download_history_result(
        yahoo_symbol=yahoo_symbol,
        period=period,
        interval=interval,
        start_date=start_date,
        end_date=end_date,
    )
    dataframe = download_result.dataframe
    error_message = download_result.error_message

    if dataframe.empty:
        logger.warning("No yfinance download data returned for %s; retrying Ticker.history", yahoo_symbol)
        ticker_result = _ticker_history_result(
            yahoo_symbol=yahoo_symbol,
            period=period,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
        )
        dataframe = ticker_result.dataframe
        error_message = ticker_result.error_message or error_message
        if dataframe.empty:
            logger.warning("No yfinance data returned for %s", yahoo_symbol)
            return HistoryFetchResult(pd.DataFrame(), error_message)
    dataframe = _normalize_yfinance_columns(dataframe)
    missing_columns = {"Open", "High", "Low", "Close", "Volume"} - set(dataframe.columns)
    if missing_columns:
        logger.warning(
            "yfinance response for %s is missing required columns: %s",
            yahoo_symbol,
            sorted(missing_columns),
        )
        return HistoryFetchResult(pd.DataFrame(), error_message)
    return HistoryFetchResult(dataframe.reset_index(), error_message)


@timed("market_data.fetch_batch_stock_histories")
def fetch_batch_stock_histories(
    yahoo_symbols: list[str],
    *,
    period: str = "1y",
    interval: str = "1d",
    start_date: date | None = None,
    end_date: date | None = None,
    allow_individual_fallback: bool = False,
    errors: dict[str, str] | None = None,
) -> dict[str, pd.DataFrame]:
    interval = ensure_daily_interval(interval)
    symbols = [symbol.strip() for symbol in yahoo_symbols if symbol and symbol.strip()]
    if not symbols:
        return {}
    if len(symbols) == 1:
        result = fetch_stock_history_result(
                symbols[0],
                period=period,
                interval=interval,
                start_date=start_date,
                end_date=end_date,
            )
        if errors is not None and result.error_message:
            errors[symbols[0]] = result.error_message
        return {symbols[0]: result.dataframe}

    kwargs: dict[str, Any] = {
        "tickers": symbols,
        "interval": interval,
        "auto_adjust": False,
        "repair": False,
        "threads": True,
        "group_by": "ticker",
        "progress": False,
        "timeout": 60,
    }
    if start_date:
        kwargs["start"] = start_date.isoformat()
        kwargs["end"] = _inclusive_yfinance_end(end_date)
    else:
        kwargs["period"] = period

    _clear_yfinance_errors(symbols)
    try:
        downloaded = yf.download(**kwargs)
    except Exception:
        logger.exception("Batch yfinance download failed for %s symbols", len(symbols))
        downloaded = pd.DataFrame()
    batch_errors = _collect_yfinance_errors(symbols)
    if errors is not None:
        errors.update(batch_errors)

    results: dict[str, pd.DataFrame] = {}
    if isinstance(downloaded, pd.DataFrame) and not downloaded.empty:
        for symbol in symbols:
            frame = _symbol_frame(downloaded, symbol)
            results[symbol] = _history_frame_to_rows(frame)

    for symbol in symbols:
        if results.get(symbol) is not None and not results[symbol].empty:
            continue
        if allow_individual_fallback:
            fallback_result = fetch_stock_history_result(
                symbol,
                period=period,
                interval=interval,
                start_date=start_date,
                end_date=end_date,
            )
            if errors is not None and fallback_result.error_message:
                errors[symbol] = fallback_result.error_message
            results[symbol] = fallback_result.dataframe
        else:
            results[symbol] = pd.DataFrame()
    return results


def _bulk_price_coverage(
    db: Session,
    stock_ids: list[int],
    *,
    start_date: date | None,
    end_date: date | None,
) -> dict[int, tuple[date | None, date | None]]:
    if not stock_ids:
        return {}
    rows = db.execute(
        text(
            """
            SELECT
                s.id AS stock_id,
                MIN(sp.price_datetime)::date AS first_date,
                MAX(sp.price_datetime)::date AS last_date
            FROM stocks s
            LEFT JOIN stock_prices sp
                ON sp.stock_id = s.id
               AND sp.timeframe = :timeframe
            WHERE s.id = ANY(:stock_ids)
            GROUP BY s.id
            """
        ),
        {"stock_ids": stock_ids, "timeframe": DAILY_TIMEFRAME},
    ).mappings()
    return {
        int(row["stock_id"]): (row["first_date"], row["last_date"])
        for row in rows
    }


def _is_history_covered(
    first_date: date | None,
    last_date: date | None,
    start_date: date,
    end_date: date,
) -> bool:
    if not first_date or not last_date:
        return False
    return first_date <= start_date + timedelta(days=7) and last_date >= end_date - timedelta(days=7)


def _first_scalar(value: Any) -> Any:
    if isinstance(value, pd.Series):
        non_null = value.dropna()
        return None if non_null.empty else non_null.iloc[0]
    return value


def _safe_decimal(value: Any) -> Decimal | None:
    value = _first_scalar(value)
    if pd.isna(value):
        return None
    return Decimal(str(round(float(value), 4)))


def _row_datetime(row: pd.Series, timeframe: str) -> datetime:
    ensure_daily_interval(timeframe)
    raw_value = row.get("Datetime", row.get("Date"))
    timestamp = pd.to_datetime(raw_value)
    return datetime.combine(timestamp.date(), time.min, tzinfo=UTC)


def _dataframe_to_price_payload(stock_id: int, dataframe: pd.DataFrame, timeframe: str) -> list[dict[str, Any]]:
    timeframe = ensure_daily_interval(timeframe)
    if dataframe.empty:
        return []

    working = dataframe.copy()
    date_col = "Datetime" if "Datetime" in working.columns else "Date"
    if date_col not in working.columns:
        return []

    working = working.dropna(subset=["Close"])
    if working.empty:
        return []

    timestamps = pd.to_datetime(working[date_col])
    payload: list[dict[str, Any]] = []
    for idx, row in working.iterrows():
        close = _first_scalar(row.get("Close"))
        if pd.isna(close):
            continue
        ts = timestamps.loc[idx]
        volume = _first_scalar(row.get("Volume"))
        payload.append(
            {
                "stock_id": stock_id,
                "price_datetime": datetime.combine(ts.date(), time.min, tzinfo=UTC),
                "timeframe": timeframe,
                "open": _safe_decimal(row.get("Open")),
                "high": _safe_decimal(row.get("High")),
                "low": _safe_decimal(row.get("Low")),
                "close": _safe_decimal(close),
                "adjusted_close": _safe_decimal(row.get("Adj Close", close)),
                "volume": None if pd.isna(volume) else int(volume),
                "source": "yfinance",
            }
        )
    return payload


def _requested_date_window(dataframe: pd.DataFrame, start_date: date | None, end_date: date | None) -> pd.DataFrame:
    """Keep only candles whose Yahoo date belongs to the requested inclusive window.

    Yahoo can return the latest available candle when the requested end-of-day
    candle has not been published yet. Without this guard, an incremental sync
    for 2026-05-28 can report a saved row while only re-upserting 2026-05-27.
    """
    if dataframe.empty or (start_date is None and end_date is None):
        return dataframe
    date_col = "Datetime" if "Datetime" in dataframe.columns else "Date"
    if date_col not in dataframe.columns:
        return dataframe

    working = dataframe.copy()
    dates = pd.to_datetime(working[date_col], errors="coerce").dt.date
    mask = dates.notna()
    if start_date is not None:
        mask &= dates >= start_date
    if end_date is not None:
        mask &= dates <= end_date
    return working.loc[mask].copy()


@timed("market_data.save_stock_prices")
def save_stock_prices(db: Session, stock_id: int, dataframe: pd.DataFrame, timeframe: str) -> int:
    payload = _dataframe_to_price_payload(stock_id, dataframe, timeframe)
    if not payload:
        return 0

    stmt = insert(StockPrice).values(payload)
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
    return len(payload)


@timed("market_data.get_latest_price")
def get_latest_price(db: Session, stock_id: int) -> Decimal | None:
    stmt = (
        select(StockPrice)
        .where(StockPrice.stock_id == stock_id, StockPrice.timeframe == DAILY_TIMEFRAME)
        .order_by(desc(StockPrice.price_datetime))
        .limit(1)
    )
    latest = db.scalar(stmt)
    return Decimal(latest.close) if latest and latest.close is not None else None


def get_last_price_date(db: Session, stock_id: int) -> date | None:
    stmt = (
        select(StockPrice.price_datetime)
        .where(StockPrice.stock_id == stock_id, StockPrice.timeframe == DAILY_TIMEFRAME)
        .order_by(desc(StockPrice.price_datetime))
        .limit(1)
    )
    latest_datetime = db.scalar(stmt)
    return latest_datetime.date() if latest_datetime else None


def get_first_price_date(db: Session, stock_id: int) -> date | None:
    stmt = (
        select(StockPrice.price_datetime)
        .where(StockPrice.stock_id == stock_id, StockPrice.timeframe == DAILY_TIMEFRAME)
        .order_by(StockPrice.price_datetime.asc())
        .limit(1)
    )
    earliest_datetime = db.scalar(stmt)
    return earliest_datetime.date() if earliest_datetime else None


def _history_covers_requested_range(
    db: Session,
    stock_id: int,
    start_date: date,
    end_date: date,
) -> bool:
    first_date = get_first_price_date(db, stock_id)
    last_date = get_last_price_date(db, stock_id)
    if not first_date or not last_date:
        return False
    return first_date <= start_date + timedelta(days=7) and last_date >= end_date - timedelta(days=7)


@timed("market_data.sync_stock_prices")
def sync_stock_prices(
    db: Session,
    stock_id: int,
    period: str | None = None,
    interval: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    chunk_days: int | None = None,
    sleep_seconds: float = 0,
    incremental: bool = False,
    progress_callback: ProgressCallback | None = None,
    stock_index: int | None = None,
    total_symbols: int | None = None,
    commit: bool = True,
    skip_probe: bool = False,
    prefetched_dataframe: pd.DataFrame | None = None,
) -> StockSyncResult:
    stock = db.get(Stock, stock_id)
    if stock is None:
        raise LookupError("Stock not found")
    interval = ensure_daily_interval(interval or settings.yfinance_default_interval)
    if chunk_days is not None and chunk_days < 1:
        raise ValueError("chunk_days must be at least 1 when provided")
    if sleep_seconds < 0:
        raise ValueError("sleep_seconds cannot be negative")

    resolved_start_date = start_date
    resolved_end_date = end_date
    last_available_date = get_last_price_date(db, stock_id) if incremental else None

    if incremental:
        resolved_end_date = resolved_end_date or previous_business_day()
        if last_available_date and last_available_date >= resolved_end_date:
            _emit_progress(
                progress_callback,
                event="stock_skipped",
                symbol=stock.yahoo_symbol,
                stock_index=stock_index,
                total_symbols=total_symbols,
                last_available_date=last_available_date,
                end_date=resolved_end_date,
                reason="already_up_to_date",
            )
            return StockSyncResult(0, "skipped_up_to_date")
        if last_available_date:
            resolved_start_date = (
                max(resolved_start_date, last_available_date)
                if resolved_start_date
                else last_available_date
            )
        elif resolved_start_date is None:
            resolved_start_date = _period_to_start_date(
                period or settings.yfinance_default_period,
                resolved_end_date,
            )

    if resolved_start_date and resolved_end_date and resolved_start_date > resolved_end_date:
        _emit_progress(
            progress_callback,
            event="stock_skipped",
            symbol=stock.yahoo_symbol,
            stock_index=stock_index,
            total_symbols=total_symbols,
            start_date=resolved_start_date,
            end_date=resolved_end_date,
            reason="empty_date_range",
        )
        return StockSyncResult(0, "skipped_empty_range")

    if (
        not incremental
        and resolved_start_date
        and resolved_end_date
        and _history_covers_requested_range(db, stock_id, resolved_start_date, resolved_end_date)
    ):
        _emit_progress(
            progress_callback,
            event="stock_skipped",
            symbol=stock.yahoo_symbol,
            stock_index=stock_index,
            total_symbols=total_symbols,
            start_date=resolved_start_date,
            end_date=resolved_end_date,
            reason="history_already_present",
        )
        return StockSyncResult(0, "skipped_covered")

    if (
        not skip_probe
        and not incremental
        and resolved_start_date
        and resolved_end_date
        and prefetched_dataframe is None
    ):
        probe_start = max(resolved_start_date, resolved_end_date - timedelta(days=365))
        probe_result = fetch_stock_history_result(
            stock.yahoo_symbol,
            period=period or settings.yfinance_default_period,
            interval=interval,
            start_date=probe_start,
            end_date=resolved_end_date,
        )
        if probe_result.dataframe.empty and is_yfinance_delisted_error(probe_result.error_message):
            mark_stock_delisted(db, stock_id=stock.id, reason=probe_result.error_message)
            if commit:
                db.commit()
            else:
                db.flush()
            return StockSyncResult(0, "marked_delisted")
        if probe_result.dataframe.empty:
            _emit_progress(
                progress_callback,
                event="stock_skipped",
                symbol=stock.yahoo_symbol,
                stock_index=stock_index,
                total_symbols=total_symbols,
                start_date=probe_start,
                end_date=resolved_end_date,
                reason="no_recent_yfinance_data",
            )
            return StockSyncResult(0, "skipped_no_recent")

    if prefetched_dataframe is not None:
        rows_saved_total = save_stock_prices(
            db,
            stock_id,
            _requested_date_window(prefetched_dataframe, resolved_start_date, resolved_end_date),
            interval,
        )
        if commit:
            db.commit()
        else:
            db.flush()
        outcome = "saved" if rows_saved_total > 0 else "failed_no_data"
        return StockSyncResult(rows_saved_total, outcome)

    windows = _date_windows(resolved_start_date, resolved_end_date, chunk_days)
    rows_saved_total = 0
    for chunk_index, (chunk_start, chunk_end) in enumerate(windows, start=1):
        _emit_progress(
            progress_callback,
            event="chunk_started",
            symbol=stock.yahoo_symbol,
            stock_index=stock_index,
            total_symbols=total_symbols,
            chunk_index=chunk_index,
            total_chunks=len(windows),
            start_date=chunk_start,
            end_date=chunk_end,
        )
        history_result = fetch_stock_history_result(
            stock.yahoo_symbol,
            period=period or settings.yfinance_default_period,
            interval=interval,
            start_date=chunk_start,
            end_date=chunk_end,
        )
        if history_result.dataframe.empty and is_yfinance_delisted_error(history_result.error_message):
            mark_stock_delisted(db, stock_id=stock.id, reason=history_result.error_message)
            if commit:
                db.commit()
            else:
                db.flush()
            return StockSyncResult(rows_saved_total, "marked_delisted")
        dataframe = history_result.dataframe
        dataframe = _requested_date_window(dataframe, chunk_start, chunk_end)
        rows_saved = save_stock_prices(db, stock_id, dataframe, interval)
        rows_saved_total += rows_saved
        _emit_progress(
            progress_callback,
            event="chunk_finished",
            symbol=stock.yahoo_symbol,
            stock_index=stock_index,
            total_symbols=total_symbols,
            chunk_index=chunk_index,
            total_chunks=len(windows),
            start_date=chunk_start,
            end_date=chunk_end,
            rows_saved=rows_saved,
            rows_saved_total=rows_saved_total,
        )
        if sleep_seconds:
            time_module.sleep(sleep_seconds)
    if commit:
        db.commit()
        try:
            from app.services.paper_trading_service import match_pending_orders

            match_pending_orders(db, stock_id=stock_id)
        except Exception:
            logger.exception("match_pending_orders failed after sync for stock_id=%s", stock_id)
    else:
        db.flush()
    outcome = "saved" if rows_saved_total > 0 else ("skipped_no_new_data" if incremental else "failed_no_data")
    return StockSyncResult(rows_saved_total, outcome)


def _record_sync_result(
    synced: dict[str, int],
    failure_messages: list[str],
    symbol: str,
    result: StockSyncResult,
    skip_breakdown: dict[str, int] | None = None,
) -> tuple[int, int, int]:
    synced[symbol] = result.rows_saved
    if skip_breakdown is not None:
        skip_breakdown[result.outcome] = skip_breakdown.get(result.outcome, 0) + 1
    if result.outcome in SKIPPED_SYNC_OUTCOMES or result.rows_saved > 0:
        return result.rows_saved, 1, 0
    failure_messages.append(f"{symbol}: {result.outcome}")
    return result.rows_saved, 0, 1


def _update_ingestion_run_progress(
    run_id: int | None,
    *,
    rows_saved: int,
    success_count: int,
    failed_count: int,
) -> None:
    if run_id is None:
        return
    progress_db = SessionLocal()
    try:
        run = progress_db.get(IngestionRun, run_id)
        if run is None or run.status != "RUNNING":
            return
        run.rows_saved = rows_saved
        run.success_count = success_count
        run.failed_count = failed_count
        progress_db.commit()
    except Exception:
        progress_db.rollback()
        logger.exception("Failed updating ingestion run progress run_id=%s", run_id)
    finally:
        progress_db.close()


def _parallel_sync_stocks(
    stocks: list[Stock],
    *,
    period: str | None,
    interval: str,
    start_date: date | None,
    end_date: date | None,
    chunk_days: int | None,
    sleep_seconds: float,
    incremental: bool,
    workers: int,
    download_batch_size: int,
    skip_probe: bool,
    progress_callback: ProgressCallback | None,
    coverage_map: dict[int, tuple[date | None, date | None]],
    effective_end_date: date | None,
    run_id: int | None = None,
    progress_update_interval: int = 25,
) -> tuple[dict[str, int], int, int, int, list[str], dict[str, int], int]:
    synced: dict[str, int] = {}
    rows_saved_total = 0
    success_count = 0
    failed_count = 0
    failure_messages: list[str] = []
    skip_breakdown: dict[str, int] = defaultdict(int)
    pre_skipped = 0

    to_sync: list[Stock] = []
    for index, stock in enumerate(stocks, start=1):
        first_date, last_date = coverage_map.get(stock.id, (None, None))
        if incremental and effective_end_date and last_date and last_date >= effective_end_date:
            synced[stock.yahoo_symbol] = 0
            success_count += 1
            skip_breakdown["skipped_up_to_date"] += 1
            pre_skipped += 1
            _emit_progress(
                progress_callback,
                event="stock_skipped",
                symbol=stock.yahoo_symbol,
                stock_index=index,
                total_symbols=len(stocks),
                last_available_date=last_date,
                end_date=effective_end_date,
                reason="already_up_to_date",
            )
            if len(synced) % progress_update_interval == 0:
                _update_ingestion_run_progress(
                    run_id,
                    rows_saved=rows_saved_total,
                    success_count=success_count,
                    failed_count=failed_count,
                )
            continue
        if (
            not incremental
            and start_date
            and effective_end_date
            and _is_history_covered(first_date, last_date, start_date, effective_end_date)
        ):
            synced[stock.yahoo_symbol] = 0
            success_count += 1
            skip_breakdown["skipped_covered"] += 1
            pre_skipped += 1
            _emit_progress(
                progress_callback,
                event="stock_skipped",
                symbol=stock.yahoo_symbol,
                stock_index=index,
                total_symbols=len(stocks),
                start_date=start_date,
                end_date=effective_end_date,
                reason="history_already_present",
            )
            if len(synced) % progress_update_interval == 0:
                _update_ingestion_run_progress(
                    run_id,
                    rows_saved=rows_saved_total,
                    success_count=success_count,
                    failed_count=failed_count,
                )
            continue
        to_sync.append(stock)

    logger.info(
        "Parallel sync pre-filter: selected=%s pre_skipped=%s to_sync=%s effective_end=%s",
        len(stocks),
        pre_skipped,
        len(to_sync),
        effective_end_date,
    )

    use_batch_download = (
        not incremental
        and start_date is not None
        and effective_end_date is not None
        and chunk_days is None
    )

    def sync_one(stock: Stock) -> tuple[str, StockSyncResult]:
        db = SessionLocal()
        try:
            result = sync_stock_prices(
                db,
                stock.id,
                period=period,
                interval=interval,
                start_date=start_date,
                end_date=end_date,
                chunk_days=chunk_days,
                sleep_seconds=sleep_seconds,
                incremental=incremental,
                progress_callback=progress_callback,
                commit=True,
                skip_probe=skip_probe,
            )
            return stock.yahoo_symbol, result
        except Exception:
            db.rollback()
            logger.exception("Failed syncing stock %s", stock.yahoo_symbol)
            return stock.yahoo_symbol, StockSyncResult(0, "failed")
        finally:
            db.close()

    def save_prefetched(
        stock: Stock,
        dataframe: pd.DataFrame,
        error_message: str | None,
    ) -> tuple[str, StockSyncResult]:
        if dataframe.empty:
            if is_yfinance_delisted_error(error_message):
                db = SessionLocal()
                try:
                    mark_stock_delisted(db, stock_id=stock.id, reason=error_message)
                    db.commit()
                    return stock.yahoo_symbol, StockSyncResult(0, "marked_delisted")
                except Exception:
                    db.rollback()
                    logger.exception("Failed marking delisted stock %s", stock.yahoo_symbol)
                    return stock.yahoo_symbol, StockSyncResult(0, "failed")
                finally:
                    db.close()
            return stock.yahoo_symbol, StockSyncResult(0, "failed_no_data")
        db = SessionLocal()
        try:
            rows_saved = save_stock_prices(db, stock.id, dataframe, interval)
            db.commit()
            outcome = "saved" if rows_saved > 0 else "failed_no_data"
            return stock.yahoo_symbol, StockSyncResult(rows_saved, outcome)
        except Exception:
            db.rollback()
            logger.exception("Failed saving prefetched prices for %s", stock.yahoo_symbol)
            return stock.yahoo_symbol, StockSyncResult(0, "failed")
        finally:
            db.close()

    if use_batch_download:
        batch_size = max(1, download_batch_size)
        download_groups = [to_sync[group_start : group_start + batch_size] for group_start in range(0, len(to_sync), batch_size)]

        def fetch_group(group: list[Stock]) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
            errors: dict[str, str] = {}
            histories = fetch_batch_stock_histories(
                [stock.yahoo_symbol for stock in group],
                period=period or settings.yfinance_default_period,
                interval=interval,
                start_date=start_date,
                end_date=effective_end_date,
                allow_individual_fallback=False,
                errors=errors,
            )
            return histories, errors

        download_workers = min(4, max(1, workers // 8))
        with ThreadPoolExecutor(max_workers=download_workers) as download_pool:
            group_futures = {download_pool.submit(fetch_group, group): group for group in download_groups}
            for future in as_completed(group_futures):
                group = group_futures[future]
                histories, errors = future.result()
                with ThreadPoolExecutor(max_workers=workers) as save_pool:
                    save_futures = [
                        save_pool.submit(
                            save_prefetched,
                            stock,
                            histories.get(stock.yahoo_symbol, pd.DataFrame()),
                            errors.get(stock.yahoo_symbol),
                        )
                        for stock in group
                    ]
                    for save_future in as_completed(save_futures):
                        symbol, result = save_future.result()
                        saved, ok, failed = _record_sync_result(
                            synced, failure_messages, symbol, result, skip_breakdown
                        )
                        rows_saved_total += saved
                        success_count += ok
                        failed_count += failed
                        if len(synced) % progress_update_interval == 0:
                            _update_ingestion_run_progress(
                                run_id,
                                rows_saved=rows_saved_total,
                                success_count=success_count,
                                failed_count=failed_count,
                            )
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(sync_one, stock) for stock in to_sync]
            for future in as_completed(futures):
                symbol, result = future.result()
                saved, ok, failed = _record_sync_result(
                    synced, failure_messages, symbol, result, skip_breakdown
                )
                rows_saved_total += saved
                success_count += ok
                failed_count += failed
                if len(synced) % progress_update_interval == 0:
                    _update_ingestion_run_progress(
                        run_id,
                        rows_saved=rows_saved_total,
                        success_count=success_count,
                        failed_count=failed_count,
                    )

    _update_ingestion_run_progress(
        run_id,
        rows_saved=rows_saved_total,
        success_count=success_count,
        failed_count=failed_count,
    )

    return (
        synced,
        rows_saved_total,
        success_count,
        failed_count,
        failure_messages,
        dict(skip_breakdown),
        pre_skipped,
    )


@timed("market_data.sync_all_active_stocks")
def sync_all_active_stocks(
    db: Session,
    limit: int | None = None,
    offset: int | None = None,
    period: str | None = None,
    interval: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    exchange: str | None = None,
    chunk_days: int | None = None,
    sleep_seconds: float = 0,
    incremental: bool = False,
    progress_callback: ProgressCallback | None = None,
    workers: int = 1,
    download_batch_size: int = 40,
    skip_probe: bool = False,
) -> SyncAllActiveStocksResult:
    sync_started_at = time_module.perf_counter()
    interval = ensure_daily_interval(interval or settings.yfinance_default_interval)
    if chunk_days is not None and chunk_days < 0:
        raise ValueError("chunk_days cannot be negative")
    if chunk_days == 0:
        chunk_days = None
    if chunk_days is not None and chunk_days < 1:
        raise ValueError("chunk_days must be at least 1 when provided")
    if sleep_seconds < 0:
        raise ValueError("sleep_seconds cannot be negative")
    if workers < 1:
        raise ValueError("workers must be at least 1")
    if download_batch_size < 1:
        raise ValueError("download_batch_size must be at least 1")

    stmt = (
        select(Stock)
        .where(Stock.is_active.is_(True))
        .order_by(Stock.symbol.asc())
    )
    if exchange:
        exchange = exchange.strip().upper()
        stmt = stmt.where(Stock.exchange == exchange)
    if offset:
        stmt = stmt.offset(offset)
    if limit:
        stmt = stmt.limit(limit)
    stocks = list(db.scalars(stmt))
    market_today = datetime.now(MARKET_TIMEZONE).date()
    effective_end_date = end_date or (previous_business_day() if incremental else None)
    latest_before = get_latest_stored_daily_date(db)
    sample_symbols = [stock.yahoo_symbol for stock in stocks[:5]]
    exchange_breakdown = _exchange_breakdown(stocks)

    logger.info("sync_all_active_stocks entered")
    logger.info(
        "Sync universe: active_stocks=%s exchange=%s limit=%s offset=%s incremental=%s "
        "market_today=%s effective_end_date=%s latest_stored_before=%s provider=yfinance",
        len(stocks),
        exchange or "ALL",
        limit,
        offset,
        incremental,
        market_today,
        effective_end_date,
        latest_before,
    )
    logger.info("First symbols: %s", sample_symbols)
    logger.info("Exchange breakdown: %s", exchange_breakdown)
    if len(stocks) == 0:
        provider_latest, provider_dates, provider_error = probe_provider_latest_date()
        logger.warning(
            "No active stocks selected for sync. provider_latest=%s provider_dates=%s error=%s",
            provider_latest,
            provider_dates,
            provider_error,
        )
        return SyncAllActiveStocksResult(
            status="warning",
            message="No active stocks were selected for sync.",
            symbol_results={},
            symbols_selected=0,
            symbols_attempted=0,
            symbols_synced=0,
            symbols_skipped=0,
            symbols_success=0,
            symbols_failed=0,
            rows_saved=0,
            rows_fetched=0,
            rows_inserted=0,
            rows_updated=0,
            latest_stored_date_before=latest_before,
            latest_stored_date_after=latest_before,
            provider_latest_date=provider_latest,
            effective_end_date=effective_end_date,
            effective_start_date=start_date,
            provider="yfinance",
            skip_breakdown={},
            sample_symbols=[],
            exchange_breakdown=exchange_breakdown,
            run_id=None,
        )

    run = IngestionRun(
        source="yfinance",
        exchange=exchange,
        timeframe=interval,
        start_date=start_date,
        end_date=effective_end_date,
        status="RUNNING",
        ingestion_mode="INCREMENTAL" if incremental else "FULL",
        total_symbols=len(stocks),
        batch_offset=offset,
        batch_limit=limit,
        chunk_days=chunk_days,
        sleep_seconds=Decimal(str(round(sleep_seconds, 2))),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    run_id = run.id

    coverage_map = _bulk_price_coverage(
        db,
        [stock.id for stock in stocks],
        start_date=start_date,
        end_date=effective_end_date,
    )

    skip_breakdown: dict[str, int] = defaultdict(int)
    pre_skipped = 0

    if workers > 1:
        (
            synced,
            rows_saved_total,
            success_count,
            failed_count,
            failure_messages,
            skip_breakdown,
            pre_skipped,
        ) = _parallel_sync_stocks(
            stocks,
            period=period,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            chunk_days=chunk_days,
            sleep_seconds=sleep_seconds,
            incremental=incremental,
            workers=workers,
            download_batch_size=download_batch_size,
            skip_probe=skip_probe or workers > 1,
            progress_callback=progress_callback,
            coverage_map=coverage_map,
            effective_end_date=effective_end_date,
            run_id=run_id,
        )
    else:
        synced: dict[str, int] = {}
        rows_saved_total = 0
        success_count = 0
        failed_count = 0
        failure_messages: list[str] = []

        for index, stock in enumerate(stocks, start=1):
            try:
                first_date, last_date = coverage_map.get(stock.id, (None, None))
                if incremental and effective_end_date and last_date and last_date >= effective_end_date:
                    synced[stock.yahoo_symbol] = 0
                    success_count += 1
                    skip_breakdown["skipped_up_to_date"] += 1
                    pre_skipped += 1
                    logger.info(
                        "Skip %s: last_date=%s >= effective_end=%s",
                        stock.yahoo_symbol,
                        last_date,
                        effective_end_date,
                    )
                    if len(synced) % 25 == 0:
                        _update_ingestion_run_progress(
                            run_id,
                            rows_saved=rows_saved_total,
                            success_count=success_count,
                            failed_count=failed_count,
                        )
                    continue
                if (
                    not incremental
                    and start_date
                    and effective_end_date
                    and _is_history_covered(first_date, last_date, start_date, effective_end_date)
                ):
                    synced[stock.yahoo_symbol] = 0
                    success_count += 1
                    skip_breakdown["skipped_covered"] += 1
                    pre_skipped += 1
                    if len(synced) % 25 == 0:
                        _update_ingestion_run_progress(
                            run_id,
                            rows_saved=rows_saved_total,
                            success_count=success_count,
                            failed_count=failed_count,
                        )
                    continue
                if index <= 3:
                    next_start = (
                        last_date
                        if incremental and last_date
                        else start_date
                    )
                    logger.info(
                        "Fetching %s incremental=%s last_date=%s next_start=%s end=%s start_gt_end=%s",
                        stock.yahoo_symbol,
                        incremental,
                        last_date,
                        next_start,
                        effective_end_date,
                        bool(next_start and effective_end_date and next_start > effective_end_date),
                    )
                sync_result = sync_stock_prices(
                    db,
                    stock.id,
                    period=period,
                    interval=interval,
                    start_date=start_date,
                    end_date=end_date,
                    chunk_days=chunk_days,
                    sleep_seconds=sleep_seconds,
                    incremental=incremental,
                    progress_callback=progress_callback,
                    stock_index=index,
                    total_symbols=len(stocks),
                    commit=True,
                    skip_probe=skip_probe,
                )
                saved, ok, failed = _record_sync_result(
                    synced,
                    failure_messages,
                    stock.yahoo_symbol,
                    sync_result,
                    skip_breakdown,
                )
                rows_saved_total += saved
                success_count += ok
                failed_count += failed
                if len(synced) % 25 == 0:
                    _update_ingestion_run_progress(
                        run_id,
                        rows_saved=rows_saved_total,
                        success_count=success_count,
                        failed_count=failed_count,
                    )
            except Exception:
                db.rollback()
                logger.exception("Failed syncing stock %s", stock.yahoo_symbol)
                synced[stock.yahoo_symbol] = 0
                failed_count += 1
                skip_breakdown["failed"] += 1
                failure_messages.append(f"{stock.yahoo_symbol}: exception during sync")
                if len(synced) % 25 == 0:
                    _update_ingestion_run_progress(
                        run_id,
                        rows_saved=rows_saved_total,
                        success_count=success_count,
                        failed_count=failed_count,
                    )

        _update_ingestion_run_progress(
            run_id,
            rows_saved=rows_saved_total,
            success_count=success_count,
            failed_count=failed_count,
        )

    latest_after = get_latest_stored_daily_date(db)
    provider_latest, provider_dates, provider_error = probe_provider_latest_date()
    symbols_synced = len([symbol for symbol, rows in synced.items() if rows > 0])
    symbols_skipped = sum(skip_breakdown.values())
    summary_status, summary_message = _finalize_sync_run_status(
        rows_saved_total=rows_saved_total,
        success_count=success_count,
        failed_count=failed_count,
        symbols_selected=len(stocks),
        symbols_skipped=symbols_skipped,
        effective_end_date=effective_end_date,
    )

    run_to_update = db.get(IngestionRun, run_id)
    if run_to_update:
        run_to_update.rows_saved = rows_saved_total
        run_to_update.success_count = success_count
        run_to_update.failed_count = failed_count
        if summary_status == "failed":
            run_to_update.status = "FAILED"
        elif summary_status == "success":
            run_to_update.status = "SUCCEEDED"
        else:
            run_to_update.status = "PARTIAL"
        run_to_update.error_message = "\n".join(
            [summary_message, *failure_messages[:19]]
        ).strip() or None
        run_to_update.finished_at = datetime.now(UTC)
        db.commit()

    logger.info(
        "sync_all_active_stocks finished in %.2fs status=%s rows_saved=%s selected=%s "
        "pre_skipped=%s synced=%s failed=%s latest_before=%s latest_after=%s "
        "provider_latest=%s provider_dates=%s provider_error=%s skip_breakdown=%s",
        time_module.perf_counter() - sync_started_at,
        summary_status,
        rows_saved_total,
        len(stocks),
        pre_skipped,
        symbols_synced,
        failed_count,
        latest_before,
        latest_after,
        provider_latest,
        provider_dates,
        provider_error,
        skip_breakdown,
    )

    return SyncAllActiveStocksResult(
        status=summary_status,
        message=summary_message,
        symbol_results=synced,
        symbols_selected=len(stocks),
        symbols_attempted=len(synced),
        symbols_synced=symbols_synced,
        symbols_skipped=symbols_skipped,
        symbols_success=success_count,
        symbols_failed=failed_count,
        rows_saved=rows_saved_total,
        rows_fetched=rows_saved_total,
        rows_inserted=rows_saved_total,
        rows_updated=0,
        latest_stored_date_before=latest_before,
        latest_stored_date_after=latest_after,
        provider_latest_date=provider_latest,
        effective_end_date=effective_end_date,
        effective_start_date=start_date,
        provider="yfinance",
        skip_breakdown=dict(skip_breakdown),
        sample_symbols=sample_symbols,
        exchange_breakdown=exchange_breakdown,
        run_id=run_id,
    )


# Default starting point for deep-history backfills. 1998-01-01 predates
# India's modern electronic-trading era (NSE/BSE both had reasonably complete
# daily records by then), so it's a practical "give me everything" anchor --
# yfinance simply returns whatever it actually has on file for each symbol
# starting from this date, which for many listings will be later than 1998.
BACKFILL_EARLIEST_START_DATE = date(1998, 1, 1)

# Backfills request decades of daily candles per symbol. Splitting the request
# into multi-year chunks keeps each yfinance call's response size reasonable
# and means a transient failure only has to be retried for one chunk rather
# than re-pulling 25+ years of history for that symbol.
BACKFILL_DEFAULT_CHUNK_DAYS = 365 * 5


def backfill_full_price_history(
    db: Session,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int | None = None,
    offset: int | None = None,
    exchange: str | None = None,
    chunk_days: int | None = BACKFILL_DEFAULT_CHUNK_DAYS,
    sleep_seconds: float = 0,
    progress_callback: ProgressCallback | None = None,
    workers: int = 1,
    download_batch_size: int = 40,
) -> SyncAllActiveStocksResult:
    """Ingest the deepest available daily price history for active stocks.

    This is a deliberately thin wrapper around `sync_all_active_stocks` --
    all the machinery for chunked fetching, delisting detection, progress
    reporting, parallel workers and `IngestionRun` bookkeeping already exists
    there and is well-exercised by the regular incremental/full sync paths.
    What a *backfill* needs on top is simply: (a) a `start_date` far enough
    back to capture "all the history we can get" rather than a rolling
    window, and (b) `incremental=False`, so the per-symbol "already up to
    date" shortcut in `sync_stock_prices` (which only looks at the most
    *recent* stored date) doesn't short-circuit a request for much *older*
    data than what's already stored.

    Defaults to `BACKFILL_EARLIEST_START_DATE` (1998-01-01) when no
    `start_date` is supplied -- yfinance trims this back automatically to
    whatever each symbol's actual listing history supports, so requesting an
    earlier-than-available date is harmless; it just yields "all of it".

    Safe to re-run: `save_stock_prices` upserts on (stock_id, timeframe,
    price_datetime), so a repeat backfill (e.g. after adding new symbols, or
    resuming after a partial failure) only adds what's missing rather than
    duplicating rows.
    """
    resolved_start = start_date or BACKFILL_EARLIEST_START_DATE
    logger.info(
        "backfill_full_price_history starting: start_date=%s end_date=%s exchange=%s "
        "limit=%s offset=%s chunk_days=%s workers=%s",
        resolved_start,
        end_date,
        exchange or "ALL",
        limit,
        offset,
        chunk_days,
        workers,
    )
    return sync_all_active_stocks(
        db,
        limit=limit,
        offset=offset,
        start_date=resolved_start,
        end_date=end_date,
        exchange=exchange,
        chunk_days=chunk_days,
        sleep_seconds=sleep_seconds,
        incremental=False,
        progress_callback=progress_callback,
        workers=workers,
        download_batch_size=download_batch_size,
        # Skip the "any recent data at all?" probe -- for a deep backfill we
        # already know exactly what we're asking for and don't want a thin
        # one-year probe window to wrongly mark a thinly-traded symbol as
        # having "no recent data" before we ever request its full history.
        skip_probe=True,
    )


def prices_to_dataframe(prices: list[StockPrice]) -> pd.DataFrame:
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
