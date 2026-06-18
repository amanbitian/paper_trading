from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.security import get_current_user
from app.services.ai_action_log_service import list_ai_action_logs
from app.services.web_ai_think_tank_helpers import (
    PAGE_DISCLAIMER,
    build_ai_analysis_request_from_form,
    build_portfolio_context,
    build_prompt_preview_context,
    build_stock_context,
    execute_analysis,
    fetch_model_status,
    http_error_message,
    optional_int,
    shape_analysis_view,
    validate_run_request,
    validation_error_view,
)
from app.services.web_backtesting_helpers import search_backtest_instruments
from app.services.web_trading_helpers import ensure_default_portfolios, list_user_portfolios
from app.web_utils import templates

logger = logging.getLogger(__name__)
timing_logger = logging.getLogger("app.timing")

router = APIRouter(prefix="/web/partials/ai-think-tank", tags=["web-ai-think-tank-partials"])


def _log_route(route: str, started_at: float, status: str = "ok") -> None:
    timing_logger.info(
        "operation=web_route route=%s status=%s duration_ms=%.2f",
        route,
        status,
        (time.perf_counter() - started_at) * 1000,
    )


@router.get("/model-status", include_in_schema=False)
async def ai_model_status_partial(
    request: Request,
    model: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    started_at = time.perf_counter()
    error_message = None
    status_payload: dict[str, Any] = {}
    try:
        status_payload = await fetch_model_status(db, model=model)
    except Exception as exc:
        logger.exception("ai-think-tank model-status failed")
        error_message = http_error_message(exc)
    response = templates.TemplateResponse(
        "partials/ai_model_status.html",
        {
            "request": request,
            "status": status_payload,
            "error_message": error_message,
            "selected_model": model or status_payload.get("default_model"),
        },
    )
    _log_route("/web/partials/ai-think-tank/model-status", started_at, "error" if error_message else "ok")
    return response


@router.get("/instrument-search", include_in_schema=False)
def ai_instrument_search(
    request: Request,
    query: str = Query(default=""),
    exchange: str | None = Query(default=None, alias="search_exchange"),
    limit: int = Query(default=12, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    clean_query = (query or "").strip()
    try:
        results, search_mode, latest_prices = search_backtest_instruments(
            db,
            query=clean_query,
            exchange=exchange,
            universe_type="stock",
            limit=limit,
        )
        response = templates.TemplateResponse(
            "partials/strategy_lab_search_results.html",
            {
                "request": request,
                "query": clean_query,
                "universe_type": "stock",
                "results": results,
                "search_mode": search_mode,
                "latest_prices": latest_prices,
            },
        )
        _log_route("/web/partials/ai-think-tank/instrument-search", started_at)
        return response
    except Exception as exc:
        logger.exception("ai-think-tank instrument-search failed")
        _log_route("/web/partials/ai-think-tank/instrument-search", started_at, "error")
        return templates.TemplateResponse(
            "partials/strategy_lab_search_results.html",
            {
                "request": request,
                "query": clean_query,
                "universe_type": "stock",
                "results": [],
                "search_mode": "error",
                "latest_prices": {},
                "error_message": http_error_message(exc),
            },
        )


@router.get("/stock-context", include_in_schema=False)
def ai_stock_context(
    request: Request,
    stock_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        context = build_stock_context(db, stock_id)
        response = templates.TemplateResponse(
            "partials/ai_stock_context.html",
            {"request": request, "context": context},
        )
        _log_route("/web/partials/ai-think-tank/stock-context", started_at)
        return response
    except Exception as exc:
        logger.exception("ai-think-tank stock-context failed")
        _log_route("/web/partials/ai-think-tank/stock-context", started_at, "error")
        return templates.TemplateResponse(
            "partials/ai_stock_context.html",
            {"request": request, "context": {"error": http_error_message(exc)}},
        )


@router.get("/portfolio-context", include_in_schema=False)
def ai_portfolio_context(
    request: Request,
    portfolio_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        context = build_portfolio_context(db, portfolio_id, current_user.id)
        response = templates.TemplateResponse(
            "partials/ai_portfolio_context.html",
            {"request": request, "context": context},
        )
        _log_route("/web/partials/ai-think-tank/portfolio-context", started_at)
        return response
    except Exception as exc:
        logger.exception("ai-think-tank portfolio-context failed")
        _log_route("/web/partials/ai-think-tank/portfolio-context", started_at, "error")
        return templates.TemplateResponse(
            "partials/ai_portfolio_context.html",
            {"request": request, "context": {"error": http_error_message(exc)}},
        )


@router.get("/prompt-preview", include_in_schema=False)
def ai_prompt_preview(
    request: Request,
    mode: str = Query(default="signal_synthesizer"),
    model: str | None = Query(default=None),
    stock_id: str | None = Query(default=None),
    portfolio_id: str | None = Query(default=None),
    user_prompt: str | None = Query(default=None),
    backtest_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()

    def _safe_int(value: str | None, label: str) -> int | None:
        try:
            return optional_int(value, label)
        except ValueError:
            return None

    parsed_stock_id = _safe_int(stock_id, "stock_id")
    parsed_portfolio_id = _safe_int(portfolio_id, "portfolio_id")
    parsed_backtest_id = _safe_int(backtest_id, "backtest_id")
    stock_ctx = build_stock_context(db, parsed_stock_id)
    portfolio_ctx = build_portfolio_context(db, parsed_portfolio_id, current_user.id)
    preview = build_prompt_preview_context(
        mode=mode,
        model=model,
        stock_context=stock_ctx,
        portfolio_context=portfolio_ctx,
        extra={
            "user_prompt": user_prompt,
            "backtest_id": parsed_backtest_id,
        },
    )
    response = templates.TemplateResponse(
        "partials/ai_prompt_preview.html",
        {"request": request, "preview": preview, "mode": mode},
    )
    _log_route("/web/partials/ai-think-tank/prompt-preview", started_at)
    return response


@router.get("/activity-log", include_in_schema=False)
def ai_activity_log(
    request: Request,
    limit: int = Query(default=80, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    error_message = None
    rows: list[dict[str, Any]] = []
    try:
        rows = list_ai_action_logs(db, user_id=current_user.id, limit=limit)
    except Exception as exc:
        logger.exception("ai-think-tank activity-log failed")
        error_message = http_error_message(exc)
    response = templates.TemplateResponse(
        "partials/ai_activity_log.html",
        {"request": request, "rows": rows, "error_message": error_message, "limit": limit},
    )
    _log_route("/web/partials/ai-think-tank/activity-log", started_at, "error" if error_message else "ok")
    return response


@router.get("/analysis-history", include_in_schema=False)
def ai_analysis_history(
    request: Request,
    limit: int = Query(default=40, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    error_message = None
    rows: list[dict[str, Any]] = []
    try:
        rows = list_ai_action_logs(db, user_id=current_user.id, limit=limit)
    except Exception as exc:
        logger.exception("ai-think-tank analysis-history failed")
        error_message = http_error_message(exc)
    response = templates.TemplateResponse(
        "partials/ai_analysis_history.html",
        {"request": request, "rows": rows, "error_message": error_message, "limit": limit},
    )
    _log_route("/web/partials/ai-think-tank/analysis-history", started_at, "error" if error_message else "ok")
    return response


@router.post("/run-analysis", include_in_schema=False)
async def ai_run_analysis(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    form = await request.form()
    payload, parse_errors = build_ai_analysis_request_from_form(form)
    mode = payload.get("mode") or ""
    prompt_text = payload.get("user_prompt") or ""

    logger.info(
        "ai_think_tank.run_analysis mode=%s model=%s portfolio_id=%s stock_id=%s backtest_id=%s prompt_len=%s",
        mode,
        payload.get("model"),
        payload.get("portfolio_id"),
        payload.get("stock_id"),
        payload.get("backtest_id"),
        len(prompt_text),
    )

    if parse_errors:
        logger.warning(
            "ai_think_tank.validation_failed mode=%s errors=%s raw_form_keys=%s",
            mode,
            parse_errors,
            list(form.keys()),
        )
        view = validation_error_view(mode, parse_errors)
        _log_route("/web/partials/ai-think-tank/run-analysis", started_at, "parse_error")
        return templates.TemplateResponse(
            "partials/ai_analysis_result.html",
            {"request": request, "result": view, "duration_ms": (time.perf_counter() - started_at) * 1000},
        )

    validation_errors = validate_run_request(
        mode=mode,
        model=payload.get("model"),
        stock_id=payload.get("stock_id"),
        symbol=payload.get("symbol"),
        portfolio_id=payload.get("portfolio_id"),
        user_prompt=payload.get("user_prompt"),
        backtest_id=payload.get("backtest_id"),
        action=payload.get("action"),
        quantity=payload.get("quantity"),
        price=payload.get("price"),
    )
    if validation_errors:
        logger.warning(
            "ai_think_tank.validation_failed mode=%s errors=%s raw_form_keys=%s",
            mode,
            validation_errors,
            list(form.keys()),
        )
        view = validation_error_view(mode, validation_errors)
        _log_route("/web/partials/ai-think-tank/run-analysis", started_at, "validation")
        return templates.TemplateResponse(
            "partials/ai_analysis_result.html",
            {"request": request, "result": view, "duration_ms": (time.perf_counter() - started_at) * 1000},
        )

    try:
        logger.info("ai_think_tank.context_ready mode=%s — calling model", mode)
        raw = await execute_analysis(
            db,
            current_user,
            mode=mode,
            model=payload.get("model"),
            stock_id=payload.get("stock_id"),
            symbol=payload.get("symbol"),
            portfolio_id=payload.get("portfolio_id"),
            user_prompt=payload.get("user_prompt"),
            backtest_id=payload.get("backtest_id"),
            action=payload.get("action"),
            quantity=payload.get("quantity"),
            price=payload.get("price"),
            notes=payload.get("notes"),
        )
        view = shape_analysis_view(
            mode,
            raw,
            user_prompt=payload.get("user_prompt"),
        )
        status = "error" if not view["ok"] else "ok"
        logger.info("ai_think_tank.run_analysis_complete mode=%s status=%s", mode, status)
        _log_route("/web/partials/ai-think-tank/run-analysis", started_at, status)
        return templates.TemplateResponse(
            "partials/ai_analysis_result.html",
            {
                "request": request,
                "result": view,
                "duration_ms": (time.perf_counter() - started_at) * 1000,
            },
        )
    except Exception as exc:
        logger.exception("ai-think-tank run-analysis failed mode=%s", mode)
        _log_route("/web/partials/ai-think-tank/run-analysis", started_at, "error")
        message = http_error_message(exc)
        if "not JSON serializable" in message or "json serializable" in message.lower():
            message = (
                "Could not run screener. The result contained unsupported data types. "
                "This has been logged."
            )
        elif "ollama" in message.lower() or "model" in message.lower() and "unavailable" in message.lower():
            message = "AI model service is unavailable. Start the local model server or choose another model."
        view = shape_analysis_view(
            mode,
            {"error": message, "disclaimer": PAGE_DISCLAIMER},
        )
        return templates.TemplateResponse(
            "partials/ai_analysis_result.html",
            {
                "request": request,
                "result": view,
                "duration_ms": (time.perf_counter() - started_at) * 1000,
            },
            status_code=500 if isinstance(exc, HTTPException) and exc.status_code >= 500 else 200,
        )
