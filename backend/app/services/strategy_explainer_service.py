from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from sqlalchemy import desc, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.fundamentals import StockFinancialStatement
from app.models.stock import Stock, StockPrice
from app.models.strategy import StockStrategyExplanation, StrategyTemplate
from app.services.market_data_service import DAILY_TIMEFRAME, prices_to_dataframe
from app.services.strategy_service import get_strategy_instance, parameters_with_point_in_time_context
from app.utils.json_safe import to_json_safe


SOURCE_VERSION = "strategy_explainer_v1"
DEFAULT_TTL_HOURS = 24
DEFAULT_PRICE_ROWS = 420
SUPPORTED_STRATEGY_TYPES = (
    "quality_momentum",
    "sma_crossover",
    "rsi",
    "macd",
    "breakout",
)


def _clean_float(value: Any, decimals: int = 4) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, decimals)


def _fmt_pct(value: Any, decimals: int = 2) -> str:
    number = _clean_float(value, decimals)
    return "-" if number is None else f"{number:.{decimals}f}%"


def _fmt_number(value: Any, decimals: int = 2) -> str:
    number = _clean_float(value, decimals)
    return "-" if number is None else f"{number:,.{decimals}f}"


def _reason(
    label: str,
    status: str,
    value: str,
    threshold: str,
    message: str,
) -> dict[str, str]:
    return {
        "label": label,
        "status": status,
        "value": value,
        "threshold": threshold,
        "message": message,
    }


def _status_positive(condition: bool, *, neutral: bool = False) -> str:
    if neutral:
        return "neutral"
    return "positive" if condition else "negative"


def _load_price_rows(db: Session, stock_id: int, row_limit: int = DEFAULT_PRICE_ROWS) -> list[StockPrice]:
    rows = list(
        db.scalars(
            select(StockPrice)
            .where(StockPrice.stock_id == stock_id, StockPrice.timeframe == DAILY_TIMEFRAME)
            .order_by(desc(StockPrice.price_datetime))
            .limit(row_limit)
        )
    )
    rows.reverse()
    return rows


def _template_map(db: Session, strategy_types: tuple[str, ...]) -> dict[str, StrategyTemplate]:
    rows = list(
        db.scalars(
            select(StrategyTemplate).where(
                StrategyTemplate.is_active.is_(True),
                StrategyTemplate.strategy_type.in_(strategy_types),
            )
        )
    )
    return {row.strategy_type: row for row in rows}


def _statement_values(
    db: Session,
    stock_id: int,
    normalized_fields: tuple[str, ...],
    *,
    as_of_date: date,
    limit: int = 2,
) -> list[StockFinancialStatement]:
    return list(
        db.scalars(
            select(StockFinancialStatement)
            .where(
                StockFinancialStatement.stock_id == stock_id,
                StockFinancialStatement.period_type == "annual",
                StockFinancialStatement.period_end <= as_of_date,
                StockFinancialStatement.normalized_field.in_(normalized_fields),
            )
            .order_by(StockFinancialStatement.period_end.desc())
            .limit(limit)
        )
    )


def _trend_label(rows: list[StockFinancialStatement]) -> tuple[str | None, str | None, date | None]:
    if len(rows) < 2:
        return None, None, rows[0].period_end if rows else None
    latest = float(rows[0].value or 0)
    previous = float(rows[1].value or 0)
    if previous == 0:
        return None, None, rows[0].period_end
    change_pct = ((latest / previous) - 1) * 100
    label = "improving" if change_pct > 0 else "falling" if change_pct < 0 else "flat"
    return label, _fmt_pct(change_pct), rows[0].period_end


def _fundamental_reason_cards(
    db: Session,
    stock_id: int,
    *,
    as_of_date: date,
) -> tuple[list[dict[str, str]], date | None]:
    cards: list[dict[str, str]] = []
    latest_periods: list[date] = []

    roce_rows = _statement_values(db, stock_id, ("roce", "roce_percent", "roce_pct"), as_of_date=as_of_date)
    roce_trend, roce_change, roce_period = _trend_label(roce_rows)
    if roce_period:
        latest_periods.append(roce_period)
    if roce_trend:
        cards.append(
            _reason(
                "ROCE trend",
                "positive" if roce_trend == "improving" else "negative",
                roce_change or "-",
                "YoY > 0%",
                "ROCE improved versus the prior annual period."
                if roce_trend == "improving"
                else "ROCE did not improve versus the prior annual period.",
            )
        )

    debt_rows = _statement_values(db, stock_id, ("borrowings", "total_debt", "debt"), as_of_date=as_of_date)
    debt_trend, debt_change, debt_period = _trend_label(debt_rows)
    if debt_period:
        latest_periods.append(debt_period)
    if debt_trend:
        debt_ok = debt_trend in {"falling", "flat"}
        cards.append(
            _reason(
                "Debt trend",
                "positive" if debt_ok else "negative",
                debt_change or "-",
                "YoY <= 0%",
                "Borrowings are not rising versus the prior annual period."
                if debt_ok
                else "Borrowings increased versus the prior annual period.",
            )
        )

    return cards, max(latest_periods) if latest_periods else None


def _quality_momentum_reasons(
    indicators: dict[str, Any],
    params: dict[str, Any],
    *,
    db: Session,
    stock_id: int,
    as_of_date: date,
) -> tuple[list[dict[str, str]], date | None]:
    momentum = _clean_float(indicators.get("momentum_long_pct"))
    trend = _clean_float(indicators.get("trend_vs_sma_pct"))
    vol = _clean_float(indicators.get("annualized_vol_pct"))
    volume = _clean_float(indicators.get("average_volume"), 0)
    quality = _clean_float(indicators.get("fundamental_quality_score"))
    max_vol = float(params.get("max_annualized_vol_pct", 45))
    min_volume = float(params.get("min_average_volume", 100000))

    cards = [
        _reason(
            "12-1 month momentum",
            _status_positive((momentum or 0) > 0),
            _fmt_pct(momentum),
            "> 0%",
            "Long-term momentum is positive." if (momentum or 0) > 0 else "Long-term momentum is not positive.",
        ),
        _reason(
            "200-DMA trend",
            _status_positive((trend or 0) > 0),
            _fmt_pct(trend),
            "> 0%",
            "Price is above its long-term trend." if (trend or 0) > 0 else "Price is below its long-term trend.",
        ),
        _reason(
            "Volatility",
            "positive" if vol is not None and vol <= max_vol else "warning",
            _fmt_pct(vol),
            f"<= {max_vol:.0f}%",
            "Volatility is within the configured limit."
            if vol is not None and vol <= max_vol
            else "Volatility is elevated versus the configured limit.",
        ),
        _reason(
            "Liquidity",
            "positive" if volume is not None and volume >= min_volume else "warning",
            _fmt_number(volume, 0),
            f">= {min_volume:,.0f}",
            "Average volume clears the liquidity floor."
            if volume is not None and volume >= min_volume
            else "Average volume is below the liquidity floor.",
        ),
    ]
    if quality is not None:
        cards.append(
            _reason(
                "Fundamental quality",
                "positive" if quality > 0 else "negative" if quality < 0 else "neutral",
                _fmt_number(quality, 2),
                "> 0",
                "Historical fundamentals support the signal."
                if quality > 0
                else "Historical fundamentals do not add support.",
            )
        )

    fundamental_cards, latest_period = _fundamental_reason_cards(db, stock_id, as_of_date=as_of_date)
    cards.extend(fundamental_cards)
    return cards, latest_period


def _rsi_reasons(indicators: dict[str, Any], params: dict[str, Any]) -> list[dict[str, str]]:
    rsi = _clean_float(indicators.get("rsi"))
    oversold = float(params.get("oversold", params.get("buy_rsi_below", 35)))
    overbought = float(params.get("overbought", params.get("sell_rsi_above", 65)))
    if rsi is None:
        return []
    status = "positive" if rsi < oversold else "negative" if rsi > overbought else "neutral"
    return [
        _reason(
            "RSI",
            status,
            _fmt_number(rsi, 2),
            f"Buy < {oversold:.0f}; Sell > {overbought:.0f}",
            "RSI is in a mean-reversion buy zone."
            if rsi < oversold
            else "RSI is in an overbought sell zone."
            if rsi > overbought
            else "RSI is neutral.",
        )
    ]


def _sma_reasons(indicators: dict[str, Any]) -> list[dict[str, str]]:
    short = _clean_float(indicators.get("short_sma"))
    long = _clean_float(indicators.get("long_sma"))
    if short is None or long is None:
        return []
    diff_pct = ((short / long) - 1) * 100 if long else 0
    return [
        _reason(
            "SMA spread",
            "positive" if short > long else "negative" if short < long else "neutral",
            _fmt_pct(diff_pct),
            "Short SMA > Long SMA",
            "Short-term trend is above long-term trend."
            if short > long
            else "Short-term trend is below long-term trend."
            if short < long
            else "Short and long trend are aligned.",
        )
    ]


def _macd_reasons(indicators: dict[str, Any]) -> list[dict[str, str]]:
    macd = _clean_float(indicators.get("macd"))
    signal = _clean_float(indicators.get("signal_line"))
    hist = _clean_float(indicators.get("histogram"))
    rsi = _clean_float(indicators.get("rsi"))
    cards: list[dict[str, str]] = []
    if macd is not None and signal is not None:
        cards.append(
            _reason(
                "MACD vs signal",
                "positive" if macd > signal else "negative" if macd < signal else "neutral",
                f"{macd:.2f} / {signal:.2f}",
                "MACD > Signal",
                "MACD is above its signal line." if macd > signal else "MACD is below its signal line.",
            )
        )
    if hist is not None:
        cards.append(
            _reason(
                "MACD histogram",
                "positive" if hist > 0 else "negative" if hist < 0 else "neutral",
                _fmt_number(hist, 2),
                "> 0",
                "Histogram confirms positive momentum." if hist > 0 else "Histogram does not confirm positive momentum.",
            )
        )
    if rsi is not None:
        cards.append(
            _reason(
                "RSI filter",
                "positive" if 40 <= rsi <= 65 else "warning",
                _fmt_number(rsi, 2),
                "40-65 for buy",
                "RSI is in the MACD buy filter range."
                if 40 <= rsi <= 65
                else "RSI is outside the preferred MACD buy filter range.",
            )
        )
    return cards


def _breakout_reasons(indicators: dict[str, Any], params: dict[str, Any]) -> list[dict[str, str]]:
    close = _clean_float(indicators.get("latest_close"))
    required_close = _clean_float(indicators.get("required_close"))
    volume = _clean_float(indicators.get("latest_volume"), 0)
    avg_volume = _clean_float(indicators.get("average_volume"), 0)
    volume_multiplier = float(params.get("volume_multiplier", 1.5))
    cards: list[dict[str, str]] = []
    if close is not None and required_close is not None:
        cards.append(
            _reason(
                "Breakout level",
                "positive" if close > required_close else "neutral",
                _fmt_number(close, 2),
                f"> {_fmt_number(required_close, 2)}",
                "Close cleared the breakout level." if close > required_close else "Close has not cleared the breakout level.",
            )
        )
    if volume is not None and avg_volume is not None:
        required_volume = avg_volume * volume_multiplier
        cards.append(
            _reason(
                "Volume confirmation",
                "positive" if volume > required_volume else "warning",
                _fmt_number(volume, 0),
                f"> {_fmt_number(required_volume, 0)}",
                "Volume confirms the breakout." if volume > required_volume else "Volume does not confirm the breakout.",
            )
        )
    return cards


def _reason_cards_for_strategy(
    strategy_type: str,
    indicators: dict[str, Any],
    params: dict[str, Any],
    *,
    db: Session,
    stock_id: int,
    as_of_date: date,
) -> tuple[list[dict[str, str]], date | None]:
    if strategy_type == "quality_momentum":
        return _quality_momentum_reasons(indicators, params, db=db, stock_id=stock_id, as_of_date=as_of_date)
    if strategy_type == "rsi":
        return _rsi_reasons(indicators, params), None
    if strategy_type == "sma_crossover":
        return _sma_reasons(indicators), None
    if strategy_type == "macd":
        return _macd_reasons(indicators), None
    if strategy_type == "breakout":
        return _breakout_reasons(indicators, params), None
    return [], None


def _headline(signal_type: str, strategy_name: str, reasons: list[dict[str, str]]) -> str:
    positives = [row["label"] for row in reasons if row.get("status") == "positive"]
    negatives = [row["label"] for row in reasons if row.get("status") == "negative"]
    if signal_type == "BUY" and positives:
        return f"BUY: {', '.join(positives[:3])} supportive"
    if signal_type == "SELL" and negatives:
        return f"SELL: {', '.join(negatives[:3])} weakening"
    if signal_type == "HOLD":
        return f"HOLD: {strategy_name} has no actionable threshold break"
    return f"{signal_type}: {strategy_name}"


def _summary(signal_type: str, result_reason: str, reasons: list[dict[str, str]]) -> str:
    positives = [row["label"] for row in reasons if row.get("status") == "positive"]
    negatives = [row["label"] for row in reasons if row.get("status") == "negative"]
    bits: list[str] = []
    if positives:
        bits.append(f"Supportive: {', '.join(positives[:4])}.")
    if negatives:
        bits.append(f"Risks: {', '.join(negatives[:4])}.")
    bits.append(result_reason)
    return " ".join(bit for bit in bits if bit)


def _serialize_explanation(row: StockStrategyExplanation) -> dict[str, Any]:
    now = datetime.now(UTC)
    expires_at = row.expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    confidence = float(row.confidence_score or 0)
    return {
        "id": row.id,
        "stock_id": row.stock_id,
        "strategy_type": row.strategy_type,
        "strategy_name": row.strategy_name,
        "signal_type": row.signal_type,
        "confidence_score": confidence,
        "confidence_pct": max(0, min(100, round(confidence))),
        "headline": row.headline,
        "explanation_summary": row.explanation_summary,
        "reasons": row.reasons_json or [],
        "indicators": row.indicators_json or {},
        "data_quality": row.data_quality_json or {},
        "price_as_of": row.price_as_of,
        "fundamentals_as_of": row.fundamentals_as_of,
        "calculated_at": row.calculated_at,
        "expires_at": row.expires_at,
        "is_stale": bool(expires_at and expires_at <= now),
        "source_version": row.source_version,
    }


def list_stock_strategy_explanations(db: Session, stock_id: int) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(StockStrategyExplanation)
            .where(StockStrategyExplanation.stock_id == stock_id)
            .order_by(StockStrategyExplanation.strategy_name.asc())
        )
    )
    return [_serialize_explanation(row) for row in rows]


def get_strategy_explanation_cache_stats(db: Session) -> dict[str, Any]:
    exchange_rows = db.execute(
        select(
            StockStrategyExplanation.exchange,
            func.count(StockStrategyExplanation.id),
            func.count(func.distinct(StockStrategyExplanation.stock_id)),
        )
        .group_by(StockStrategyExplanation.exchange)
        .order_by(StockStrategyExplanation.exchange.asc())
    ).all()
    stale_rows = db.scalar(
        select(func.count(StockStrategyExplanation.id)).where(
            StockStrategyExplanation.expires_at <= datetime.now(UTC)
        )
    )
    return {
        "total_rows": int(db.scalar(select(func.count(StockStrategyExplanation.id))) or 0),
        "stocks_cached": int(db.scalar(select(func.count(func.distinct(StockStrategyExplanation.stock_id)))) or 0),
        "stale_rows": int(stale_rows or 0),
        "exchange_rows": [
            {
                "exchange": row[0],
                "rows": int(row[1] or 0),
                "stocks_cached": int(row[2] or 0),
            }
            for row in exchange_rows
        ],
    }


def build_strategy_explanation_payload(
    db: Session,
    stock: Stock,
    strategy_type: str,
    *,
    template: StrategyTemplate | None = None,
    ttl_hours: int = DEFAULT_TTL_HOURS,
    price_rows: list[StockPrice] | None = None,
) -> dict[str, Any]:
    rows = price_rows if price_rows is not None else _load_price_rows(db, stock.id)
    dataframe = prices_to_dataframe(rows)
    strategy = get_strategy_instance(strategy_type)
    strategy_name = template.strategy_name if template else strategy.name
    params = dict((template.default_parameters if template else strategy.default_parameters) or {})

    price_as_of = rows[-1].price_datetime if rows else None
    as_of_date = price_as_of.date() if price_as_of else datetime.now(UTC).date()
    if strategy_type == "quality_momentum":
        params = parameters_with_point_in_time_context(
            db,
            strategy_type,
            params,
            stock_id=stock.id,
            as_of_date=as_of_date,
        )

    result = strategy.generate_signal(dataframe, params)
    indicators = dict(result.indicators or {})
    reasons, fundamentals_period = _reason_cards_for_strategy(
        strategy_type,
        indicators,
        params,
        db=db,
        stock_id=stock.id,
        as_of_date=as_of_date,
    )
    fundamentals_as_of = fundamentals_period
    if strategy_type == "quality_momentum" and params.get("fundamental_as_of"):
        try:
            fundamentals_as_of = date.fromisoformat(str(params["fundamental_as_of"]))
        except ValueError:
            fundamentals_as_of = fundamentals_period

    data_quality = {
        "price_rows": len(rows),
        "has_price_data": bool(rows),
        "price_as_of": price_as_of.isoformat() if price_as_of else None,
        "fundamental_source": params.get("fundamental_source") if strategy_type == "quality_momentum" else None,
        "missing_fundamentals": strategy_type == "quality_momentum"
        and params.get("fundamental_quality_score") is None,
    }
    calculated_at = datetime.now(UTC)
    expires_at = calculated_at + timedelta(hours=ttl_hours)
    signal_type = str(result.signal_type or "HOLD").upper()
    confidence = max(0.0, min(100.0, float(result.confidence_score or 0)))

    return {
        "stock_id": stock.id,
        "symbol": stock.symbol,
        "exchange": stock.exchange,
        "strategy_type": strategy_type,
        "strategy_name": strategy_name,
        "signal_type": signal_type,
        "confidence_score": Decimal(str(round(confidence, 2))),
        "headline": _headline(signal_type, strategy_name, reasons),
        "explanation_summary": _summary(signal_type, result.reason or "", reasons),
        "reasons_json": to_json_safe(reasons),
        "indicators_json": to_json_safe(indicators),
        "data_quality_json": to_json_safe(data_quality),
        "price_as_of": price_as_of,
        "fundamentals_as_of": fundamentals_as_of,
        "calculated_at": calculated_at,
        "expires_at": expires_at,
        "source_version": SOURCE_VERSION,
    }


def upsert_strategy_explanation(db: Session, payload: dict[str, Any]) -> None:
    stmt = insert(StockStrategyExplanation).values(payload)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_stock_strategy_explanations_stock_strategy",
        set_={
            "symbol": stmt.excluded.symbol,
            "exchange": stmt.excluded.exchange,
            "strategy_name": stmt.excluded.strategy_name,
            "signal_type": stmt.excluded.signal_type,
            "confidence_score": stmt.excluded.confidence_score,
            "headline": stmt.excluded.headline,
            "explanation_summary": stmt.excluded.explanation_summary,
            "reasons_json": stmt.excluded.reasons_json,
            "indicators_json": stmt.excluded.indicators_json,
            "data_quality_json": stmt.excluded.data_quality_json,
            "price_as_of": stmt.excluded.price_as_of,
            "fundamentals_as_of": stmt.excluded.fundamentals_as_of,
            "calculated_at": stmt.excluded.calculated_at,
            "expires_at": stmt.excluded.expires_at,
            "source_version": stmt.excluded.source_version,
            "updated_at": datetime.now(UTC),
        },
    )
    db.execute(stmt)


def refresh_stock_strategy_explanations(
    db: Session,
    stock: Stock,
    *,
    strategy_types: tuple[str, ...] = SUPPORTED_STRATEGY_TYPES,
    ttl_hours: int = DEFAULT_TTL_HOURS,
    commit: bool = True,
) -> dict[str, Any]:
    templates = _template_map(db, strategy_types)
    price_rows = _load_price_rows(db, stock.id)
    refreshed: list[str] = []
    failed: list[dict[str, str]] = []
    for strategy_type in strategy_types:
        try:
            template = templates.get(strategy_type)
            payload = build_strategy_explanation_payload(
                db,
                stock,
                strategy_type,
                template=template,
                ttl_hours=ttl_hours,
                price_rows=price_rows,
            )
            upsert_strategy_explanation(db, payload)
            refreshed.append(strategy_type)
        except Exception as exc:
            failed.append({"strategy_type": strategy_type, "error": str(exc)})
    if commit:
        db.commit()
    return {
        "stock_id": stock.id,
        "symbol": stock.symbol,
        "refreshed": refreshed,
        "failed": failed,
        "price_rows": len(price_rows),
    }


def refresh_strategy_explanations_for_stocks(
    db: Session,
    *,
    limit: int = 25,
    exchange: str | None = None,
    symbol: str | None = None,
    offset: int | None = None,
    strategy_types: tuple[str, ...] = SUPPORTED_STRATEGY_TYPES,
    ttl_hours: int = DEFAULT_TTL_HOURS,
) -> dict[str, Any]:
    stmt = select(Stock).where(Stock.is_active.is_(True)).order_by(Stock.exchange.asc(), Stock.symbol.asc())
    if exchange:
        stmt = stmt.where(Stock.exchange == exchange.strip().upper())
    if symbol:
        stmt = stmt.where(Stock.symbol == symbol.strip().upper())
    if offset:
        stmt = stmt.offset(offset)
    if limit:
        stmt = stmt.limit(limit)
    stocks = list(db.scalars(stmt))

    results = []
    for stock in stocks:
        results.append(
            refresh_stock_strategy_explanations(
                db,
                stock,
                strategy_types=strategy_types,
                ttl_hours=ttl_hours,
                commit=True,
            )
        )
    return {
        "selected": len(stocks),
        "results": results,
        "refreshed": sum(len(row["refreshed"]) for row in results),
        "failed": sum(len(row["failed"]) for row in results),
    }
