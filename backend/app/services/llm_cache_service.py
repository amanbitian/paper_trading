from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.stock import MarketAnalyticsCache

LLM_CACHE_PREFIX = "llm:"


def llm_cache_key(suffix: str) -> str:
    key = f"{LLM_CACHE_PREFIX}{suffix}"
    return key[:80]


def get_llm_cache(db: Session, suffix: str, *, ttl_hours: float | None) -> dict[str, Any] | None:
    row = db.get(MarketAnalyticsCache, llm_cache_key(suffix))
    if row is None:
        return None
    if ttl_hours is not None:
        refreshed = row.refreshed_at
        if refreshed.tzinfo is None:
            refreshed = refreshed.replace(tzinfo=UTC)
        if datetime.now(UTC) - refreshed > timedelta(hours=ttl_hours):
            return None
    payload = row.payload
    return payload if isinstance(payload, dict) else None


def set_llm_cache(db: Session, suffix: str, payload: dict[str, Any]) -> None:
    key = llm_cache_key(suffix)
    stmt = insert(MarketAnalyticsCache).values(
        cache_key=key,
        payload=payload,
        refreshed_at=datetime.now(UTC),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["cache_key"],
        set_={
            "payload": stmt.excluded.payload,
            "refreshed_at": stmt.excluded.refreshed_at,
        },
    )
    db.execute(stmt)
    db.commit()
