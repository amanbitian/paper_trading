from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.security import get_current_user
from app.services.web_backtesting_helpers import (
    BENCHMARK_OPTIONS,
    COST_MODEL_OPTIONS,
    EXECUTION_MODE_OPTIONS,
    INTRABAR_OPTIONS,
    PARAMETER_CONFIG,
    build_drawdown_plotly_json,
    build_equity_plotly_json,
    coerce_date,
    coerce_decimal,
    get_strategy_template,
    load_backtest_run,
    parse_parameters_json,
    resolve_run_basket,
    run_basket_backtests,
    search_backtest_instruments,
    serialize_backtest_result,
    serialize_trades,
    validate_run_form,
)
from app.web_utils import templates

logger = logging.getLogger(__name__)
timing_logger = logging.getLogger("app.timing")

router = APIRouter(prefix="/web/partials/backtesting", tags=["web-backtesting-partials"])


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
    universe_type: str | None = Query(default=None, alias="instrument_type"),
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
            "backtesting.instrument_search query=%s exchange=%s universe_type=%s "
            "index_membership=%s results=%s mode=%s",
            clean_query,
            exchange or "",
            resolved_universe,
            index_membership or "",
            len(results),
            search_mode,
        )
        return templates.TemplateResponse(
            "partials/backtesting_search_results.html",
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
        _log_route("/web/partials/backtesting/instrument-search", started_at)


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
                {
                    "request": request,
                    "errors": ["Strategy not found."],
                },
            )
        parameters = dict(template.default_parameters or {})
        logger.info(
            "backtesting partial=strategy-params strategy_id=%s strategy_type=%s advanced=%s param_count=%s",
            strategy_id,
            template.strategy_type,
            advanced,
            len(parameters),
        )
        return templates.TemplateResponse(
            "partials/backtesting_strategy_params.html",
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
        _log_route("/web/partials/backtesting/strategy-params", started_at)


@router.post("/run", include_in_schema=False)
async def run_backtest_partial(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    form = await request.form()
    strategy_id = int(form.get("strategy_id") or 0)
    instrument_type = str(form.get("instrument_type") or "stock")
    start_date = str(form.get("start_date") or "")
    end_date = str(form.get("end_date") or "")
    initial_capital = str(form.get("initial_capital") or "")
    slippage_bps = int(form.get("slippage_bps") or 10)
    walk_forward = form.get("walk_forward") in ("true", "on", "1", True)
    execution_mode = str(form.get("execution_mode") or "signal_on_close_execute_next_open")
    intrabar_assumption = str(form.get("intrabar_assumption") or "conservative")
    cost_model = str(form.get("cost_model") or "zerodha_equity_delivery")
    benchmark_code = str(form.get("benchmark_code") or "buy_and_hold")
    parameters_json = str(form.get("parameters_json") or "{}")
    basket_symbols = form.getlist("basket_symbols")
    basket_exchanges = form.getlist("basket_exchanges")
    basket_instrument_ids = form.getlist("basket_instrument_ids")
    basket_instrument_types = form.getlist("basket_instrument_types")
    basket_json = str(form.get("basket_json") or "[]")

    try:
        template = get_strategy_template(db, strategy_id)
        if template is None:
            return templates.TemplateResponse(
                "partials/backtesting_run_result.html",
                {
                    "request": request,
                    "errors": ["Strategy not found."],
                    "successful": [],
                    "failed": [],
                    "primary": None,
                    "comparison": [],
                    "persisted_runs": True,
                },
            )

        validation_errors: list[str] = []
        basket: list[dict] = []
        parsed_start: date | None = None
        parsed_end: date | None = None
        capital: Decimal | None = None
        parameters = dict(template.default_parameters or {})

        try:
            basket = resolve_run_basket(
                symbols=basket_symbols,
                exchanges=basket_exchanges,
                instrument_ids=basket_instrument_ids,
                instrument_types=basket_instrument_types,
                basket_json=basket_json,
            )
            parsed_start = coerce_date(start_date, field="Start date")
            parsed_end = coerce_date(end_date, field="End date")
            capital = coerce_decimal(initial_capital)
            parameters = parse_parameters_json(parameters_json, template)
            parameters["slippage_bps"] = int(slippage_bps)
        except ValueError as exc:
            validation_errors.append(str(exc))

        validation_errors.extend(
            validate_run_form(
                basket=basket,
                strategy_id=strategy_id,
                start_date=parsed_start,
                end_date=parsed_end,
                initial_capital=capital,
                slippage_bps=int(slippage_bps),
                strategy_type=template.strategy_type,
                parameters=parameters,
            )
        )

        logger.info(
            "backtesting.run strategy_id=%s instrument_type=%s basket_count=%s symbols=%s "
            "exchanges=%s instrument_ids=%s start=%s end=%s slippage_bps=%s walk_forward=%s",
            strategy_id,
            instrument_type,
            len(basket),
            [item.get("symbol") for item in basket],
            [item.get("exchange") for item in basket],
            [item.get("id") for item in basket],
            start_date,
            end_date,
            slippage_bps,
            walk_forward,
        )

        if validation_errors:
            return templates.TemplateResponse(
                "partials/backtesting_run_result.html",
                {
                    "request": request,
                    "errors": validation_errors,
                    "successful": [],
                    "failed": [],
                    "primary": None,
                    "comparison": [],
                    "persisted_runs": True,
                },
            )

        successful, failed = run_basket_backtests(
            db,
            current_user,
            basket=basket,
            request_kwargs={
                "strategy_id": strategy_id,
                "start_date": parsed_start,
                "end_date": parsed_end,
                "initial_capital": capital,
                "parameters": parameters,
                "walk_forward": walk_forward,
                "execution_mode": execution_mode,
                "intrabar_assumption": intrabar_assumption,
                "cost_model": cost_model,
                "benchmark_code": benchmark_code,
            },
        )

        for row in successful:
            row["equity_plotly"] = build_equity_plotly_json(
                row.get("equity_curve") or [],
                row.get("benchmark_curve") or [],
            )
            row["drawdown_plotly"] = build_drawdown_plotly_json(row.get("drawdown_curve") or [])

        primary = successful[0] if successful else None
        run_ids = [row.get("run_id") for row in successful]
        logger.info(
            "backtesting.run completed result_count=%s success=%s failed=%s run_ids=%s statuses=%s",
            len(successful) + len(failed),
            len(successful),
            len(failed),
            run_ids,
            [{"instrument": row.get("label"), "run_id": row.get("run_id")} for row in successful],
        )

        return templates.TemplateResponse(
            "partials/backtesting_run_result.html",
            {
                "request": request,
                "errors": [],
                "successful": successful,
                "failed": failed,
                "primary": primary,
                "comparison": successful,
                "persisted_runs": True,
                "curve_note": (
                    "Equity and drawdown curves are included in this response. "
                    "Reloading results by run_id alone does not restore curves from the database yet."
                ),
            },
        )
    except Exception as exc:
        logger.exception("Backtesting run partial failed")
        return templates.TemplateResponse(
            "partials/backtesting_run_result.html",
            {
                "request": request,
                "errors": [f"Backtest failed: {exc}"],
                "successful": [],
                "failed": [],
                "primary": None,
                "comparison": [],
                "persisted_runs": True,
            },
        )
    finally:
        _log_route("/web/partials/backtesting/run", started_at)


@router.get("/results/{run_id}", include_in_schema=False)
def backtest_results_partial(
    request: Request,
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        run = load_backtest_run(db, current_user.id, run_id)
        primary = serialize_backtest_result(
            run,
            equity_curve=[],
            benchmark_curve=[],
            instrument={},
            label=f"Run #{run.id}",
            extra=None,
        )
        logger.info("backtesting partial=results run_id=%s trades=%s", run_id, run.total_trades)
        return templates.TemplateResponse(
            "partials/backtesting_results.html",
            {
                "request": request,
                "primary": primary,
                "curve_missing": True,
            },
        )
    finally:
        _log_route(f"/web/partials/backtesting/results/{run_id}", started_at)


@router.get("/trades/{run_id}", include_in_schema=False)
def backtest_trades_partial(
    request: Request,
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        run = load_backtest_run(db, current_user.id, run_id)
        rows = serialize_trades(list(run.trades or []))
        logger.info("backtesting partial=trades run_id=%s row_count=%s", run_id, len(rows))
        return templates.TemplateResponse(
            "partials/backtesting_trades.html",
            {"request": request, "rows": rows, "run_id": run_id},
        )
    finally:
        _log_route(f"/web/partials/backtesting/trades/{run_id}", started_at)


@router.get("/monthly-returns/{run_id}", include_in_schema=False)
def backtest_monthly_returns_partial(
    request: Request,
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = time.perf_counter()
    try:
        run = load_backtest_run(db, current_user.id, run_id)
        rows = []
        logger.info("backtesting partial=monthly-returns run_id=%s row_count=%s", run_id, len(rows))
        return templates.TemplateResponse(
            "partials/backtesting_monthly_returns.html",
            {
                "request": request,
                "rows": rows,
                "run_id": run_id,
                "empty_message": "Monthly returns are available on the run response. Re-run backtest or open Overview from the latest result.",
            },
        )
    finally:
        _log_route(f"/web/partials/backtesting/monthly-returns/{run_id}", started_at)
