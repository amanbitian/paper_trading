from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any, Literal

import pandas as pd
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.index_fund import IndexFund, IndexFundPrice
from app.models.stock import Stock
from app.services.index_fund_service import index_prices_to_dataframe
from app.services.market_data_service import DAILY_TIMEFRAME
from app.services.portfolio_service import D


BenchmarkCode = Literal["buy_and_hold", "cash", "nifty50", "nifty500", "sector"]

BENCHMARK_INDEXES: dict[str, dict[str, str]] = {
    "nifty50": {"name": "NIFTY 50", "symbol": "^NSEI"},
    "nifty500": {"name": "NIFTY 500", "symbol": "^CRSLDX"},
}

SECTOR_BENCHMARKS: dict[str, dict[str, str]] = {
    "technology": {"name": "NIFTY IT", "symbol": "^CNXIT"},
    "information technology": {"name": "NIFTY IT", "symbol": "^CNXIT"},
    "financial services": {"name": "NIFTY Financial Services", "symbol": "^CNXFIN"},
    "finance": {"name": "NIFTY Financial Services", "symbol": "^CNXFIN"},
    "banks": {"name": "NIFTY Bank", "symbol": "^NSEBANK"},
    "banking": {"name": "NIFTY Bank", "symbol": "^NSEBANK"},
    "healthcare": {"name": "NIFTY Pharma", "symbol": "^CNXPHARMA"},
    "pharmaceuticals": {"name": "NIFTY Pharma", "symbol": "^CNXPHARMA"},
    "energy": {"name": "NIFTY Energy", "symbol": "^CNXENERGY"},
    "consumer defensive": {"name": "NIFTY FMCG", "symbol": "^CNXFMCG"},
    "consumer staples": {"name": "NIFTY FMCG", "symbol": "^CNXFMCG"},
    "consumer cyclical": {"name": "NIFTY Consumption", "symbol": "^CNXCONSUM"},
    "consumer discretionary": {"name": "NIFTY Consumption", "symbol": "^CNXCONSUM"},
    "industrials": {"name": "NIFTY Infrastructure", "symbol": "^CNXINFRA"},
    "infrastructure": {"name": "NIFTY Infrastructure", "symbol": "^CNXINFRA"},
    "real estate": {"name": "NIFTY Realty", "symbol": "^CNXREALTY"},
    "basic materials": {"name": "NIFTY Commodities", "symbol": "^CNXCMDT"},
    "materials": {"name": "NIFTY Commodities", "symbol": "^CNXCMDT"},
    "communication services": {"name": "NIFTY Services Sector", "symbol": "^CNXSERVICE"},
    "telecom": {"name": "NIFTY Services Sector", "symbol": "^CNXSERVICE"},
}


def _date_index_from_values(values: Any) -> list[date]:
    return [pd.Timestamp(value).date() for value in values]


def _close_series(dataframe: pd.DataFrame) -> pd.Series:
    if dataframe.empty or "close" not in dataframe:
        return pd.Series(dtype=float)
    series = pd.Series(
        pd.to_numeric(dataframe["close"], errors="coerce").values,
        index=pd.Index(_date_index_from_values(dataframe.index), name="date"),
        dtype=float,
    ).dropna()
    return series.groupby(level=0).last().sort_index()


def strategy_equity_series(equity_curve: list[dict[str, Any]]) -> pd.Series:
    rows = [
        (pd.Timestamp(point["date"]).date(), float(point["equity"]))
        for point in equity_curve
        if point.get("date") and point.get("equity") is not None
    ]
    if not rows:
        return pd.Series(dtype=float)
    dates, values = zip(*rows)
    return pd.Series(values, index=pd.Index(dates, name="date"), dtype=float).groupby(level=0).last().sort_index()


def build_cash_curve(strategy_dates: list[date], initial_capital: Decimal) -> list[dict[str, Any]]:
    return [{"date": item.isoformat(), "equity": float(initial_capital)} for item in strategy_dates]


def build_buy_and_hold_curve(
    price_dataframe: pd.DataFrame,
    strategy_dates: list[date],
    initial_capital: Decimal,
) -> list[dict[str, Any]]:
    close = _close_series(price_dataframe)
    if close.empty or not strategy_dates:
        return []
    target_index = pd.Index(strategy_dates, name="date")
    aligned = close.reindex(close.index.union(target_index)).sort_index().ffill().reindex(target_index).dropna()
    if aligned.empty or float(aligned.iloc[0]) == 0:
        return []
    base = float(aligned.iloc[0])
    return [
        {
            "date": item_date.isoformat(),
            "equity": round(float(initial_capital) * float(close_value) / base, 6),
        }
        for item_date, close_value in aligned.items()
    ]


def _pct_return(series: pd.Series) -> Decimal | None:
    if len(series) < 2 or float(series.iloc[0]) == 0:
        return None
    return D((float(series.iloc[-1]) / float(series.iloc[0]) - 1) * 100)


def _round_optional(value: float | None, places: int = 4) -> Decimal | None:
    if value is None or pd.isna(value):
        return None
    return D(round(float(value), places))


def _capture_ratio(strategy_returns: pd.Series, benchmark_returns: pd.Series, *, upside: bool) -> Decimal | None:
    mask = benchmark_returns > 0 if upside else benchmark_returns < 0
    if not bool(mask.any()):
        return None
    strategy_compound = float((1 + strategy_returns[mask]).prod() - 1)
    benchmark_compound = float((1 + benchmark_returns[mask]).prod() - 1)
    if benchmark_compound == 0:
        return None
    return D(round(strategy_compound / benchmark_compound * 100, 4))


def calculate_benchmark_metrics(
    strategy_curve: list[dict[str, Any]],
    benchmark_curve: list[dict[str, Any]],
) -> dict[str, Decimal | None]:
    strategy = strategy_equity_series(strategy_curve)
    benchmark = strategy_equity_series(benchmark_curve)
    strategy_return = _pct_return(strategy) or Decimal("0")
    benchmark_return = _pct_return(benchmark)
    if benchmark.empty or benchmark_return is None:
        return {
            "benchmark_return": None,
            "excess_return": None,
            "alpha": None,
            "beta": None,
            "tracking_error": None,
            "information_ratio": None,
            "upside_capture": None,
            "downside_capture": None,
        }

    aligned = pd.concat([strategy, benchmark], axis=1, join="inner").dropna()
    aligned.columns = ["strategy", "benchmark"]
    excess_return = strategy_return - benchmark_return
    beta = alpha = tracking_error = information_ratio = upside_capture = downside_capture = None

    if len(aligned) >= 3:
        returns = aligned.pct_change().dropna()
        strategy_returns = returns["strategy"]
        benchmark_returns = returns["benchmark"]
        benchmark_var = float(benchmark_returns.var(ddof=0))
        if benchmark_var != 0:
            covariance = float(
                ((strategy_returns - strategy_returns.mean()) * (benchmark_returns - benchmark_returns.mean())).mean()
            )
            beta_float = covariance / benchmark_var
            beta = _round_optional(beta_float)
            alpha = _round_optional((strategy_returns.mean() - beta_float * benchmark_returns.mean()) * 252 * 100)
        active_returns = strategy_returns - benchmark_returns
        active_std = float(active_returns.std(ddof=0))
        if active_std != 0:
            tracking_error = _round_optional(active_std * (252**0.5) * 100)
            information_ratio = _round_optional(active_returns.mean() / active_std * (252**0.5))
        upside_capture = _capture_ratio(strategy_returns, benchmark_returns, upside=True)
        downside_capture = _capture_ratio(strategy_returns, benchmark_returns, upside=False)

    return {
        "benchmark_return": _round_optional(float(benchmark_return)),
        "excess_return": _round_optional(float(excess_return)),
        "alpha": alpha,
        "beta": beta,
        "tracking_error": tracking_error,
        "information_ratio": information_ratio,
        "upside_capture": upside_capture,
        "downside_capture": downside_capture,
    }


def _find_index_fund(db: Session, yahoo_symbol: str, name: str) -> IndexFund | None:
    return db.scalar(
        select(IndexFund)
        .where(
            IndexFund.is_active.is_(True),
            or_(
                IndexFund.yahoo_symbol == yahoo_symbol,
                IndexFund.symbol.ilike(name),
                IndexFund.symbol.ilike(name.replace(" ", "%")),
            ),
        )
        .order_by(IndexFund.id.asc())
        .limit(1)
    )


def _load_index_dataframe(db: Session, fund_id: int, start_date: date, end_date: date) -> pd.DataFrame:
    start_dt = datetime.combine(start_date, time.min, tzinfo=UTC)
    end_dt = datetime.combine(end_date, time.max, tzinfo=UTC)
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
    return index_prices_to_dataframe(prices)


def _sector_definition(stock: Stock | None) -> dict[str, str] | None:
    sector = (stock.sector or "").strip().lower() if stock else ""
    industry = (stock.industry or "").strip().lower() if stock else ""
    for text_value in (sector, industry):
        if not text_value:
            continue
        if text_value in SECTOR_BENCHMARKS:
            return SECTOR_BENCHMARKS[text_value]
        for key, definition in SECTOR_BENCHMARKS.items():
            if key in text_value:
                return definition
    return None


def _benchmark_dataframe(
    db: Session,
    *,
    benchmark_code: str,
    instrument_dataframe: pd.DataFrame,
    stock: Stock | None,
    start_date: date,
    end_date: date,
) -> tuple[pd.DataFrame | None, str | None, str | None, list[str]]:
    warnings: list[str] = []
    if benchmark_code == "buy_and_hold":
        symbol = stock.yahoo_symbol if stock else "selected_instrument"
        name = stock.company_name if stock and stock.company_name else "Buy and hold"
        return instrument_dataframe, symbol, name, warnings
    if benchmark_code == "cash":
        return None, "CASH", "Cash / zero return", warnings
    if benchmark_code == "sector":
        definition = _sector_definition(stock)
        if definition is None:
            return None, None, None, ["No sector benchmark mapping was available for this instrument."]
    else:
        definition = BENCHMARK_INDEXES.get(benchmark_code)
        if definition is None:
            return None, None, None, [f"Unsupported benchmark '{benchmark_code}'."]

    fund = _find_index_fund(db, definition["symbol"], definition["name"])
    if fund is None:
        warnings.append(f"Benchmark {definition['name']} ({definition['symbol']}) is not loaded in index_funds.")
        return None, definition["symbol"], definition["name"], warnings
    dataframe = _load_index_dataframe(db, fund.id, start_date, end_date)
    if dataframe.empty:
        warnings.append(f"Benchmark {definition['name']} ({definition['symbol']}) has no stored prices for this range.")
        return None, definition["symbol"], definition["name"], warnings
    return dataframe, fund.yahoo_symbol, fund.symbol, warnings


def compare_to_benchmark(
    db: Session,
    *,
    benchmark_code: BenchmarkCode | str,
    strategy_equity_curve: list[dict[str, Any]],
    instrument_dataframe: pd.DataFrame,
    initial_capital: Decimal,
    start_date: date,
    end_date: date,
    stock: Stock | None = None,
) -> dict[str, Any]:
    strategy_series = strategy_equity_series(strategy_equity_curve)
    strategy_dates = list(strategy_series.index)
    code = benchmark_code or "buy_and_hold"
    dataframe, symbol, name, warnings = _benchmark_dataframe(
        db,
        benchmark_code=code,
        instrument_dataframe=instrument_dataframe,
        stock=stock,
        start_date=start_date,
        end_date=end_date,
    )

    if code == "cash":
        curve = build_cash_curve(strategy_dates, initial_capital)
    elif dataframe is not None:
        curve = build_buy_and_hold_curve(dataframe, strategy_dates, initial_capital)
        if not curve:
            warnings.append("Benchmark curve could not be aligned to the strategy dates.")
    else:
        curve = []

    metrics = calculate_benchmark_metrics(strategy_equity_curve, curve)
    return {
        "benchmark_code": code,
        "benchmark_symbol": symbol,
        "benchmark_name": name,
        "benchmark_curve": curve,
        "benchmark_warnings": warnings,
        **metrics,
    }
