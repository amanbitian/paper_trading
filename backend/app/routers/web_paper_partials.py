from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.security import get_current_user
from app.services.web_trading_helpers import (
    ORDER_TYPES,
    build_open_positions_rows,
    build_order_history_rows,
    build_paper_order_preview,
    ensure_default_portfolios,
    submit_paper_order_from_form,
)
from app.web_utils import templates

logger = logging.getLogger(__name__)
timing_logger = logging.getLogger("app.timing")

router = APIRouter(prefix="/web/partials/paper-trading", tags=["web-paper-partials"])


def _log_route(route: str, started_at: float) -> None:
    timing_logger.info(
        "operation=web_route route=%s status=ok duration_ms=%.2f",
        route,
        (time.perf_counter() - started_at) * 1000,
    )


@router.get("/order-preview", include_in_schema=False)
def paper_order_preview(
    request: Request,
    portfolio_id: int | None = Query(default=None),
    stock_id: int | None = Query(default=None),
    side: str = Query(default="BUY"),
    order_type: str = Query(default="MARKET"),
    quantity: str = Query(default="1"),
    limit_price: str | None = Query(default=None),
    stop_price: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        preview = build_paper_order_preview(
            db,
            current_user,
            portfolio_id=portfolio_id,
            stock_id=stock_id,
            side=side,
            order_type=order_type,
            quantity_raw=quantity,
            limit_price_raw=limit_price,
            stop_price_raw=stop_price,
        )
        return templates.TemplateResponse(
            "partials/order_preview.html",
            {"request": request, "preview": preview, "order_types": ORDER_TYPES},
        )
    except Exception:
        logger.exception("paper order preview failed")
        return templates.TemplateResponse(
            "partials/order_preview.html",
            {
                "request": request,
                "preview": {"errors": ["Unable to build order preview."], "warnings": []},
                "order_types": ORDER_TYPES,
            },
        )
    finally:
        _log_route("/web/partials/paper-trading/order-preview", started_at)


@router.post("/submit-order", include_in_schema=False)
def paper_submit_order(
    request: Request,
    portfolio_id: str = Form(default=""),
    stock_id: str = Form(default=""),
    side: str = Form(default="BUY"),
    order_type: str = Form(default="MARKET"),
    quantity: str = Form(default=""),
    limit_price: str = Form(default=""),
    stop_price: str = Form(default=""),
    notes: str = Form(default=""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    logger.info(
        "paper order submit portfolio_id=%s stock_id=%s side=%s type=%s",
        portfolio_id,
        stock_id,
        side,
        order_type,
    )
    try:
        if not portfolio_id.strip():
            raise ValueError("Portfolio is required.")
        if not stock_id.strip():
            raise ValueError("Stock is required.")
        order = submit_paper_order_from_form(
            db,
            current_user,
            portfolio_id=int(portfolio_id),
            stock_id=int(stock_id),
            side=side,
            order_type=order_type,
            quantity_raw=quantity,
            limit_price_raw=limit_price or None,
            stop_price_raw=stop_price or None,
            notes=notes,
        )
        tone = "success" if order.status == "EXECUTED" else "warning"
        response = templates.TemplateResponse(
            "partials/order_submit_result.html",
            {
                "request": request,
                "success": order.status in {"EXECUTED", "PENDING"},
                "tone": tone,
                "message": f"Order {order.status}: {order.reason or 'Submitted.'}",
                "order": order,
            },
        )
        response.headers["HX-Trigger"] = "paper-order-submitted"
        return response
    except ValueError as exc:
        return templates.TemplateResponse(
            "partials/order_submit_result.html",
            {
                "request": request,
                "success": False,
                "tone": "danger",
                "message": str(exc),
            },
            status_code=400,
        )
    except HTTPException as exc:
        detail = exc.detail
        message = detail if isinstance(detail, str) else "Order submission failed."
        return templates.TemplateResponse(
            "partials/order_submit_result.html",
            {
                "request": request,
                "success": False,
                "tone": "danger",
                "message": message,
            },
            status_code=exc.status_code,
        )
    except Exception as exc:
        logger.exception("paper order submit failed")
        message = "Order submission failed. Check backend logs."
        return templates.TemplateResponse(
            "partials/order_submit_result.html",
            {
                "request": request,
                "success": False,
                "tone": "danger",
                "message": message,
            },
            status_code=500,
        )
    finally:
        _log_route("/web/partials/paper-trading/submit-order", started_at)


@router.get("/open-positions", include_in_schema=False)
def paper_open_positions(
    request: Request,
    portfolio_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        if not portfolio_id:
            return templates.TemplateResponse(
                "partials/open_positions.html",
                {"request": request, "rows": [], "portfolio_id": None},
            )
        rows = build_open_positions_rows(db, current_user.id, portfolio_id)
        return templates.TemplateResponse(
            "partials/open_positions.html",
            {"request": request, "rows": rows, "portfolio_id": portfolio_id},
        )
    except Exception:
        logger.exception("open positions failed portfolio_id=%s", portfolio_id)
        return templates.TemplateResponse(
            "partials/open_positions.html",
            {
                "request": request,
                "rows": [],
                "portfolio_id": portfolio_id,
                "error_message": "Unable to load open positions.",
            },
        )
    finally:
        _log_route("/web/partials/paper-trading/open-positions", started_at)


@router.get("/order-history", include_in_schema=False)
def paper_order_history(
    request: Request,
    portfolio_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        if not portfolio_id:
            return templates.TemplateResponse(
                "partials/order_history.html",
                {"request": request, "rows": [], "portfolio_id": None},
            )
        rows = build_order_history_rows(db, current_user.id, portfolio_id, limit=limit)
        return templates.TemplateResponse(
            "partials/order_history.html",
            {"request": request, "rows": rows, "portfolio_id": portfolio_id},
        )
    except Exception:
        logger.exception("order history failed portfolio_id=%s", portfolio_id)
        return templates.TemplateResponse(
            "partials/order_history.html",
            {
                "request": request,
                "rows": [],
                "portfolio_id": portfolio_id,
                "error_message": "Unable to load order history.",
            },
        )
    finally:
        _log_route("/web/partials/paper-trading/order-history", started_at)
