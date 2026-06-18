from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.telemetry import AiActionLog

ai_logger = logging.getLogger("app.ai")

MAX_TEXT_FIELD = 12_000
MAX_JSON_CHARS = 16_000


def _truncate_text(value: str | None, limit: int = MAX_TEXT_FIELD) -> str | None:
    if value is None:
        return None
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n... [truncated {len(value) - limit} chars]"


def _safe_json_payload(data: Any) -> dict[str, Any] | list[Any] | None:
    if data is None:
        return None
    try:
        serialized = json.loads(json.dumps(data, default=str))
    except (TypeError, ValueError):
        return {"_raw": str(data)[:MAX_JSON_CHARS]}
    text = json.dumps(serialized, default=str)
    if len(text) <= MAX_JSON_CHARS:
        return serialized
    return {"_truncated": True, "preview": text[:MAX_JSON_CHARS]}


def _preview(text: str, limit: int = 600) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + "..."


def persist_ai_action_log(
    db: Session,
    *,
    user_id: int | None,
    action_type: str,
    endpoint: str,
    http_method: str = "POST",
    model_name: str | None = None,
    ollama_connected: bool | None = None,
    request_data: Any = None,
    response_data: Any = None,
    llm_prompt: str | None = None,
    llm_response: str | None = None,
    cache_hit: bool = False,
    status: str = "ok",
    error_message: str | None = None,
    duration_ms: float = 0.0,
    source: str = "api",
) -> AiActionLog:
    request_payload = _safe_json_payload(request_data)
    response_payload = _safe_json_payload(response_data)
    prompt_stored = _truncate_text(llm_prompt)
    response_stored = _truncate_text(llm_response)

    ai_logger.info(
        "ai_action action=%s endpoint=%s method=%s status=%s user_id=%s model=%s "
        "ollama_url=%s ollama_connected=%s cache_hit=%s duration_ms=%.1f",
        action_type,
        endpoint,
        http_method,
        status,
        user_id,
        model_name,
        settings.ollama_base_url,
        ollama_connected,
        cache_hit,
        duration_ms,
    )
    if request_payload is not None:
        ai_logger.info("ai_request action=%s payload=%s", action_type, json.dumps(request_payload, default=str)[:2000])
    if llm_prompt:
        ai_logger.info(
            "ai_ollama_query action=%s model=%s connected=%s chars=%s preview=%s",
            action_type,
            model_name,
            ollama_connected,
            len(llm_prompt),
            _preview(llm_prompt, 900),
        )
    if llm_response:
        ai_logger.info(
            "ai_ollama_response action=%s model=%s chars=%s preview=%s",
            action_type,
            model_name,
            len(llm_response),
            _preview(llm_response, 900),
        )
    if response_payload is not None:
        ai_logger.info("ai_response action=%s payload=%s", action_type, json.dumps(response_payload, default=str)[:2000])
    if error_message:
        ai_logger.warning("ai_action_error action=%s error=%s", action_type, error_message)

    row = AiActionLog(
        user_id=user_id,
        source=source,
        action_type=action_type,
        endpoint=endpoint,
        http_method=http_method,
        model_name=model_name,
        ollama_base_url=settings.ollama_base_url,
        ollama_connected=ollama_connected,
        request_payload=request_payload,
        response_payload=response_payload,
        llm_prompt=prompt_stored,
        llm_response=response_stored,
        cache_hit=cache_hit,
        status=status,
        error_message=error_message,
        duration_ms=round(duration_ms, 3),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    ai_logger.info("ai_action_saved id=%s action=%s status=%s", row.id, action_type, status)
    return row


def list_ai_action_logs(
    db: Session,
    *,
    user_id: int | None = None,
    action_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    stmt = select(AiActionLog).order_by(desc(AiActionLog.created_at)).limit(limit)
    if user_id is not None:
        stmt = stmt.where(AiActionLog.user_id == user_id)
    if action_type:
        stmt = stmt.where(AiActionLog.action_type == action_type)
    rows = db.scalars(stmt).all()
    return [
        {
            "id": row.id,
            "user_id": row.user_id,
            "source": row.source,
            "action_type": row.action_type,
            "endpoint": row.endpoint,
            "http_method": row.http_method,
            "model_name": row.model_name,
            "ollama_base_url": row.ollama_base_url,
            "ollama_connected": row.ollama_connected,
            "request_payload": row.request_payload,
            "response_payload": row.response_payload,
            "llm_prompt": row.llm_prompt,
            "llm_response": row.llm_response,
            "cache_hit": row.cache_hit,
            "status": row.status,
            "error_message": row.error_message,
            "duration_ms": float(row.duration_ms),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
