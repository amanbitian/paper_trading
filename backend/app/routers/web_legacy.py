from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Request

from app.services.web_legacy_service import start_legacy_mode
from app.web_utils import templates

logger = logging.getLogger(__name__)
timing_logger = logging.getLogger("app.timing")

router = APIRouter(prefix="/web", tags=["web-legacy"])


@router.post("/legacy/start", include_in_schema=False)
def legacy_start(request: Request):
    started_at = time.perf_counter()
    result = start_legacy_mode()
    duration_ms = (time.perf_counter() - started_at) * 1000
    timing_logger.info(
        "operation=web_route route=/web/legacy/start status=%s duration_ms=%.2f",
        "ok" if result.get("ok") else "unavailable",
        duration_ms,
    )
    logger.info(
        "legacy.start_request ok=%s url=%s already_running=%s",
        result.get("ok"),
        result.get("url"),
        result.get("already_running"),
    )
    return templates.TemplateResponse(
        "partials/legacy_mode_result.html",
        {"request": request, "result": result},
    )
