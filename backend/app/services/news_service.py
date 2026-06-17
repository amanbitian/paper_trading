from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
import hashlib
import html
import logging
import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
import xml.etree.ElementTree as ET

import requests
import yfinance as _yf
from sqlalchemy import desc, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings
from app.models.news import (
    CompanyAlias,
    NewsIngestionRun,
    NewsProviderQuotaState,
    StockNewsArticle,
    StockNewsIngestionMeta,
    StockNewsLink,
)
from app.models.stock import Stock
from app.utils.json_safe import to_json_safe


logger = logging.getLogger(__name__)

NEWS_LOCK_NAMESPACE = 424_200_000_000
DEFAULT_NEWS_LIMIT = 10
STRIP_URL_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
    "ref",
    "source",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "_ga",
}
STALE_THRESHOLDS_MINUTES = {1: 15, 2: 60, 3: 360}
FINANCE_CONTEXT_TERMS = {
    "stock",
    "stocks",
    "share",
    "shares",
    "market",
    "markets",
    "nse",
    "bse",
    "earnings",
    "profit",
    "revenue",
    "quarter",
    "q1",
    "q2",
    "q3",
    "q4",
    "dividend",
    "ipo",
    "merger",
    "acquisition",
    "brokerage",
    "price",
}
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, application/rss+xml, application/xml;q=0.9, */*;q=0.8",
}


@dataclass
class RawArticle:
    headline: str
    url: str
    published_at: datetime
    source: str
    provider: str
    summary: str = ""
    body: str = ""
    tickers: list[str] = field(default_factory=list)
    sentiment_score: float | None = None
    provider_article_id: str | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderFetchResult:
    provider: str
    articles: list[RawArticle] = field(default_factory=list)
    error: str | None = None
    skipped_reason: str | None = None


def canonicalize_url(url: str) -> tuple[str, str]:
    raw = (url or "").strip()
    parsed = urlparse(raw)
    if not parsed.scheme and not parsed.netloc:
        canonical = raw
    else:
        clean_params = {
            key: value
            for key, value in parse_qs(parsed.query, keep_blank_values=True).items()
            if key.lower() not in STRIP_URL_PARAMS
        }
        canonical = parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
            query=urlencode(clean_params, doseq=True),
            fragment="",
        ).geturl()
    return canonical, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def content_hash(headline: str, published_at: datetime, source: str | None) -> str:
    payload = "|".join(
        [
            normalize_text(headline),
            published_at.astimezone(UTC).isoformat()[:13],
            normalize_text(source or ""),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_text(value: str | None) -> str:
    text_value = html.unescape(value or "").lower()
    text_value = re.sub(r"[^a-z0-9&]+", " ", text_value)
    return re.sub(r"\s+", " ", text_value).strip()


def source_domain(url: str, source: str | None = None) -> str | None:
    parsed = urlparse(url or "")
    domain = parsed.netloc.lower().removeprefix("www.")
    if domain:
        return domain
    return (source or "").lower().removeprefix("www.") or None


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _ensure_utc(value)
    if value is None:
        return None
    text_value = str(value).strip()
    if not text_value:
        return None
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
        try:
            return datetime.strptime(text_value, fmt).replace(tzinfo=UTC)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
        return _ensure_utc(parsed)
    except ValueError:
        pass
    try:
        return _ensure_utc(parsedate_to_datetime(text_value))
    except (TypeError, ValueError, IndexError):
        return None


def _entity_symbols(payload: dict[str, Any]) -> list[str]:
    symbols: list[str] = []
    for entity in payload.get("entities") or []:
        symbol = str(entity.get("symbol") or "").strip().upper()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


class NewsAdapter:
    provider = "base"

    def fetch_for_stock(self, stock: Stock, *, limit: int, published_after: datetime | None = None) -> ProviderFetchResult:
        raise NotImplementedError


class MarketauxAdapter(NewsAdapter):
    provider = "marketaux"

    def __init__(self, api_token: str) -> None:
        self.api_token = api_token

    def fetch_for_stock(self, stock: Stock, *, limit: int, published_after: datetime | None = None) -> ProviderFetchResult:
        params: dict[str, Any] = {
            "api_token": self.api_token,
            "symbols": stock.yahoo_symbol or stock.symbol,
            "filter_entities": "true",
            "must_have_entities": "true",
            "language": "en",
            "group_similar": "true",
            "limit": min(max(limit, 1), 3),
        }
        if published_after:
            params["published_after"] = published_after.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")
        try:
            response = requests.get(
                "https://api.marketaux.com/v1/news/all",
                params=params,
                headers=REQUEST_HEADERS,
                timeout=settings.news_request_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return ProviderFetchResult(provider=self.provider, error=str(exc))

        articles: list[RawArticle] = []
        for row in payload.get("data") or []:
            published_at = _parse_datetime(row.get("published_at")) or datetime.now(UTC)
            article_url = str(row.get("url") or "").strip()
            if not article_url:
                continue
            entities = row.get("entities") or []
            sentiment_values = [
                entity.get("sentiment_score")
                for entity in entities
                if isinstance(entity.get("sentiment_score"), (int, float))
            ]
            sentiment = (
                float(sum(sentiment_values) / len(sentiment_values))
                if sentiment_values
                else None
            )
            articles.append(
                RawArticle(
                    headline=str(row.get("title") or "").strip(),
                    summary=str(row.get("description") or row.get("snippet") or "").strip(),
                    url=article_url,
                    published_at=published_at,
                    source=str(row.get("source") or "").strip(),
                    provider=self.provider,
                    tickers=_entity_symbols(row),
                    sentiment_score=sentiment,
                    provider_article_id=str(row.get("uuid") or "") or None,
                    raw_payload=row,
                )
            )
        return ProviderFetchResult(provider=self.provider, articles=articles)


class AlphaVantageAdapter(NewsAdapter):
    provider = "alpha_vantage"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def fetch_for_stock(self, stock: Stock, *, limit: int, published_after: datetime | None = None) -> ProviderFetchResult:
        params: dict[str, Any] = {
            "function": "NEWS_SENTIMENT",
            "tickers": stock.yahoo_symbol or stock.symbol,
            "apikey": self.api_key,
            "limit": min(max(limit, 1), 50),
            "sort": "LATEST",
        }
        if published_after:
            params["time_from"] = published_after.astimezone(UTC).strftime("%Y%m%dT%H%M")
        try:
            response = requests.get(
                "https://www.alphavantage.co/query",
                params=params,
                headers=REQUEST_HEADERS,
                timeout=settings.news_request_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return ProviderFetchResult(provider=self.provider, error=str(exc))

        if payload.get("Information") or payload.get("Note"):
            message = str(payload.get("Information") or payload.get("Note"))
            return ProviderFetchResult(provider=self.provider, error=message)

        articles: list[RawArticle] = []
        for row in payload.get("feed") or []:
            published_at = _parse_datetime(row.get("time_published")) or datetime.now(UTC)
            article_url = str(row.get("url") or "").strip()
            if not article_url:
                continue
            ticker_sentiment = row.get("ticker_sentiment") or []
            tickers = [
                str(item.get("ticker") or "").strip().upper()
                for item in ticker_sentiment
                if item.get("ticker")
            ]
            sentiment_value = row.get("overall_sentiment_score")
            if not isinstance(sentiment_value, (int, float)):
                sentiment_value = None
            articles.append(
                RawArticle(
                    headline=str(row.get("title") or "").strip(),
                    summary=str(row.get("summary") or "").strip(),
                    url=article_url,
                    published_at=published_at,
                    source=str(row.get("source") or "").strip(),
                    provider=self.provider,
                    tickers=tickers,
                    sentiment_score=float(sentiment_value) if sentiment_value is not None else None,
                    raw_payload=row,
                )
            )
        return ProviderFetchResult(provider=self.provider, articles=articles)


class YahooRssAdapter(NewsAdapter):
    provider = "yahoo_rss"

    def fetch_for_stock(self, stock: Stock, *, limit: int, published_after: datetime | None = None) -> ProviderFetchResult:
        url = "https://feeds.finance.yahoo.com/rss/2.0/headline"
        params = {
            "s": stock.yahoo_symbol or stock.symbol,
            "region": "IN",
            "lang": "en-IN",
        }
        try:
            response = requests.get(
                url,
                params=params,
                headers=REQUEST_HEADERS,
                timeout=settings.news_request_timeout_seconds,
            )
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except Exception as exc:
            return ProviderFetchResult(provider=self.provider, error=str(exc))

        articles: list[RawArticle] = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            published_at = _parse_datetime(item.findtext("pubDate")) or datetime.now(UTC)
            if published_after and published_at <= published_after:
                continue
            if not title or not link:
                continue
            source_name = item.findtext("source") or "Yahoo Finance"
            articles.append(
                RawArticle(
                    headline=title,
                    summary=(item.findtext("description") or "").strip(),
                    url=link,
                    published_at=published_at,
                    source=source_name,
                    provider=self.provider,
                    tickers=[],
                    raw_payload={"title": title, "link": link, "pubDate": item.findtext("pubDate")},
                )
            )
            if len(articles) >= limit:
                break
        return ProviderFetchResult(provider=self.provider, articles=articles)


class YahooFinanceAdapter(NewsAdapter):
    """Uses the yfinance Ticker.news property — free, no API key, works with NSE/BSE .NS/.BO symbols."""

    provider = "yahoo_finance"

    def fetch_for_stock(self, stock: Stock, *, limit: int, published_after: datetime | None = None) -> ProviderFetchResult:
        ticker_symbol = stock.yahoo_symbol or f"{stock.symbol}.NS"
        try:
            ticker = _yf.Ticker(ticker_symbol)
            raw_news = ticker.news or []
        except Exception as exc:
            return ProviderFetchResult(provider=self.provider, error=str(exc))

        articles: list[RawArticle] = []
        for item in raw_news[:limit]:
            content = item.get("content") or {}
            if not content:
                content = item  # old yfinance API fallback

            title = str(content.get("title") or "").strip()
            if not title:
                continue

            # New API: canonicalUrl is a dict; old API: link is a string
            url_obj = content.get("canonicalUrl") or {}
            url = str(url_obj.get("url") if isinstance(url_obj, dict) else url_obj or "").strip()
            if not url:
                url = str(content.get("link") or item.get("link") or "").strip()
            if not url:
                continue

            # Published timestamp: ISO string in new API, Unix int in old API
            pub_raw = content.get("pubDate") or content.get("providerPublishTime")
            if isinstance(pub_raw, (int, float)):
                published_at = datetime.fromtimestamp(pub_raw, tz=UTC)
            else:
                published_at = _parse_datetime(pub_raw) or datetime.now(UTC)

            if published_after and published_at <= published_after:
                continue

            provider_obj = content.get("provider") or {}
            if isinstance(provider_obj, dict):
                source = str(provider_obj.get("displayName") or provider_obj.get("sourceId") or "Yahoo Finance").strip()
            else:
                source = str(provider_obj or content.get("publisher") or "Yahoo Finance").strip()

            articles.append(
                RawArticle(
                    headline=title,
                    summary=str(content.get("summary") or content.get("description") or "").strip(),
                    url=url,
                    published_at=published_at,
                    source=source,
                    provider=self.provider,
                    tickers=[],
                    provider_article_id=str(item.get("id") or content.get("id") or "") or None,
                    raw_payload={"item_id": item.get("id"), "content_type": content.get("contentType")},
                )
            )

        return ProviderFetchResult(provider=self.provider, articles=articles)


class GdeltAdapter(NewsAdapter):
    provider = "gdelt"

    def fetch_for_stock(self, stock: Stock, *, limit: int, published_after: datetime | None = None) -> ProviderFetchResult:
        company = (stock.company_name or stock.symbol).replace('"', "")
        query = f'"{company}" (stock OR shares OR NSE OR BSE OR earnings OR revenue)'
        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "sort": "datedesc",
            "maxrecords": min(max(limit, 1), 20),
        }
        if published_after:
            params["startdatetime"] = published_after.astimezone(UTC).strftime("%Y%m%d%H%M%S")
            params["enddatetime"] = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        else:
            params["timespan"] = "3months"
        try:
            response = requests.get(
                "https://api.gdeltproject.org/api/v2/doc/doc",
                params=params,
                headers=REQUEST_HEADERS,
                timeout=settings.news_request_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return ProviderFetchResult(provider=self.provider, error=str(exc))

        articles: list[RawArticle] = []
        for row in payload.get("articles") or []:
            published_at = _parse_datetime(row.get("seendate")) or datetime.now(UTC)
            article_url = str(row.get("url") or "").strip()
            title = str(row.get("title") or "").strip()
            if not article_url or not title:
                continue
            articles.append(
                RawArticle(
                    headline=title,
                    url=article_url,
                    published_at=published_at,
                    source=str(row.get("domain") or "").strip(),
                    provider=self.provider,
                    summary=str(row.get("snippet") or "").strip(),
                    raw_payload=row,
                )
            )
        return ProviderFetchResult(provider=self.provider, articles=articles)


def _quota_reset_at(now: datetime | None = None) -> datetime:
    current = now or datetime.now(UTC)
    tomorrow = current.date() + timedelta(days=1)
    return datetime.combine(tomorrow, datetime.min.time(), tzinfo=UTC)


def _quota_available(db: Session, provider: str, limit_daily: int) -> bool:
    if limit_daily <= 0:
        return True
    now = datetime.now(UTC)
    state = db.get(NewsProviderQuotaState, provider)
    if state is None:
        state = NewsProviderQuotaState(
            provider=provider,
            used_today=0,
            limit_daily=limit_daily,
            reset_at=_quota_reset_at(now),
        )
        db.add(state)
        db.flush()
    if state.reset_at <= now:
        state.used_today = 0
        state.reset_at = _quota_reset_at(now)
        state.limit_daily = limit_daily
        db.flush()
    return state.used_today < state.limit_daily


def _increment_quota(db: Session, provider: str, limit_daily: int) -> None:
    if limit_daily <= 0:
        return
    state = db.get(NewsProviderQuotaState, provider)
    if state is None:
        state = NewsProviderQuotaState(
            provider=provider,
            used_today=1,
            limit_daily=limit_daily,
            reset_at=_quota_reset_at(),
        )
        db.add(state)
    else:
        state.used_today += 1
        state.limit_daily = limit_daily
    db.flush()


def _provider_daily_limit(provider: str) -> int:
    if provider == "marketaux":
        return settings.news_marketaux_daily_limit
    if provider == "alpha_vantage":
        return settings.news_alpha_vantage_daily_limit
    return 0


def _adapters(db: Session) -> list[NewsAdapter]:
    adapters: list[NewsAdapter] = []
    if settings.marketaux_api_token and _quota_available(
        db, "marketaux", settings.news_marketaux_daily_limit
    ):
        adapters.append(MarketauxAdapter(settings.marketaux_api_token))
    if settings.alpha_vantage_api_key and _quota_available(
        db, "alpha_vantage", settings.news_alpha_vantage_daily_limit
    ):
        adapters.append(AlphaVantageAdapter(settings.alpha_vantage_api_key))
    adapters.append(YahooFinanceAdapter())
    adapters.append(YahooRssAdapter())
    adapters.append(GdeltAdapter())
    return adapters


def _stock_symbols(stock: Stock) -> set[str]:
    symbols = {
        (stock.symbol or "").upper(),
        (stock.yahoo_symbol or "").upper(),
        (stock.yahoo_symbol or "").upper().replace(".NS", "").replace(".BO", ""),
    }
    return {symbol for symbol in symbols if symbol}


def _alias_values(db: Session, stock: Stock) -> list[tuple[str, str]]:
    aliases = [
        (stock.symbol or "", "symbol"),
        (stock.yahoo_symbol or "", "ticker"),
        ((stock.company_name or ""), "company"),
    ]
    aliases.extend((row.alias, row.alias_type or "alias") for row in db.scalars(select(CompanyAlias).where(CompanyAlias.stock_id == stock.id)))
    cleaned: list[tuple[str, str]] = []
    seen: set[str] = set()
    for alias, alias_type in aliases:
        normalized = normalize_text(alias)
        if len(normalized) < 2 or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append((normalized, alias_type))
    return cleaned


def _has_phrase(text_value: str, phrase: str) -> bool:
    if not phrase:
        return False
    pattern = r"(^|\s)" + re.escape(phrase) + r"($|\s)"
    return re.search(pattern, text_value) is not None


def match_article_to_stock(db: Session, article: RawArticle, stock: Stock) -> tuple[float, str, dict[str, Any]] | None:
    article_symbols = {symbol.upper().replace(".NS", "").replace(".BO", "") for symbol in article.tickers}
    stock_symbols = {symbol.replace(".NS", "").replace(".BO", "") for symbol in _stock_symbols(stock)}
    overlap = sorted(article_symbols & stock_symbols)
    if overlap:
        return 1.0, "provider_entity", {"matched_symbols": overlap}

    text_blob = normalize_text(" ".join([article.headline, article.summary, article.body]))
    aliases = _alias_values(db, stock)
    for alias, alias_type in aliases:
        if len(alias) >= 4 and _has_phrase(text_blob, alias):
            return 0.86, "alias", {"alias": alias, "alias_type": alias_type}

    symbol = normalize_text(stock.symbol)
    has_finance_context = bool(set(text_blob.split()) & FINANCE_CONTEXT_TERMS)
    if symbol and len(symbol) >= 3 and _has_phrase(text_blob, symbol) and has_finance_context:
        return 0.74, "symbol_context", {"symbol": stock.symbol}

    return None


def insert_article(db: Session, article: RawArticle) -> tuple[StockNewsArticle, bool]:
    published_at = _ensure_utc(article.published_at)
    article_content_hash = content_hash(article.headline, published_at, article.source)
    raw_url = article.url or f"urn:{article.provider}:{article.provider_article_id or article_content_hash}"
    canonical_url, url_hash = canonicalize_url(raw_url)
    existing = db.scalar(select(StockNewsArticle).where(StockNewsArticle.url_hash == url_hash))
    if existing is not None:
        return existing, False

    row = StockNewsArticle(
        url_hash=url_hash,
        content_hash=article_content_hash,
        provider_article_id=article.provider_article_id,
        headline=article.headline.strip()[:2000],
        summary=(article.summary or "").strip()[:4000] or None,
        url=raw_url,
        canonical_url=canonical_url,
        source=article.source or source_domain(canonical_url),
        source_domain=source_domain(canonical_url, article.source),
        provider=article.provider,
        published_at=published_at,
        sentiment_score=article.sentiment_score,
        body=(article.body or "").strip() or None,
        raw_payload=to_json_safe(article.raw_payload or {}),
    )
    db.add(row)
    db.flush()
    return row, True


def link_article_to_stock(
    db: Session,
    *,
    article_id: int,
    stock_id: int,
    published_at: datetime,
    confidence: float,
    method: str,
    evidence: dict[str, Any],
) -> bool:
    stmt = insert(StockNewsLink).values(
        article_id=article_id,
        stock_id=stock_id,
        published_at=published_at,
        match_confidence=confidence,
        match_method=method,
        match_evidence=to_json_safe(evidence),
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=["article_id", "stock_id"])
    result = db.execute(stmt)
    db.flush()
    return bool(result.rowcount)


def serialize_news_row(article: StockNewsArticle, link: StockNewsLink | None = None) -> dict[str, Any]:
    return {
        "id": article.id,
        "headline": article.headline,
        "summary": article.summary,
        "url": article.canonical_url or article.url,
        "source": article.source,
        "source_domain": article.source_domain,
        "provider": article.provider,
        "published_at": article.published_at,
        "sentiment_score": article.sentiment_score,
        "match_confidence": link.match_confidence if link else None,
        "match_method": link.match_method if link else None,
    }


def list_stock_news(db: Session, stock_id: int, *, limit: int = DEFAULT_NEWS_LIMIT) -> list[dict[str, Any]]:
    rows = db.execute(
        select(StockNewsArticle, StockNewsLink)
        .join(StockNewsLink, StockNewsLink.article_id == StockNewsArticle.id)
        .where(StockNewsLink.stock_id == stock_id)
        .order_by(desc(StockNewsLink.published_at), desc(StockNewsArticle.id))
        .limit(max(1, min(limit, 100)))
    ).all()
    return [serialize_news_row(article, link) for article, link in rows]


def _get_or_create_meta(db: Session, stock_id: int) -> StockNewsIngestionMeta:
    meta = db.get(StockNewsIngestionMeta, stock_id)
    if meta is None:
        meta = StockNewsIngestionMeta(stock_id=stock_id, tier=3)
        db.add(meta)
        db.flush()
    return meta


def _is_fresh(meta: StockNewsIngestionMeta) -> bool:
    if meta.last_ingested_at is None:
        return False
    threshold = STALE_THRESHOLDS_MINUTES.get(int(meta.tier or 3), 360)
    age = datetime.now(UTC) - meta.last_ingested_at.astimezone(UTC)
    return age.total_seconds() < threshold * 60


def refresh_stock_news(
    db: Session,
    stock_id: int,
    *,
    force: bool = False,
    limit: int | None = None,
    mode: str = "on_demand",
) -> dict[str, Any]:
    stock = db.get(Stock, stock_id)
    if stock is None:
        raise LookupError("Stock not found")

    lock_id = NEWS_LOCK_NAMESPACE + int(stock_id)
    acquired = bool(db.scalar(text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": lock_id}))
    if not acquired:
        return {
            "status": "skipped",
            "reason": "refresh_already_running",
            "stock_id": stock_id,
            "news": list_stock_news(db, stock_id, limit=limit or settings.news_on_demand_limit),
        }

    run = NewsIngestionRun(
        provider="multi",
        mode=mode,
        stock_id=stock_id,
        articles_in=0,
        articles_new=0,
        links_new=0,
        status="running",
    )
    db.add(run)
    db.flush()

    try:
        meta = _get_or_create_meta(db, stock_id)
        if not force and _is_fresh(meta):
            run.status = "skipped"
            run.finished_at = datetime.now(UTC)
            db.commit()
            return {
                "status": "skipped",
                "reason": "fresh_enough",
                "stock_id": stock_id,
                "run_id": run.id,
                "news": list_stock_news(db, stock_id, limit=limit or settings.news_on_demand_limit),
            }

        requested_limit = limit or settings.news_on_demand_limit
        provider_results: list[dict[str, Any]] = []
        articles_in = 0
        articles_new = 0
        links_new = 0
        linked_any = False
        last_error: str | None = None
        latest_article_at = meta.last_article_at

        for adapter in _adapters(db):
            daily_limit = _provider_daily_limit(adapter.provider)
            if not _quota_available(db, adapter.provider, daily_limit):
                provider_results.append({"provider": adapter.provider, "status": "skipped", "reason": "quota_exhausted"})
                continue
            if daily_limit:
                _increment_quota(db, adapter.provider, daily_limit)

            result = adapter.fetch_for_stock(
                stock,
                limit=requested_limit,
                published_after=meta.last_article_at if not force else None,
            )
            if result.error:
                last_error = result.error
                provider_results.append({"provider": adapter.provider, "status": "error", "error": result.error})
                continue

            provider_articles = len(result.articles)
            provider_new = 0
            provider_links = 0
            articles_in += provider_articles
            for raw_article in result.articles:
                if not raw_article.headline.strip():
                    continue
                match = match_article_to_stock(db, raw_article, stock)
                if match is None:
                    continue
                article_row, is_new = insert_article(db, raw_article)
                if is_new:
                    articles_new += 1
                    provider_new += 1
                confidence, method, evidence = match
                if link_article_to_stock(
                    db,
                    article_id=article_row.id,
                    stock_id=stock.id,
                    published_at=article_row.published_at,
                    confidence=confidence,
                    method=method,
                    evidence={"provider": adapter.provider, **evidence},
                ):
                    links_new += 1
                    provider_links += 1
                    linked_any = True
                    if latest_article_at is None or article_row.published_at > latest_article_at:
                        latest_article_at = article_row.published_at

            provider_results.append(
                {
                    "provider": adapter.provider,
                    "status": "ok",
                    "articles_in": provider_articles,
                    "articles_new": provider_new,
                    "links_new": provider_links,
                }
            )
            if linked_any:
                break

        now = datetime.now(UTC)
        meta.last_ingested_at = now
        meta.last_article_at = latest_article_at
        meta.last_provider = next(
            (row["provider"] for row in reversed(provider_results) if row.get("links_new")),
            None,
        ) or meta.last_provider
        run.articles_in = articles_in
        run.articles_new = articles_new
        run.links_new = links_new
        run.status = "success" if linked_any else ("warning" if last_error else "no_results")
        run.error = last_error
        run.finished_at = now
        db.commit()

        return {
            "status": run.status,
            "stock_id": stock_id,
            "run_id": run.id,
            "articles_in": articles_in,
            "articles_new": articles_new,
            "links_new": links_new,
            "providers": provider_results,
            "news": list_stock_news(db, stock_id, limit=requested_limit),
        }
    except Exception as exc:
        db.rollback()
        try:
            run.status = "failed"
            run.error = str(exc)
            run.finished_at = datetime.now(UTC)
            db.add(run)
            db.commit()
        except Exception:
            db.rollback()
        raise
    finally:
        try:
            db.scalar(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id})
            db.commit()
        except Exception:
            db.rollback()


def refresh_priority_news(db: Session, *, limit_stocks: int = 25, force: bool = False) -> dict[str, Any]:
    stocks = list(
        db.scalars(
            select(Stock)
            .where(
                Stock.is_active.is_(True),
                or_(
                    Stock.is_nifty50.is_(True),
                    Stock.is_nifty100.is_(True),
                    Stock.is_banknifty.is_(True),
                    Stock.is_finnifty.is_(True),
                    Stock.is_sensex.is_(True),
                ),
            )
            .order_by(Stock.symbol.asc())
            .limit(max(1, min(limit_stocks, 100)))
        )
    )
    results = []
    for stock in stocks:
        try:
            results.append(refresh_stock_news(db, stock.id, force=force, mode="bulk"))
        except Exception as exc:
            db.rollback()
            logger.exception("news priority refresh failed stock_id=%s symbol=%s", stock.id, stock.yahoo_symbol)
            results.append(
                {
                    "status": "failed",
                    "stock_id": stock.id,
                    "symbol": stock.yahoo_symbol,
                    "error": str(exc),
                    "links_new": 0,
                }
            )
    return {
        "status": "warning" if any(row.get("status") == "failed" for row in results) else "success",
        "stocks_selected": len(stocks),
        "results": results,
        "links_new": sum(int(row.get("links_new") or 0) for row in results),
    }


def news_database_summary(db: Session) -> dict[str, Any]:
    latest_article_at = db.scalar(select(func.max(StockNewsArticle.published_at)))
    return {
        "articles": int(db.scalar(select(func.count()).select_from(StockNewsArticle)) or 0),
        "links": int(db.scalar(select(func.count()).select_from(StockNewsLink)) or 0),
        "stocks_with_news": int(db.scalar(select(func.count(func.distinct(StockNewsLink.stock_id)))) or 0),
        "latest_article_at": latest_article_at,
    }
