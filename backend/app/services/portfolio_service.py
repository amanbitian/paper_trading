from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import delete, desc, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from app.models.portfolio import (
    Portfolio,
    PortfolioDailySnapshot,
    PortfolioHolding,
    Transaction,
)
from app.models.stock import Stock
from app.models.user import User
from app.services.market_data_service import get_latest_prices_map
from app.utils.observability import timed

logger = logging.getLogger(__name__)
ZERO = Decimal("0")


def D(value: object) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _lock_portfolio(db: Session, user_id: int, portfolio_id: int) -> Portfolio:
    portfolio = db.scalar(
        select(Portfolio)
        .where(Portfolio.id == portfolio_id, Portfolio.user_id == user_id)
        .with_for_update()
    )
    if portfolio is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found")
    return portfolio


def _paper_cash(portfolio: Portfolio) -> Decimal:
    return D(portfolio.cash_balance) if portfolio.portfolio_type == "paper" else ZERO


def _effective_buy_price(quantity: Decimal, buy_price: Decimal, charges: Decimal) -> Decimal:
    """Fold brokerage/charges into the per-share cost basis.

    A buy of `quantity` shares at `buy_price` that also incurs `charges`
    effectively costs `(quantity * buy_price + charges) / quantity` per
    share. Recording this "all-in" cost (rather than the raw price) means
    `realized_pnl` on a later sell automatically nets out entry charges
    without double-counting them.
    """
    if quantity <= 0:
        return buy_price
    return (quantity * buy_price + charges) / quantity


@timed("portfolio.create_default_portfolios_for_user")
def create_default_portfolios_for_user(db: Session, user_id: int) -> list[Portfolio]:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    defaults = [
        ("Manual Portfolio", "manual", Decimal("0"), ZERO),
        ("Paper Trading", "paper", D(user.starting_cash), D(user.starting_cash)),
    ]
    created_or_existing: list[Portfolio] = []
    for name, portfolio_type, starting_value, cash_balance in defaults:
        existing = db.scalar(
            select(Portfolio).where(
                Portfolio.user_id == user_id,
                Portfolio.portfolio_type == portfolio_type,
                Portfolio.portfolio_name == name,
            )
        )
        if existing:
            if portfolio_type == "paper" and D(existing.cash_balance) == ZERO and cash_balance > ZERO:
                existing.cash_balance = cash_balance
                existing.starting_value = starting_value
            created_or_existing.append(existing)
            continue
        portfolio = Portfolio(
            user_id=user_id,
            portfolio_name=name,
            portfolio_type=portfolio_type,
            base_currency="INR",
            starting_value=starting_value,
            cash_balance=cash_balance,
        )
        db.add(portfolio)
        db.flush()
        created_or_existing.append(portfolio)
    return created_or_existing


def _get_user_portfolio(db: Session, user_id: int, portfolio_id: int) -> Portfolio:
    portfolio = db.scalar(
        select(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.user_id == user_id)
    )
    if portfolio is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found")
    return portfolio


def _cleanup_zero_holdings(db: Session, portfolio_id: int, stock_id: int) -> None:
    holding = db.scalar(
        select(PortfolioHolding).where(
            PortfolioHolding.portfolio_id == portfolio_id,
            PortfolioHolding.stock_id == stock_id,
        )
    )
    if holding is not None and D(holding.quantity) <= 0:
        db.delete(holding)


@timed("portfolio.update_holding_after_buy")
def update_holding_after_buy(
    db: Session, portfolio_id: int, stock_id: int, quantity: Decimal, buy_price: Decimal, charges: Decimal = ZERO
) -> PortfolioHolding:
    """Apply a BUY to a holding, recomputing its weighted-average cost basis.

    When adding to an existing position, the new average price is the
    quantity-weighted blend of the old average and this fill's effective
    price:
        new_avg = (old_qty * old_avg + new_qty * effective_price) / total_qty
    This is the standard weighted-average-cost method — it's what makes
    `realized_pnl` on partial sells well-defined later.
    """
    cost_per_share = _effective_buy_price(quantity, buy_price, charges)
    holding = db.scalar(
        select(PortfolioHolding).where(
            PortfolioHolding.portfolio_id == portfolio_id,
            PortfolioHolding.stock_id == stock_id,
        )
    )
    gross_amount = quantity * cost_per_share
    if holding is None:
        holding = PortfolioHolding(
            portfolio_id=portfolio_id,
            stock_id=stock_id,
            quantity=quantity,
            average_buy_price=cost_per_share,
            total_invested=gross_amount,
            realized_pnl=ZERO,
            last_updated_at=datetime.now(UTC),
        )
        db.add(holding)
    else:
        old_quantity = D(holding.quantity)
        old_average = D(holding.average_buy_price)
        new_quantity = old_quantity + quantity
        if new_quantity > 0:
            holding.average_buy_price = (
                (old_quantity * old_average + quantity * cost_per_share) / new_quantity
            )
        holding.quantity = new_quantity
        holding.total_invested = D(holding.average_buy_price) * new_quantity
        holding.last_updated_at = datetime.now(UTC)
    db.flush()
    return holding


@timed("portfolio.update_holding_after_sell")
def update_holding_after_sell(
    db: Session, portfolio_id: int, stock_id: int, quantity: Decimal, sell_price: Decimal, charges: Decimal = ZERO
) -> tuple[PortfolioHolding, Decimal]:
    """Apply a SELL to a holding and realize P&L on the portion sold.

    Realized P&L for the sold quantity is:
        (sell_price - average_buy_price) * quantity - charges
    The holding's average cost basis is *not* changed by a sell — only its
    quantity shrinks — because weighted-average-cost accounting only
    recomputes the average on new buys. Returns the updated holding and the
    realized P&L from this specific sell (the holding also accumulates it
    into `realized_pnl`).
    """
    holding = db.scalar(
        select(PortfolioHolding).where(
            PortfolioHolding.portfolio_id == portfolio_id,
            PortfolioHolding.stock_id == stock_id,
        )
    )
    if holding is None or D(holding.quantity) < quantity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Insufficient holding quantity",
        )

    avg_price = D(holding.average_buy_price)
    realized_pnl = (sell_price - avg_price) * quantity - charges
    remaining_quantity = D(holding.quantity) - quantity
    holding.quantity = remaining_quantity
    holding.total_invested = avg_price * remaining_quantity
    holding.realized_pnl = D(holding.realized_pnl) + realized_pnl
    holding.last_updated_at = datetime.now(UTC)
    db.flush()
    _cleanup_zero_holdings(db, portfolio_id, stock_id)
    return holding, realized_pnl


@timed("portfolio.add_manual_buy")
def add_manual_buy(
    db: Session,
    *,
    user: User,
    portfolio_id: int,
    stock_id: int,
    quantity: Decimal,
    price: Decimal,
    transaction_date: datetime,
    charges: Decimal = ZERO,
    notes: str | None = None,
    source: str = "manual",
    update_cash: bool = False,
) -> Transaction:
    portfolio = _lock_portfolio(db, user.id, portfolio_id)
    if db.get(Stock, stock_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stock not found")
    gross_amount = quantity * price
    net_amount = gross_amount + charges
    should_update_cash = update_cash or portfolio.portfolio_type == "paper"
    if should_update_cash:
        if _paper_cash(portfolio) < net_amount:
            raise HTTPException(status_code=400, detail="Insufficient paper cash")
        portfolio.cash_balance = _paper_cash(portfolio) - net_amount
    transaction = Transaction(
        user_id=user.id,
        portfolio_id=portfolio_id,
        stock_id=stock_id,
        transaction_type="BUY",
        quantity=quantity,
        price=price,
        gross_amount=gross_amount,
        charges=charges,
        net_amount=net_amount,
        transaction_date=transaction_date,
        source=source,
        notes=notes,
    )
    db.add(transaction)
    update_holding_after_buy(db, portfolio_id, stock_id, quantity, price, charges)
    db.flush()
    return transaction


@timed("portfolio.add_manual_sell")
def add_manual_sell(
    db: Session,
    *,
    user: User,
    portfolio_id: int,
    stock_id: int,
    quantity: Decimal,
    price: Decimal,
    transaction_date: datetime,
    charges: Decimal = ZERO,
    notes: str | None = None,
    source: str = "manual",
    update_cash: bool = False,
) -> Transaction:
    portfolio = _lock_portfolio(db, user.id, portfolio_id)
    gross_amount = quantity * price
    net_amount = gross_amount - charges
    update_holding_after_sell(db, portfolio_id, stock_id, quantity, price, charges)
    should_update_cash = update_cash or portfolio.portfolio_type == "paper"
    if should_update_cash:
        portfolio.cash_balance = _paper_cash(portfolio) + net_amount
    transaction = Transaction(
        user_id=user.id,
        portfolio_id=portfolio_id,
        stock_id=stock_id,
        transaction_type="SELL",
        quantity=quantity,
        price=price,
        gross_amount=gross_amount,
        charges=charges,
        net_amount=net_amount,
        transaction_date=transaction_date,
        source=source,
        notes=notes,
    )
    db.add(transaction)
    db.flush()
    return transaction


@timed("portfolio.calculate_portfolio_value")
def calculate_portfolio_value(db: Session, portfolio_id: int) -> dict:
    """Mark every holding to the latest known price and roll up totals.

    For each holding we look up the latest stored close
    (`get_latest_prices_map`, batched in one query) and fall back to the
    holding's own average buy price if no price data exists yet (so a
    freshly-bought, not-yet-synced stock doesn't show as a 100% loss).

    `total_return_pct` is computed differently per portfolio type:
      - paper portfolios compare total value (positions + cash) against the
        starting cash, since cash balance is part of "your money" here;
      - manual/tracking portfolios (no cash leg) instead compare combined
        realized + unrealized P&L against the amount actually invested.

    Returns a dict with aggregate values (`invested_value`, `market_value`,
    `cash_balance`, `total_value`, `realized_pnl`, `unrealized_pnl`,
    `total_return_pct`) plus a `holdings` list with the same breakdown
    per-position.
    """
    portfolio = db.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    holdings = db.scalars(
        select(PortfolioHolding)
        .where(PortfolioHolding.portfolio_id == portfolio_id, PortfolioHolding.quantity > 0)
        .options(selectinload(PortfolioHolding.stock))
    ).all()
    stock_ids = [holding.stock_id for holding in holdings]
    latest_prices = get_latest_prices_map(db, stock_ids)

    holding_values = []
    invested_value = ZERO
    market_value = ZERO
    realized_pnl = ZERO
    unrealized_pnl = ZERO
    for holding in holdings:
        quantity = D(holding.quantity)
        if quantity <= 0:
            continue
        latest_price = latest_prices.get(holding.stock_id) or D(holding.average_buy_price)
        holding_market_value = quantity * latest_price
        holding_unrealized = (latest_price - D(holding.average_buy_price)) * quantity
        total_invested = D(holding.total_invested)
        return_pct = (holding_unrealized / total_invested * 100) if total_invested else ZERO
        invested_value += total_invested
        market_value += holding_market_value
        realized_pnl += D(holding.realized_pnl)
        unrealized_pnl += holding_unrealized
        holding_values.append(
            {
                "stock_id": holding.stock_id,
                "symbol": holding.stock.symbol if holding.stock else str(holding.stock_id),
                "yahoo_symbol": holding.stock.yahoo_symbol if holding.stock else "",
                "quantity": quantity,
                "average_buy_price": D(holding.average_buy_price),
                "total_invested": total_invested,
                "current_price": latest_price,
                "market_value": holding_market_value,
                "realized_pnl": D(holding.realized_pnl),
                "unrealized_pnl": holding_unrealized,
                "return_pct": return_pct,
            }
        )
    cash_balance = _paper_cash(portfolio)
    total_value = market_value + cash_balance
    starting = D(portfolio.starting_value)
    if portfolio.portfolio_type == "paper" and starting > 0:
        total_return_pct = (total_value - starting) / starting * 100
    elif invested_value > 0:
        total_return_pct = (unrealized_pnl + realized_pnl) / invested_value * 100
    else:
        total_return_pct = ZERO
    return {
        "portfolio_id": portfolio_id,
        "invested_value": invested_value,
        "market_value": market_value,
        "cash_balance": cash_balance,
        "total_value": total_value,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "total_return_pct": total_return_pct,
        "holdings": holding_values,
    }


@timed("portfolio.calculate_unrealized_pnl")
def calculate_unrealized_pnl(db: Session, portfolio_id: int) -> Decimal:
    return D(calculate_portfolio_value(db, portfolio_id)["unrealized_pnl"])


@timed("portfolio.generate_daily_snapshot")
def generate_daily_snapshot(db: Session, portfolio_id: int, *, force: bool = False) -> PortfolioDailySnapshot | None:
    snapshot_date = date.today()
    if not force:
        existing = db.scalar(
            select(PortfolioDailySnapshot).where(
                PortfolioDailySnapshot.portfolio_id == portfolio_id,
                PortfolioDailySnapshot.snapshot_date == snapshot_date,
            )
        )
        if existing is not None:
            return existing

    values = calculate_portfolio_value(db, portfolio_id)
    previous = db.scalar(
        select(PortfolioDailySnapshot)
        .where(
            PortfolioDailySnapshot.portfolio_id == portfolio_id,
            PortfolioDailySnapshot.snapshot_date < snapshot_date,
        )
        .order_by(desc(PortfolioDailySnapshot.snapshot_date))
        .limit(1)
    )
    previous_value = D(previous.total_value) if previous else D(values["total_value"])
    day_pnl = D(values["total_value"]) - previous_value
    stmt = insert(PortfolioDailySnapshot).values(
        portfolio_id=portfolio_id,
        snapshot_date=snapshot_date,
        invested_value=values["invested_value"],
        market_value=values["market_value"],
        cash_balance=values["cash_balance"],
        total_value=values["total_value"],
        realized_pnl=values["realized_pnl"],
        unrealized_pnl=values["unrealized_pnl"],
        day_pnl=day_pnl,
        total_return_pct=values["total_return_pct"],
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_snapshot_portfolio_date",
        set_={
            "invested_value": stmt.excluded.invested_value,
            "market_value": stmt.excluded.market_value,
            "cash_balance": stmt.excluded.cash_balance,
            "total_value": stmt.excluded.total_value,
            "realized_pnl": stmt.excluded.realized_pnl,
            "unrealized_pnl": stmt.excluded.unrealized_pnl,
            "day_pnl": stmt.excluded.day_pnl,
            "total_return_pct": stmt.excluded.total_return_pct,
        },
    ).returning(PortfolioDailySnapshot.id)
    snapshot_id = db.execute(stmt).scalar_one()
    db.flush()
    return db.get(PortfolioDailySnapshot, snapshot_id)
