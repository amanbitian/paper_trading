from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models.stock import Stock
from app.services.fundamentals_service import get_stock_fundamentals
from app.services.llm_cache_service import get_llm_cache, set_llm_cache
from app.services.news_service import list_stock_news
from app.services.stock_performance_service import get_stock_performance_by_ids
from models.ollama_client import OllamaClient, OllamaSettings, OllamaUnavailableError

logger = logging.getLogger(__name__)

_CACHE_TTL_HOURS = 24
_SYSTEM_PROMPT = (
    "You are a concise financial analyst writing plain-English stock briefs for retail traders. "
    "Write in clear, direct paragraphs only. No markdown, no asterisks, no bullet points, "
    "no headers, no disclaimers. Numbers should appear naturally in sentences."
)


def _ollama_client() -> OllamaClient:
    s = OllamaSettings(
        base_url=settings.ollama_base_url.rstrip("/"),
        default_model=settings.ollama_default_model,
        fallback_model=settings.ollama_fallback_model,
        timeout_seconds=max(settings.ollama_timeout_seconds, 90),
        max_tokens=600,
    )
    return OllamaClient(s)


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_num(value: Any, decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_crore(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        v = float(value)
        if v >= 1e11:
            return f"₹{v/1e11:,.2f}L Cr"
        if v >= 1e7:
            return f"₹{v/1e7:,.0f} Cr"
        return f"₹{v:,.0f}"
    except (TypeError, ValueError):
        return "N/A"


def _build_prompt(stock: Stock, fundamentals: Any, performance: dict, news: list[dict]) -> str:
    name = stock.company_name or stock.symbol
    sector = stock.sector or "diversified"
    industry = stock.industry or sector

    perf_1d = _fmt_pct(performance.get("change_1d_pct") or performance.get("change_1d"))
    perf_1m = _fmt_pct(performance.get("change_1m_pct"))
    perf_3m = _fmt_pct(performance.get("change_3m_pct"))
    perf_1y = _fmt_pct(performance.get("change_1y_pct"))
    latest_price = _fmt_num(performance.get("latest_price"), 2)

    f = fundamentals
    mktcap = _fmt_crore(f.market_cap) if f else "N/A"
    pe = _fmt_num(f.trailing_pe) if f else "N/A"
    pb = _fmt_num(f.price_to_book) if f else "N/A"
    roe = _fmt_pct(f.roe) if f else "N/A"
    de = _fmt_num(f.debt_to_equity) if f else "N/A"
    rev_growth = _fmt_pct(f.sales_growth) if f else "N/A"
    earn_growth = _fmt_pct(f.earnings_growth) if f else "N/A"
    div_yield = _fmt_pct(f.dividend_yield) if f else "N/A"

    news_lines = []
    for i, article in enumerate(news[:5], 1):
        headline = (article.get("headline") or "").strip()
        source = article.get("source") or article.get("provider") or ""
        pub = article.get("published_at")
        if pub:
            try:
                if hasattr(pub, "strftime"):
                    pub_str = pub.strftime("%b %d")
                else:
                    pub_str = str(pub)[:10]
            except Exception:
                pub_str = ""
        else:
            pub_str = ""
        news_lines.append(f"{i}. {headline}" + (f" ({source}, {pub_str})" if source or pub_str else ""))

    news_block = "\n".join(news_lines) if news_lines else "No recent news available."

    return f"""Write a 3-paragraph stock brief for {name} ({stock.symbol}), a {sector} company listed on {stock.exchange}.

COMPANY PROFILE:
Sector: {sector}
Industry: {industry}
Exchange: {stock.exchange}

PRICE PERFORMANCE:
Current price: ₹{latest_price}
1-day change: {perf_1d}
1-month change: {perf_1m}
3-month change: {perf_3m}
1-year change: {perf_1y}

KEY FUNDAMENTALS:
Market cap: {mktcap}
Trailing P/E: {pe}
Price-to-book: {pb}
Return on equity: {roe}
Debt-to-equity: {de}
Revenue growth: {rev_growth}
Earnings growth: {earn_growth}
Dividend yield: {div_yield}

RECENT NEWS:
{news_block}

Instructions: Write exactly 3 paragraphs separated by blank lines.
Paragraph 1: What the company does, its scale, and market position.
Paragraph 2: Assessment of financial health based on the fundamentals above.
Paragraph 3: Recent price momentum, news sentiment, and near-term observation.
Each paragraph: 2-4 sentences. No lists. No headers. No disclaimers."""


def build_stock_brief_context(db: Session, stock_id: int) -> dict[str, Any] | None:
    stock = db.get(Stock, stock_id)
    if stock is None:
        return None

    fundamentals = get_stock_fundamentals(db, stock_id)
    news = list_stock_news(db, stock_id, limit=5)

    perf_map = get_stock_performance_by_ids(db, [stock_id])
    performance = perf_map.get(stock_id) or {}

    return {
        "stock": stock,
        "fundamentals": fundamentals,
        "news": news,
        "performance": performance,
    }


async def generate_stock_brief(
    db: Session,
    stock_id: int,
    *,
    model: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    cache_suffix = f"brief:{stock_id}"

    if not force:
        cached = get_llm_cache(db, cache_suffix, ttl_hours=_CACHE_TTL_HOURS)
        if cached:
            logger.info("stock_brief cache_hit stock_id=%s", stock_id)
            return {**cached, "cached": True}

    ctx = build_stock_brief_context(db, stock_id)
    if ctx is None:
        return {"error": "Stock not found.", "cached": False}

    prompt = _build_prompt(
        ctx["stock"],
        ctx["fundamentals"],
        ctx["performance"],
        ctx["news"],
    )

    client = _ollama_client()
    used_model = model or settings.ollama_default_model

    try:
        text = await client.chat(prompt, system=_SYSTEM_PROMPT, model=used_model)
    except OllamaUnavailableError as exc:
        logger.warning("stock_brief ollama_unavailable stock_id=%s error=%s", stock_id, exc)
        return {"error": str(exc), "cached": False}
    except Exception as exc:
        logger.exception("stock_brief failed stock_id=%s", stock_id)
        return {"error": f"Generation failed: {exc}", "cached": False}

    paragraphs = [p.strip() for p in text.strip().split("\n\n") if p.strip()]

    result: dict[str, Any] = {
        "paragraphs": paragraphs,
        "model": client.last_chat_log.get("model") if client.last_chat_log else used_model,
        "generated_at": datetime.now(UTC).isoformat(),
        "cached": False,
        "stock_name": ctx["stock"].company_name or ctx["stock"].symbol,
        "stock_symbol": ctx["stock"].symbol,
    }

    set_llm_cache(db, cache_suffix, result)
    logger.info("stock_brief generated stock_id=%s paragraphs=%s", stock_id, len(paragraphs))
    return result
