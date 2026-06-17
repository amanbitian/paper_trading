from __future__ import annotations

import logging
import threading
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.index_fund import IndexFund, IndexFundPrice
from app.services.index_fund_service import (
    calculate_index_return_series,
    list_index_fund_performance,
    sync_all_active_index_funds,
)
from app.services.market_data_service import DAILY_TIMEFRAME

logger = logging.getLogger(__name__)

SYNC_BATCH_LIMIT = 25
MIN_STRATEGY_ROWS = 80
NIFTY_YAHOO_SYMBOL = "^NSEI"

_SORT_KEYS = {
    "symbol": lambda row: (row.get("symbol") or "").upper(),
    "latest_date": lambda row: row.get("latest_price_datetime") or datetime.min.replace(tzinfo=UTC),
    "latest_price": lambda row: row.get("latest_price") if row.get("latest_price") is not None else -1,
    "change_1m_pct": lambda row: row.get("change_1m_pct") if row.get("change_1m_pct") is not None else float("-inf"),
    "change_3m_pct": lambda row: row.get("change_3m_pct") if row.get("change_3m_pct") is not None else float("-inf"),
    "change_6m_pct": lambda row: row.get("change_6m_pct") if row.get("change_6m_pct") is not None else float("-inf"),
    "change_1y_pct": lambda row: row.get("change_1y_pct") if row.get("change_1y_pct") is not None else float("-inf"),
    "volume": lambda row: row.get("latest_volume") if row.get("latest_volume") is not None else -1,
}

_PERIOD_DAYS = {
    "1m": 31,
    "3m": 92,
    "6m": 183,
    "1y": 365,
    "3y": 365 * 3,
    "5y": 365 * 5,
    "10y": 365 * 10,
    "15y": 365 * 15,
    "20y": 365 * 20,
}

# Sentinel period value meaning "as far back as we have stored data for the
# selected instruments" -- resolved dynamically per request (see
# `_earliest_available_date`) rather than via a fixed day count, since the
# answer depends on which instruments are selected.
MAX_PERIOD_VALUE = "max"

_SYNC_LOCK = threading.Lock()
_SYNC_THREAD: threading.Thread | None = None
_SYNC_STATE: dict[str, Any] = {
    "is_running": False,
    "status": None,
    "message": None,
    "started_at": None,
    "finished_at": None,
    "rows_saved": 0,
    "symbols_synced": 0,
    "symbols_failed": 0,
    "symbol_results": {},
}


def http_error_message(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, str):
        return detail
    return str(exc) or "Request failed."


def get_index_sync_status() -> dict[str, Any]:
    with _SYNC_LOCK:
        return dict(_SYNC_STATE)


def start_index_fund_sync() -> dict[str, Any]:
    global _SYNC_THREAD
    with _SYNC_LOCK:
        if _SYNC_STATE.get("is_running") or (_SYNC_THREAD is not None and _SYNC_THREAD.is_alive()):
            return {
                "started": False,
                "message": "Index fund sync is already running.",
            }
        _SYNC_STATE.update(
            {
                "is_running": True,
                "status": "RUNNING",
                "message": "Index fund sync started.",
                "started_at": datetime.now(UTC),
                "finished_at": None,
                "rows_saved": 0,
                "symbols_synced": 0,
                "symbols_failed": 0,
                "symbol_results": {},
            }
        )
        _SYNC_THREAD = threading.Thread(target=_run_index_sync_job, daemon=True, name="index-fund-sync")
        _SYNC_THREAD.start()
    return {
        "started": True,
        "message": f"Index fund sync started for up to {SYNC_BATCH_LIMIT} active instruments (incremental).",
    }


def _run_index_sync_job() -> None:
    global _SYNC_THREAD
    db = SessionLocal()
    rows_saved = 0
    synced = 0
    failed = 0
    results: dict[int, int] = {}
    status = "SUCCEEDED"
    message = "Index fund sync completed."
    try:
        logger.info("Background index fund sync started (limit=%s)", SYNC_BATCH_LIMIT)
        results = sync_all_active_index_funds(
            db,
            limit=SYNC_BATCH_LIMIT,
            incremental=True,
            chunk_days=365,
            sleep_seconds=0,
        )
        for fund_id, saved in results.items():
            rows_saved += int(saved or 0)
            if saved and saved > 0:
                synced += 1
            else:
                failed += 1
        if synced == 0 and failed > 0:
            status = "FAILED"
            message = "Index fund sync finished with no rows saved."
        elif failed > 0:
            status = "PARTIAL"
            message = f"Index fund sync saved {rows_saved} rows with {failed} instruments unchanged or failed."
        else:
            message = f"Index fund sync saved {rows_saved} rows across {synced} instruments."
    except Exception:
        logger.exception("Background index fund sync failed")
        status = "FAILED"
        message = "Index fund sync failed. Check backend logs."
    finally:
        db.close()
        with _SYNC_LOCK:
            _SYNC_STATE.update(
                {
                    "is_running": False,
                    "status": status,
                    "message": message,
                    "finished_at": datetime.now(UTC),
                    "rows_saved": rows_saved,
                    "symbols_synced": synced,
                    "symbols_failed": failed,
                    "symbol_results": results,
                }
            )
            _SYNC_THREAD = None


def build_summary_context(db: Session) -> dict[str, Any]:
    total = int(db.scalar(select(func.count()).select_from(IndexFund).where(IndexFund.is_active.is_(True))) or 0)
    with_prices = int(
        db.scalar(
            select(func.count(func.distinct(IndexFundPrice.index_fund_id))).where(
                IndexFundPrice.timeframe == DAILY_TIMEFRAME,
                IndexFundPrice.close.is_not(None),
            )
        )
        or 0
    )
    latest_date = db.scalar(
        select(func.max(func.date(IndexFundPrice.price_datetime))).where(
            IndexFundPrice.timeframe == DAILY_TIMEFRAME
        )
    )
    categories_count = int(
        db.scalar(
            select(func.count(func.distinct(IndexFund.category))).where(IndexFund.is_active.is_(True))
        )
        or 0
    )
    sync_status = get_index_sync_status()
    return {
        "total_instruments": total,
        "with_prices": with_prices,
        "without_prices": max(0, total - with_prices),
        "latest_price_date": latest_date,
        "categories_count": categories_count,
        "last_sync_status": sync_status.get("status"),
        "last_sync_message": sync_status.get("message"),
        "sync_is_running": bool(sync_status.get("is_running")),
    }


def list_filter_options(db: Session) -> dict[str, list[str]]:
    categories = list(
        db.scalars(
            select(IndexFund.category)
            .where(IndexFund.is_active.is_(True), IndexFund.category.is_not(None))
            .distinct()
            .order_by(IndexFund.category.asc())
        )
    )
    currencies = list(
        db.scalars(
            select(IndexFund.base_currency)
            .where(IndexFund.is_active.is_(True))
            .distinct()
            .order_by(IndexFund.base_currency.asc())
        )
    )
    return {
        "categories": [value for value in categories if value],
        "currencies": [value for value in currencies if value],
    }


def list_universe_rows(
    db: Session,
    *,
    query: str | None = None,
    category: str | None = None,
    currency: str | None = None,
    has_prices: str = "all",
    sort_by: str = "latest_date",
    descending: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clean_query = (query or "").strip()
    fetch_limit = min(max(limit + offset, limit), 5000)
    only_with = has_prices == "with"
    rows = list_index_fund_performance(
        db,
        query=clean_query if len(clean_query) >= 2 else None,
        category=category,
        limit=fetch_limit,
        offset=0,
        only_with_prices=only_with,
    )
    if has_prices == "without":
        rows = [row for row in rows if row.get("latest_price") is None]
    if currency:
        currency_upper = currency.strip().upper()
        rows = [row for row in rows if (row.get("base_currency") or "").upper() == currency_upper]
    sort_key = _SORT_KEYS.get(sort_by, _SORT_KEYS["latest_date"])
    rows.sort(key=sort_key, reverse=descending)
    sliced = rows[offset : offset + limit]
    for row in sliced:
        row["has_price"] = row.get("latest_price") is not None
        row["status_label"] = "Has prices" if row["has_price"] else "Missing prices"
        row["status_tone"] = "success" if row["has_price"] else "warning"
    return sliced


def _earliest_available_date(db: Session, index_fund_ids: list[int]) -> date | None:
    """Earliest stored daily close across the given instruments, or None if there's no data yet.

    Backs the "Max" period option -- rather than guessing how far back history
    might go, this looks at what's actually stored for *these* instruments so
    the resulting plot start date is always one we can actually fill in.
    """
    if not index_fund_ids:
        return None
    earliest = db.scalar(
        select(func.min(IndexFundPrice.price_datetime)).where(
            IndexFundPrice.index_fund_id.in_(index_fund_ids),
            IndexFundPrice.timeframe == DAILY_TIMEFRAME,
            IndexFundPrice.close.is_not(None),
        )
    )
    if earliest is None:
        return None
    return earliest.date() if isinstance(earliest, datetime) else earliest


def resolve_plot_dates(
    *,
    db: Session | None = None,
    period: str | None,
    start_date: date | None,
    end_date: date | None,
    index_fund_ids: list[int] | None = None,
) -> tuple[date, date]:
    resolved_end = end_date or date.today()
    if start_date:
        return start_date, resolved_end
    normalized_period = (period or "1y").strip().lower()
    if normalized_period == MAX_PERIOD_VALUE:
        earliest = (
            _earliest_available_date(db, index_fund_ids)
            if db is not None and index_fund_ids
            else None
        )
        # Fall back to the longest fixed window we offer if we can't look up
        # stored history yet (e.g. no instruments selected) -- still better
        # than silently collapsing to the 1y default.
        fallback_start = resolved_end - timedelta(days=_PERIOD_DAYS["20y"])
        return earliest or fallback_start, resolved_end
    days = _PERIOD_DAYS.get(normalized_period, 365)
    return resolved_end - timedelta(days=days), resolved_end


def _resolve_nifty_fund_id(db: Session) -> int | None:
    fund = db.scalar(select(IndexFund.id).where(IndexFund.yahoo_symbol == NIFTY_YAHOO_SYMBOL).limit(1))
    return int(fund) if fund is not None else None


def _instrument_label(item: dict[str, Any]) -> str:
    return f"{item.get('symbol')} [{item.get('yahoo_symbol')}]"


def _build_return_comparison_plotly(series: list[dict[str, Any]]) -> dict[str, Any] | None:
    traces: list[dict[str, Any]] = []
    for item in series:
        points = item.get("points") or []
        if not points:
            continue
        traces.append(
            {
                "type": "scatter",
                "mode": "lines",
                "name": _instrument_label(item),
                "x": [point["date"] for point in points],
                "y": [point["return_pct"] for point in points],
            }
        )
    if not traces:
        return None
    return _chart_layout(traces, title="Return comparison (rebased to 0%)", y_title="Return %")


def _build_indexed_value_plotly(series: list[dict[str, Any]]) -> dict[str, Any] | None:
    traces: list[dict[str, Any]] = []
    for item in series:
        points = item.get("points") or []
        if not points:
            continue
        traces.append(
            {
                "type": "scatter",
                "mode": "lines",
                "name": _instrument_label(item),
                "x": [point["date"] for point in points],
                "y": [100 + float(point["return_pct"]) for point in points],
            }
        )
    if not traces:
        return None
    return _chart_layout(traces, title="Indexed value (base 100)", y_title="Index level")


def _build_drawdown_plotly(series: list[dict[str, Any]]) -> dict[str, Any] | None:
    traces: list[dict[str, Any]] = []
    for item in series:
        points = item.get("points") or []
        if len(points) < 2:
            continue
        equity = [100 + float(point["return_pct"]) for point in points]
        peak = equity[0]
        drawdowns: list[float] = []
        dates: list[str] = []
        for point, value in zip(points, equity):
            peak = max(peak, value)
            dd = ((value - peak) / peak * 100) if peak else 0.0
            drawdowns.append(round(dd, 4))
            dates.append(point["date"])
        traces.append(
            {
                "type": "scatter",
                "mode": "lines",
                "name": _instrument_label(item),
                "x": dates,
                "y": drawdowns,
                "fill": "tozeroy",
            }
        )
    if not traces:
        return None
    return _chart_layout(traces, title="Drawdown from rebased path", y_title="Drawdown %")


def _chart_layout(traces: list[dict[str, Any]], *, title: str, y_title: str) -> dict[str, Any]:
    return {
        "data": traces,
        "layout": {
            "autosize": True,
            "height": 420,
            "paper_bgcolor": "#050607",
            "plot_bgcolor": "#11151b",
            "font": {"color": "#f4f7fb", "size": 12},
            # Extra headroom (t=90) plus an explicit title position keeps the
            # in-chart title and the horizontal legend from overlapping --
            # the title sits at the very top of that margin, the legend just
            # above the plot area, with clear vertical separation between them.
            "margin": {"l": 50, "r": 20, "t": 90, "b": 40},
            "title": {"text": title, "y": 0.97, "yanchor": "top", "x": 0.02, "xanchor": "left"},
            "xaxis": {"title": "Date"},
            "yaxis": {"title": y_title},
            "legend": {"orientation": "h", "y": 1.04, "yanchor": "bottom"},
        },
    }


def build_history_context(
    db: Session,
    *,
    index_fund_id: int | None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 250,
) -> dict[str, Any]:
    if not index_fund_id:
        return {
            "selected": None,
            "rows": [],
            "needs_selection": True,
        }
    fund = db.get(IndexFund, index_fund_id)
    if fund is None:
        return {"selected": None, "rows": [], "error": "Index instrument not found."}

    resolved_end = end_date or date.today()
    resolved_start = start_date or (resolved_end - timedelta(days=365))
    start_dt = datetime.combine(resolved_start, time.min, tzinfo=UTC)
    end_dt = datetime.combine(resolved_end, time.max, tzinfo=UTC)

    prices = list(
        db.scalars(
            select(IndexFundPrice)
            .where(
                IndexFundPrice.index_fund_id == index_fund_id,
                IndexFundPrice.timeframe == DAILY_TIMEFRAME,
                IndexFundPrice.price_datetime >= start_dt,
                IndexFundPrice.price_datetime <= end_dt,
            )
            .order_by(IndexFundPrice.price_datetime.desc())
            .limit(min(limit, 1000))
        )
    )
    prices.reverse()
    rows: list[dict[str, Any]] = []
    prev_close: float | None = None
    for price in prices:
        close = float(price.close) if price.close is not None else None
        daily_return = None
        if close is not None and prev_close not in (None, 0):
            daily_return = round(((close - prev_close) / prev_close) * 100, 4)
        rows.append(
            {
                "date": price.price_datetime.date(),
                "open": price.open,
                "high": price.high,
                "low": price.low,
                "close": price.close,
                "adjusted_close": price.adjusted_close,
                "volume": price.volume,
                "daily_return_pct": daily_return,
                "currency": fund.base_currency,
            }
        )
        if close is not None:
            prev_close = close

    return {
        "selected": {
            "id": fund.id,
            "symbol": fund.symbol,
            "yahoo_symbol": fund.yahoo_symbol,
            "base_currency": fund.base_currency,
            "category": fund.category,
        },
        "rows": rows,
        "needs_selection": False,
        "start_date": resolved_start,
        "end_date": resolved_end,
        "row_count": len(rows),
    }


def build_strategy_ready_context(db: Session, *, min_rows: int = MIN_STRATEGY_ROWS) -> dict[str, Any]:
    stats_rows = db.execute(
        text(
            """
            SELECT
                f.id,
                f.symbol,
                f.yahoo_symbol,
                f.category,
                f.base_currency,
                COUNT(p.id)::int AS row_count,
                MIN(p.price_datetime)::date AS min_date,
                MAX(p.price_datetime)::date AS max_date
            FROM index_funds f
            LEFT JOIN index_fund_prices p
                ON p.index_fund_id = f.id
               AND p.timeframe = :timeframe
               AND p.close IS NOT NULL
            WHERE f.is_active = TRUE
            GROUP BY f.id, f.symbol, f.yahoo_symbol, f.category, f.base_currency
            ORDER BY row_count DESC, f.symbol ASC
            """
        ),
        {"timeframe": DAILY_TIMEFRAME},
    ).mappings().all()

    instruments: list[dict[str, Any]] = []
    for row in stats_rows:
        row_count = int(row["row_count"] or 0)
        usable = row_count >= min_rows
        warning = None
        if row_count == 0:
            warning = "No stored daily candles."
        elif not usable:
            warning = f"Needs at least {min_rows} rows for backtesting (has {row_count})."
        instruments.append(
            {
                "id": row["id"],
                "symbol": row["symbol"],
                "yahoo_symbol": row["yahoo_symbol"],
                "category": row["category"],
                "base_currency": row["base_currency"],
                "row_count": row_count,
                "min_date": row["min_date"],
                "max_date": row["max_date"],
                "usable_in_backtest": usable,
                "warning": warning,
            }
        )

    ready = [item for item in instruments if item["usable_in_backtest"]]
    return {
        "instruments": instruments,
        "ready_count": len(ready),
        "min_rows": min_rows,
        "backtesting_note": "Open Backtesting, choose Index funds universe, and search for the symbol to add it to the basket.",
    }


def _json_safe_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def instrument_picker_row(row: dict[str, Any]) -> dict[str, Any]:
    has_price = row.get("latest_price") is not None
    return {
        "id": row["id"],
        "symbol": row["symbol"],
        "yahoo_symbol": row["yahoo_symbol"],
        "category": row.get("category"),
        "base_currency": row.get("base_currency"),
        "latest_price": row.get("latest_price"),
        "latest_price_datetime": _json_safe_datetime(row.get("latest_price_datetime")),
        "label": f"{row['symbol']} [{row['yahoo_symbol']}]",
        "has_price": has_price,
        "status_label": "Has prices" if has_price else "Missing prices",
        "status_tone": "success" if has_price else "warning",
    }


def search_plot_instruments(
    db: Session,
    *,
    query: str,
    category: str | None = None,
    has_prices: str = "all",
    limit: int = 20,
) -> list[dict[str, Any]]:
    clean = (query or "").strip()
    if len(clean) < 1:
        return []
    only_with = has_prices == "with"
    rows = list_index_fund_performance(
        db,
        query=clean,
        category=category,
        limit=min(limit * 3, 120),
        only_with_prices=only_with,
    )
    if has_prices == "without":
        rows = [row for row in rows if row.get("latest_price") is None]
    return [instrument_picker_row(row) for row in rows[:limit]]


def resolve_default_plot_instruments(db: Session, *, max_count: int = 3) -> list[dict[str, Any]]:
    preferred_symbols = ("NIFTY 50", "NIFTY 100", "NIFTY 200")
    selected: list[dict[str, Any]] = []
    seen: set[int] = set()

    # `list_index_fund_performance` runs a bulk query/computation over up to
    # 5000 rows. The original code called it once per preferred symbol inside
    # the loop (up to 3x); it depends only on `db`, not on the symbol, so we
    # compute it once and reuse it — and index it by id for O(1) lookups
    # instead of a linear `next(...)` scan per symbol.
    perf_rows = list_index_fund_performance(db, limit=5000)
    perf_by_id = {item["id"]: item for item in perf_rows}

    for symbol in preferred_symbols:
        row = db.execute(
            text(
                """
                SELECT f.id
                FROM index_funds f
                WHERE f.is_active = TRUE AND UPPER(f.symbol) = :symbol
                LIMIT 1
                """
            ),
            {"symbol": symbol.upper()},
        ).first()
        if not row:
            continue
        fund_id = int(row[0])
        if fund_id in seen:
            continue
        match = perf_by_id.get(fund_id)
        if match:
            selected.append(instrument_picker_row(match))
            seen.add(fund_id)
        if len(selected) >= max_count:
            break
    return selected


def instruments_by_ids(db: Session, ids: list[int]) -> list[dict[str, Any]]:
    if not ids:
        return []
    rows = list_index_fund_performance(db, limit=5000)
    by_id = {row["id"]: row for row in rows}
    result: list[dict[str, Any]] = []
    for fund_id in ids:
        row = by_id.get(fund_id)
        if row:
            result.append(instrument_picker_row(row))
    return result


def build_return_plot_table_rows(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    table_rows: list[dict[str, Any]] = []
    for item in series:
        label = _instrument_label(item)
        for point in item.get("points") or []:
            table_rows.append(
                {
                    "instrument": label,
                    "date": point.get("date"),
                    "close": point.get("close"),
                    "return_pct": point.get("return_pct"),
                }
            )
    return table_rows


def build_return_plot_context(
    db: Session,
    *,
    ids: list[int],
    period: str = "1y",
    start_date: date | None = None,
    end_date: date | None = None,
    normalize_indexed: bool = False,
    compare_nifty: bool = False,
) -> dict[str, Any]:
    selected_ids = list(dict.fromkeys(ids))
    if compare_nifty:
        nifty_id = _resolve_nifty_fund_id(db)
        if nifty_id is not None and nifty_id not in selected_ids:
            selected_ids.append(nifty_id)
    # Resolved *after* selected_ids is final so the "Max" period can look up
    # the earliest stored date for the actual instruments being plotted.
    plot_start, plot_end = resolve_plot_dates(
        db=db,
        period=period,
        start_date=start_date,
        end_date=end_date,
        index_fund_ids=selected_ids,
    )
    if not selected_ids:
        return {
            "has_data": False,
            "return_chart_json": None,
            "indexed_chart_json": None,
            "drawdown_chart_json": None,
            "return_table_rows": [],
            "missing_instruments": [],
            "period_label": period,
            "start_date": plot_start,
            "end_date": plot_end,
        }

    series = calculate_index_return_series(
        db,
        index_fund_ids=selected_ids,
        start_date=plot_start,
        end_date=plot_end,
    )
    has_points = any(item.get("points") for item in series)
    missing = [item for item in series if not item.get("points")]
    if not has_points:
        return {
            "has_data": False,
            "return_chart_json": None,
            "indexed_chart_json": None,
            "drawdown_chart_json": None,
            "return_table_rows": [],
            "missing_instruments": [_instrument_label(item) for item in missing],
            "period_label": period,
            "start_date": plot_start,
            "end_date": plot_end,
        }

    return {
        "has_data": True,
        "return_chart_json": _build_return_comparison_plotly(series),
        "indexed_chart_json": _build_indexed_value_plotly(series) if normalize_indexed else None,
        "drawdown_chart_json": _build_drawdown_plotly(series),
        "return_table_rows": build_return_plot_table_rows(series),
        "missing_instruments": [_instrument_label(item) for item in missing],
        "period_label": period,
        "start_date": plot_start,
        "end_date": plot_end,
        "series_count": len(series),
    }


def build_return_plots_shell_context(db: Session) -> dict[str, Any]:
    selected = resolve_default_plot_instruments(db)
    return {
        "selected_instruments": selected,
        "filters": {
            "period": "5y",
            "start_date": "",
            "end_date": "",
            "normalize_indexed": False,
            "compare_nifty": False,
        },
    }


def list_instrument_options(db: Session, *, query: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    rows = list_index_fund_performance(
        db,
        query=query.strip() if query and len(query.strip()) >= 2 else None,
        limit=limit,
    )
    return [instrument_picker_row(row) for row in rows]
