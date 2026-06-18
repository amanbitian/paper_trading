from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.stock import Stock
from app.services.news_service import list_stock_news, refresh_stock_news
from app.services.stock_brief_service import generate_stock_brief
from app.services.web_backtesting_helpers import get_strategy_template, validate_strategy_parameters
from app.services.web_strategy_lab_helpers import parse_parameters_json, run_preview_signal
from app.web_utils import templates

logger = logging.getLogger(__name__)
timing_logger = logging.getLogger("app.timing")

router = APIRouter(prefix="/web/partials/explore", tags=["web-explore-stock-partials"])


def _log_route(route: str, started_at: float, status: str = "ok") -> None:
    timing_logger.info(
        "operation=web_route route=%s status=%s duration_ms=%.2f",
        route,
        status,
        (time.perf_counter() - started_at) * 1000,
    )


@router.post("/stock-strategy-preview", include_in_schema=False)
async def explore_stock_strategy_preview(
    request: Request,
    db: Session = Depends(get_db),
):
    started_at = time.perf_counter()
    form = await request.form()
    errors: list[str] = []
    preview: dict | None = None
    stock_id_raw = form.get("stock_id")
    template_id_raw = form.get("strategy_template_id")

    try:
        stock_id = int(stock_id_raw) if stock_id_raw else None
        strategy_template_id = int(template_id_raw) if template_id_raw else None
    except ValueError:
        errors.append("Invalid stock or strategy selection.")
        stock_id = None
        strategy_template_id = None

    if not stock_id:
        errors.append("Stock is required for strategy preview.")
    if not strategy_template_id:
        errors.append("Select a strategy template.")

    template = get_strategy_template(db, strategy_template_id) if strategy_template_id else None
    if strategy_template_id and template is None:
        errors.append("Strategy template not found.")

    if not errors and template and stock_id:
        try:
            parameters = dict(template.default_parameters or {})
            errors.extend(validate_strategy_parameters(template.strategy_type, parameters))
            if not errors:
                preview = run_preview_signal(
                    db,
                    stock_id=stock_id,
                    strategy_template_id=template.id,
                    parameters=parameters,
                )
        except Exception as exc:
            logger.exception("explore stock strategy preview failed")
            errors.append(str(exc))

    response = templates.TemplateResponse(
        "partials/stock_strategy_preview_result.html",
        {"request": request, "errors": errors, "preview": preview},
    )
    _log_route("/web/partials/explore/stock-strategy-preview", started_at, "error" if errors else "ok")
    return response


@router.post("/stock-news/{stock_id}/refresh", include_in_schema=False)
async def explore_stock_news_refresh(
    request: Request,
    stock_id: int,
    db: Session = Depends(get_db),
):
    started_at = time.perf_counter()
    stock = db.get(Stock, stock_id)
    if stock is None:
        raise HTTPException(status_code=404, detail="Stock not found")

    refresh_error: str | None = None
    try:
        result = refresh_stock_news(db, stock_id, force=True, limit=8, mode="web")
        news = result.get("news") or list_stock_news(db, stock_id, limit=8)
    except Exception as exc:
        logger.exception("explore stock news refresh failed")
        refresh_error = str(exc)
        result = None
        news = list_stock_news(db, stock_id, limit=8)

    detail = {
        "stock": {
            "id": stock.id,
            "symbol": stock.symbol,
            "company_name": stock.company_name or stock.symbol,
            "yahoo_symbol": stock.yahoo_symbol,
        },
        "news": news,
        "news_refresh": result,
        "news_refresh_error": refresh_error,
    }
    response = templates.TemplateResponse(
        "partials/stock_news.html",
        {"request": request, "detail": detail},
    )
    _log_route("/web/partials/explore/stock-news-refresh", started_at, "error" if refresh_error else "ok")
    return response


@router.post("/stock-brief/{stock_id}", include_in_schema=False)
async def explore_stock_brief_generate(
    request: Request,
    stock_id: int,
    force: bool = False,
    db: Session = Depends(get_db),
):
    started_at = time.perf_counter()
    if db.get(Stock, stock_id) is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Stock not found")

    brief = await generate_stock_brief(db, stock_id, force=force)
    response = templates.TemplateResponse(
        "partials/stock_brief_result.html",
        {"request": request, "brief": brief, "stock_id": stock_id},
    )
    _log_route(f"/web/partials/explore/stock-brief/{stock_id}", started_at, "error" if brief.get("error") else "ok")
    return response
