from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.stock import Stock, StockDetailSnapshot

SOURCE_VERSION = "stock-detail-v1"
DEFAULT_TTL_HOURS = 12


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def get_stock_detail_snapshot(
    db: Session,
    stock_id: int,
    *,
    allow_stale: bool = True,
) -> dict[str, Any] | None:
    row = db.get(StockDetailSnapshot, stock_id)
    if row is None:
        return None
    if row.source_version != SOURCE_VERSION:
        return None
    now = datetime.now(UTC)
    is_stale = bool(row.expires_at and row.expires_at <= now)
    if is_stale and not allow_stale:
        return None

    chart_json = row.chart_json
    findings = list(row.algo_findings_json or [])
    for finding in findings:
        chart_payload = finding.get("chart_json")
        finding["chart_json_str"] = json.dumps(chart_payload) if chart_payload else ""

    detail = dict(row.summary_json or {})
    detail.update(
        {
            "performance": detail.get("performance") or {},
            "price_rows": row.price_rows_json or [],
            "price_row_count": int(row.price_row_count or 0),
            "from_date": row.from_date.isoformat() if row.from_date else None,
            "to_date": row.to_date.isoformat() if row.to_date else None,
            "latest_close": _as_float(row.latest_close),
            "change_1d_pct": _as_float(row.change_1d_pct),
            "latest_volume": _as_int(row.latest_volume),
            "chart_type": detail.get("chart_type") or "candlestick",
            "chart_json": chart_json,
            "chart_json_str": json.dumps(chart_json) if chart_json else "",
            "findings": findings,
            "fundamentals": row.fundamentals_json,
            "strategy_explanations": row.strategy_explanations_json or [],
            "news": row.news_json or [],
            "strategy_options": row.strategy_options_json or [],
            "snapshot": {
                "source": "stock_detail_snapshots",
                "refreshed_at": row.refreshed_at,
                "expires_at": row.expires_at,
                "is_stale": is_stale,
                "source_version": row.source_version,
            },
        }
    )
    return detail


def get_stock_detail_snapshot_stats(db: Session) -> dict[str, Any]:
    now = datetime.now(UTC)
    exchange_rows = db.execute(
        select(
            StockDetailSnapshot.exchange,
            func.count(StockDetailSnapshot.stock_id),
        )
        .group_by(StockDetailSnapshot.exchange)
        .order_by(StockDetailSnapshot.exchange.asc())
    ).all()
    return {
        "total_rows": int(db.scalar(select(func.count(StockDetailSnapshot.stock_id))) or 0),
        "stale_rows": int(
            db.scalar(
                select(func.count(StockDetailSnapshot.stock_id)).where(
                    StockDetailSnapshot.expires_at <= now
                )
            )
            or 0
        ),
        "exchange_rows": [
            {"exchange": row[0], "rows": int(row[1] or 0)}
            for row in exchange_rows
        ],
    }


def upsert_stock_detail_snapshot(
    db: Session,
    stock: Stock,
    detail: dict[str, Any],
    *,
    ttl_hours: int = DEFAULT_TTL_HOURS,
    commit: bool = False,
) -> None:
    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=max(1, int(ttl_hours)))
    summary = {
        "stock": detail.get("stock") or {},
        "performance": detail.get("performance") or {},
        "has_prices": bool(detail.get("has_prices")),
        "chart_type": detail.get("chart_type") or "candlestick",
        "action_links": detail.get("action_links") or {},
    }
    payload = {
        "stock_id": stock.id,
        "symbol": stock.symbol,
        "yahoo_symbol": stock.yahoo_symbol,
        "exchange": stock.exchange,
        "summary_json": _json_safe(summary),
        "price_rows_json": _json_safe(detail.get("price_rows") or []),
        "chart_json": _json_safe(detail.get("chart_json")),
        "algo_findings_json": _json_safe(detail.get("findings") or []),
        "fundamentals_json": _json_safe(detail.get("fundamentals")),
        "strategy_explanations_json": _json_safe(detail.get("strategy_explanations") or []),
        "news_json": _json_safe(detail.get("news") or []),
        "strategy_options_json": _json_safe(detail.get("strategy_options") or []),
        "price_row_count": int(detail.get("price_row_count") or 0),
        "from_date": _parse_date(detail.get("from_date")),
        "to_date": _parse_date(detail.get("to_date")),
        "latest_close": detail.get("latest_close"),
        "change_1d_pct": detail.get("change_1d_pct"),
        "latest_volume": detail.get("latest_volume"),
        "source_version": SOURCE_VERSION,
        "refreshed_at": now,
        "expires_at": expires_at,
    }
    stmt = insert(StockDetailSnapshot).values(payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=["stock_id"],
        set_={
            "symbol": stmt.excluded.symbol,
            "yahoo_symbol": stmt.excluded.yahoo_symbol,
            "exchange": stmt.excluded.exchange,
            "summary_json": stmt.excluded.summary_json,
            "price_rows_json": stmt.excluded.price_rows_json,
            "chart_json": stmt.excluded.chart_json,
            "algo_findings_json": stmt.excluded.algo_findings_json,
            "fundamentals_json": stmt.excluded.fundamentals_json,
            "strategy_explanations_json": stmt.excluded.strategy_explanations_json,
            "news_json": stmt.excluded.news_json,
            "strategy_options_json": stmt.excluded.strategy_options_json,
            "price_row_count": stmt.excluded.price_row_count,
            "from_date": stmt.excluded.from_date,
            "to_date": stmt.excluded.to_date,
            "latest_close": stmt.excluded.latest_close,
            "change_1d_pct": stmt.excluded.change_1d_pct,
            "latest_volume": stmt.excluded.latest_volume,
            "source_version": stmt.excluded.source_version,
            "refreshed_at": stmt.excluded.refreshed_at,
            "expires_at": stmt.excluded.expires_at,
            "updated_at": now,
        },
    )
    db.execute(stmt)
    if commit:
        db.commit()
