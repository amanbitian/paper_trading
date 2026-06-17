from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.portfolio import Portfolio
from app.models.user import User
from app.security import get_current_user
from app.services.portfolio_service import calculate_portfolio_value
from app.services.web_analytics_helpers import (
    build_allocation_plotly_json,
    build_concentration_rows,
    build_drawdown_plotly_json,
    build_risk_metrics_cards,
    fetch_portfolio_risk,
)
from app.services.web_trading_helpers import ensure_default_portfolios, list_user_portfolios
from app.web_utils import templates

logger = logging.getLogger(__name__)
timing_logger = logging.getLogger("app.timing")

router = APIRouter(prefix="/web/partials/risk", tags=["web-risk-partials"])


def _log_route(route: str, started_at: float, status: str = "ok") -> None:
    timing_logger.info(
        "operation=web_route route=%s status=%s duration_ms=%.2f",
        route,
        status,
        (time.perf_counter() - started_at) * 1000,
    )


def _get_user_portfolio(db: Session, user_id: int, portfolio_id: int) -> Portfolio:
    portfolio = db.scalar(
        select(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.user_id == user_id)
    )
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return portfolio


@router.get("/metrics", include_in_schema=False)
def risk_metrics_partial(
    request: Request,
    portfolio_id: int = Query(...),
    lookback_days: int = Query(default=252),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        _get_user_portfolio(db, current_user.id, portfolio_id)
        metrics = fetch_portfolio_risk(db, portfolio_id, lookback_days)
        values = calculate_portfolio_value(db, portfolio_id)
        cards = build_risk_metrics_cards(metrics, values)
        return templates.TemplateResponse(
            "partials/risk_metrics.html",
            {
                "request": request,
                "cards": cards,
                "portfolio_id": portfolio_id,
                "lookback_days": lookback_days,
                "error": None,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Risk metrics partial failed")
        return templates.TemplateResponse(
            "partials/risk_metrics.html",
            {
                "request": request,
                "cards": None,
                "portfolio_id": portfolio_id,
                "lookback_days": lookback_days,
                "error": str(exc),
            },
        )
    finally:
        _log_route("/web/partials/risk/metrics", started_at)


@router.get("/allocation", include_in_schema=False)
def risk_allocation_partial(
    request: Request,
    portfolio_id: int = Query(...),
    lookback_days: int = Query(default=252),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        _get_user_portfolio(db, current_user.id, portfolio_id)
        charts = build_allocation_plotly_json(db, portfolio_id)
        return templates.TemplateResponse(
            "partials/risk_allocation.html",
            {
                "request": request,
                "charts": charts,
                "error": None,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Risk allocation partial failed")
        return templates.TemplateResponse(
            "partials/risk_allocation.html",
            {"request": request, "charts": None, "error": str(exc)},
        )
    finally:
        _log_route("/web/partials/risk/allocation", started_at)


@router.get("/drawdown", include_in_schema=False)
def risk_drawdown_partial(
    request: Request,
    portfolio_id: int = Query(...),
    lookback_days: int = Query(default=252),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        _get_user_portfolio(db, current_user.id, portfolio_id)
        figure = build_drawdown_plotly_json(db, portfolio_id, lookback_days)
        metrics = fetch_portfolio_risk(db, portfolio_id, lookback_days)
        dd = metrics.get("drawdown") or {}
        return templates.TemplateResponse(
            "partials/risk_drawdown.html",
            {
                "request": request,
                "figure": figure,
                "drawdown": dd,
                "error": None,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Risk drawdown partial failed")
        return templates.TemplateResponse(
            "partials/risk_drawdown.html",
            {"request": request, "figure": None, "drawdown": {}, "error": str(exc)},
        )
    finally:
        _log_route("/web/partials/risk/drawdown", started_at)


@router.get("/concentration", include_in_schema=False)
def risk_concentration_partial(
    request: Request,
    portfolio_id: int = Query(...),
    lookback_days: int = Query(default=252),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        _get_user_portfolio(db, current_user.id, portfolio_id)
        metrics = fetch_portfolio_risk(db, portfolio_id, lookback_days)
        rows = build_concentration_rows(db, portfolio_id, metrics)
        conc = metrics.get("concentration") or {}
        return templates.TemplateResponse(
            "partials/risk_concentration.html",
            {
                "request": request,
                "rows": rows,
                "concentration": conc,
                "error": None,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Risk concentration partial failed")
        return templates.TemplateResponse(
            "partials/risk_concentration.html",
            {
                "request": request,
                "rows": [],
                "concentration": {},
                "error": str(exc),
            },
        )
    finally:
        _log_route("/web/partials/risk/concentration", started_at)


@router.post("/refresh", include_in_schema=False)
def risk_refresh(
    request: Request,
    portfolio_id: int = Form(...),
    lookback_days: int = Form(default=252),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        _get_user_portfolio(db, current_user.id, portfolio_id)
        metrics = fetch_portfolio_risk(db, portfolio_id, lookback_days)
        values = calculate_portfolio_value(db, portfolio_id)
        cards = build_risk_metrics_cards(metrics, values)
        response = templates.TemplateResponse(
            "partials/risk_refresh_result.html",
            {
                "request": request,
                "cards": cards,
                "refreshed_at": metrics.get("refreshed_at"),
                "error": None,
            },
        )
        response.headers["HX-Trigger"] = "risk-refreshed"
        return response
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Risk refresh failed")
        response = templates.TemplateResponse(
            "partials/risk_refresh_result.html",
            {
                "request": request,
                "cards": None,
                "refreshed_at": None,
                "error": str(exc),
            },
        )
        return response
    finally:
        _log_route("/web/partials/risk/refresh", started_at)
