from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import get_db
from app.models.backtest import BacktestRun
from app.models.portfolio import PaperOrder, PaperTrade, Portfolio, PortfolioHolding
from app.models.stock import Stock
from app.models.strategy import UserStrategy
from app.models.user import User
from app.schemas.ai import (
    AIEvaluateTradeRequest,
    AIExplainRiskRequest,
    AIInterpretBacktestRequest,
    AINLScreenerRequest,
    AISynthesizeSignalsRequest,
)
from app.security import get_current_user
from app.services.ai_action_log_service import list_ai_action_logs, persist_ai_action_log
from app.services.algo_finding_service import generate_stock_algo_findings
from app.services.llm_cache_service import get_llm_cache, set_llm_cache
from app.services.market_data_service import get_latest_prices_map
from app.services.portfolio_service import calculate_portfolio_value
from app.services.risk_service import get_portfolio_risk_metrics
from app.services.stock_performance_service import list_stock_performance
from models.backtest_interpreter import interpret_backtest
from models.journal_analyzer import analyze_journal_patterns
from models.nl_screener import parse_nl_query
from models.ollama_client import DISCLAIMER, OllamaClient, OllamaJSONError, OllamaSettings, OllamaUnavailableError
from models.portfolio_analyst import generate_portfolio_narrative
from models.risk_explainer import explain_risk_metrics
from models.signal_synthesizer import synthesize_signals
from models.trade_advisor import evaluate_trade_reasoning

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])


def _ollama_settings() -> OllamaSettings:
    return OllamaSettings(
        base_url=settings.ollama_base_url.rstrip("/"),
        default_model=settings.ollama_default_model,
        fallback_model=settings.ollama_fallback_model,
        timeout_seconds=settings.ollama_timeout_seconds,
        max_tokens=settings.ollama_max_tokens,
    )


def _client() -> OllamaClient:
    return OllamaClient(_ollama_settings())


def _disabled_response() -> dict[str, Any]:
    return {
        "error": "AI features disabled. Set AI_FEATURES_ENABLED=true in backend/.env.",
        "disclaimer": DISCLAIMER,
    }


def _ollama_down_response() -> dict[str, Any]:
    return {
        "error": "Ollama is not running. Install from https://ollama.com and run `ollama serve`, then `ollama pull qwen3:14b`.",
        "disclaimer": DISCLAIMER,
    }


async def _ensure_ai_ready(client: OllamaClient) -> dict[str, Any] | None:
    if not settings.ai_features_enabled:
        return _disabled_response()
    if not await client.is_available():
        return _ollama_down_response()
    return None


def _resolve_model(explicit: str | None, query_model: str | None = None) -> str | None:
    return query_model or explicit or None


def _effective_model(model: str | None) -> str:
    return model or settings.ollama_default_model


async def _record_ai_action(
    db: Session,
    *,
    user: User | None,
    client: OllamaClient,
    action_type: str,
    endpoint: str,
    http_method: str,
    request_data: Any,
    response_data: Any,
    model_name: str | None,
    started_at: float,
    cache_hit: bool = False,
    ollama_connected: bool | None = None,
) -> None:
    if ollama_connected is None:
        ollama_connected = (
            await client.is_available() if settings.ai_features_enabled else False
        )
    model_used = _effective_model(model_name)
    status = "cache_hit" if cache_hit else "ok"
    error_message: str | None = None
    if isinstance(response_data, dict) and response_data.get("error"):
        msg = str(response_data["error"])
        error_message = msg
        lowered = msg.lower()
        if "disabled" in lowered:
            status = "disabled"
        elif "ollama" in lowered:
            status = "ollama_down"
        else:
            status = "error"

    meta = client.last_chat_log or {}
    persist_ai_action_log(
        db,
        user_id=user.id if user else None,
        action_type=action_type,
        endpoint=endpoint,
        http_method=http_method,
        model_name=str(meta.get("model") or model_used),
        ollama_connected=ollama_connected,
        request_data=request_data,
        response_data=response_data,
        llm_prompt=meta.get("prompt"),
        llm_response=meta.get("response"),
        cache_hit=cache_hit,
        status=status,
        error_message=error_message,
        duration_ms=(time.perf_counter() - started_at) * 1000,
    )


@router.get("/status")
async def ai_status(
    model: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    started = time.perf_counter()
    client = _client()
    available = await client.is_available() if settings.ai_features_enabled else False
    models = await client.list_models() if available else []
    response = {
        "ai_features_enabled": settings.ai_features_enabled,
        "available": available,
        "models": models,
        "default_model": model or settings.ollama_default_model,
        "ollama_base_url": settings.ollama_base_url,
    }
    await _record_ai_action(
        db,
        user=None,
        client=client,
        action_type="status",
        endpoint="/ai/status",
        http_method="GET",
        request_data={"model": model},
        response_data={
            "ai_features_enabled": response["ai_features_enabled"],
            "available": available,
            "models_count": len(models),
        },
        model_name=_effective_model(model),
        started_at=started,
        ollama_connected=available,
    )
    return response


@router.get("/logs")
def get_ai_logs(
    limit: int = Query(default=50, ge=1, le=200),
    action_type: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    return list_ai_action_logs(
        db,
        user_id=current_user.id,
        action_type=action_type,
        limit=limit,
    )


@router.get("/backtest-runs")
def list_backtest_runs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    limit: int = Query(default=20, ge=1, le=50),
) -> list[dict[str, Any]]:
    started = time.perf_counter()
    runs = db.scalars(
        select(BacktestRun)
        .where(BacktestRun.user_id == current_user.id)
        .order_by(desc(BacktestRun.created_at))
        .limit(limit)
    )
    result = []
    for run in runs:
        symbol = None
        if run.stock_id:
            stock = db.get(Stock, run.stock_id)
            symbol = stock.symbol if stock else None
        result.append(
            {
                "id": run.id,
                "symbol": symbol,
                "start_date": run.start_date.isoformat(),
                "end_date": run.end_date.isoformat(),
                "total_return_pct": float(run.total_return_pct),
                "sharpe_ratio": float(run.sharpe_ratio),
                "max_drawdown_pct": float(run.max_drawdown_pct),
                "total_trades": run.total_trades,
                "walk_forward_enabled": run.walk_forward_enabled,
                "created_at": run.created_at.isoformat() if run.created_at else None,
            }
        )
    persist_ai_action_log(
        db,
        user_id=current_user.id,
        action_type="list_backtest_runs",
        endpoint="/ai/backtest-runs",
        http_method="GET",
        request_data={"limit": limit},
        response_data={"count": len(result)},
        status="ok",
        duration_ms=(time.perf_counter() - started) * 1000,
    )
    return result


@router.post("/synthesize-signals")
async def api_synthesize_signals(
    payload: AISynthesizeSignalsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    started = time.perf_counter()
    client = _client()
    client.last_chat_log = None
    request_data = payload.model_dump()
    model = _resolve_model(payload.model)
    blocked = await _ensure_ai_ready(client)
    if blocked:
        await _record_ai_action(
            db,
            user=current_user,
            client=client,
            action_type="synthesize_signals",
            endpoint="/ai/synthesize-signals",
            http_method="POST",
            request_data=request_data,
            response_data=blocked,
            model_name=model,
            started_at=started,
        )
        return blocked

    cache_suffix = f"signals:{payload.symbol.upper()}:{client.cache_key(json.dumps(payload.findings, sort_keys=True), model or settings.ollama_default_model)}"
    cached = get_llm_cache(db, cache_suffix, ttl_hours=float(settings.llm_cache_ttl_hours))
    if cached:
        await _record_ai_action(
            db,
            user=current_user,
            client=client,
            action_type="synthesize_signals",
            endpoint="/ai/synthesize-signals",
            http_method="POST",
            request_data=request_data,
            response_data=cached,
            model_name=model,
            started_at=started,
            cache_hit=True,
        )
        return cached

    try:
        result = await synthesize_signals(
            client,
            symbol=payload.symbol,
            findings=payload.findings,
            model=model,
        )
        set_llm_cache(db, cache_suffix, result)
        await _record_ai_action(
            db,
            user=current_user,
            client=client,
            action_type="synthesize_signals",
            endpoint="/ai/synthesize-signals",
            http_method="POST",
            request_data=request_data,
            response_data=result,
            model_name=model,
            started_at=started,
        )
        return result
    except OllamaUnavailableError as exc:
        logger.warning("synthesize-signals: %s", exc)
        response = {**_ollama_down_response(), "detail": str(exc)}
        await _record_ai_action(
            db,
            user=current_user,
            client=client,
            action_type="synthesize_signals",
            endpoint="/ai/synthesize-signals",
            http_method="POST",
            request_data=request_data,
            response_data=response,
            model_name=model,
            started_at=started,
        )
        return response
    except OllamaJSONError as exc:
        logger.warning("synthesize-signals json: %s", exc)
        response = {"error": str(exc), "disclaimer": DISCLAIMER}
        await _record_ai_action(
            db,
            user=current_user,
            client=client,
            action_type="synthesize_signals",
            endpoint="/ai/synthesize-signals",
            http_method="POST",
            request_data=request_data,
            response_data=response,
            model_name=model,
            started_at=started,
        )
        return response


@router.post("/interpret-backtest")
async def api_interpret_backtest(
    payload: AIInterpretBacktestRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    started = time.perf_counter()
    client = _client()
    client.last_chat_log = None
    request_data = payload.model_dump()
    model = _resolve_model(payload.model)
    blocked = await _ensure_ai_ready(client)
    if blocked:
        await _record_ai_action(
            db, user=current_user, client=client, action_type="interpret_backtest",
            endpoint="/ai/interpret-backtest", http_method="POST",
            request_data=request_data, response_data=blocked, model_name=model, started_at=started,
        )
        return blocked

    run = db.scalar(
        select(BacktestRun)
        .where(BacktestRun.id == payload.backtest_id, BacktestRun.user_id == current_user.id)
        .options(joinedload(BacktestRun.stock), joinedload(BacktestRun.user_strategy).joinedload(UserStrategy.strategy_template))
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest not found")

    strategy_name = "Strategy"
    if run.user_strategy and run.user_strategy.strategy_template:
        strategy_name = run.user_strategy.strategy_template.strategy_name

    symbol = run.stock.symbol if run.stock else "Instrument"
    sector = run.stock.sector if run.stock else ""
    metrics = {
        "sharpe_ratio": float(run.sharpe_ratio),
        "max_drawdown_pct": float(run.max_drawdown_pct),
        "win_rate": float(run.win_rate),
        "total_return_pct": float(run.total_return_pct),
        "num_trades": run.total_trades,
        "is_sharpe_ratio": float(run.is_sharpe_ratio) if run.is_sharpe_ratio is not None else None,
    }

    cache_suffix = f"backtest:{run.id}"
    cached = get_llm_cache(db, cache_suffix, ttl_hours=None)
    if cached:
        await _record_ai_action(
            db, user=current_user, client=client, action_type="interpret_backtest",
            endpoint="/ai/interpret-backtest", http_method="POST",
            request_data=request_data, response_data=cached, model_name=model, started_at=started, cache_hit=True,
        )
        return cached

    try:
        result = await interpret_backtest(
            client,
            strategy_name=strategy_name,
            symbol=symbol,
            sector=sector or "",
            metrics=metrics,
            benchmark_return=0.0,
            oos_sharpe=float(run.oos_sharpe_ratio) if run.oos_sharpe_ratio is not None else None,
            overfitting_score=float(run.overfitting_score) if run.overfitting_score is not None else None,
            model=model,
        )
        set_llm_cache(db, cache_suffix, result)
        await _record_ai_action(
            db, user=current_user, client=client, action_type="interpret_backtest",
            endpoint="/ai/interpret-backtest", http_method="POST",
            request_data=request_data, response_data=result, model_name=model, started_at=started,
        )
        return result
    except OllamaUnavailableError as exc:
        response = {**_ollama_down_response(), "detail": str(exc)}
        await _record_ai_action(
            db, user=current_user, client=client, action_type="interpret_backtest",
            endpoint="/ai/interpret-backtest", http_method="POST",
            request_data=request_data, response_data=response, model_name=model, started_at=started,
        )
        return response
    except OllamaJSONError as exc:
        response = {"error": str(exc), "disclaimer": DISCLAIMER}
        await _record_ai_action(
            db, user=current_user, client=client, action_type="interpret_backtest",
            endpoint="/ai/interpret-backtest", http_method="POST",
            request_data=request_data, response_data=response, model_name=model, started_at=started,
        )
        return response


@router.post("/evaluate-trade")
async def api_evaluate_trade(
    payload: AIEvaluateTradeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    started = time.perf_counter()
    client = _client()
    client.last_chat_log = None
    request_data = payload.model_dump()
    model = _resolve_model(payload.model)
    blocked = await _ensure_ai_ready(client)
    if blocked:
        await _record_ai_action(
            db, user=current_user, client=client, action_type="evaluate_trade",
            endpoint="/ai/evaluate-trade", http_method="POST",
            request_data=request_data, response_data=blocked, model_name=model, started_at=started,
        )
        return blocked

    portfolio = db.scalar(
        select(Portfolio).where(
            Portfolio.id == payload.portfolio_id,
            Portfolio.user_id == current_user.id,
        )
    )
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    stock = None
    if payload.stock_id:
        stock = db.get(Stock, payload.stock_id)
    if stock is None:
        stock = db.scalar(select(Stock).where(Stock.symbol == payload.symbol.upper()))
    if stock is None:
        raise HTTPException(status_code=404, detail="Stock not found")

    values = calculate_portfolio_value(db, portfolio.id)
    total_value = float(values.get("total_value") or 0)
    trade_value = payload.price * payload.quantity
    concentration = (trade_value / total_value * 100) if total_value > 0 else 0.0

    findings = generate_stock_algo_findings(db, stock.id, limit=12)
    algo_signals = [
        {
            "algorithm_name": f.algorithm_name,
            "action": f.action,
            "confidence_score": float(f.confidence_score),
        }
        for f in findings
    ]

    try:
        result = await evaluate_trade_reasoning(
            client,
            symbol=payload.symbol,
            action=payload.action.upper(),
            quantity=payload.quantity,
            price=payload.price,
            user_notes=payload.notes,
            algo_signals=algo_signals,
            portfolio_concentration_pct=concentration,
            current_portfolio_value=total_value,
            atr_stop_price=None,
            stop_loss_pct=None,
            model=model,
        )
        await _record_ai_action(
            db, user=current_user, client=client, action_type="evaluate_trade",
            endpoint="/ai/evaluate-trade", http_method="POST",
            request_data=request_data, response_data=result, model_name=model, started_at=started,
        )
        return result
    except OllamaUnavailableError as exc:
        response = {**_ollama_down_response(), "detail": str(exc)}
        await _record_ai_action(
            db, user=current_user, client=client, action_type="evaluate_trade",
            endpoint="/ai/evaluate-trade", http_method="POST",
            request_data=request_data, response_data=response, model_name=model, started_at=started,
        )
        return response
    except OllamaJSONError as exc:
        response = {"error": str(exc), "disclaimer": DISCLAIMER}
        await _record_ai_action(
            db, user=current_user, client=client, action_type="evaluate_trade",
            endpoint="/ai/evaluate-trade", http_method="POST",
            request_data=request_data, response_data=response, model_name=model, started_at=started,
        )
        return response


@router.post("/nl-screener")
async def api_nl_screener(
    payload: AINLScreenerRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    started = time.perf_counter()
    client = _client()
    client.last_chat_log = None
    request_data = payload.model_dump()
    model = _resolve_model(payload.model)
    blocked = await _ensure_ai_ready(client)
    if blocked:
        await _record_ai_action(
            db, user=current_user, client=client, action_type="nl_screener",
            endpoint="/ai/nl-screener", http_method="POST",
            request_data=request_data, response_data=blocked, model_name=model, started_at=started,
        )
        return blocked

    query_hash = hashlib.sha256(payload.query.strip().lower().encode()).hexdigest()[:16]
    cache_suffix = f"nl:{query_hash}"
    cached = get_llm_cache(db, cache_suffix, ttl_hours=1.0)
    if cached:
        await _record_ai_action(
            db, user=current_user, client=client, action_type="nl_screener",
            endpoint="/ai/nl-screener", http_method="POST",
            request_data=request_data, response_data=cached, model_name=model, started_at=started, cache_hit=True,
        )
        return cached

    try:
        parsed = await parse_nl_query(client, query=payload.query, model=model)
        filters = parsed.get("filters") or {}
        rows = list_stock_performance(
            db,
            sector=filters.get("sector"),
            exchange=filters.get("exchange"),
            limit=500,
            only_with_prices=True,
            min_change_1m_pct=filters.get("min_change_1m_pct"),
            max_change_1m_pct=filters.get("max_change_1m_pct"),
            min_change_3m_pct=filters.get("min_change_3m_pct"),
            max_change_3m_pct=filters.get("max_change_3m_pct"),
            min_change_6m_pct=filters.get("min_change_6m_pct"),
            max_change_6m_pct=filters.get("max_change_6m_pct"),
            min_change_1y_pct=filters.get("min_change_1y_pct"),
            max_change_1y_pct=filters.get("max_change_1y_pct"),
            sort_by=filters.get("sort_by"),
            sort_desc=bool(filters.get("sort_desc", True)),
        )
        result = {**parsed, "stocks": rows, "count": len(rows)}
        set_llm_cache(db, cache_suffix, {k: v for k, v in result.items() if k != "stocks"})
        await _record_ai_action(
            db, user=current_user, client=client, action_type="nl_screener",
            endpoint="/ai/nl-screener", http_method="POST",
            request_data=request_data,
            response_data={k: v for k, v in result.items() if k != "stocks"},
            model_name=model, started_at=started,
        )
        return result
    except OllamaUnavailableError as exc:
        response = {**_ollama_down_response(), "detail": str(exc)}
        await _record_ai_action(
            db, user=current_user, client=client, action_type="nl_screener",
            endpoint="/ai/nl-screener", http_method="POST",
            request_data=request_data, response_data=response, model_name=model, started_at=started,
        )
        return response
    except OllamaJSONError as exc:
        response = {"error": str(exc), "disclaimer": DISCLAIMER}
        await _record_ai_action(
            db, user=current_user, client=client, action_type="nl_screener",
            endpoint="/ai/nl-screener", http_method="POST",
            request_data=request_data, response_data=response, model_name=model, started_at=started,
        )
        return response


@router.get("/portfolio-narrative/{portfolio_id}")
async def api_portfolio_narrative(
    portfolio_id: int,
    model: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    started = time.perf_counter()
    client = _client()
    client.last_chat_log = None
    request_data = {"portfolio_id": portfolio_id, "model": model}
    model_resolved = _resolve_model(model)
    blocked = await _ensure_ai_ready(client)
    if blocked:
        await _record_ai_action(
            db, user=current_user, client=client, action_type="portfolio_narrative",
            endpoint=f"/ai/portfolio-narrative/{portfolio_id}", http_method="GET",
            request_data=request_data, response_data=blocked, model_name=model_resolved, started_at=started,
        )
        return blocked

    portfolio = db.scalar(
        select(Portfolio).where(
            Portfolio.id == portfolio_id,
            Portfolio.user_id == current_user.id,
        )
    )
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    cache_suffix = f"portfolio:{portfolio_id}"
    cached = get_llm_cache(db, cache_suffix, ttl_hours=1.0)
    if cached:
        await _record_ai_action(
            db, user=current_user, client=client, action_type="portfolio_narrative",
            endpoint=f"/ai/portfolio-narrative/{portfolio_id}", http_method="GET",
            request_data=request_data, response_data=cached, model_name=model_resolved, started_at=started, cache_hit=True,
        )
        return cached

    summary = calculate_portfolio_value(db, portfolio_id)
    holdings = list(
        db.scalars(
            select(PortfolioHolding)
            .where(PortfolioHolding.portfolio_id == portfolio_id)
            .options(joinedload(PortfolioHolding.stock))
            .limit(10)
        )
    )
    price_map = get_latest_prices_map(db, [h.stock_id for h in holdings])
    top_holdings = [
        {
            "symbol": h.stock.symbol,
            "quantity": float(h.quantity),
            "market_value": float(h.quantity) * float(price_map.get(h.stock_id) or 0),
        }
        for h in holdings
        if h.stock
    ]
    risk_metrics = get_portfolio_risk_metrics(db, portfolio_id)
    trades = list(
        db.scalars(
            select(PaperTrade)
            .where(PaperTrade.portfolio_id == portfolio_id)
            .options(joinedload(PaperTrade.stock))
            .order_by(desc(PaperTrade.executed_at))
            .limit(10)
        )
    )
    recent = [
        {
            "symbol": t.stock.symbol if t.stock else "",
            "side": t.side,
            "quantity": float(t.quantity),
            "price": float(t.executed_price),
        }
        for t in trades
    ]

    try:
        result = await generate_portfolio_narrative(
            client,
            portfolio_summary=summary,
            top_holdings=top_holdings,
            risk_metrics=risk_metrics,
            recent_trades=recent,
            benchmark_return_1w=0.0,
            model=model_resolved,
        )
        set_llm_cache(db, cache_suffix, result)
        await _record_ai_action(
            db, user=current_user, client=client, action_type="portfolio_narrative",
            endpoint=f"/ai/portfolio-narrative/{portfolio_id}", http_method="GET",
            request_data=request_data, response_data=result, model_name=model_resolved, started_at=started,
        )
        return result
    except OllamaUnavailableError as exc:
        response = {**_ollama_down_response(), "detail": str(exc)}
        await _record_ai_action(
            db, user=current_user, client=client, action_type="portfolio_narrative",
            endpoint=f"/ai/portfolio-narrative/{portfolio_id}", http_method="GET",
            request_data=request_data, response_data=response, model_name=model_resolved, started_at=started,
        )
        return response
    except OllamaJSONError as exc:
        response = {"error": str(exc), "disclaimer": DISCLAIMER}
        await _record_ai_action(
            db, user=current_user, client=client, action_type="portfolio_narrative",
            endpoint=f"/ai/portfolio-narrative/{portfolio_id}", http_method="GET",
            request_data=request_data, response_data=response, model_name=model_resolved, started_at=started,
        )
        return response


@router.post("/analyze-journal/{portfolio_id}")
async def api_analyze_journal(
    portfolio_id: int,
    model: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    started = time.perf_counter()
    client = _client()
    client.last_chat_log = None
    request_data = {"portfolio_id": portfolio_id, "model": model}
    model_resolved = _resolve_model(model)
    blocked = await _ensure_ai_ready(client)
    if blocked:
        await _record_ai_action(
            db, user=current_user, client=client, action_type="analyze_journal",
            endpoint=f"/ai/analyze-journal/{portfolio_id}", http_method="POST",
            request_data=request_data, response_data=blocked, model_name=model_resolved, started_at=started,
        )
        return blocked

    portfolio = db.scalar(
        select(Portfolio).where(
            Portfolio.id == portfolio_id,
            Portfolio.user_id == current_user.id,
        )
    )
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    cache_suffix = f"journal:{portfolio_id}"
    cached = get_llm_cache(db, cache_suffix, ttl_hours=6.0)
    if cached:
        await _record_ai_action(
            db, user=current_user, client=client, action_type="analyze_journal",
            endpoint=f"/ai/analyze-journal/{portfolio_id}", http_method="POST",
            request_data=request_data, response_data=cached, model_name=model_resolved, started_at=started, cache_hit=True,
        )
        return cached

    trades = list(
        db.scalars(
            select(PaperTrade)
            .where(PaperTrade.portfolio_id == portfolio_id)
            .options(joinedload(PaperTrade.stock))
            .order_by(desc(PaperTrade.executed_at))
            .limit(100)
        )
    )
    trade_rows: list[dict[str, Any]] = []
    for trade in trades:
        order = db.get(PaperOrder, trade.order_id)
        stock = trade.stock
        trade_rows.append(
            {
                "date": trade.executed_at.date().isoformat() if trade.executed_at else "",
                "symbol": stock.symbol if stock else "",
                "action": trade.side,
                "pnl": 0,
                "notes": (order.notes if order else "") or "",
            }
        )

    try:
        result = await analyze_journal_patterns(
            client,
            trades_with_notes=trade_rows,
            model=model_resolved,
        )
        if "error" not in result:
            set_llm_cache(db, cache_suffix, result)
        await _record_ai_action(
            db, user=current_user, client=client, action_type="analyze_journal",
            endpoint=f"/ai/analyze-journal/{portfolio_id}", http_method="POST",
            request_data=request_data, response_data=result, model_name=model_resolved, started_at=started,
        )
        return result
    except OllamaUnavailableError as exc:
        response = {**_ollama_down_response(), "detail": str(exc)}
        await _record_ai_action(
            db, user=current_user, client=client, action_type="analyze_journal",
            endpoint=f"/ai/analyze-journal/{portfolio_id}", http_method="POST",
            request_data=request_data, response_data=response, model_name=model_resolved, started_at=started,
        )
        return response
    except OllamaJSONError as exc:
        response = {"error": str(exc), "disclaimer": DISCLAIMER}
        await _record_ai_action(
            db, user=current_user, client=client, action_type="analyze_journal",
            endpoint=f"/ai/analyze-journal/{portfolio_id}", http_method="POST",
            request_data=request_data, response_data=response, model_name=model_resolved, started_at=started,
        )
        return response


@router.post("/explain-risk")
async def api_explain_risk(
    payload: AIExplainRiskRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    started = time.perf_counter()
    client = _client()
    client.last_chat_log = None
    request_data = payload.model_dump()
    model = _resolve_model(payload.model)
    blocked = await _ensure_ai_ready(client)
    if blocked:
        await _record_ai_action(
            db, user=current_user, client=client, action_type="explain_risk",
            endpoint="/ai/explain-risk", http_method="POST",
            request_data=request_data, response_data=blocked, model_name=model, started_at=started,
        )
        return blocked

    cache_suffix = f"risk:{hashlib.sha256(json.dumps(payload.model_dump(), sort_keys=True).encode()).hexdigest()[:24]}"
    cached = get_llm_cache(db, cache_suffix, ttl_hours=1.0)
    if cached:
        await _record_ai_action(
            db, user=current_user, client=client, action_type="explain_risk",
            endpoint="/ai/explain-risk", http_method="POST",
            request_data=request_data, response_data=cached, model_name=model, started_at=started, cache_hit=True,
        )
        return cached

    try:
        result = await explain_risk_metrics(
            client,
            symbol_or_portfolio=payload.label,
            beta=payload.beta,
            var_1d_inr=payload.var_1d_inr,
            hhi_concentration=payload.hhi,
            max_drawdown_pct=payload.max_drawdown_pct,
            portfolio_value=payload.portfolio_value,
            model=model,
        )
        set_llm_cache(db, cache_suffix, result)
        await _record_ai_action(
            db, user=current_user, client=client, action_type="explain_risk",
            endpoint="/ai/explain-risk", http_method="POST",
            request_data=request_data, response_data=result, model_name=model, started_at=started,
        )
        return result
    except OllamaUnavailableError as exc:
        response = {**_ollama_down_response(), "detail": str(exc)}
        await _record_ai_action(
            db, user=current_user, client=client, action_type="explain_risk",
            endpoint="/ai/explain-risk", http_method="POST",
            request_data=request_data, response_data=response, model_name=model, started_at=started,
        )
        return response
    except OllamaJSONError as exc:
        response = {"error": str(exc), "disclaimer": DISCLAIMER}
        await _record_ai_action(
            db, user=current_user, client=client, action_type="explain_risk",
            endpoint="/ai/explain-risk", http_method="POST",
            request_data=request_data, response_data=response, model_name=model, started_at=started,
        )
        return response
