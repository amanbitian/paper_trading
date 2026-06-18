from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.market import (
    MarketMoversRead,
    MarketOverviewRead,
    MarketSyncStartRead,
    MarketSyncStatusRead,
    MarketTrendFiltersRead,
    MarketTrendsRead,
    SequentialRankingsRead,
)
from app.security import get_current_user
from app.services.analytics_refresh_service import (
    get_cached_sequential_rankings,
    refresh_all_analytics,
    refresh_sequential_rankings_cache,
)
from app.services.algo_finding_service import generate_sequential_rankings
from app.services.market_movers_service import DEFAULT_MOVER_LIMIT, compute_market_movers_from_db
from app.services.market_overview_service import get_market_overview
from app.services.market_data_service import (
    get_latest_stored_daily_date,
    previous_business_day,
    probe_provider_latest_date,
)
from app.services.market_sync_service import get_market_sync_status, start_market_sync
from app.services.market_trends_service import get_market_trend_filters, get_market_trends


router = APIRouter(prefix="/market", tags=["market"], dependencies=[Depends(get_current_user)])


@router.get("/overview", response_model=MarketOverviewRead)
def overview(refresh: bool = False, db: Session = Depends(get_db)) -> dict:
    return get_market_overview(db=db, refresh=refresh)


@router.get("/movers", response_model=MarketMoversRead)
def movers(
    nifty_index: str | None = None,
    limit: int = DEFAULT_MOVER_LIMIT,
    db: Session = Depends(get_db),
) -> dict:
    from datetime import UTC, datetime

    bounded_limit = min(max(limit, 1), DEFAULT_MOVER_LIMIT)
    payload = compute_market_movers_from_db(db, limit=bounded_limit, nifty_index=nifty_index)
    return {
        "as_of": datetime.now(UTC),
        "record_date": payload.get("record_date"),
        "source": "database",
        "eligible_count": int(payload.get("eligible_count") or 0),
        "nifty_index": payload.get("nifty_index"),
        "nifty_index_label": payload.get("nifty_index_label"),
        "top_gainers": payload.get("top_gainers") or [],
        "top_losers": payload.get("top_losers") or [],
        "volume_shockers": payload.get("volume_shockers") or [],
        "most_bought": payload.get("most_bought") or [],
    }


@router.post("/refresh-analytics")
def refresh_analytics(db: Session = Depends(get_db)) -> dict:
    return refresh_all_analytics(db)


@router.get("/sync-status", response_model=MarketSyncStatusRead)
def sync_status(db: Session = Depends(get_db)) -> dict:
    return get_market_sync_status(db)


@router.post("/sync", response_model=MarketSyncStartRead)
def sync_market_data(db: Session = Depends(get_db)) -> dict:
    return start_market_sync(db)


@router.get("/debug/provider-fetch")
def debug_provider_fetch(
    symbol: str = Query(default="RELIANCE.NS"),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    resolved_end = end_date or date(2026, 5, 30)
    resolved_start = start_date or date(2026, 5, 27)
    provider_latest, provider_dates, provider_error = probe_provider_latest_date(
        symbol,
        start_date=resolved_start,
        end_date=resolved_end,
    )
    return {
        "symbol": symbol,
        "start_date": resolved_start,
        "end_date": resolved_end,
        "market_previous_business_day": previous_business_day(),
        "latest_stored_daily_date": get_latest_stored_daily_date(db),
        "provider_latest_date": provider_latest,
        "provider_dates": [value.isoformat() for value in provider_dates],
        "provider_error": provider_error,
    }


@router.get("/sequential-rankings", response_model=SequentialRankingsRead)
def sequential_rankings(
    limit: int = 15,
    universe_limit: int | None = None,
    refresh: bool = False,
    db: Session = Depends(get_db),
) -> dict:
    bounded_limit = min(max(limit, 1), 50)
    bounded_universe = min(max(universe_limit, 1), 2000) if universe_limit else 2000
    if refresh:
        return refresh_sequential_rankings_cache(
            db=db,
            limit=bounded_limit,
            universe_limit=bounded_universe,
        )
    cached = get_cached_sequential_rankings(db)
    if cached is not None:
        return cached
    return refresh_sequential_rankings_cache(
        db=db,
        limit=bounded_limit,
        universe_limit=bounded_universe,
    )


@router.get("/trends/filters", response_model=MarketTrendFiltersRead)
def trend_filters(db: Session = Depends(get_db)) -> dict:
    return get_market_trend_filters(db)


@router.get("/trends", response_model=MarketTrendsRead)
def trends(
    period: str = "daily",
    limit: int = 1000,
    market: str = "stocks",
    nifty_index: str | None = None,
    industry_group: str | None = None,
    sort_by: str = "size",
    db: Session = Depends(get_db),
) -> dict:
    from app.services.market_trends_service import DEFAULT_TREND_LIMIT, MAX_TREND_LIMIT

    bounded_limit = min(max(limit, 1), MAX_TREND_LIMIT)
    return get_market_trends(
        db,
        period=period,
        limit=bounded_limit,
        market_filter=market,
        nifty_index=nifty_index,
        industry_group=industry_group,
        sort_by=sort_by,
    )
