from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import desc, select
from sqlalchemy.orm import Session, joinedload

from app.models.stock import Stock
from app.models.strategy import StrategySignal, StrategyTemplate, UserStrategy
from app.models.user import User
from app.schemas.strategy import (
    GenerateSignalRequest,
    StrategyPreviewRequest,
    UserStrategyCreate,
)
from app.services.market_data_service import get_latest_price
from app.services.strategy_service import create_user_strategy, generate_signal, preview_signal
from app.services.web_backtesting_helpers import (
    PARAMETER_CONFIG,
    get_strategy_template,
    validate_strategy_parameters,
)
from app.services.web_trading_helpers import _holding_quantity

logger = logging.getLogger(__name__)

SIGNAL_ACTION_TONES = {
    "BUY": "positive",
    "SELL": "negative",
    "HOLD": "neutral",
    "NO_SIGNAL": "neutral",
}


def parse_parameters_json(raw: str | None, template: StrategyTemplate) -> dict[str, Any]:
    parameters = dict(template.default_parameters or {})
    if raw and str(raw).strip():
        try:
            overrides = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("Strategy parameters JSON is invalid.") from exc
        if isinstance(overrides, dict):
            parameters.update(overrides)
    return parameters


def parse_risk_per_trade(value: str | None) -> float:
    if value is None or not str(value).strip():
        return 1.0
    try:
        parsed = float(str(value).strip())
    except ValueError as exc:
        raise ValueError("Risk per trade % must be a number.") from exc
    if parsed <= 0 or parsed > 10:
        raise ValueError("Risk per trade % must be between 0.1 and 10.")
    return parsed


def validate_create_strategy_form(
    *,
    portfolio_id: int | None,
    strategy_template_id: int | None,
    strategy_type: str,
    parameters: dict[str, Any],
    risk_per_trade_pct: float,
) -> list[str]:
    errors: list[str] = []
    if not portfolio_id:
        errors.append("Select a portfolio.")
    if not strategy_template_id:
        errors.append("Select a strategy template.")
    errors.extend(validate_strategy_parameters(strategy_type, parameters))
    if risk_per_trade_pct <= 0 or risk_per_trade_pct > 10:
        errors.append("Risk per trade % must be between 0.1 and 10.")
    return errors


def validate_generate_signal_form(
    *,
    user_strategy_id: int | None,
    stock_id: int | None,
) -> list[str]:
    errors: list[str] = []
    if not user_strategy_id:
        errors.append("Select or create a user strategy first.")
    if not stock_id:
        errors.append("Select a stock before generating a signal.")
    return errors


def build_stock_context(
    db: Session,
    *,
    user_id: int,
    stock_id: int,
    portfolio_id: int | None,
) -> dict[str, Any] | None:
    stock = db.get(Stock, stock_id)
    if stock is None:
        return None
    latest_price = get_latest_price(db, stock_id)
    holding_qty = Decimal("0")
    if portfolio_id:
        holding_qty = _holding_quantity(db, portfolio_id, stock_id)
    return {
        "id": stock.id,
        "symbol": stock.symbol,
        "exchange": stock.exchange,
        "company_name": stock.company_name or stock.symbol,
        "yahoo_symbol": stock.yahoo_symbol,
        "sector": stock.sector,
        "industry": stock.industry,
        "latest_price": float(latest_price) if latest_price is not None else None,
        "holding_qty": float(holding_qty),
        "has_holding": holding_qty > 0,
    }


def serialize_user_strategy_rows(
    db: Session,
    strategies: list[UserStrategy],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for strategy in strategies:
        template = strategy.strategy_template or db.get(StrategyTemplate, strategy.strategy_template_id)
        risk = strategy.risk_settings or {}
        params = strategy.parameters or {}
        param_keys = list(params.keys())[:4]
        summary = ", ".join(f"{key}={params[key]}" for key in param_keys)
        if len(params) > 4:
            summary += ", …"
        rows.append(
            {
                "id": strategy.id,
                "strategy_name": strategy.strategy_name,
                "template_name": template.strategy_name if template else "Template",
                "portfolio_id": strategy.portfolio_id,
                "parameters_summary": summary or "Defaults",
                "risk_per_trade_pct": risk.get("risk_per_trade_pct", 1),
                "is_enabled": strategy.is_enabled,
                "created_at": strategy.created_at,
            }
        )
    return rows


def list_user_strategy_models(
    db: Session,
    user_id: int,
    *,
    portfolio_id: int | None = None,
) -> list[UserStrategy]:
    stmt = (
        select(UserStrategy)
        .where(UserStrategy.user_id == user_id)
        .options(joinedload(UserStrategy.strategy_template))
        .order_by(UserStrategy.id.desc())
    )
    if portfolio_id:
        stmt = stmt.where(UserStrategy.portfolio_id == portfolio_id)
    return list(db.scalars(stmt))


def serialize_activity_log(db: Session, signals: list[StrategySignal]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for signal in signals:
        strategy = signal.user_strategy
        stock = signal.stock or db.get(Stock, signal.stock_id)
        rows.append(
            {
                "time": signal.created_at,
                "event_type": "SIGNAL",
                "strategy": strategy.strategy_name if strategy else f"#{signal.user_strategy_id}",
                "symbol": stock.symbol if stock else str(signal.stock_id),
                "status": signal.signal_type,
                "message": signal.reason or "",
                "executed": signal.executed_as_order,
            }
        )
    return rows


def list_recent_signals(db: Session, user_id: int, *, limit: int = 25) -> list[StrategySignal]:
    return list(
        db.scalars(
            select(StrategySignal)
            .join(UserStrategy)
            .where(UserStrategy.user_id == user_id)
            .options(
                joinedload(StrategySignal.user_strategy).joinedload(UserStrategy.strategy_template),
                joinedload(StrategySignal.stock),
            )
            .order_by(StrategySignal.created_at.desc())
            .limit(limit)
        )
    )


def serialize_signal_preview(
    *,
    signal_type: str,
    confidence_score: float | None,
    suggested_price: Any,
    suggested_quantity: Any = None,
    strategy_name: str,
    risk_per_trade_pct: float | None = None,
    reason: str | None = None,
    indicators: dict | None = None,
    warnings: list[str] | None = None,
    signal_id: int | None = None,
    stock_label: str | None = None,
    persisted: bool = False,
) -> dict[str, Any]:
    return {
        "signal_id": signal_id,
        "signal_type": signal_type,
        "action_tone": SIGNAL_ACTION_TONES.get(signal_type.upper(), "neutral"),
        "confidence_score": confidence_score,
        "suggested_price": suggested_price,
        "suggested_quantity": suggested_quantity,
        "strategy_name": strategy_name,
        "risk_per_trade_pct": risk_per_trade_pct,
        "reason": reason,
        "indicators": indicators or {},
        "warnings": warnings or [],
        "stock_label": stock_label,
        "persisted": persisted,
        "stop_price": (indicators or {}).get("stop_price"),
        "stop_pct": (indicators or {}).get("stop_pct"),
    }


def run_create_user_strategy(
    db: Session,
    user: User,
    *,
    portfolio_id: int,
    strategy_template_id: int,
    strategy_name: str | None,
    parameters: dict[str, Any],
    risk_per_trade_pct: float,
) -> UserStrategy:
    payload = UserStrategyCreate(
        portfolio_id=portfolio_id,
        strategy_template_id=strategy_template_id,
        strategy_name=strategy_name,
        parameters=parameters,
        risk_settings={"risk_per_trade_pct": risk_per_trade_pct},
        is_enabled=True,
    )
    return create_user_strategy(db, user, payload)


def run_generate_signal(
    db: Session,
    user: User,
    *,
    user_strategy_id: int,
    stock_id: int,
) -> dict[str, Any]:
    signal = generate_signal(
        db,
        user,
        GenerateSignalRequest(user_strategy_id=user_strategy_id, stock_id=stock_id),
    )
    user_strategy = db.get(UserStrategy, signal.user_strategy_id)
    stock = db.get(Stock, signal.stock_id)
    risk = (user_strategy.risk_settings or {}) if user_strategy else {}
    warnings: list[str] = []
    if signal.signal_type in {"HOLD", "NO_SIGNAL"}:
        warnings.append("No actionable BUY/SELL signal for the latest candles.")
    if float(signal.suggested_quantity or 0) <= 0 and signal.signal_type == "BUY":
        warnings.append("Suggested quantity is zero — check risk settings, stop loss, or available capital.")
    if signal.suggested_price is None:
        warnings.append("Latest price is unavailable for this stock.")
    return serialize_signal_preview(
        signal_id=signal.id,
        signal_type=signal.signal_type,
        confidence_score=float(signal.confidence_score),
        suggested_price=signal.suggested_price,
        suggested_quantity=signal.suggested_quantity,
        strategy_name=user_strategy.strategy_name if user_strategy else "Strategy",
        risk_per_trade_pct=risk.get("risk_per_trade_pct"),
        reason=signal.reason,
        indicators=signal.indicators or {},
        warnings=warnings,
        stock_label=f"{stock.symbol} ({stock.exchange})" if stock else None,
        persisted=True,
    )


def run_preview_signal(
    db: Session,
    *,
    stock_id: int,
    strategy_template_id: int,
    parameters: dict[str, Any],
    risk_per_trade_pct: float | None = None,
) -> dict[str, Any]:
    preview = preview_signal(
        db,
        StrategyPreviewRequest(
            stock_id=stock_id,
            instrument_type="stock",
            strategy_template_id=strategy_template_id,
            parameters=parameters,
        ),
    )
    warnings: list[str] = []
    if preview.get("signal_type") in {"HOLD", "NO_SIGNAL"}:
        warnings.append("Preview did not produce an actionable BUY/SELL signal.")
    if preview.get("suggested_price") is None:
        warnings.append("Latest price is unavailable — sync market data for this stock.")
    stock = db.get(Stock, stock_id)
    return serialize_signal_preview(
        signal_type=str(preview.get("signal_type") or "NO_SIGNAL"),
        confidence_score=preview.get("confidence_score"),
        suggested_price=preview.get("suggested_price"),
        strategy_name=str(preview.get("strategy_name") or "Strategy"),
        risk_per_trade_pct=risk_per_trade_pct,
        reason=preview.get("reason"),
        indicators=preview.get("indicators") or {},
        warnings=warnings,
        stock_label=f"{stock.symbol} ({stock.exchange})" if stock else None,
        persisted=False,
    )


def http_error_message(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, list):
        return "; ".join(str(item) for item in detail)
    return str(detail)
