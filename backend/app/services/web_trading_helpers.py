from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, selectinload

from app.models.portfolio import PaperOrder, Portfolio, PortfolioHolding, Transaction
from app.models.stock import Stock
from app.models.user import User
from app.schemas.paper_trading import PaperOrderCreate
from app.services.charges_service import compute_charges
from app.services.execution_service import compute_execution_price_decimal
from app.services.market_data_service import get_latest_price
from app.services.paper_trading_service import place_paper_order
from app.services.portfolio_service import D, _get_user_portfolio, calculate_portfolio_value, create_default_portfolios_for_user
from app.utils.observability import timed

logger = logging.getLogger(__name__)

PORTFOLIO_TYPES = ("manual", "paper", "sip", "algo")
ORDER_TYPES = ("MARKET", "LIMIT", "STOP_LOSS")


def parse_decimal(value: str | None, *, field: str) -> Decimal:
    if value is None or not str(value).strip():
        raise ValueError(f"{field} is required.")
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a valid number.") from exc
    return parsed


def parse_positive_decimal(value: str | None, *, field: str) -> Decimal:
    parsed = parse_decimal(value, field=field)
    if parsed <= 0:
        raise ValueError(f"{field} must be greater than zero.")
    return parsed


def parse_non_negative_decimal(value: str | None, *, field: str, default: str = "0") -> Decimal:
    raw = value if value is not None and str(value).strip() else default
    parsed = parse_decimal(raw, field=field)
    if parsed < 0:
        raise ValueError(f"{field} cannot be negative.")
    return parsed


def parse_purchase_datetime(value: str | None) -> datetime:
    if not value or not str(value).strip():
        return datetime.now(UTC)
    text = str(value).strip()
    try:
        if len(text) == 10:
            parsed_date = date.fromisoformat(text)
            return datetime.combine(parsed_date, datetime.min.time(), tzinfo=UTC)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except ValueError as exc:
        raise ValueError("Purchase date must be a valid date (YYYY-MM-DD).") from exc


@timed("web.list_user_portfolios")
def list_user_portfolios(
    db: Session,
    user_id: int,
    *,
    portfolio_type: str | None = None,
) -> list[Portfolio]:
    stmt = select(Portfolio).where(Portfolio.user_id == user_id).order_by(Portfolio.id.asc())
    if portfolio_type:
        stmt = stmt.where(Portfolio.portfolio_type == portfolio_type)
    return list(db.scalars(stmt))


def ensure_default_portfolios(db: Session, user: User) -> None:
    create_default_portfolios_for_user(db, user.id)
    db.commit()


@timed("web.holdings_purchase_dates")
def holdings_purchase_dates(db: Session, portfolio_id: int) -> dict[int, date]:
    rows = db.execute(
        select(
            Transaction.stock_id,
            func.min(Transaction.transaction_date).label("first_buy_at"),
        )
        .where(
            Transaction.portfolio_id == portfolio_id,
            Transaction.transaction_type == "BUY",
            Transaction.stock_id.is_not(None),
        )
        .group_by(Transaction.stock_id)
    ).all()
    result: dict[int, date] = {}
    for stock_id, first_buy_at in rows:
        if stock_id is None or first_buy_at is None:
            continue
        result[int(stock_id)] = (
            first_buy_at.date() if isinstance(first_buy_at, datetime) else first_buy_at
        )
    return result


@timed("web.build_holdings_rows")
def build_holdings_rows(db: Session, user_id: int, portfolio_id: int) -> list[dict]:
    _get_user_portfolio(db, user_id, portfolio_id)
    values = calculate_portfolio_value(db, portfolio_id)
    purchase_dates = holdings_purchase_dates(db, portfolio_id)
    holding_rows = values.get("holdings") or []
    stock_ids = [int(row["stock_id"]) for row in holding_rows]
    stocks = (
        {
            stock.id: stock
            for stock in db.scalars(select(Stock).where(Stock.id.in_(stock_ids)))
        }
        if stock_ids
        else {}
    )

    rows: list[dict] = []
    for holding in values.get("holdings") or []:
        stock_id = int(holding["stock_id"])
        stock = stocks.get(stock_id)
        rows.append(
            {
                **holding,
                "company_name": stock.company_name if stock else holding.get("symbol"),
                "exchange": stock.exchange if stock else "",
                "purchase_date": purchase_dates.get(stock_id),
            }
        )
    return rows


@timed("web.build_open_positions_rows")
def build_open_positions_rows(db: Session, user_id: int, portfolio_id: int) -> list[dict]:
    return build_holdings_rows(db, user_id, portfolio_id)


@timed("web.build_order_history_rows")
def build_order_history_rows(
    db: Session,
    user_id: int,
    portfolio_id: int,
    *,
    limit: int = 50,
) -> list[dict]:
    _get_user_portfolio(db, user_id, portfolio_id)
    orders = list(
        db.scalars(
            select(PaperOrder)
            .where(PaperOrder.user_id == user_id, PaperOrder.portfolio_id == portfolio_id)
            .options(selectinload(PaperOrder.stock))
            .order_by(desc(PaperOrder.placed_at))
            .limit(min(max(limit, 1), 100))
        )
    )
    rows: list[dict] = []
    for order in orders:
        stock = order.stock
        qty = D(order.quantity)
        price = D(order.executed_price or order.limit_price or order.stop_price or 0)
        rows.append(
            {
                "id": order.id,
                "placed_at": order.placed_at,
                "symbol": stock.symbol if stock else str(order.stock_id),
                "company_name": stock.company_name if stock else "",
                "exchange": stock.exchange if stock else "",
                "side": order.side,
                "order_type": order.order_type,
                "quantity": qty,
                "price": price,
                "value": qty * price if price else Decimal("0"),
                "status": order.status,
                "reason": order.reason,
                "notes": order.notes,
            }
        )
    return rows


def _holding_quantity(db: Session, portfolio_id: int, stock_id: int) -> Decimal:
    holding = db.scalar(
        select(PortfolioHolding).where(
            PortfolioHolding.portfolio_id == portfolio_id,
            PortfolioHolding.stock_id == stock_id,
        )
    )
    return D(holding.quantity) if holding else Decimal("0")


@timed("web.build_paper_order_preview")
def build_paper_order_preview(
    db: Session,
    user: User,
    *,
    portfolio_id: int | None,
    stock_id: int | None,
    side: str,
    order_type: str,
    quantity_raw: str | None,
    limit_price_raw: str | None = None,
    stop_price_raw: str | None = None,
) -> dict:
    warnings: list[str] = []
    errors: list[str] = []

    if not portfolio_id:
        errors.append("Select a portfolio.")
    if not stock_id:
        errors.append("Select a stock.")

    side_norm = (side or "BUY").strip().upper()
    if side_norm not in {"BUY", "SELL"}:
        errors.append("Side must be BUY or SELL.")

    order_type_norm = (order_type or "MARKET").strip().upper()
    if order_type_norm not in ORDER_TYPES:
        errors.append(f"Order type must be one of: {', '.join(ORDER_TYPES)}.")

    quantity = Decimal("0")
    if not errors:
        try:
            quantity = parse_positive_decimal(quantity_raw, field="Quantity")
        except ValueError as exc:
            errors.append(str(exc))

    portfolio = None
    stock = None
    latest_price = None
    if not errors and portfolio_id and stock_id:
        portfolio = _get_user_portfolio(db, user.id, portfolio_id)
        if portfolio.portfolio_type != "paper":
            errors.append("Paper orders require a paper trading portfolio.")
        stock = db.get(Stock, stock_id)
        if stock is None:
            errors.append("Selected stock was not found.")
        else:
            latest_price = get_latest_price(db, stock_id)
            if latest_price is None and order_type_norm == "MARKET":
                warnings.append("No stored latest price. Sync market data or choose LIMIT/STOP_LOSS.")

    limit_price = None
    stop_price = None
    if not errors and order_type_norm == "LIMIT":
        try:
            limit_price = parse_positive_decimal(limit_price_raw, field="Limit price")
        except ValueError as exc:
            errors.append(str(exc))
    if not errors and order_type_norm == "STOP_LOSS":
        try:
            stop_price = parse_positive_decimal(stop_price_raw, field="Stop price")
        except ValueError as exc:
            errors.append(str(exc))

    quoted_price = latest_price
    if order_type_norm == "LIMIT" and limit_price is not None:
        quoted_price = limit_price
    elif order_type_norm == "STOP_LOSS" and stop_price is not None:
        quoted_price = stop_price

    gross_value = Decimal("0")
    charges = Decimal("0")
    estimated_total = Decimal("0")
    slippage_bps = 0
    executed_estimate = quoted_price

    if not errors and quoted_price is not None and quantity > 0 and stock is not None:
        if order_type_norm == "MARKET":
            execution = compute_execution_price_decimal(
                db,
                stock_id,
                quoted_price,
                side_norm,
            )
            executed_estimate = execution["executed_price"]
            slippage_bps = int(execution.get("slippage_bps") or 0)
        gross_value = quantity * D(executed_estimate)
        charge_breakdown = compute_charges(
            float(gross_value),
            side_norm,
            trade_type="delivery",
            exchange=stock.exchange or "NSE",
        )
        charges = Decimal(str(charge_breakdown["total_charges"]))
        estimated_total = gross_value + charges if side_norm == "BUY" else gross_value - charges

    available_cash = D(portfolio.cash_balance) if portfolio else Decimal("0")
    holding_qty = (
        _holding_quantity(db, portfolio_id, stock_id)
        if portfolio_id and stock_id and not errors
        else Decimal("0")
    )

    if not errors and portfolio and side_norm == "BUY" and estimated_total > available_cash:
        warnings.append("Estimated total exceeds available paper cash.")
    if not errors and side_norm == "SELL" and quantity > holding_qty:
        warnings.append("Sell quantity exceeds current holding.")

    return {
        "errors": errors,
        "warnings": warnings,
        "portfolio": portfolio,
        "stock": stock,
        "side": side_norm,
        "order_type": order_type_norm,
        "quantity": quantity,
        "latest_price": latest_price,
        "quoted_price": quoted_price,
        "executed_estimate": executed_estimate,
        "gross_value": gross_value,
        "charges": charges,
        "estimated_total": estimated_total,
        "available_cash": available_cash,
        "holding_qty": holding_qty,
        "slippage_bps": slippage_bps,
        "limit_price": limit_price,
        "stop_price": stop_price,
    }


@timed("web.submit_paper_order_from_form")
def submit_paper_order_from_form(
    db: Session,
    user: User,
    *,
    portfolio_id: int,
    stock_id: int,
    side: str,
    order_type: str,
    quantity_raw: str,
    limit_price_raw: str | None = None,
    stop_price_raw: str | None = None,
    notes: str | None = None,
) -> PaperOrder:
    preview = build_paper_order_preview(
        db,
        user,
        portfolio_id=portfolio_id,
        stock_id=stock_id,
        side=side,
        order_type=order_type,
        quantity_raw=quantity_raw,
        limit_price_raw=limit_price_raw,
        stop_price_raw=stop_price_raw,
    )
    if preview["errors"]:
        raise HTTPException(status_code=400, detail=" ".join(preview["errors"]))
    if preview["order_type"] == "MARKET" and preview["latest_price"] is None:
        raise HTTPException(
            status_code=400,
            detail="Latest stored price is required for MARKET orders.",
        )

    payload = PaperOrderCreate(
        portfolio_id=portfolio_id,
        stock_id=stock_id,
        order_type=preview["order_type"],
        side=preview["side"],
        quantity=preview["quantity"],
        limit_price=preview["limit_price"],
        stop_price=preview["stop_price"],
        notes=notes.strip() if notes else None,
    )
    order = place_paper_order(db, user, payload)
    db.commit()
    db.refresh(order)
    return order
