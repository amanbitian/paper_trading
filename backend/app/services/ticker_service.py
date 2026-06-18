from __future__ import annotations

import logging
import re

from sqlalchemy import case, exists, literal, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.constants.market_indices import stock_index_flag_for_code
from app.models.stock import Stock
from app.utils.observability import timed

logger = logging.getLogger(__name__)

SEARCH_STOPWORDS = {
    "and",
    "co",
    "company",
    "corp",
    "corporation",
    "equity",
    "india",
    "limited",
    "ltd",
    "nse",
    "bse",
    "share",
    "shares",
    "stock",
    "stocks",
    "the",
}

SEARCH_ALIASES = {
    "cab": "ola",
    "cabs": "ola",
    "taxi": "ola",
    "taxis": "ola",
    "ev": "electric",
}


def normalize_nse_symbol(symbol: str) -> str:
    clean = symbol.strip().upper().replace(".NS", "")
    return f"{clean}.NS"


def normalize_bse_symbol(code: str) -> str:
    clean = str(code).strip().upper().replace(".BO", "")
    return f"{clean}.BO"


def build_stock_search_tokens(query: str) -> list[str]:
    raw_tokens = [token.lower() for token in re.findall(r"[a-zA-Z0-9]+", query)]
    expanded = [SEARCH_ALIASES.get(token, token) for token in raw_tokens]
    filtered = [token for token in expanded if token not in SEARCH_STOPWORDS]
    tokens = filtered or expanded
    unique_tokens: list[str] = []
    for token in tokens:
        if token and token not in unique_tokens:
            unique_tokens.append(token)
    return unique_tokens


def _stock_search_order(clean_query: str, tokens: list[str]):
    first_token = tokens[0]
    exact_symbol = clean_query.upper().replace(".NS", "").replace(".BO", "")
    compact_query = re.sub(r"[^A-Z0-9]", "", clean_query.upper())
    relevance_score = literal(0)
    for token in tokens:
        relevance_score += case(
            (Stock.symbol.ilike(f"{token}%"), 8),
            (Stock.yahoo_symbol.ilike(f"{token}%"), 7),
            (Stock.symbol.ilike(f"%{token}%"), 5),
            (Stock.yahoo_symbol.ilike(f"%{token}%"), 4),
            (Stock.company_name.ilike(f"%{token}%"), 3),
            (Stock.sector.ilike(f"%{token}%"), 1),
            (Stock.industry.ilike(f"%{token}%"), 1),
            else_=0,
        )
    rank = case(
        (Stock.symbol == exact_symbol, 0),
        (Stock.symbol == compact_query, 0),
        (Stock.yahoo_symbol == clean_query.upper(), 1),
        (Stock.symbol.ilike(f"{first_token}%"), 2),
        (Stock.yahoo_symbol.ilike(f"{first_token}%"), 3),
        (Stock.company_name.ilike(f"{first_token}%"), 4),
        else_=10,
    )
    exchange_rank = case((Stock.exchange == "NSE", 0), else_=1)
    return rank.asc(), relevance_score.desc(), exchange_rank.asc(), Stock.symbol.asc()


@timed("ticker.upsert_stock")
def upsert_stock(
    db: Session,
    *,
    symbol: str,
    yahoo_symbol: str,
    exchange: str,
    company_name: str | None = None,
    sector: str | None = None,
    industry: str | None = None,
    currency: str = "INR",
    is_active: bool = True,
) -> Stock:
    values = {
        "symbol": symbol.strip().upper(),
        "yahoo_symbol": yahoo_symbol.strip().upper(),
        "exchange": exchange.strip().upper(),
        "company_name": company_name,
        "sector": sector,
        "industry": industry,
        "currency": currency,
        "is_active": is_active,
    }
    stmt = insert(Stock).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_stocks_symbol_exchange",
        set_={
            "yahoo_symbol": stmt.excluded.yahoo_symbol,
            "company_name": stmt.excluded.company_name,
            "sector": stmt.excluded.sector,
            "industry": stmt.excluded.industry,
            "currency": stmt.excluded.currency,
            "is_active": stmt.excluded.is_active,
        },
    ).returning(Stock.id)
    stock_id = db.execute(stmt).scalar_one()
    stock = db.get(Stock, stock_id)
    if stock is None:
        raise RuntimeError("Stock upsert did not return a persisted row")
    return stock


@timed("ticker.search_stocks")
def search_stocks(
    db: Session,
    query: str,
    exchange: str | None = None,
    index_code: str | None = None,
    limit: int = 50,
    *,
    require_active: bool = True,
) -> list[Stock]:
    clean_query = query.strip()
    tokens = build_stock_search_tokens(clean_query)
    if not tokens:
        return []
    index_flag = stock_index_flag_for_code(index_code)
    if index_code and not index_flag:
        return []

    searchable_fields = [
        Stock.symbol,
        Stock.yahoo_symbol,
        Stock.company_name,
        Stock.sector,
        Stock.industry,
    ]
    token_conditions = [
        or_(*(field.ilike(f"%{token}%") for field in searchable_fields))
        for token in tokens
    ]
    if require_active:
        base_filters = [Stock.is_active.is_(True)]
    else:
        from app.models.stock import StockPrice

        base_filters = [
            exists(select(1).where(StockPrice.stock_id == Stock.id)),
        ]
    if exchange:
        base_filters.append(Stock.exchange == exchange.strip().upper())
    if index_flag:
        base_filters.append(getattr(Stock, index_flag).is_(True))

    order_by = _stock_search_order(clean_query, tokens)
    strict_stmt = (
        select(Stock)
        .where(*base_filters, *token_conditions)
        .order_by(*order_by)
        .limit(limit)
    )
    strict_matches = list(db.scalars(strict_stmt))
    if strict_matches or len(tokens) == 1:
        return strict_matches

    broad_stmt = (
        select(Stock)
        .where(*base_filters, or_(*token_conditions))
        .order_by(*order_by)
        .limit(limit)
    )
    return list(db.scalars(broad_stmt))
