from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.models.portfolio import PaperTrade, Portfolio, PortfolioHolding
from app.models.stock import Stock
from app.models.user import User
from app.schemas.ai import (
    AIEvaluateTradeRequest,
    AIInterpretBacktestRequest,
    AINLScreenerRequest,
    AISynthesizeSignalsRequest,
)
from app.services.algo_finding_service import generate_stock_algo_findings
from app.services.portfolio_service import calculate_portfolio_value
from app.services.web_nl_screener_service import (
    build_nl_screener_result_view,
    build_nl_screener_view_from_api,
    run_deterministic_nl_screener,
)
from app.utils.json_safe import to_json_safe
from models.ollama_client import DISCLAIMER

logger = logging.getLogger(__name__)

PAGE_DISCLAIMER = (
    "Educational analysis only. Not investment advice, not a trade recommendation, "
    "and not a future price prediction."
)

SUPPORTED_MODES = {
    "signal_synthesizer": {
        "label": "Signal Synthesizer",
        "action_label": "Run Signal Synthesis",
        "requires_stock": True,
        "requires_portfolio": False,
    },
    "backtest_interpreter": {
        "label": "Backtest Interpreter",
        "action_label": "Interpret Backtest",
        "requires_stock": False,
        "requires_portfolio": False,
    },
    "pre_trade_advisor": {
        "label": "Pre-Trade Advisor",
        "action_label": "Run Pre-Trade Check",
        "requires_stock": True,
        "requires_portfolio": True,
    },
    "nl_screener": {
        "label": "Natural Language Screener",
        "action_label": "Run Screener",
        "requires_stock": False,
        "requires_portfolio": False,
    },
    "portfolio_health": {
        "label": "Portfolio Health",
        "action_label": "Analyze Portfolio Health",
        "requires_stock": False,
        "requires_portfolio": True,
    },
    "journal_insights": {
        "label": "Journal Insights",
        "action_label": "Analyze Journal",
        "requires_stock": False,
        "requires_portfolio": True,
    },
}


def http_error_message(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        return detail if isinstance(detail, str) else str(detail)
    return str(exc) or "Request failed."


def none_if_blank(value: Any) -> str | None:
    if value is None:
        return None
    clean = str(value).strip()
    return clean if clean else None


def optional_int(value: Any, field_name: str) -> int | None:
    clean = none_if_blank(value)
    if clean is None:
        return None
    try:
        return int(clean)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid integer") from exc


def optional_float(value: Any, field_name: str) -> float | None:
    clean = none_if_blank(value)
    if clean is None:
        return None
    try:
        return float(clean)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid number") from exc


def build_ai_analysis_request_from_form(form: Any) -> tuple[dict[str, Any], list[str]]:
    """Parse multipart form into normalized fields; blank strings become None."""
    parse_errors: list[str] = []
    payload: dict[str, Any] = {
        "mode": none_if_blank(form.get("mode")) or "",
        "model": none_if_blank(form.get("model")),
        "user_prompt": none_if_blank(form.get("user_prompt")),
        "symbol": none_if_blank(form.get("symbol")),
        "action": none_if_blank(form.get("action")),
        "notes": none_if_blank(form.get("notes")),
        "portfolio_id": None,
        "stock_id": None,
        "backtest_id": None,
        "quantity": None,
        "price": None,
    }

    int_fields = (
        ("portfolio_id", "portfolio_id"),
        ("stock_id", "stock_id"),
        ("backtest_id", "backtest_id"),
        ("quantity", "quantity"),
    )
    for key, label in int_fields:
        try:
            payload[key] = optional_int(form.get(key), label)
        except ValueError as exc:
            parse_errors.append(str(exc))

    if not parse_errors:
        try:
            payload["price"] = optional_float(form.get("price"), "price")
        except ValueError as exc:
            parse_errors.append(str(exc))

    return payload, parse_errors


def validate_run_request(
    *,
    mode: str,
    model: str | None,
    stock_id: int | None,
    symbol: str | None,
    portfolio_id: int | None,
    user_prompt: str | None,
    backtest_id: int | None,
    action: str | None,
    quantity: int | None,
    price: float | None,
) -> list[str]:
    errors: list[str] = []
    if mode not in SUPPORTED_MODES:
        errors.append(f"Unsupported analysis mode: {mode}")
        return errors
    if not (model or "").strip() and not settings.ollama_default_model:
        errors.append("Model is required.")
    meta = SUPPORTED_MODES[mode]
    if meta["requires_stock"] and not stock_id and not (symbol or "").strip():
        errors.append("Select a stock for this analysis mode.")
    if meta["requires_portfolio"] and not portfolio_id:
        errors.append("Select a portfolio for this analysis mode.")
    if mode == "nl_screener" and len((user_prompt or "").strip()) < 3:
        errors.append("Describe the screen you want to run (at least 3 characters).")
    if mode == "backtest_interpreter" and not backtest_id:
        errors.append("Select a backtest run before using Backtest Interpreter.")
    if mode == "pre_trade_advisor":
        if not action or action.upper() not in ("BUY", "SELL"):
            errors.append("Select BUY or SELL for the pre-trade check.")
        if not quantity or quantity < 1:
            errors.append("Quantity must be at least 1.")
        if not price or price <= 0:
            errors.append("Enter a valid price.")
    return errors


def build_stock_context(db: Session, stock_id: int | None) -> dict[str, Any]:
    if not stock_id:
        return {"selected": None, "findings": [], "findings_count": 0}
    stock = db.get(Stock, stock_id)
    if stock is None:
        return {"selected": None, "error": "Stock not found.", "findings": [], "findings_count": 0}
    findings = generate_stock_algo_findings(db, stock_id, limit=15)
    return {
        "selected": {
            "id": stock.id,
            "symbol": stock.symbol,
            "exchange": stock.exchange,
            "company_name": stock.company_name or stock.symbol,
            "yahoo_symbol": stock.yahoo_symbol,
            "sector": stock.sector,
            "industry": stock.industry,
        },
        "findings": [
            {
                "algorithm_name": row.get("algorithm_name"),
                "action": row.get("action"),
                "confidence_score": row.get("confidence_score"),
                "status": row.get("status"),
            }
            for row in findings
        ],
        "findings_count": len(findings),
    }


def build_portfolio_context(db: Session, portfolio_id: int | None, user_id: int) -> dict[str, Any]:
    if not portfolio_id:
        return {"selected": None}
    portfolio = db.scalar(
        select(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.user_id == user_id)
    )
    if portfolio is None:
        return {"selected": None, "error": "Portfolio not found."}
    summary = calculate_portfolio_value(db, portfolio_id)
    holdings_count = int(
        db.scalar(
            select(func.count())
            .select_from(PortfolioHolding)
            .where(PortfolioHolding.portfolio_id == portfolio_id)
        )
        or 0
    )
    trades_count = int(
        db.scalar(
            select(func.count()).select_from(PaperTrade).where(PaperTrade.portfolio_id == portfolio_id)
        )
        or 0
    )
    return {
        "selected": {
            "id": portfolio.id,
            "name": portfolio.portfolio_name,
            "type": portfolio.portfolio_type,
        },
        "total_value": summary.get("total_value"),
        "holdings_count": holdings_count,
        "trades_count": trades_count,
        "journal_ready": trades_count >= 10,
    }


def build_prompt_preview_context(
    *,
    mode: str,
    model: str | None,
    stock_context: dict[str, Any] | None,
    portfolio_context: dict[str, Any] | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stock = (stock_context or {}).get("selected")
    portfolio = (portfolio_context or {}).get("selected")
    items = [
        {"label": "Mode", "value": SUPPORTED_MODES.get(mode, {}).get("label", mode)},
        {"label": "Model", "value": model or settings.ollama_default_model},
        {"label": "Stock", "value": f"{stock['symbol']} ({stock['exchange']})" if stock else "—"},
        {"label": "Portfolio", "value": portfolio["name"] if portfolio else "—"},
    ]
    if stock_context and stock_context.get("findings_count") is not None:
        items.append({"label": "Algo findings", "value": str(stock_context["findings_count"])})
    if portfolio_context:
        items.append({"label": "Paper trades", "value": str(portfolio_context.get("trades_count", 0))})
    if extra:
        for key, value in extra.items():
            if value is not None and value != "":
                items.append({"label": key.replace("_", " ").title(), "value": str(value)})
    return {
        "context_items": items,
        "disclaimer": "Prompt text is built server-side; API keys are never exposed.",
    }


def _attach_raw_debug(view: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    try:
        view["raw_json"] = json.dumps(to_json_safe(raw), indent=2)
    except Exception:
        logger.exception("ai_screener.serialization_failed")
        view["raw_json"] = None
        view["raw_json_error"] = "Raw debug payload unavailable due to serialization error."
    return view


def shape_analysis_view(
    mode: str,
    raw: dict[str, Any],
    *,
    user_prompt: str | None = None,
) -> dict[str, Any]:
    disclaimer = raw.get("disclaimer") or DISCLAIMER
    if raw.get("error"):
        view = {
            "ok": False,
            "error": str(raw["error"]),
            "disclaimer": disclaimer,
            "mode": mode,
            "sections": [],
            "highlights": [],
            "stocks": [],
        }
        return _attach_raw_debug(view, raw)

    sections: list[dict[str, Any]] = []
    highlights: list[dict[str, str]] = []
    nl_screener: dict[str, Any] | None = None

    if mode == "signal_synthesizer":
        consensus = raw.get("consensus", "NEUTRAL")
        highlights = [
            {"label": "Consensus", "value": str(consensus)},
            {"label": "Strength", "value": f"{raw.get('consensus_strength', 0)}/100"},
        ]
        sections = [
            {"title": "Headline", "body": raw.get("headline"), "tone": "info"},
            {"title": "Summary", "body": raw.get("summary")},
            {"title": "Key risk", "body": raw.get("key_risk"), "tone": "warning"},
            {"title": "Educational note", "body": raw.get("educational_note"), "tone": "success"},
            {"title": "Agreement note", "body": raw.get("agreement_note")},
        ]
    elif mode == "backtest_interpreter":
        highlights = [{"label": "Verdict", "value": str(raw.get("verdict", "—"))}]
        sections = [
            {"title": "Headline", "body": raw.get("headline")},
            {"title": "Interpretation", "body": raw.get("interpretation")},
            {"title": "Improvement tip", "body": raw.get("improvement_tip"), "tone": "info"},
        ]
        for flag in raw.get("red_flags") or []:
            sections.append({"title": "Red flag", "body": flag, "tone": "warning"})
    elif mode == "pre_trade_advisor":
        highlights = [{"label": "Reasoning quality", "value": str(raw.get("reasoning_quality", "—"))}]
        sections = [
            {"title": "What you got right", "body": raw.get("positive_note"), "tone": "success"},
            {"title": "Risk / reward", "body": raw.get("risk_reward_note"), "tone": "info"},
            {"title": "Educational note", "body": raw.get("educational_note")},
        ]
        for item in raw.get("considerations") or []:
            sections.append({"title": "Consideration", "body": item, "tone": "warning"})
    elif mode == "nl_screener":
        prompt_text = user_prompt or raw.get("user_prompt") or ""
        nl_screener = build_nl_screener_result_view(raw, prompt_text)
        highlights = [
            {"label": "Matched stocks", "value": str(nl_screener.get("matched_count", 0))},
            {"label": "Return basis", "value": str(nl_screener.get("return_basis") or "—")},
            {
                "label": "Filter used",
                "value": (nl_screener.get("filter_label") or "—")[:120],
            },
        ]
        if nl_screener.get("return_basis_note"):
            latest_date = None
            stocks = nl_screener.get("stocks") or []
            if stocks:
                latest_date = stocks[0].get("latest_date")
            highlights.append(
                {"label": "Latest data", "value": str(latest_date or "—")}
            )
        sections = [
            {"title": "Interpreted query", "body": nl_screener.get("interpreted_query")},
            {"title": "Summary", "body": nl_screener.get("summary"), "tone": "info"},
        ]
        if nl_screener.get("return_basis_note"):
            sections.append(
                {
                    "title": "Return basis",
                    "body": nl_screener.get("return_basis_note"),
                    "tone": "info",
                }
            )
        for warning in nl_screener.get("warnings") or []:
            sections.append({"title": "Note", "body": warning, "tone": "info"})
    elif mode == "portfolio_health":
        highlights = [
            {"label": "Health score", "value": str(raw.get("health_score", "—"))},
            {"label": "Label", "value": str(raw.get("health_label", "—"))},
        ]
        sections = [
            {"title": "Narrative", "body": raw.get("narrative")},
            {"title": "Top concern", "body": raw.get("top_concern"), "tone": "warning"},
        ]
    elif mode == "journal_insights":
        sections = [
            {"title": "Summary", "body": raw.get("summary")},
        ]
        for item in raw.get("patterns_found") or []:
            sections.append({"title": "Pattern", "body": item})
        for item in raw.get("biases_detected") or []:
            sections.append({"title": "Bias", "body": item, "tone": "warning"})
        for item in raw.get("strengths") or []:
            sections.append({"title": "Strength", "body": item, "tone": "success"})
        for item in raw.get("improvement_areas") or []:
            sections.append({"title": "Improve", "body": item, "tone": "info"})

    sections = [s for s in sections if s.get("body")]
    stocks: list[dict[str, Any]] = []
    if mode == "nl_screener" and nl_screener:
        stocks = nl_screener.get("stocks") or []
    elif mode == "nl_screener":
        stocks = []

    view = {
        "ok": True,
        "error": None,
        "disclaimer": disclaimer,
        "mode": mode,
        "mode_label": SUPPORTED_MODES.get(mode, {}).get("label", mode),
        "sections": sections,
        "highlights": highlights,
        "stocks": stocks,
        "nl_screener": nl_screener,
    }
    return _attach_raw_debug(view, raw)


def validation_error_view(mode: str, errors: list[str]) -> dict[str, Any]:
    friendly = (
        "Could not run AI analysis. "
        + " ".join(errors)
        if errors
        else "Check the selected mode and inputs."
    )
    if mode != "backtest_interpreter" and any("backtest" in err.lower() for err in errors):
        friendly = (
            "Could not run AI analysis. backtest_id is only required for Backtest Interpreter. "
            "Please check the selected mode and inputs."
        )
    return {
        "ok": False,
        "error": friendly,
        "disclaimer": PAGE_DISCLAIMER,
        "mode": mode,
        "sections": [],
        "highlights": [],
        "stocks": [],
        "validation_errors": errors,
    }


async def execute_analysis(
    db: Session,
    user: User,
    *,
    mode: str,
    model: str | None,
    stock_id: int | None,
    symbol: str | None,
    portfolio_id: int | None,
    user_prompt: str | None,
    backtest_id: int | None,
    action: str | None,
    quantity: int | None,
    price: float | None,
    notes: str | None,
) -> dict[str, Any]:
    from app.routers.ai import (
        api_analyze_journal,
        api_evaluate_trade,
        api_interpret_backtest,
        api_nl_screener,
        api_portfolio_narrative,
        api_synthesize_signals,
    )

    resolved_model = (model or "").strip() or None
    logger.info(
        "ai_think_tank.execute_analysis start mode=%s model=%s portfolio_id=%s stock_id=%s backtest_id=%s",
        mode,
        resolved_model,
        portfolio_id,
        stock_id,
        backtest_id,
    )

    if mode == "signal_synthesizer":
        stock = db.get(Stock, stock_id) if stock_id else None
        if stock is None and symbol:
            stock = db.scalar(select(Stock).where(Stock.symbol == symbol.upper()))
        if stock is None:
            raise HTTPException(status_code=404, detail="Stock not found")
        findings = generate_stock_algo_findings(db, stock.id, limit=15)
        payload = AISynthesizeSignalsRequest(
            symbol=stock.symbol,
            findings=findings,
            model=resolved_model,
        )
        return await api_synthesize_signals(payload, db=db, current_user=user)

    if mode == "backtest_interpreter":
        payload = AIInterpretBacktestRequest(backtest_id=int(backtest_id), model=resolved_model)
        return await api_interpret_backtest(payload, db=db, current_user=user)

    if mode == "pre_trade_advisor":
        stock = db.get(Stock, stock_id) if stock_id else None
        sym = stock.symbol if stock else (symbol or "").upper()
        payload = AIEvaluateTradeRequest(
            symbol=sym,
            action=(action or "BUY").upper(),
            quantity=int(quantity or 1),
            price=float(price or 0),
            notes=notes or "",
            portfolio_id=int(portfolio_id),
            stock_id=stock_id,
            model=resolved_model,
        )
        return await api_evaluate_trade(payload, db=db, current_user=user)

    if mode == "nl_screener":
        query = (user_prompt or "").strip()
        deterministic = run_deterministic_nl_screener(db, query)
        if deterministic is not None:
            deterministic["user_prompt"] = query
            deterministic["disclaimer"] = DISCLAIMER
            return deterministic

        payload = AINLScreenerRequest(query=query, model=resolved_model)
        api_result = await api_nl_screener(payload, db=db, current_user=user)
        if api_result.get("error"):
            return api_result
        shaped = build_nl_screener_view_from_api(api_result, query)
        shaped["user_prompt"] = query
        shaped["disclaimer"] = api_result.get("disclaimer") or DISCLAIMER
        return shaped

    if mode == "portfolio_health":
        return await api_portfolio_narrative(
            int(portfolio_id),
            model=resolved_model,
            db=db,
            current_user=user,
        )

    if mode == "journal_insights":
        return await api_analyze_journal(
            int(portfolio_id),
            model=resolved_model,
            db=db,
            current_user=user,
        )

    raise HTTPException(status_code=400, detail=f"Unsupported mode: {mode}")


async def fetch_model_status(db: Session, model: str | None = None) -> dict[str, Any]:
    from app.routers.ai import ai_status

    return await ai_status(model=model, db=db)
