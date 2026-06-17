from __future__ import annotations

from decimal import Decimal

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.stock import StockPrice
from app.services.market_data_service import DAILY_TIMEFRAME


def get_default_slippage_bps(avg_daily_volume: float) -> int:
    if avg_daily_volume > 1_000_000:
        return 5
    if avg_daily_volume > 100_000:
        return 10
    return 20


def apply_slippage(price: float, side: str, slippage_bps: int = 10) -> float:
    multiplier = slippage_bps / 10_000
    if side.upper() == "BUY":
        return price * (1 + multiplier)
    return price * (1 - multiplier)


def _avg_daily_volume(db: Session, stock_id: int, lookback: int = 20) -> float:
    rows = list(
        db.scalars(
            select(StockPrice.volume)
            .where(StockPrice.stock_id == stock_id, StockPrice.timeframe == DAILY_TIMEFRAME)
            .order_by(desc(StockPrice.price_datetime))
            .limit(lookback)
        )
    )
    volumes = [float(v) for v in rows if v]
    if not volumes:
        return 0.0
    return sum(volumes) / len(volumes)


def compute_execution_price(
    db: Session,
    stock_id: int,
    quoted_price: float,
    side: str,
    override_slippage_bps: int | None = None,
) -> dict:
    quoted = float(quoted_price)
    bps = override_slippage_bps if override_slippage_bps is not None else get_default_slippage_bps(
        _avg_daily_volume(db, stock_id)
    )
    executed = apply_slippage(quoted, side, bps)
    slippage_cost = abs(executed - quoted)
    return {
        "quoted_price": round(quoted, 4),
        "slippage_bps": int(bps),
        "executed_price": round(executed, 4),
        "slippage_cost": round(slippage_cost, 4),
    }


def compute_execution_price_decimal(
    db: Session,
    stock_id: int,
    quoted_price: Decimal,
    side: str,
    override_slippage_bps: int | None = None,
) -> dict:
    result = compute_execution_price(
        db,
        stock_id,
        float(quoted_price),
        side,
        override_slippage_bps=override_slippage_bps,
    )
    return {
        "quoted_price": Decimal(str(result["quoted_price"])),
        "slippage_bps": result["slippage_bps"],
        "executed_price": Decimal(str(result["executed_price"])),
        "slippage_cost": Decimal(str(result["slippage_cost"])),
    }
