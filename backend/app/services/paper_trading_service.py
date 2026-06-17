from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.portfolio import PaperOrder, PaperTrade, PortfolioHolding
from app.models.stock import Stock, StockPrice
from app.models.user import User
from app.schemas.paper_trading import PaperOrderCreate
from app.services.charges_service import compute_charges
from app.services.execution_service import compute_execution_price_decimal
from app.services.market_data_service import DAILY_TIMEFRAME, get_latest_price, get_last_price_date, sync_stock_prices
from app.services.portfolio_service import D, add_manual_buy, add_manual_sell, _lock_portfolio, _paper_cash
from app.utils.observability import timed

logger = logging.getLogger(__name__)

ORDER_EXPIRY_DAYS = 30


def _latest_daily_bar(db: Session, stock_id: int) -> StockPrice | None:
    return db.scalar(
        select(StockPrice)
        .where(StockPrice.stock_id == stock_id, StockPrice.timeframe == DAILY_TIMEFRAME)
        .order_by(desc(StockPrice.price_datetime))
        .limit(1)
    )


def _order_expires_at(placed_at: datetime) -> datetime:
    base = placed_at if placed_at.tzinfo else placed_at.replace(tzinfo=UTC)
    return base + timedelta(days=ORDER_EXPIRY_DAYS)


def _match_trigger_price(order: PaperOrder, bar: StockPrice) -> Decimal | None:
    high = D(bar.high if bar.high is not None else bar.close)
    low = D(bar.low if bar.low is not None else bar.close)
    if order.order_type == "LIMIT":
        limit = D(order.limit_price)
        if order.side == "BUY" and low <= limit:
            return limit
        if order.side == "SELL" and high >= limit:
            return limit
        return None
    if order.order_type == "STOP_LOSS":
        stop = D(order.stop_price)
        if order.side == "BUY" and high >= stop:
            return stop
        if order.side == "SELL" and low <= stop:
            return stop
        return None
    return None


def _execute_paper_fill(
    db: Session,
    *,
    user: User,
    order: PaperOrder,
    quoted_price: Decimal,
    apply_slippage_to_fill: bool,
    slippage_bps_override: int | None = None,
    match_reason: str,
) -> None:
    stock = db.get(Stock, order.stock_id)
    exchange = stock.exchange if stock else "NSE"

    if apply_slippage_to_fill:
        execution = compute_execution_price_decimal(
            db,
            order.stock_id,
            quoted_price,
            order.side,
            override_slippage_bps=slippage_bps_override,
        )
        executed_price = execution["executed_price"]
        slippage_bps = execution["slippage_bps"]
        slippage_cost = execution["slippage_cost"]
        quoted = execution["quoted_price"]
    else:
        executed_price = quoted_price
        quoted = quoted_price
        slippage_bps = 0
        slippage_cost = Decimal("0")

    quantity = D(order.quantity)
    trade_value = quantity * executed_price
    charges_breakdown = compute_charges(
        float(trade_value),
        order.side,
        trade_type="delivery",
        exchange=exchange,
    )
    charges = Decimal(str(charges_breakdown["total_charges"]))
    now = datetime.now(UTC)
    price_as_of = get_last_price_date(db, order.stock_id)
    portfolio = _lock_portfolio(db, user.id, order.portfolio_id)

    if order.side == "BUY":
        if _paper_cash(portfolio) < trade_value + charges:
            order.status = "REJECTED"
            order.reason = "Insufficient cash at fill"
            return
        add_manual_buy(
            db,
            user=user,
            portfolio_id=order.portfolio_id,
            stock_id=order.stock_id,
            quantity=quantity,
            price=executed_price,
            transaction_date=now,
            charges=charges,
            notes=order.notes,
            source="paper_trade",
            update_cash=True,
        )
    else:
        holding = db.scalar(
            select(PortfolioHolding).where(
                PortfolioHolding.portfolio_id == order.portfolio_id,
                PortfolioHolding.stock_id == order.stock_id,
            )
        )
        if holding is None or D(holding.quantity) < quantity:
            order.status = "REJECTED"
            order.reason = "Insufficient holding quantity at fill"
            return
        add_manual_sell(
            db,
            user=user,
            portfolio_id=order.portfolio_id,
            stock_id=order.stock_id,
            quantity=quantity,
            price=executed_price,
            transaction_date=now,
            charges=charges,
            notes=order.notes,
            source="paper_trade",
            update_cash=True,
        )

    order.status = "EXECUTED"
    order.executed_at = now
    order.matched_at = now
    order.matched_price = executed_price
    order.executed_price = executed_price
    order.reason = match_reason + (
        f" ({price_as_of.isoformat() if price_as_of else 'unknown date'})"
    )
    db.add(
        PaperTrade(
            order_id=order.id,
            user_id=user.id,
            portfolio_id=order.portfolio_id,
            stock_id=order.stock_id,
            side=order.side,
            quantity=quantity,
            executed_price=executed_price,
            quoted_price=quoted,
            slippage_bps=slippage_bps,
            slippage_cost=slippage_cost,
            trade_value=trade_value,
            charges=charges,
            charges_breakdown=charges_breakdown,
            executed_at=now,
        )
    )


@timed("paper_trading.match_pending_orders")
def match_pending_orders(db: Session, stock_id: int | None = None) -> dict:
    stmt = (
        select(PaperOrder)
        .where(PaperOrder.status == "PENDING", PaperOrder.order_type.in_(("LIMIT", "STOP_LOSS")))
        .order_by(PaperOrder.placed_at.asc())
        .with_for_update()
    )
    if stock_id is not None:
        stmt = stmt.where(PaperOrder.stock_id == stock_id)

    orders = list(db.scalars(stmt))
    filled = 0
    expired = 0
    now = datetime.now(UTC)

    for order in orders:
        user = db.get(User, order.user_id)
        if user is None:
            continue

        expires_at = order.expires_at or _order_expires_at(order.placed_at)
        if now >= expires_at:
            order.status = "EXPIRED"
            order.reason = "Order expired after 30 days without fill"
            expired += 1
            continue

        bar = _latest_daily_bar(db, order.stock_id)
        if bar is None:
            continue

        trigger_price = _match_trigger_price(order, bar)
        if trigger_price is None:
            continue

        apply_slip = order.order_type == "STOP_LOSS"
        _execute_paper_fill(
            db,
            user=user,
            order=order,
            quoted_price=trigger_price,
            apply_slippage_to_fill=apply_slip,
            slippage_bps_override=None,
            match_reason=f"Matched {order.order_type} on daily bar",
        )
        if order.status == "EXECUTED":
            filled += 1

    pending_stmt = select(PaperOrder.id).where(
        PaperOrder.status == "PENDING",
        PaperOrder.order_type.in_(("LIMIT", "STOP_LOSS")),
    )
    if stock_id is not None:
        pending_stmt = pending_stmt.where(PaperOrder.stock_id == stock_id)
    pending_count = len(list(db.scalars(pending_stmt)))
    db.commit()
    return {"filled": filled, "expired": expired, "pending": pending_count}


@timed("paper_trading.place_paper_order")
def place_paper_order(
    db: Session,
    user: User,
    payload: PaperOrderCreate,
    *,
    slippage_bps_override: int | None = None,
) -> PaperOrder:
    portfolio = _lock_portfolio(db, user.id, payload.portfolio_id)
    if portfolio.portfolio_type != "paper":
        raise HTTPException(
            status_code=400,
            detail="Paper orders can only be placed on paper trading portfolios",
        )
    if db.get(Stock, payload.stock_id) is None:
        raise HTTPException(status_code=404, detail="Stock not found")

    now = datetime.now(UTC)
    order = PaperOrder(
        user_id=user.id,
        portfolio_id=payload.portfolio_id,
        stock_id=payload.stock_id,
        order_type=payload.order_type,
        side=payload.side,
        quantity=payload.quantity,
        limit_price=payload.limit_price,
        stop_price=payload.stop_price,
        status="PENDING",
        notes=payload.notes,
        expires_at=_order_expires_at(now) if payload.order_type != "MARKET" else None,
    )
    db.add(order)
    db.flush()

    if payload.order_type != "MARKET":
        order.reason = "Pending — will match on next price sync or manual match"
        db.commit()
        db.refresh(order)
        return order

    latest_price = get_latest_price(db, payload.stock_id)
    if latest_price is None:
        sync_stock_prices(db, payload.stock_id, period="5d", interval="1d", commit=False)
        latest_price = get_latest_price(db, payload.stock_id)
    if latest_price is None:
        order.status = "REJECTED"
        order.reason = "No latest price available"
        db.commit()
        db.refresh(order)
        return order

    _execute_paper_fill(
        db,
        user=user,
        order=order,
        quoted_price=latest_price,
        apply_slippage_to_fill=True,
        slippage_bps_override=slippage_bps_override,
        match_reason="Executed at latest stored close",
    )
    if order.status == "REJECTED":
        db.commit()
        db.refresh(order)
        return order

    db.commit()
    db.refresh(order)
    return order


@timed("paper_trading.cancel_paper_order")
def cancel_paper_order(db: Session, user: User, order_id: int) -> PaperOrder:
    order = db.scalar(
        select(PaperOrder).where(PaperOrder.id == order_id, PaperOrder.user_id == user.id)
    )
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != "PENDING":
        raise HTTPException(status_code=400, detail="Only pending orders can be cancelled")
    order.status = "CANCELLED"
    order.reason = "Cancelled by user"
    db.commit()
    db.refresh(order)
    return order
