from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import and_, desc, func, select
from sqlalchemy.orm import Session

from app.models.stock import StockPrice
from app.models.strategy import StrategySignal, StrategySignalOutcome, StrategyTemplate, UserStrategy
from app.services.market_data_service import DAILY_TIMEFRAME
from app.utils.observability import timed


def _signal_date_only(signal_date: datetime) -> date:
    if isinstance(signal_date, date) and not isinstance(signal_date, datetime):
        return signal_date
    return signal_date.date() if hasattr(signal_date, "date") else signal_date


def _trading_dates_after(db: Session, stock_id: int, start: date, count: int) -> list[date]:
    rows = db.scalars(
        select(StockPrice.price_datetime)
        .where(
            StockPrice.stock_id == stock_id,
            StockPrice.timeframe == DAILY_TIMEFRAME,
            func.date(StockPrice.price_datetime) > start,
        )
        .order_by(StockPrice.price_datetime.asc())
        .limit(count)
    ).all()
    return [_signal_date_only(row) for row in rows]


def _close_on_date(db: Session, stock_id: int, target: date) -> Decimal | None:
    row = db.scalar(
        select(StockPrice.close)
        .where(
            StockPrice.stock_id == stock_id,
            StockPrice.timeframe == DAILY_TIMEFRAME,
            func.date(StockPrice.price_datetime) == target,
        )
        .limit(1)
    )
    return Decimal(str(row)) if row is not None else None


def create_signal_outcome_stub(
    db: Session,
    signal_id: int,
    stock_id: int,
    signal_type: str,
    signal_date: datetime,
    signal_price: float | Decimal | None,
) -> StrategySignalOutcome:
    stub = StrategySignalOutcome(
        signal_id=signal_id,
        stock_id=stock_id,
        signal_type=signal_type,
        signal_date=_signal_date_only(signal_date),
        signal_price=Decimal(str(signal_price)) if signal_price is not None else None,
    )
    db.add(stub)
    db.flush()
    return stub


def _return_pct(signal_price: Decimal, future_price: Decimal, signal_type: str) -> Decimal:
    if signal_price == 0:
        return Decimal("0")
    raw = (future_price - signal_price) / signal_price * 100
    return -raw if signal_type == "SELL" else raw


def _is_profitable(return_pct: Decimal) -> bool:
    return return_pct > 0


def _check_stop_hit(
    db: Session,
    stock_id: int,
    signal_date: date,
    stop_price: float | None,
    signal_type: str,
    horizon_days: int = 20,
) -> tuple[bool | None, date | None]:
    if stop_price is None:
        return None, None

    end_dates = _trading_dates_after(db, stock_id, signal_date, horizon_days)
    if not end_dates:
        return None, None
    end_date = end_dates[-1]

    prices = db.execute(
        select(StockPrice.low, StockPrice.high, func.date(StockPrice.price_datetime))
        .where(
            StockPrice.stock_id == stock_id,
            StockPrice.timeframe == DAILY_TIMEFRAME,
            func.date(StockPrice.price_datetime) > signal_date,
            func.date(StockPrice.price_datetime) <= end_date,
        )
        .order_by(StockPrice.price_datetime.asc())
    ).all()

    stop = Decimal(str(stop_price))
    for low, high, price_date in prices:
        bar_date = price_date if isinstance(price_date, date) else _signal_date_only(price_date)
        if signal_type == "BUY" and Decimal(str(low)) <= stop:
            return True, bar_date
        if signal_type == "SELL" and Decimal(str(high)) >= stop:
            return True, bar_date
    return False, None


@timed("signal_outcome.evaluate_pending")
def evaluate_pending_outcomes(db: Session) -> int:
    pending = list(
        db.scalars(
            select(StrategySignalOutcome)
            .where(StrategySignalOutcome.outcome_evaluated_at.is_(None))
            .order_by(StrategySignalOutcome.id.asc())
            .limit(500)
        )
    )
    updated = 0
    today = datetime.now(UTC).date()

    # Batch-load every referenced signal in one query instead of issuing a
    # `db.get()` per outcome — avoids N round-trips for a 500-row page.
    signal_ids = {outcome.signal_id for outcome in pending if outcome.signal_id is not None}
    signals_by_id: dict[int, StrategySignal] = {}
    if signal_ids:
        signals_by_id = {
            signal.id: signal
            for signal in db.scalars(
                select(StrategySignal).where(StrategySignal.id.in_(signal_ids))
            )
        }

    for outcome in pending:
        if outcome.stock_id is None or outcome.signal_price is None:
            outcome.outcome_evaluated_at = datetime.now(UTC)
            updated += 1
            continue

        signal = signals_by_id.get(outcome.signal_id)
        stop_price = None
        if signal and signal.indicators:
            stop_price = signal.indicators.get("stop_price")

        trading_dates = _trading_dates_after(
            db,
            outcome.stock_id,
            outcome.signal_date,
            20,
        )
        if len(trading_dates) < 5 and today <= outcome.signal_date:
            continue

        horizons = {5: trading_dates[4] if len(trading_dates) >= 5 else None,
                    10: trading_dates[9] if len(trading_dates) >= 10 else None,
                    20: trading_dates[19] if len(trading_dates) >= 20 else None}

        for days, target_date in horizons.items():
            if target_date is None or target_date > today:
                continue
            price = _close_on_date(db, outcome.stock_id, target_date)
            if price is None:
                continue
            ret = _return_pct(outcome.signal_price, price, outcome.signal_type)
            setattr(outcome, f"price_{days}d", price)
            setattr(outcome, f"return_{days}d_pct", ret)
            setattr(outcome, f"profitable_{days}d", _is_profitable(ret))

        stop_hit, stop_hit_date = _check_stop_hit(
            db,
            outcome.stock_id,
            outcome.signal_date,
            stop_price,
            outcome.signal_type,
        )
        outcome.stop_hit = stop_hit
        outcome.stop_hit_date = stop_hit_date

        if horizons.get(20) and horizons[20] <= today:
            outcome.outcome_evaluated_at = datetime.now(UTC)
            updated += 1
        elif horizons.get(10) and horizons[10] <= today and not horizons.get(20):
            outcome.outcome_evaluated_at = datetime.now(UTC)
            updated += 1

    if pending:
        db.commit()
    return updated


@timed("signal_outcome.accuracy")
def get_strategy_accuracy(
    db: Session,
    strategy_template_id: int | None = None,
    user_id: int | None = None,
    lookback_days: int = 90,
) -> list[dict]:
    cutoff = datetime.now(UTC).date()
    from datetime import timedelta

    cutoff = cutoff - timedelta(days=lookback_days)

    stmt = (
        select(
            StrategyTemplate.strategy_name,
            StrategyTemplate.strategy_type,
            StrategySignalOutcome.signal_type,
            StrategySignalOutcome.profitable_5d,
            StrategySignalOutcome.profitable_10d,
            StrategySignalOutcome.profitable_20d,
            StrategySignalOutcome.return_5d_pct,
            StrategySignalOutcome.return_10d_pct,
            StrategySignalOutcome.return_20d_pct,
            StrategySignalOutcome.stop_hit,
        )
        .join(StrategySignal, StrategySignal.id == StrategySignalOutcome.signal_id)
        .join(UserStrategy, UserStrategy.id == StrategySignal.user_strategy_id)
        .join(StrategyTemplate, StrategyTemplate.id == UserStrategy.strategy_template_id)
        .where(StrategySignalOutcome.signal_date >= cutoff)
    )
    if strategy_template_id is not None:
        stmt = stmt.where(UserStrategy.strategy_template_id == strategy_template_id)
    if user_id is not None:
        stmt = stmt.where(UserStrategy.user_id == user_id)

    rows = db.execute(stmt).all()
    grouped: dict[str, dict] = {}

    for row in rows:
        key = row.strategy_name
        if key not in grouped:
            grouped[key] = {
                "strategy_name": row.strategy_name,
                "strategy_type": row.strategy_type,
                "total_signals": 0,
                "buy_signals": 0,
                "sell_signals": 0,
                "wins_5d": 0,
                "eval_5d": 0,
                "wins_10d": 0,
                "eval_10d": 0,
                "wins_20d": 0,
                "eval_20d": 0,
                "returns_5d": [],
                "returns_10d": [],
                "returns_20d": [],
                "stop_hits": 0,
                "stop_evaluated": 0,
            }
        bucket = grouped[key]
        bucket["total_signals"] += 1
        if row.signal_type == "BUY":
            bucket["buy_signals"] += 1
        elif row.signal_type == "SELL":
            bucket["sell_signals"] += 1

        for horizon, win_key, eval_key, ret_key in [
            (5, "wins_5d", "eval_5d", "returns_5d"),
            (10, "wins_10d", "eval_10d", "returns_10d"),
            (20, "wins_20d", "eval_20d", "returns_20d"),
        ]:
            profitable = getattr(row, f"profitable_{horizon}d")
            ret = getattr(row, f"return_{horizon}d_pct")
            if profitable is not None:
                bucket[eval_key] += 1
                if profitable:
                    bucket[win_key] += 1
            if ret is not None:
                bucket[ret_key].append(float(ret))

        if row.stop_hit is not None:
            bucket["stop_evaluated"] += 1
            if row.stop_hit:
                bucket["stop_hits"] += 1

    results = []
    for bucket in grouped.values():
        results.append(
            {
                "strategy_name": bucket["strategy_name"],
                "strategy_type": bucket["strategy_type"],
                "total_signals": bucket["total_signals"],
                "buy_signals": bucket["buy_signals"],
                "sell_signals": bucket["sell_signals"],
                "win_rate_5d": round(bucket["wins_5d"] / bucket["eval_5d"] * 100, 2)
                if bucket["eval_5d"]
                else 0.0,
                "win_rate_10d": round(bucket["wins_10d"] / bucket["eval_10d"] * 100, 2)
                if bucket["eval_10d"]
                else 0.0,
                "win_rate_20d": round(bucket["wins_20d"] / bucket["eval_20d"] * 100, 2)
                if bucket["eval_20d"]
                else 0.0,
                "avg_return_5d": round(sum(bucket["returns_5d"]) / len(bucket["returns_5d"]), 4)
                if bucket["returns_5d"]
                else 0.0,
                "avg_return_10d": round(sum(bucket["returns_10d"]) / len(bucket["returns_10d"]), 4)
                if bucket["returns_10d"]
                else 0.0,
                "avg_return_20d": round(sum(bucket["returns_20d"]) / len(bucket["returns_20d"]), 4)
                if bucket["returns_20d"]
                else 0.0,
                "stop_hit_rate": round(bucket["stop_hits"] / bucket["stop_evaluated"] * 100, 2)
                if bucket["stop_evaluated"]
                else 0.0,
            }
        )
    return sorted(results, key=lambda item: item["strategy_name"])
