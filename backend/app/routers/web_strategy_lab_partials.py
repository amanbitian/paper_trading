from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.security import get_current_user
from app.services.web_backtesting_helpers import (
    PARAMETER_CONFIG,
    get_strategy_template,
    search_backtest_instruments,
    validate_strategy_parameters,
)
from app.services.web_strategy_lab_helpers import (
    build_stock_context,
    http_error_message,
    list_recent_signals,
    list_user_strategy_models,
    parse_parameters_json,
    parse_risk_per_trade,
    run_create_user_strategy,
    run_generate_signal,
    run_preview_signal,
    serialize_activity_log,
    serialize_user_strategy_rows,
    validate_create_strategy_form,
    validate_generate_signal_form,
)
from app.web_utils import templates

logger = logging.getLogger(__name__)
timing_logger = logging.getLogger("app.timing")

router = APIRouter(prefix="/web/partials/strategy-lab", tags=["web-strategy-lab-partials"])


def _log_route(route: str, started_at: float, status: str = "ok") -> None:
    timing_logger.info(
        "operation=web_route route=%s status=%s duration_ms=%.2f",
        route,
        status,
        (time.perf_counter() - started_at) * 1000,
    )


@router.get("/instrument-search", include_in_schema=False)
def instrument_search(
    request: Request,
    query: str = Query(default=""),
    exchange: str | None = Query(default=None, alias="search_exchange"),
    universe_type: str | None = Query(default="stock", alias="instrument_type"),
    index_membership: str | None = Query(default=None, alias="search_index_code"),
    search_category: str | None = Query(default=None),
    limit: int = Query(default=12, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        clean_query = (query or "").strip()
        resolved_universe = (universe_type or "stock").strip().lower()
        results, search_mode, latest_prices = search_backtest_instruments(
            db,
            query=clean_query,
            exchange=exchange,
            universe_type=resolved_universe,
            index_membership=index_membership,
            category=search_category,
            limit=limit,
        )
        logger.info(
            "strategy_lab.instrument_search query=%s exchange=%s results=%s mode=%s",
            clean_query,
            exchange or "",
            len(results),
            search_mode,
        )
        return templates.TemplateResponse(
            "partials/strategy_lab_search_results.html",
            {
                "request": request,
                "query": clean_query,
                "universe_type": resolved_universe,
                "results": results,
                "search_mode": search_mode,
                "latest_prices": latest_prices,
            },
        )
    finally:
        _log_route("/web/partials/strategy-lab/instrument-search", started_at)


@router.get("/stock-context", include_in_schema=False)
def stock_context_partial(
    request: Request,
    stock_id: int | None = Query(default=None),
    portfolio_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        context = None
        if stock_id:
            context = build_stock_context(
                db,
                user_id=current_user.id,
                stock_id=stock_id,
                portfolio_id=portfolio_id,
            )
        return templates.TemplateResponse(
            "partials/strategy_lab_stock_context.html",
            {"request": request, "context": context},
        )
    finally:
        _log_route("/web/partials/strategy-lab/stock-context", started_at)


@router.get("/strategy-params", include_in_schema=False)
def strategy_params(
    request: Request,
    strategy_id: int,
    advanced: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        template = get_strategy_template(db, strategy_id)
        if template is None:
            return templates.TemplateResponse(
                "partials/backtesting_validation_error.html",
                {"request": request, "errors": ["Strategy template not found."]},
            )
        parameters = dict(template.default_parameters or {})
        logger.info(
            "strategy_lab partial=strategy-params strategy_id=%s strategy_type=%s advanced=%s",
            strategy_id,
            template.strategy_type,
            advanced,
        )
        return templates.TemplateResponse(
            "partials/strategy_lab_params.html",
            {
                "request": request,
                "template": template,
                "parameters": parameters,
                "parameter_config": PARAMETER_CONFIG,
                "advanced": advanced,
                "errors": [],
            },
        )
    finally:
        _log_route("/web/partials/strategy-lab/strategy-params", started_at)


@router.post("/create-strategy", include_in_schema=False)
async def create_strategy_partial(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    form = await request.form()
    portfolio_id_raw = form.get("portfolio_id")
    strategy_template_id_raw = form.get("strategy_template_id")
    strategy_name = str(form.get("strategy_name") or "").strip() or None
    params_json = str(form.get("params_json") or "{}")
    risk_raw = form.get("risk_per_trade_pct")

    errors: list[str] = []
    portfolio_id: int | None = None
    strategy_template_id: int | None = None
    template = None
    parameters: dict = {}
    risk_per_trade = 1.0

    try:
        portfolio_id = int(portfolio_id_raw) if portfolio_id_raw else None
        strategy_template_id = int(strategy_template_id_raw) if strategy_template_id_raw else None
        template = get_strategy_template(db, strategy_template_id) if strategy_template_id else None
        if template is None and strategy_template_id:
            errors.append("Strategy template not found.")
        elif template:
            parameters = parse_parameters_json(params_json, template)
            risk_per_trade = parse_risk_per_trade(risk_raw)
            errors.extend(
                validate_create_strategy_form(
                    portfolio_id=portfolio_id,
                    strategy_template_id=strategy_template_id,
                    strategy_type=template.strategy_type,
                    parameters=parameters,
                    risk_per_trade_pct=risk_per_trade,
                )
            )
    except ValueError as exc:
        errors.append(str(exc))

    logger.info(
        "strategy_lab.create_strategy portfolio_id=%s strategy_template_id=%s errors=%s",
        portfolio_id,
        strategy_template_id,
        len(errors),
    )

    created = None
    if not errors and template and portfolio_id and strategy_template_id:
        try:
            created = run_create_user_strategy(
                db,
                current_user,
                portfolio_id=portfolio_id,
                strategy_template_id=strategy_template_id,
                strategy_name=strategy_name or template.strategy_name,
                parameters=parameters,
                risk_per_trade_pct=risk_per_trade,
            )
            logger.info("strategy_lab.create_strategy success user_strategy_id=%s", created.id)
        except HTTPException as exc:
            errors.append(http_error_message(exc))
        except Exception as exc:
            logger.exception("strategy_lab create strategy failed")
            errors.append(f"Could not create user strategy: {exc}")

    return templates.TemplateResponse(
        "partials/strategy_lab_create_result.html",
        {
            "request": request,
            "errors": errors,
            "success": created is not None,
            "created_id": created.id if created else None,
            "created_name": created.strategy_name if created else None,
        },
    )


@router.post("/generate-signal", include_in_schema=False)
async def generate_signal_partial(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    form = await request.form()
    user_strategy_id_raw = form.get("user_strategy_id")
    stock_id_raw = form.get("stock_id")

    errors: list[str] = []
    user_strategy_id: int | None = None
    stock_id: int | None = None
    preview: dict | None = None

    try:
        user_strategy_id = int(user_strategy_id_raw) if user_strategy_id_raw else None
        stock_id = int(stock_id_raw) if stock_id_raw else None
        errors.extend(
            validate_generate_signal_form(
                user_strategy_id=user_strategy_id,
                stock_id=stock_id,
            )
        )
    except ValueError as exc:
        errors.append(str(exc))

    if not errors and user_strategy_id and stock_id:
        try:
            preview = run_generate_signal(
                db,
                current_user,
                user_strategy_id=user_strategy_id,
                stock_id=stock_id,
            )
            logger.info(
                "strategy_lab.generate_signal success signal_id=%s type=%s",
                preview.get("signal_id"),
                preview.get("signal_type"),
            )
        except HTTPException as exc:
            errors.append(http_error_message(exc))
        except Exception as exc:
            logger.exception("strategy_lab generate signal failed")
            errors.append(f"Signal generation failed: {exc}")

    return templates.TemplateResponse(
        "partials/strategy_lab_signal_preview.html",
        {
            "request": request,
            "errors": errors,
            "preview": preview,
        },
    )


@router.get("/signal-preview", include_in_schema=False)
def signal_preview_partial(
    request: Request,
    stock_id: int | None = Query(default=None),
    strategy_template_id: int | None = Query(default=None),
    params_json: str = Query(default="{}"),
    risk_per_trade_pct: str = Query(default="1"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    errors: list[str] = []
    preview: dict | None = None
    try:
        if not stock_id:
            errors.append("Select a stock to preview a signal.")
        if not strategy_template_id:
            errors.append("Select a strategy template.")
        template = get_strategy_template(db, strategy_template_id) if strategy_template_id else None
        if strategy_template_id and template is None:
            errors.append("Strategy template not found.")
        if not errors and template and stock_id:
            parameters = parse_parameters_json(params_json, template)
            risk = parse_risk_per_trade(risk_per_trade_pct)
            errors.extend(validate_strategy_parameters(template.strategy_type, parameters))
            if not errors:
                preview = run_preview_signal(
                    db,
                    stock_id=stock_id,
                    strategy_template_id=template.id,
                    parameters=parameters,
                    risk_per_trade_pct=risk,
                )
    except ValueError as exc:
        errors.append(str(exc))
    except HTTPException as exc:
        errors.append(http_error_message(exc))
    except Exception as exc:
        logger.exception("strategy_lab signal preview failed")
        errors.append(f"Preview failed: {exc}")

    logger.info(
        "strategy_lab.signal_preview stock_id=%s strategy_template_id=%s errors=%s",
        stock_id,
        strategy_template_id,
        len(errors),
    )
    return templates.TemplateResponse(
        "partials/strategy_lab_signal_preview.html",
        {
            "request": request,
            "errors": errors,
            "preview": preview,
        },
    )


@router.get("/user-strategies", include_in_schema=False)
def user_strategies_partial(
    request: Request,
    portfolio_id: int | None = Query(default=None),
    selected_user_strategy_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        strategies = list_user_strategy_models(db, current_user.id, portfolio_id=portfolio_id)
        rows = serialize_user_strategy_rows(db, strategies)
        logger.info(
            "strategy_lab.user_strategies portfolio_id=%s count=%s",
            portfolio_id,
            len(rows),
        )
        return templates.TemplateResponse(
            "partials/strategy_lab_user_strategies.html",
            {
                "request": request,
                "rows": rows,
                "selected_user_strategy_id": selected_user_strategy_id,
                "portfolio_id": portfolio_id,
            },
        )
    finally:
        _log_route("/web/partials/strategy-lab/user-strategies", started_at)


@router.get("/activity-log", include_in_schema=False)
def activity_log_partial(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        signals = list_recent_signals(db, current_user.id, limit=25)
        rows = serialize_activity_log(db, signals)
        logger.info("strategy_lab.activity_log count=%s", len(rows))
        return templates.TemplateResponse(
            "partials/strategy_lab_activity_log.html",
            {"request": request, "rows": rows},
        )
    finally:
        _log_route("/web/partials/strategy-lab/activity-log", started_at)
