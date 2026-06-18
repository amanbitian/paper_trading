from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import SessionLocal
from app.models.telemetry import SearchQueryLog


logger = logging.getLogger(__name__)


def record_search_query(
    *,
    search_type: str,
    query_text: str,
    duration_ms: float,
    result_count: int = 0,
    filter_name: str | None = None,
    filter_value: str | None = None,
    status: str = "ok",
    error_message: str | None = None,
) -> None:
    """Persist search timing without coupling telemetry to the request transaction."""
    try:
        with SessionLocal() as db:
            db.add(
                SearchQueryLog(
                    search_type=search_type[:50],
                    query_text=query_text.strip()[:1000] or "<blank>",
                    filter_name=filter_name[:50] if filter_name else None,
                    filter_value=filter_value[:120] if filter_value else None,
                    result_count=max(0, int(result_count or 0)),
                    duration_ms=round(float(duration_ms), 3),
                    status=status[:30],
                    error_message=error_message[:2000] if error_message else None,
                )
            )
            db.commit()
    except SQLAlchemyError:
        logger.warning("Search telemetry could not be recorded", exc_info=True)


def get_search_latency_summary(*, recent_limit: int = 25) -> dict[str, Any]:
    try:
        with SessionLocal() as db:
            totals = db.execute(
                text(
                    """
                    SELECT
                        COUNT(*)::int AS total_searches,
                        AVG(duration_ms)::float AS avg_response_ms,
                        MAX(duration_ms)::float AS max_response_ms,
                        percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)::float AS p95_response_ms,
                        MAX(created_at) AS latest_search_at
                    FROM search_query_logs
                    """
                )
            ).mappings().first()

            recent_rows = db.execute(
                text(
                    """
                    SELECT
                        id,
                        search_type,
                        query_text,
                        filter_name,
                        filter_value,
                        result_count,
                        duration_ms::float AS duration_ms,
                        status,
                        created_at
                    FROM search_query_logs
                    ORDER BY created_at DESC
                    LIMIT :limit
                    """
                ),
                {"limit": recent_limit},
            ).mappings().all()

            average_rows = db.execute(
                text(
                    """
                    SELECT
                        search_type,
                        query_text,
                        filter_name,
                        filter_value,
                        COUNT(*)::int AS search_count,
                        AVG(duration_ms)::float AS avg_response_ms,
                        MAX(duration_ms)::float AS max_response_ms,
                        MAX(created_at) AS latest_search_at
                    FROM search_query_logs
                    GROUP BY search_type, query_text, filter_name, filter_value
                    ORDER BY search_count DESC, latest_search_at DESC
                    LIMIT 50
                    """
                )
            ).mappings().all()
    except SQLAlchemyError:
        logger.warning("Search telemetry summary could not be loaded", exc_info=True)
        return {
            "total_searches": 0,
            "avg_response_ms": None,
            "max_response_ms": None,
            "p95_response_ms": None,
            "latest_search_at": None,
            "recent_searches": [],
            "average_by_query": [],
        }

    return {
        "total_searches": int(totals["total_searches"] or 0) if totals else 0,
        "avg_response_ms": totals["avg_response_ms"] if totals else None,
        "max_response_ms": totals["max_response_ms"] if totals else None,
        "p95_response_ms": totals["p95_response_ms"] if totals else None,
        "latest_search_at": totals["latest_search_at"] if totals else None,
        "recent_searches": [dict(row) for row in recent_rows],
        "average_by_query": [dict(row) for row in average_rows],
    }
