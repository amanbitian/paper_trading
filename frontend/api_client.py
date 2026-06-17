from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import requests
import streamlit as st


API_BASE_URL = os.getenv("PAPER_TRADING_API_URL", "http://localhost:8000")
CURRENCY_PREFIX = "₹"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logging.getLogger("frontend.ai").setLevel(logging.INFO)
timing_logger = logging.getLogger("frontend.timing")
ai_logger = logging.getLogger("frontend.ai")


def _duration_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000


def format_indian_number(value: Any, decimals: int = 2) -> str:
    number = float(value or 0)
    sign = "-" if number < 0 else ""
    number = abs(number)
    formatted = f"{number:.{decimals}f}"
    integer_part, _, decimal_part = formatted.partition(".")
    if len(integer_part) > 3:
        last_three = integer_part[-3:]
        leading = integer_part[:-3]
        groups: list[str] = []
        while len(leading) > 2:
            groups.insert(0, leading[-2:])
            leading = leading[:-2]
        if leading:
            groups.insert(0, leading)
        integer_part = ",".join([*groups, last_three])
    if decimals == 0:
        return f"{sign}{integer_part}"
    return f"{sign}{integer_part}.{decimal_part}"


def format_compact_indian_number(value: Any, decimals: int = 2) -> str:
    number = float(value or 0)
    sign = "-" if number < 0 else ""
    absolute = abs(number)
    if absolute >= 10_000_000:
        return f"{sign}{absolute / 10_000_000:.{decimals}f} Cr"
    if absolute >= 100_000:
        return f"{sign}{absolute / 100_000:.{decimals}f} L"
    if absolute >= 1_000:
        return f"{sign}{absolute / 1_000:.{decimals}f} K"
    return f"{sign}{absolute:.{decimals}f}"


def format_inr(value: Any, decimals: int = 2, compact: bool = False) -> str:
    formatter = format_compact_indian_number if compact else format_indian_number
    return f"{CURRENCY_PREFIX}{formatter(value, decimals)}"


def format_signed_inr(value: Any, decimals: int = 2, compact: bool = False) -> str:
    number = float(value or 0)
    sign = "+" if number > 0 else ""
    return f"{sign}{format_inr(number, decimals=decimals, compact=compact)}"


def format_pct(value: Any, decimals: int = 2, signed: bool = False) -> str:
    number = float(value or 0)
    sign = "+" if signed and number > 0 else ""
    return f"{sign}{number:.{decimals}f}%"


def format_duration(seconds: Any) -> str:
    if seconds is None:
        return "—"
    total = int(float(seconds))
    if total < 60:
        return f"{total} sec"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} min {secs} sec" if secs else f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} hr {minutes} min" if minutes else f"{hours} hr"
    days, hours = divmod(hours, 24)
    return f"{days} day{'s' if days != 1 else ''} {hours} hr"


def format_time_ago(value: Any) -> str:
    if not value:
        return "never"
    if isinstance(value, str):
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif isinstance(value, datetime):
        timestamp = value
    else:
        return str(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    seconds = max(0, int((now - timestamp).total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        unit = "minute" if minutes == 1 else "minutes"
        return f"{minutes} {unit} ago"
    hours = minutes // 60
    if hours < 24:
        unit = "hour" if hours == 1 else "hours"
        return f"{hours} {unit} ago"
    days = hours // 24
    unit = "day" if days == 1 else "days"
    return f"{days} {unit} ago"


def start_timer() -> float:
    return time.perf_counter()


def log_page_load(page_name: str, started_at: float) -> None:
    timing_logger.info(
        "operation=page_load page=%s status=ok duration_ms=%.2f",
        page_name,
        _duration_ms(started_at),
    )


@contextmanager
def timed_frontend_block(operation: str):
    started_at = start_timer()
    try:
        yield
        timing_logger.info(
            "operation=%s status=ok duration_ms=%.2f",
            operation,
            _duration_ms(started_at),
        )
    except Exception:
        timing_logger.exception(
            "operation=%s status=error duration_ms=%.2f",
            operation,
            _duration_ms(started_at),
        )
        raise


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def auth_headers() -> dict[str, str]:
    if debug_auth_enabled():
        return {}
    token = st.session_state.get("token")
    return {"Authorization": f"Bearer {token}"} if token else {}


def clear_auth_state() -> None:
    st.session_state.token = None
    st.session_state.pop("last_signal_id", None)


def debug_auth_enabled() -> bool:
    env_value = os.getenv("PAPER_TRADING_DEBUG_AUTH_BYPASS", "").strip().lower()
    if env_value in {"1", "true", "yes", "on"}:
        st.session_state.debug_auth_enabled = True
        return True
    try:
        response = requests.get(f"{API_BASE_URL}/auth/debug-status", timeout=3)
        response.raise_for_status()
        st.session_state.debug_auth_enabled = bool(response.json().get("debug_auth_bypass"))
    except requests.RequestException:
        st.session_state.debug_auth_enabled = False
    return bool(st.session_state.debug_auth_enabled)


def format_error_detail(detail) -> str:
    if isinstance(detail, list):
        messages: list[str] = []
        for item in detail:
            if not isinstance(item, dict):
                messages.append(str(item))
                continue
            if "field" in item and "message" in item:
                messages.append(f"{item['field']}: {item['message']}")
                continue
            loc_parts = [str(part) for part in item.get("loc", []) if part != "body"]
            field = ".".join(loc_parts)
            message = item.get("msg", "Invalid value")
            messages.append(f"{field}: {message}" if field else message)
        return "\n".join(messages)
    if isinstance(detail, dict):
        if "errors" in detail:
            return format_error_detail(detail["errors"])
        if "message" in detail:
            return str(detail["message"])
        if "detail" in detail:
            return format_error_detail(detail["detail"])
    return str(detail)


def log_think_tank_action(action: str, **details) -> None:
    """Log UI button clicks for AI Think Tank (visible in frontend container logs)."""
    ai_logger.info(
        "think_tank_ui action=%s details=%s model=%s",
        action,
        details,
        st.session_state.get("ai_model"),
    )


def api_request(
    method: str,
    path: str,
    *,
    return_error: bool = False,
    show_error: bool = True,
    **kwargs,
):
    url = f"{API_BASE_URL}{path}"
    headers = kwargs.pop("headers", {})
    headers.update(auth_headers())
    started_at = start_timer()
    is_ai = path.startswith("/ai")
    if is_ai:
        payload = kwargs.get("json") or kwargs.get("params")
        ai_logger.info(
            "think_tank_api_start method=%s path=%s payload=%s",
            method,
            path,
            payload,
        )
    status_code = None
    try:
        response = requests.request(method, url, headers=headers, timeout=60, **kwargs)
        status_code = response.status_code
    except requests.RequestException as exc:
        message = f"API request failed: {exc}"
        timing_logger.exception(
            "operation=api_request method=%s path=%s status=error duration_ms=%.2f",
            method,
            path,
            _duration_ms(started_at),
        )
        if show_error:
            st.error(message)
        if return_error:
            return {"error": True, "status_code": None, "message": message}
        return None
    finally:
        if status_code is not None:
            timing_logger.info(
                "operation=api_request method=%s path=%s status_code=%s duration_ms=%.2f",
                method,
                path,
                status_code,
                _duration_ms(started_at),
            )
    if response.status_code == 401:
        try:
            payload = response.json()
            detail = payload.get("detail", response.text)
            message = format_error_detail(detail)
        except ValueError:
            payload = None
            message = response.text or "Unauthorized"
        if path != "/auth/login":
            clear_auth_state()
            message = "Session expired or invalid. Please log in again."
        if show_error:
            st.warning(message)
        if return_error:
            return {
                "error": True,
                "status_code": response.status_code,
                "message": message,
                "payload": payload,
            }
        return None
    if response.status_code >= 400:
        try:
            payload = response.json()
            detail = payload.get("errors") or payload.get("detail", response.text)
        except ValueError:
            payload = None
            detail = response.text
        message = format_error_detail(detail)
        if show_error:
            st.error(message)
        if return_error:
            return {
                "error": True,
                "status_code": response.status_code,
                "message": message,
                "payload": payload,
            }
        return None
    if not response.content:
        if is_ai:
            ai_logger.info("think_tank_api_done method=%s path=%s status=%s empty_body=true", method, path, status_code)
        return None
    body = response.json()
    if is_ai:
        preview = body
        if isinstance(body, dict):
            preview = {k: body[k] for k in list(body.keys())[:12]}
        ai_logger.info(
            "think_tank_api_done method=%s path=%s status=%s response_preview=%s",
            method,
            path,
            status_code,
            preview,
        )
    return body


def get(
    path: str,
    params: dict | None = None,
    *,
    return_error: bool = False,
    show_error: bool = True,
):
    return api_request(
        "GET",
        path,
        params=params,
        return_error=return_error,
        show_error=show_error,
    )


def post(
    path: str,
    payload: dict | None = None,
    params: dict | None = None,
    *,
    return_error: bool = False,
    show_error: bool = True,
):
    return api_request(
        "POST",
        path,
        json=payload,
        params=params,
        return_error=return_error,
        show_error=show_error,
    )


def require_login() -> bool:
    if debug_auth_enabled():
        st.caption("Debugger mode active: login is bypassed for local development.")
        return True
    if not st.session_state.get("token"):
        st.warning("Log in from the main page to continue.")
        return False
    st.caption("This is a paper trading and educational tool. It does not provide financial advice.")
    return True


def _stock_label(stock: dict[str, Any]) -> str:
    company = stock.get("company_name") or stock.get("yahoo_symbol") or stock.get("symbol")
    return (
        f"{stock.get('symbol')} ({stock.get('exchange')}) - "
        f"{company} [{stock.get('yahoo_symbol')}]"
    )


def get_stock_suggestions(
    query: str,
    *,
    exchange: str | None = None,
    index_code: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    if not query.strip():
        return []
    params: dict[str, Any] = {"query": query.strip(), "limit": limit}
    if exchange:
        params["exchange"] = exchange
    if index_code:
        params["index_code"] = index_code
    return get("/stocks/search", params=params) or []


def get_index_fund_suggestions(
    query: str,
    *,
    category: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    if not query.strip():
        return []
    params: dict[str, Any] = {"query": query.strip(), "limit": limit}
    if category:
        params["category"] = category
    return get("/index-funds/search", params=params) or []


def search_stock_widget(key_prefix: str = "stock", *, multiple: bool = False):
    query = st.text_input(
        "Search stock",
        key=f"{key_prefix}_query",
        placeholder="Try RELIANCE, HDFC Bank, OLA cab, Tata Motors...",
        help="Search by symbol, company name, Yahoo ticker, sector, or common keywords.",
    )
    exchange = st.selectbox("Exchange", ["", "NSE", "BSE"], key=f"{key_prefix}_exchange")
    results = get_stock_suggestions(query, exchange=exchange or None)
    if not results:
        if query.strip():
            st.info("No matching stocks found. Try fewer words or a symbol like OLAELEC, RELIANCE, or HDFCBANK.")
        return None

    labels = {_stock_label(stock): stock for stock in results}
    st.caption(f"Showing {len(results)} ranked suggestion{'s' if len(results) != 1 else ''}.")
    if multiple:
        selected_labels = st.multiselect(
            "Suggested stocks",
            list(labels.keys()),
            key=f"{key_prefix}_selected_multi",
            placeholder="Select one or more stocks",
        )
        return [labels[label] for label in selected_labels]

    selected_label = st.selectbox("Suggested stocks", list(labels.keys()), key=f"{key_prefix}_selected")
    return labels[selected_label]


def portfolio_select(key: str = "portfolio"):
    portfolios = get("/portfolios") or []
    if not portfolios:
        st.info("Create or register a user to get default portfolios.")
        return None
    labels = {
        f"{portfolio['portfolio_name']} ({portfolio['portfolio_type']})": portfolio
        for portfolio in portfolios
    }
    selected = st.selectbox("Portfolio", list(labels.keys()), key=key)
    return labels[selected]
