from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.portfolio import Portfolio
from app.models.user import User
from app.security import get_current_user
from app.services.portfolio_service import add_manual_buy
from app.services.web_trading_helpers import (
    PORTFOLIO_TYPES,
    build_holdings_rows,
    ensure_default_portfolios,
    list_user_portfolios,
    parse_non_negative_decimal,
    parse_positive_decimal,
    parse_purchase_datetime,
)
from app.web_utils import templates

logger = logging.getLogger(__name__)
timing_logger = logging.getLogger("app.timing")

router = APIRouter(prefix="/web/partials/portfolio", tags=["web-portfolio-partials"])


def _log_route(route: str, started_at: float, status: str = "ok") -> None:
    timing_logger.info(
        "operation=web_route route=%s status=%s duration_ms=%.2f",
        route,
        status,
        (time.perf_counter() - started_at) * 1000,
    )


@router.get("/list", include_in_schema=False)
def portfolio_list(
    request: Request,
    portfolio_type: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        ensure_default_portfolios(db, current_user)
        portfolios = list_user_portfolios(db, current_user.id, portfolio_type=portfolio_type)
        return templates.TemplateResponse(
            "partials/portfolio_list.html",
            {
                "request": request,
                "portfolios": portfolios,
                "filter_type": portfolio_type or "",
            },
        )
    finally:
        _log_route("/web/partials/portfolio/list", started_at)


@router.get("/selector", include_in_schema=False)
def portfolio_selector(
    request: Request,
    name: str = Query(default="portfolio_id"),
    portfolio_type: str | None = Query(default=None),
    selected_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        ensure_default_portfolios(db, current_user)
        portfolios = list_user_portfolios(db, current_user.id, portfolio_type=portfolio_type)
        if selected_id is None and portfolios:
            selected_id = portfolios[0].id
        return templates.TemplateResponse(
            "partials/portfolio_selector.html",
            {
                "request": request,
                "portfolios": portfolios,
                "field_name": name,
                "selected_id": selected_id,
            },
        )
    finally:
        _log_route("/web/partials/portfolio/selector", started_at)


@router.get("/holdings", include_in_schema=False)
def portfolio_holdings(
    request: Request,
    portfolio_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        if not portfolio_id:
            return templates.TemplateResponse(
                "partials/portfolio_holdings.html",
                {"request": request, "rows": [], "portfolio_id": None},
            )
        rows = build_holdings_rows(db, current_user.id, portfolio_id)
        return templates.TemplateResponse(
            "partials/portfolio_holdings.html",
            {
                "request": request,
                "rows": rows,
                "portfolio_id": portfolio_id,
            },
        )
    except Exception:
        logger.exception("portfolio holdings failed portfolio_id=%s", portfolio_id)
        return templates.TemplateResponse(
            "partials/portfolio_holdings.html",
            {
                "request": request,
                "rows": [],
                "portfolio_id": portfolio_id,
                "error_message": "Unable to load holdings.",
            },
        )
    finally:
        _log_route("/web/partials/portfolio/holdings", started_at)


@router.post("/create", include_in_schema=False)
def portfolio_create(
    request: Request,
    portfolio_name: str = Form(default=""),
    portfolio_type: str = Form(default="manual"),
    starting_value: str = Form(default="0"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    logger.info("portfolio create submitted name=%s type=%s", portfolio_name, portfolio_type)
    try:
        clean_name = portfolio_name.strip()
        if len(clean_name) < 2:
            return templates.TemplateResponse(
                "partials/portfolio_create_result.html",
                {
                    "request": request,
                    "success": False,
                    "message": "Portfolio name is required (at least 2 characters).",
                },
                status_code=400,
            )
        clean_type = portfolio_type.strip().lower()
        if clean_type not in PORTFOLIO_TYPES:
            return templates.TemplateResponse(
                "partials/portfolio_create_result.html",
                {
                    "request": request,
                    "success": False,
                    "message": f"Portfolio type must be one of: {', '.join(PORTFOLIO_TYPES)}.",
                },
                status_code=400,
            )
        starting = parse_non_negative_decimal(starting_value, field="Starting cash")
        portfolio = Portfolio(
            user_id=current_user.id,
            portfolio_name=clean_name,
            portfolio_type=clean_type,
            base_currency="INR",
            starting_value=starting,
            cash_balance=starting if clean_type == "paper" else 0,
        )
        db.add(portfolio)
        db.commit()
        db.refresh(portfolio)
        response = templates.TemplateResponse(
            "partials/portfolio_create_result.html",
            {
                "request": request,
                "success": True,
                "message": f"Portfolio '{portfolio.portfolio_name}' created.",
                "portfolio": portfolio,
            },
        )
        response.headers["HX-Trigger"] = "portfolio-created"
        return response
    except ValueError as exc:
        return templates.TemplateResponse(
            "partials/portfolio_create_result.html",
            {"request": request, "success": False, "message": str(exc)},
            status_code=400,
        )
    except Exception:
        logger.exception("portfolio create failed")
        return templates.TemplateResponse(
            "partials/portfolio_create_result.html",
            {
                "request": request,
                "success": False,
                "message": "Portfolio creation failed. Check backend logs.",
            },
            status_code=500,
        )
    finally:
        _log_route("/web/partials/portfolio/create", started_at)


@router.post("/add-holding", include_in_schema=False)
def portfolio_add_holding(
    request: Request,
    portfolio_id: str = Form(default=""),
    stock_id: str = Form(default=""),
    quantity: str = Form(default=""),
    buy_price: str = Form(default=""),
    purchase_date: str = Form(default=""),
    charges: str = Form(default="0"),
    notes: str = Form(default=""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    logger.info("add holding submitted portfolio_id=%s stock_id=%s", portfolio_id, stock_id)
    try:
        if not portfolio_id.strip():
            raise ValueError("Portfolio is required.")
        if not stock_id.strip():
            raise ValueError("Stock is required.")
        parsed_portfolio_id = int(portfolio_id)
        parsed_stock_id = int(stock_id)
        parsed_quantity = parse_positive_decimal(quantity, field="Quantity")
        parsed_price = parse_positive_decimal(buy_price, field="Buy price")
        parsed_charges = parse_non_negative_decimal(charges, field="Charges")
        parsed_date = parse_purchase_datetime(purchase_date)

        transaction = add_manual_buy(
            db,
            user=current_user,
            portfolio_id=parsed_portfolio_id,
            stock_id=parsed_stock_id,
            quantity=parsed_quantity,
            price=parsed_price,
            transaction_date=parsed_date,
            charges=parsed_charges,
            notes=notes.strip() or None,
            source="manual",
        )
        db.commit()
        response = templates.TemplateResponse(
            "partials/add_holding_result.html",
            {
                "request": request,
                "success": True,
                "message": f"Added {transaction.quantity} shares at {transaction.price}.",
            },
        )
        response.headers["HX-Trigger"] = "holding-added"
        return response
    except ValueError as exc:
        return templates.TemplateResponse(
            "partials/add_holding_result.html",
            {"request": request, "success": False, "message": str(exc)},
            status_code=400,
        )
    except HTTPException as exc:
        detail = exc.detail
        message = detail if isinstance(detail, str) else "Unable to add holding."
        return templates.TemplateResponse(
            "partials/add_holding_result.html",
            {"request": request, "success": False, "message": message},
            status_code=exc.status_code,
        )
    except Exception as exc:
        logger.exception("add holding failed")
        message = "Unable to add holding. Check backend logs."
        return templates.TemplateResponse(
            "partials/add_holding_result.html",
            {"request": request, "success": False, "message": message},
            status_code=500,
        )
    finally:
        _log_route("/web/partials/portfolio/add-holding", started_at)
