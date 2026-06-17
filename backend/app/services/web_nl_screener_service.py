from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.services.stock_performance_service import list_stock_performance
from app.services.web_explore_stock_helpers import stock_route_key
from app.utils.json_safe import to_json_safe

logger = logging.getLogger(__name__)

RETURN_BASIS_1Y_NOTE = (
    "Using stored 1Y return from daily candles (YTD is not computed separately)."
)
MAX_TABLE_ROWS = 50
MAX_MATCHES = 500


def _normalize_prompt(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip().lower())


def _extract_percent(query: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", query)
    if match:
        return float(match.group(1))
    match = re.search(r"(\d+(?:\.\d+)?)\s*percent", query)
    if match:
        return float(match.group(1))
    return None


def _mentions_year_period(query: str) -> bool:
    return any(
        token in query
        for token in (
            "this year",
            "past year",
            "last year",
            "over 1y",
            "1 year",
            "1y",
            "year",
        )
    )


def _mentions_up(query: str) -> bool:
    return any(
        token in query
        for token in (
            "moved up",
            "move up",
            "rose",
            "rise",
            "risen",
            "up by",
            "up ",
            "gained",
            "gain",
            "increased",
            "increase",
            "higher",
            "positive",
        )
    )


def parse_deterministic_screener_intent(query: str) -> dict[str, Any] | None:
    """Rule-based intent for common screener prompts. Returns None if no rule matches."""
    q = _normalize_prompt(query)
    if len(q) < 3:
        return None

    pct = _extract_percent(q)
    if pct is not None and _mentions_year_period(q) and _mentions_up(q):
        return {
            "matched": True,
            "filters": {
                "min_change_1y_pct": pct,
                "sort_by": "change_1y_pct",
                "sort_desc": True,
            },
            "interpreted_query": f"Stocks up at least {pct:g}% over the past year (1Y return)",
            "return_basis": "1Y",
            "return_field": "change_1y_pct",
            "min_return_pct": pct,
            "reason_template": "1Y return >= {pct:g}%",
        }

    if ("bank" in q or "banking" in q) and ("volume" in q or "high volume" in q):
        return {
            "matched": True,
            "filters": {
                "sort_by": "latest_volume",
                "sort_desc": True,
            },
            "interpreted_query": "Banking / financial sector stocks sorted by latest volume",
            "return_basis": "volume",
            "return_field": None,
            "min_return_pct": None,
            "reason_template": "Banking sector, sorted by volume",
            "sector_keywords": ("bank", "financ"),
        }

    if (
        any(token in q for token in ("it stock", "it stocks", "information technology"))
        and any(token in q for token in ("fell", "down", "decline", "negative"))
        and any(token in q for token in ("recovered", "recover", "up last month", "up in the last month"))
    ):
        return {
            "matched": True,
            "filters": {
                "sector": "Information Technology",
                "max_change_1y_pct": -0.01,
                "min_change_1m_pct": 0.01,
                "sort_by": "change_1m_pct",
                "sort_desc": True,
            },
            "interpreted_query": (
                "IT stocks down over 1Y but positive over the last month"
            ),
            "return_basis": "1M vs 1Y",
            "return_field": "change_1m_pct",
            "min_return_pct": None,
            "reason_template": "1Y return negative, 1M return positive",
        }

    return None


def _format_latest_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return value[:10] if len(value) >= 10 else value
    return str(value)


def _stock_reason(
    row: dict[str, Any],
    *,
    return_basis: str | None,
    return_field: str | None,
    min_return_pct: float | None,
    reason_template: str | None,
) -> str:
    if reason_template and min_return_pct is not None:
        return reason_template.format(pct=min_return_pct)
    if reason_template:
        return reason_template
    if return_field and row.get(return_field) is not None:
        pct = float(row[return_field])
        label = return_basis or return_field.replace("change_", "").replace("_pct", "").upper()
        return f"{label} return {pct:+.1f}%"
    if return_basis == "volume":
        return "High latest volume (banking/financial sector)"
    return "Matched screener filters"


def build_nl_screener_stock_row(
    row: dict[str, Any],
    *,
    return_basis: str | None = None,
    return_field: str | None = None,
    min_return_pct: float | None = None,
    reason_template: str | None = None,
) -> dict[str, Any]:
    route_key = stock_route_key(row)
    return_pct = None
    if return_field:
        return_pct = row.get(return_field)
    elif return_basis == "1Y":
        return_pct = row.get("change_1y_pct")

    return {
        "company_name": row.get("company_name") or row.get("symbol") or "—",
        "symbol": row.get("symbol") or "—",
        "exchange": row.get("exchange") or "—",
        "route_key": route_key or None,
        "detail_url": f"/web/explore?stock={route_key}" if route_key else None,
        "latest_price": row.get("latest_price"),
        "latest_date": _format_latest_date(row.get("latest_price_datetime")),
        "return_pct": return_pct,
        "volume": row.get("latest_volume"),
        "sector": row.get("sector") or "—",
        "industry": row.get("industry") or "—",
        "reason": _stock_reason(
            row,
            return_basis=return_basis,
            return_field=return_field,
            min_return_pct=min_return_pct,
            reason_template=reason_template,
        ),
        "change_1m_pct": row.get("change_1m_pct"),
        "change_3m_pct": row.get("change_3m_pct"),
        "change_1y_pct": row.get("change_1y_pct"),
    }


def _apply_sector_keyword_filter(
    rows: list[dict[str, Any]], keywords: tuple[str, ...]
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for row in rows:
        sector = (row.get("sector") or "").lower()
        industry = (row.get("industry") or "").lower()
        if any(keyword in sector or keyword in industry for keyword in keywords):
            filtered.append(row)
    return filtered


def run_deterministic_nl_screener(db: Session, query: str) -> dict[str, Any] | None:
    intent = parse_deterministic_screener_intent(query)
    if not intent or not intent.get("matched"):
        return None

    filters = dict(intent.get("filters") or {})
    sector_keywords = intent.get("sector_keywords")
    list_filters = {k: v for k, v in filters.items() if k in {
        "sector", "exchange",
        "min_change_1m_pct", "max_change_1m_pct",
        "min_change_3m_pct", "max_change_3m_pct",
        "min_change_6m_pct", "max_change_6m_pct",
        "min_change_1y_pct", "max_change_1y_pct",
        "sort_by", "sort_desc",
    }}

    rows = list_stock_performance(
        db,
        limit=MAX_MATCHES,
        only_with_prices=True,
        **list_filters,
    )
    if sector_keywords:
        rows = _apply_sector_keyword_filter(rows, sector_keywords)

    stock_rows = [
        build_nl_screener_stock_row(
            row,
            return_basis=intent.get("return_basis"),
            return_field=intent.get("return_field"),
            min_return_pct=intent.get("min_return_pct"),
            reason_template=intent.get("reason_template"),
        )
        for row in rows
    ]

    matched_count = len(stock_rows)
    logger.info(
        "ai_screener prompt=%r interpreted=%s matched_count=%s return_basis=%s deterministic=true",
        query[:120],
        intent.get("interpreted_query"),
        matched_count,
        intent.get("return_basis"),
    )

    warnings: list[str] = []
    if intent.get("return_basis") == "1Y":
        warnings.append(RETURN_BASIS_1Y_NOTE)

    summary = (
        f"{matched_count} stock{'s' if matched_count != 1 else ''} matched: "
        f"{intent.get('interpreted_query')}."
        if matched_count
        else f"No stocks matched: {intent.get('interpreted_query')}."
    )

    return {
        "filters": to_json_safe(filters),
        "explanation": intent.get("interpreted_query"),
        "confidence": "HIGH",
        "count": matched_count,
        "stocks": stock_rows,
        "interpreted_query": intent.get("interpreted_query"),
        "return_basis": intent.get("return_basis"),
        "return_basis_note": RETURN_BASIS_1Y_NOTE if intent.get("return_basis") == "1Y" else None,
        "summary": summary,
        "warnings": warnings,
        "deterministic": True,
    }


def build_nl_screener_view_from_api(
    raw: dict[str, Any], user_prompt: str
) -> dict[str, Any]:
    """Convert API/LLM screener payload into a JSON-safe web view model."""
    filters = raw.get("filters") or {}
    return_basis = None
    return_field = None
    min_return_pct = None
    if filters.get("min_change_1y_pct") is not None or filters.get("max_change_1y_pct") is not None:
        return_basis = "1Y"
        return_field = "change_1y_pct"
        min_return_pct = filters.get("min_change_1y_pct")
    elif filters.get("min_change_1m_pct") is not None:
        return_basis = "1M"
        return_field = "change_1m_pct"
        min_return_pct = filters.get("min_change_1m_pct")
    elif filters.get("sort_by") == "latest_volume":
        return_basis = "volume"
        return_field = None

    rows = raw.get("stocks") or []
    stock_rows = [
        build_nl_screener_stock_row(
            row,
            return_basis=return_basis,
            return_field=return_field,
            min_return_pct=min_return_pct,
        )
        for row in rows
    ]
    matched_count = raw.get("count", len(stock_rows))
    interpreted = raw.get("explanation") or raw.get("interpreted_query") or user_prompt

    logger.info(
        "ai_screener prompt=%r interpreted=%s matched_count=%s return_basis=%s deterministic=false",
        user_prompt[:120],
        interpreted,
        matched_count,
        return_basis,
    )

    warnings: list[str] = []
    if return_basis == "1Y":
        warnings.append(RETURN_BASIS_1Y_NOTE)

    return {
        **{k: v for k, v in raw.items() if k != "stocks"},
        "filters": to_json_safe(filters),
        "stocks": stock_rows,
        "interpreted_query": interpreted,
        "return_basis": return_basis,
        "return_basis_note": RETURN_BASIS_1Y_NOTE if return_basis == "1Y" else None,
        "summary": raw.get("summary")
        or (
            f"{matched_count} stocks matched: {interpreted}."
            if matched_count
            else f"No stocks matched: {interpreted}."
        ),
        "warnings": warnings,
        "count": matched_count,
    }


def _rows_are_view_models(stocks: list[Any]) -> bool:
    return bool(stocks) and isinstance(stocks[0], dict) and "detail_url" in stocks[0]


def build_nl_screener_result_view(
    raw: dict[str, Any], user_prompt: str
) -> dict[str, Any]:
    """Top-level NL screener block for templates."""
    if raw.get("error"):
        return {"ok": False, "error": str(raw["error"])}

    if _rows_are_view_models(raw.get("stocks") or []):
        payload = raw
    else:
        payload = build_nl_screener_view_from_api(raw, user_prompt)

    matched_count = int(payload.get("count") or len(payload.get("stocks") or []))
    stocks = list(payload.get("stocks") or [])
    table_limit = min(len(stocks), MAX_TABLE_ROWS)

    return {
        "mode": "nl_screener",
        "title": "Natural Language Screener",
        "user_prompt": user_prompt,
        "interpreted_query": payload.get("interpreted_query") or payload.get("explanation") or user_prompt,
        "summary": payload.get("summary") or "",
        "matched_count": matched_count,
        "table_limit": table_limit,
        "showing_note": (
            f"Showing top {table_limit} of {matched_count} matches."
            if matched_count > table_limit
            else None
        ),
        "stocks": stocks[:table_limit],
        "warnings": payload.get("warnings") or [],
        "return_basis": payload.get("return_basis"),
        "return_basis_note": payload.get("return_basis_note"),
        "filter_label": str(payload.get("filters") or {}),
        "confidence": payload.get("confidence"),
        "deterministic": bool(payload.get("deterministic")),
    }
