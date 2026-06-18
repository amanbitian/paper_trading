from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from fastapi.templating import Jinja2Templates

from app.constants.market_indices import STOCK_INDEX_FILTER_OPTIONS


APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_inr(value: Any, decimals: int = 2) -> str:
    amount = _as_float(value)
    if amount is None:
        return "-"
    return f"Rs {amount:,.{decimals}f}"


def format_number(value: Any, decimals: int = 2) -> str:
    number = _as_float(value)
    if number is None:
        return "-"
    return f"{number:,.{decimals}f}"


def format_pct(value: Any, decimals: int = 2, signed: bool = True) -> str:
    number = _as_float(value)
    if number is None:
        return "-"
    sign = "+" if signed and number > 0 else ""
    return f"{sign}{number:,.{decimals}f}%"


def format_volume(value: Any) -> str:
    number = _as_float(value)
    if number is None:
        return "-"
    if abs(number) >= 10_000_000:
        return f"{number / 10_000_000:.2f} Cr"
    if abs(number) >= 100_000:
        return f"{number / 100_000:.2f} L"
    return f"{number:,.0f}"


def format_optional_pct(value: Any, decimals: int = 2) -> str:
    number = _as_float(value)
    if number is None:
        return "-"
    return format_pct(number, decimals=decimals, signed=True)


def format_date(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    return text[:19].replace("T", " ")


def format_time_ago(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return str(value)[:10]
    if not isinstance(value, datetime):
        return str(value)[:10]
    from datetime import timezone
    now = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    diff = now - value.astimezone(timezone.utc)
    seconds = diff.total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    if seconds < 7 * 86400:
        return f"{int(seconds // 86400)}d ago"
    return value.strftime("%b %d")


def tone_for_change(value: Any) -> str:
    number = _as_float(value)
    if number is None or abs(number) < 0.000001:
        return "neutral"
    return "positive" if number > 0 else "negative"


def stock_index_labels(row: dict[str, Any]) -> str:
    labels = [
        option["label"]
        for option in STOCK_INDEX_FILTER_OPTIONS
        if bool(row.get(option["flag_column"]))
    ]
    return ", ".join(labels) if labels else "-"


def sparkline_points(values: Iterable[Any] | None, width: int = 150, height: int = 42) -> str:
    numbers = [_as_float(value) for value in (values or [])]
    series = [value for value in numbers if value is not None]
    if len(series) < 2:
        return ""

    low = min(series)
    high = max(series)
    spread = high - low
    if spread == 0:
        spread = 1.0

    padding = 3
    x_step = (width - padding * 2) / (len(series) - 1)
    points: list[str] = []
    for index, value in enumerate(series):
        x = padding + index * x_step
        y = padding + (height - padding * 2) * (1 - ((value - low) / spread))
        points.append(f"{x:.2f},{y:.2f}")
    return " ".join(points)


def sort_quotes(
    quotes: list[dict[str, Any]],
    *,
    sort_by: str = "trend",
    descending: bool = True,
) -> list[dict[str, Any]]:
    key_name = {
        "trend": "change_pct",
        "price": "price",
        "volume": "volume",
    }.get(sort_by, "change_pct")

    def key(quote: dict[str, Any]) -> float:
        value = _as_float(quote.get(key_name))
        if value is None:
            return float("-inf") if descending else float("inf")
        return value

    return sorted(quotes, key=key, reverse=descending)


templates.env.filters["inr"] = format_inr
templates.env.filters["number"] = format_number
templates.env.filters["pct"] = format_pct
templates.env.filters["optional_pct"] = format_optional_pct
templates.env.filters["volume"] = format_volume
templates.env.filters["date_text"] = format_date
templates.env.filters["time_ago"] = format_time_ago


def format_duration(value: Any) -> str:
    if value is None:
        return "-"
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return "-"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    remainder = seconds % 60
    return f"{minutes}m {remainder:.0f}s"


def format_ms_filter(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value):,.1f} ms"


templates.env.filters["duration_text"] = format_duration
templates.env.filters["ms_text"] = format_ms_filter
templates.env.filters["tone"] = tone_for_change
templates.env.filters["sparkline_points"] = sparkline_points
templates.env.filters["stock_index_labels"] = stock_index_labels

from app.services.web_explore_stock_helpers import (  # noqa: E402
    add_portfolio_url,
    stock_detail_url,
    stock_route_key,
)

templates.env.filters["stock_route_key"] = stock_route_key
templates.env.filters["stock_detail_url"] = stock_detail_url
templates.env.filters["add_portfolio_url"] = add_portfolio_url
