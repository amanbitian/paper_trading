from __future__ import annotations

import logging
import threading
from datetime import UTC, date, datetime, time, timedelta
from numbers import Integral
from typing import Any

import pandas as pd
import yfinance as yf
from sqlalchemy import desc, select, text
from sqlalchemy.orm import Session

from app.models.stock import Stock, StockPrice
from app.services.analytics_refresh_service import (
    get_cached_market_movers,
    refresh_market_movers_cache,
    store_market_movers_cache,
)
from app.services.market_movers_service import DEFAULT_MOVER_LIMIT, compute_market_movers_from_db
from app.utils.observability import timed


logger = logging.getLogger(__name__)

# Fresh window: serve from cache without any refresh attempt.
# Stale window: serve cached data immediately and trigger a background refresh.
# Beyond stale: block and refresh synchronously.
CACHE_FRESH_TTL = timedelta(minutes=5)
CACHE_STALE_TTL = timedelta(minutes=30)

_CACHE: dict[str, tuple[datetime, dict[str, Any]]] = {}
_REFRESH_LOCK = threading.Lock()
_REFRESH_IN_FLIGHT = False


def clear_market_overview_cache() -> None:
    _CACHE.pop("overview", None)


def _background_refresh() -> None:
    global _REFRESH_IN_FLIGHT
    try:
        from app.database import SessionLocal
        with SessionLocal() as db:
            _compute_and_store_overview(db, datetime.now(UTC))
    except Exception:
        logger.exception("Background market overview refresh failed")
    finally:
        with _REFRESH_LOCK:
            _REFRESH_IN_FLIGHT = False


def _trigger_background_refresh() -> None:
    global _REFRESH_IN_FLIGHT
    with _REFRESH_LOCK:
        if _REFRESH_IN_FLIGHT:
            return
        _REFRESH_IN_FLIGHT = True
    threading.Thread(target=_background_refresh, daemon=True).start()

INDICES = [
    {"label": "NIFTY", "symbol": "NIFTY 50", "yahoo_symbol": "^NSEI", "kind": "index"},
    {"label": "SENSEX", "symbol": "SENSEX", "yahoo_symbol": "^BSESN", "kind": "index"},
    {"label": "BANKNIFTY", "symbol": "BANK NIFTY", "yahoo_symbol": "^NSEBANK", "kind": "index"},
    {"label": "MIDCPNIFTY", "symbol": "NIFTY MID SELECT", "yahoo_symbol": "NIFTY_MID_SELECT.NS", "kind": "index"},
    {"label": "FINNIFTY", "symbol": "NIFTY FIN SERVICE", "yahoo_symbol": "NIFTY_FIN_SERVICE.NS", "kind": "index"},
]

MOST_BOUGHT = [
    {"label": "Reliance", "symbol": "RELIANCE", "yahoo_symbol": "RELIANCE.NS", "kind": "stock"},
    {"label": "Tata Consultancy", "symbol": "TCS", "yahoo_symbol": "TCS.NS", "kind": "stock"},
    {"label": "Infosys", "symbol": "INFY", "yahoo_symbol": "INFY.NS", "kind": "stock"},
    {"label": "HDFC Bank", "symbol": "HDFCBANK", "yahoo_symbol": "HDFCBANK.NS", "kind": "stock"},
]

MOVER_UNIVERSE = [
    *MOST_BOUGHT,
    {"label": "ICICI Bank", "symbol": "ICICIBANK", "yahoo_symbol": "ICICIBANK.NS", "kind": "stock"},
    {"label": "State Bank of India", "symbol": "SBIN", "yahoo_symbol": "SBIN.NS", "kind": "stock"},
    {"label": "Larsen & Toubro", "symbol": "LT", "yahoo_symbol": "LT.NS", "kind": "stock"},
    {"label": "Bharti Airtel", "symbol": "BHARTIARTL", "yahoo_symbol": "BHARTIARTL.NS", "kind": "stock"},
    {"label": "ITC", "symbol": "ITC", "yahoo_symbol": "ITC.NS", "kind": "stock"},
    {"label": "Axis Bank", "symbol": "AXISBANK", "yahoo_symbol": "AXISBANK.NS", "kind": "stock"},
    {"label": "Kotak Bank", "symbol": "KOTAKBANK", "yahoo_symbol": "KOTAKBANK.NS", "kind": "stock"},
    {"label": "Maruti Suzuki", "symbol": "MARUTI", "yahoo_symbol": "MARUTI.NS", "kind": "stock"},
    {"label": "Sun Pharma", "symbol": "SUNPHARMA", "yahoo_symbol": "SUNPHARMA.NS", "kind": "stock"},
    {"label": "Tata Motors", "symbol": "TATAMOTORS", "yahoo_symbol": "TATAMOTORS.NS", "kind": "stock"},
    {"label": "Adani Enterprises", "symbol": "ADANIENT", "yahoo_symbol": "ADANIENT.NS", "kind": "stock"},
    {"label": "Cipla", "symbol": "CIPLA", "yahoo_symbol": "CIPLA.NS", "kind": "stock"},
    {"label": "Vedanta", "symbol": "VEDL", "yahoo_symbol": "VEDL.NS", "kind": "stock"},
    {"label": "HAL", "symbol": "HAL", "yahoo_symbol": "HAL.NS", "kind": "stock"},
]

SAMPLE_PRICES = {
    "^NSEI": (23726.90, 314.30, None),
    "^BSESN": (75541.33, 932.35, None),
    "^NSEBANK": (54305.80, 849.65, None),
    "NIFTY_MID_SELECT.NS": (14201.55, 127.50, None),
    "NIFTY_FIN_SERVICE.NS": (25584.20, 235.40, None),
    "RELIANCE.NS": (2868.40, 32.25, 7421850),
    "TCS.NS": (3915.20, 18.65, 1865312),
    "INFY.NS": (1488.35, 22.10, 6349281),
    "HDFCBANK.NS": (1682.75, -8.45, 8294012),
    "ICICIBANK.NS": (1124.60, 14.80, 6138112),
    "SBIN.NS": (835.10, 9.30, 11209118),
    "LT.NS": (3576.30, 41.70, 1728810),
    "BHARTIARTL.NS": (1881.30, 92.10, 14171563),
    "ITC.NS": (436.95, -2.15, 10383210),
    "AXISBANK.NS": (1183.50, 11.40, 4720111),
    "KOTAKBANK.NS": (1742.20, -7.10, 2318222),
    "MARUTI.NS": (12442.00, 143.25, 438112),
    "SUNPHARMA.NS": (1608.90, 19.60, 3041884),
    "TATAMOTORS.NS": (981.55, 26.20, 18391328),
    "ADANIENT.NS": (2652.60, 154.60, 7370096),
    "CIPLA.NS": (1435.20, 107.60, 6713112),
    "VEDL.NS": (336.00, 12.65, 3708955),
    "HAL.NS": (4418.70, 88.30, 2140931),
}


def _download_history(symbols: list[str]) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()
    return yf.download(
        symbols,
        period="1mo",
        interval="1d",
        auto_adjust=False,
        group_by="ticker",
        threads=True,
        progress=False,
    )


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
    return dataframe.dropna(how="all")


def _previous_business_day(today: date) -> date:
    candidate = today - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _date_from_index(value: Any) -> date | None:
    try:
        return pd.to_datetime(value).date()
    except Exception:
        return None


def _resolve_record_date(quotes: list[dict[str, Any]]) -> date | None:
    record_dates = [quote.get("record_date") for quote in quotes if quote.get("record_date")]
    return max(record_dates) if record_dates else None


def _quote_from_history(metadata: dict[str, str], dataframe: pd.DataFrame) -> dict[str, Any] | None:
    if dataframe.empty or "Close" not in dataframe.columns:
        return None
    closes = pd.to_numeric(dataframe["Close"], errors="coerce").dropna()
    if closes.empty:
        return None

    today = datetime.now(UTC).date()
    close_dates = pd.Series([_date_from_index(index) for index in closes.index], index=closes.index)
    eligible_mask = close_dates.apply(lambda value: value is not None and value < today)
    eligible = closes[eligible_mask]
    if eligible.empty:
        eligible = closes
    record_index = eligible.index[-1]
    record_position = closes.index.get_loc(record_index)
    if isinstance(record_position, slice):
        record_position = record_position.stop - 1
    if not isinstance(record_position, Integral):
        record_position = int(record_position[-1])
    else:
        record_position = int(record_position)

    previous_position = max(record_position - 1, 0)
    price = float(closes.iloc[record_position])
    previous = float(closes.iloc[previous_position])
    change = price - previous
    change_pct = (change / previous * 100) if previous else 0.0
    volume = None
    if "Volume" in dataframe.columns:
        volumes = pd.to_numeric(dataframe["Volume"], errors="coerce").dropna()
        if record_index in volumes.index:
            volume = int(volumes.loc[record_index])
        elif not volumes.empty:
            volume = int(volumes.iloc[min(record_position, len(volumes) - 1)])
    spark_start = max(record_position - 13, 0)
    sparkline = closes.iloc[spark_start : record_position + 1]

    return {
        **metadata,
        "price": round(price, 2),
        "change": round(change, 2),
        "change_pct": round(change_pct, 2),
        "volume": volume,
        "record_date": _date_from_index(record_index),
        "sparkline": [round(float(value), 2) for value in sparkline],
    }


def _quote_from_price_pair(
    metadata: dict[str, str],
    price: float,
    previous: float,
    volume: int | None,
    record_date: date | None = None,
) -> dict[str, Any]:
    change = price - previous
    change_pct = (change / previous * 100) if previous else 0.0
    sparkline = [round(previous + (change * step / 13), 2) for step in range(14)]
    return {
        **metadata,
        "price": round(price, 2),
        "change": round(change, 2),
        "change_pct": round(change_pct, 2),
        "volume": volume,
        "record_date": record_date,
        "sparkline": sparkline,
    }


def _sample_quotes_for(items: list[dict[str, str]]) -> list[dict[str, Any]]:
    quotes: list[dict[str, Any]] = []
    for item in items:
        sample = SAMPLE_PRICES.get(item["yahoo_symbol"])
        if not sample:
            continue
        price, change, volume = sample
        quotes.append(
            _quote_from_price_pair(
                item,
                price,
                price - change,
                volume,
                record_date=_previous_business_day(datetime.now(UTC).date()),
            )
        )
    return quotes


def _database_quotes_for(items: list[dict[str, str]], db: Session | None) -> list[dict[str, Any]]:
    """Look up the latest two daily closes for each item's yahoo_symbol.

    Originally this issued two queries per item (one to resolve the Stock,
    one for its latest prices) — for the ~17-symbol mover universe that was
    up to 34 round-trips per call. We now resolve every symbol in a single
    batched query and pull the latest-two-rows-per-stock in a second batched
    query using ROW_NUMBER(), then assemble the quotes in memory. Output is
    identical to the original per-item loop.
    """
    if db is None or not items:
        return []

    yahoo_symbols = [item["yahoo_symbol"] for item in items]
    stocks = db.scalars(select(Stock).where(Stock.yahoo_symbol.in_(yahoo_symbols))).all()
    stock_by_symbol = {stock.yahoo_symbol: stock for stock in stocks}
    stock_ids = [stock.id for stock in stocks]
    if not stock_ids:
        return []

    cutoff = datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)
    rows = db.execute(
        text(
            """
            SELECT stock_id, close, volume, price_datetime
            FROM (
                SELECT
                    sp.stock_id,
                    sp.close,
                    sp.volume,
                    sp.price_datetime,
                    ROW_NUMBER() OVER (
                        PARTITION BY sp.stock_id ORDER BY sp.price_datetime DESC
                    ) AS rn
                FROM stock_prices sp
                WHERE sp.stock_id = ANY(:stock_ids)
                  AND sp.timeframe = :timeframe
                  AND sp.close IS NOT NULL
                  AND sp.price_datetime < :cutoff
            ) ranked
            WHERE rn <= 2
            ORDER BY stock_id, price_datetime DESC
            """
        ),
        {"stock_ids": stock_ids, "timeframe": "1d", "cutoff": cutoff},
    ).mappings()

    prices_by_stock: dict[int, list[Any]] = {}
    for row in rows:
        prices_by_stock.setdefault(int(row["stock_id"]), []).append(row)

    quotes: list[dict[str, Any]] = []
    for item in items:
        stock = stock_by_symbol.get(item["yahoo_symbol"])
        if stock is None:
            continue
        prices = prices_by_stock.get(stock.id)
        if not prices:
            continue
        latest = float(prices[0]["close"])
        previous = float(prices[1]["close"]) if len(prices) > 1 else latest
        quotes.append(
            _quote_from_price_pair(
                item,
                latest,
                previous,
                prices[0]["volume"],
                record_date=prices[0]["price_datetime"].date(),
            )
        )
    return quotes


def _quotes_for(items: list[dict[str, str]], dataframe: pd.DataFrame) -> list[dict[str, Any]]:
    quotes: list[dict[str, Any]] = []
    for item in items:
        frame = _symbol_frame(dataframe, item["yahoo_symbol"])
        quote = _quote_from_history(item, frame)
        if quote:
            quotes.append(quote)
        else:
            logger.warning("No market quote available for %s", item["yahoo_symbol"])
    return quotes


def _legacy_movers_from_yfinance(history: pd.DataFrame) -> tuple[list[dict[str, Any]], str]:
    movers = _quotes_for(MOVER_UNIVERSE, history)
    if movers:
        return movers, "yfinance"
    return [], "sample_fallback"


def _movers_from_db(db: Session, *, refresh: bool = False) -> tuple[dict[str, Any] | None, str]:
    if refresh:
        payload = refresh_market_movers_cache(db, limit=DEFAULT_MOVER_LIMIT)
    else:
        payload = get_cached_market_movers(db)
        cached_count = len(payload.get("top_gainers") or []) if payload else 0
        if payload is None or cached_count < DEFAULT_MOVER_LIMIT:
            payload = compute_market_movers_from_db(db, limit=DEFAULT_MOVER_LIMIT)
            if int(payload.get("eligible_count") or 0) > 0:
                store_market_movers_cache(db, payload)

    if not payload or int(payload.get("eligible_count") or 0) == 0:
        return None, "database"
    return payload, "database"


def _fast_cold_overview(db: Session | None, now: datetime) -> dict[str, Any]:
    """Return a low-latency snapshot from the analytics cache + sample data.

    Called on cold start so the first page renders in <200 ms instead of 10+ s.
    No yfinance download, no heavy SQL — just two cheap DB reads.
    A background thread running _compute_and_store_overview will overwrite this
    with real index quotes and fresh movers within a few seconds.
    """
    indices = _database_quotes_for(INDICES, db) if db is not None else []
    if not indices:
        indices = _sample_quotes_for(INDICES)

    movers_payload = get_cached_market_movers(db) if db is not None else None
    eligible_count = int(movers_payload.get("eligible_count") or 0) if movers_payload else 0

    if movers_payload and eligible_count > 0:
        top_gainers = movers_payload["top_gainers"][:DEFAULT_MOVER_LIMIT]
        top_losers = movers_payload["top_losers"][:DEFAULT_MOVER_LIMIT]
        volume_shockers = movers_payload["volume_shockers"][:DEFAULT_MOVER_LIMIT]
        most_bought = movers_payload["most_bought"][:4]
        record_date = movers_payload.get("record_date") or _resolve_record_date([*indices, *top_gainers])
        source = "database"
    else:
        top_gainers = []
        top_losers = []
        volume_shockers = []
        most_bought = []
        record_date = _resolve_record_date(indices)
        eligible_count = 0
        source = "sample_fallback"

    payload = {
        "as_of": now,
        "record_date": record_date,
        "source": source,
        "movers_universe_count": eligible_count,
        "indices": indices,
        "most_bought": most_bought,
        "top_gainers": top_gainers,
        "top_losers": top_losers,
        "volume_shockers": volume_shockers,
    }
    _CACHE["overview"] = (now, payload)
    return payload


def _compute_and_store_overview(db: Session | None, now: datetime) -> dict[str, Any]:
    """Fetch index quotes + movers, store result in the in-process cache, and return it."""
    unique_index_symbols = list(dict.fromkeys(item["yahoo_symbol"] for item in INDICES))

    # Try DB first for index quotes so we avoid a blocking yfinance call when local
    # price data is available.
    indices = _database_quotes_for(INDICES, db) if db is not None else []
    source = "database" if indices else "yfinance"

    if not indices:
        try:
            history = _download_history(unique_index_symbols)
        except Exception:
            logger.exception("Failed fetching market overview from yfinance")
            history = pd.DataFrame()
        indices = _quotes_for(INDICES, history)
        source = "yfinance"
    else:
        history = pd.DataFrame()

    if not indices:
        indices = _sample_quotes_for(INDICES)
        source = "sample_fallback"

    movers_payload: dict[str, Any] | None = None
    movers_source = source
    if db is not None:
        movers_payload, movers_source = _movers_from_db(db, refresh=False)

    if movers_payload is None:
        legacy_movers, legacy_source = _legacy_movers_from_yfinance(history)
        if not legacy_movers and db is not None:
            legacy_movers = _database_quotes_for(MOVER_UNIVERSE, db)
            legacy_source = "database"
        if not legacy_movers:
            legacy_movers = _sample_quotes_for(MOVER_UNIVERSE)
            legacy_source = "sample_fallback"
        sorted_by_change = sorted(legacy_movers, key=lambda quote: quote["change_pct"], reverse=True)
        sorted_by_volume = sorted(
            [quote for quote in legacy_movers if quote.get("volume") is not None],
            key=lambda quote: quote["volume"] or 0,
            reverse=True,
        )
        most_bought_symbols = {item["yahoo_symbol"] for item in MOST_BOUGHT}
        most_bought = [quote for quote in legacy_movers if quote["yahoo_symbol"] in most_bought_symbols]
        top_gainers = [quote for quote in sorted_by_change if quote["change_pct"] > 0][:DEFAULT_MOVER_LIMIT]
        top_losers = sorted(
            [quote for quote in legacy_movers if quote["change_pct"] < 0],
            key=lambda quote: quote["change_pct"],
        )[:DEFAULT_MOVER_LIMIT]
        volume_shockers = sorted_by_volume[:DEFAULT_MOVER_LIMIT]
        record_date = _resolve_record_date([*indices, *legacy_movers])
        movers_universe_count = len(legacy_movers)
        final_source = legacy_source
    else:
        top_gainers = movers_payload["top_gainers"]
        top_losers = movers_payload["top_losers"]
        volume_shockers = movers_payload["volume_shockers"]
        most_bought = movers_payload["most_bought"]
        record_date = movers_payload.get("record_date") or _resolve_record_date([*indices, *top_gainers])
        movers_universe_count = int(movers_payload.get("eligible_count") or 0)
        final_source = movers_source

    payload = {
        "as_of": now,
        "record_date": record_date,
        "source": final_source,
        "movers_universe_count": movers_universe_count,
        "indices": indices,
        "most_bought": most_bought[:4],
        "top_gainers": top_gainers[:DEFAULT_MOVER_LIMIT],
        "top_losers": top_losers[:DEFAULT_MOVER_LIMIT],
        "volume_shockers": volume_shockers[:DEFAULT_MOVER_LIMIT],
    }
    _CACHE["overview"] = (now, payload)
    return payload


@timed("market.get_market_overview")
def get_market_overview(db: Session | None = None, refresh: bool = False) -> dict[str, Any]:
    """Return the market overview dict.

    Cache behaviour:
    - Fresh (< 5 min): served directly from in-process cache.
    - Stale (5–30 min): stale data is returned immediately and a background
      thread starts a silent refresh so the next caller gets fresh data.
    - Beyond 30 min / empty / forced refresh: blocks and recomputes synchronously.
    """
    now = datetime.now(UTC)
    cached = _CACHE.get("overview")

    if cached and not refresh:
        cached_at, payload = cached
        age = now - cached_at
        if age < CACHE_FRESH_TTL:
            return payload
        if age < CACHE_STALE_TTL:
            _trigger_background_refresh()
            return payload

    if not refresh:
        # Cold cache (empty or beyond stale window): respond in <200 ms using
        # pre-cached movers and sample/DB index quotes, then let a background
        # thread do the full yfinance download + movers recompute.
        payload = _fast_cold_overview(db, now)
        _trigger_background_refresh()
        return payload

    return _compute_and_store_overview(db, now)
