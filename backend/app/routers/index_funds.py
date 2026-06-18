from datetime import date
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.index_fund import IndexFund, IndexFundPrice
from app.schemas.algo import AlgoFindingRead
from app.schemas.index_fund import (
    IndexFundPerformanceRead,
    IndexFundPriceRead,
    IndexFundRead,
    IndexFundReturnSeries,
    IndexFundSyncResponse,
)
from app.security import get_current_user
from app.services.algo_finding_service import generate_index_fund_algo_findings
from app.services.index_fund_service import (
    calculate_index_return_series,
    list_index_fund_performance,
    search_index_funds,
    sync_all_active_index_funds,
    sync_index_fund_prices,
)
from app.services.market_data_service import ensure_daily_interval
from app.services.search_telemetry_service import record_search_query


router = APIRouter(prefix="/index-funds", tags=["index-funds"], dependencies=[Depends(get_current_user)])


@router.get("/search", response_model=list[IndexFundRead])
def search(
    query: str = Query(min_length=1),
    category: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[IndexFund]:
    started_at = time.perf_counter()
    result_count = 0
    status = "ok"
    error_message = None
    try:
        results = search_index_funds(db, query, category=category, limit=limit)
        result_count = len(results)
        return results
    except Exception as exc:
        status = "error"
        error_message = str(exc)
        raise
    finally:
        record_search_query(
            search_type="index_fund_search",
            query_text=query,
            filter_name="category" if category else None,
            filter_value=category,
            result_count=result_count,
            duration_ms=(time.perf_counter() - started_at) * 1000,
            status=status,
            error_message=error_message,
        )


@router.get("/performance", response_model=list[IndexFundPerformanceRead])
def performance(
    query: str | None = None,
    category: str | None = None,
    limit: int = Query(default=5000, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    only_with_prices: bool = False,
    db: Session = Depends(get_db),
) -> list[dict]:
    return list_index_fund_performance(
        db,
        query=query,
        category=category,
        limit=limit,
        offset=offset,
        only_with_prices=only_with_prices,
    )


@router.get("/returns", response_model=list[IndexFundReturnSeries])
def returns(
    ids: list[int] = Query(default_factory=list),
    start_date: date = Query(...),
    end_date: date = Query(...),
    db: Session = Depends(get_db),
) -> list[dict]:
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date must be on or before end_date")
    return calculate_index_return_series(
        db,
        index_fund_ids=ids,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/{index_fund_id}", response_model=IndexFundRead)
def get_index_fund(index_fund_id: int, db: Session = Depends(get_db)) -> IndexFund:
    index_fund = db.get(IndexFund, index_fund_id)
    if index_fund is None:
        raise HTTPException(status_code=404, detail="Index fund not found")
    return index_fund


@router.get("/{index_fund_id}/prices", response_model=list[IndexFundPriceRead])
def get_prices(
    index_fund_id: int,
    timeframe: str = "1d",
    limit: int = Query(default=250, ge=1, le=10000),
    db: Session = Depends(get_db),
) -> list[IndexFundPrice]:
    try:
        timeframe = ensure_daily_interval(timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    stmt = (
        select(IndexFundPrice)
        .where(IndexFundPrice.index_fund_id == index_fund_id, IndexFundPrice.timeframe == timeframe)
        .order_by(IndexFundPrice.price_datetime.desc())
        .limit(limit)
    )
    return list(reversed(list(db.scalars(stmt))))


@router.get("/{index_fund_id}/algo-findings", response_model=list[AlgoFindingRead])
def algo_findings(
    index_fund_id: int,
    limit: int = Query(default=10000, ge=80, le=10000),
    db: Session = Depends(get_db),
) -> list[dict]:
    if db.get(IndexFund, index_fund_id) is None:
        raise HTTPException(status_code=404, detail="Index fund not found")
    return generate_index_fund_algo_findings(db, index_fund_id, limit=limit)


@router.post("/{index_fund_id}/sync-prices", response_model=IndexFundSyncResponse)
def sync_prices(
    index_fund_id: int,
    period: str = "1y",
    interval: str = "1d",
    start_date: date | None = None,
    end_date: date | None = None,
    chunk_days: int | None = Query(default=365, ge=1, le=365),
    sleep_seconds: float = Query(default=0, ge=0, le=60),
    incremental: bool = False,
    db: Session = Depends(get_db),
) -> IndexFundSyncResponse:
    try:
        result = sync_index_fund_prices(
            db,
            index_fund_id,
            period=period,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            chunk_days=chunk_days,
            sleep_seconds=sleep_seconds,
            incremental=incremental,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Index fund not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return IndexFundSyncResponse(
        index_fund_id=index_fund_id,
        rows_saved=result.rows_saved,
        timeframe=ensure_daily_interval(interval),
    )


@router.post("/sync-all")
def sync_all(
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    category: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    chunk_days: int | None = Query(default=365, ge=1, le=365),
    sleep_seconds: float = Query(default=0, ge=0, le=60),
    incremental: bool = False,
    db: Session = Depends(get_db),
) -> dict:
    if limit is None or limit > 25:
        raise HTTPException(
            status_code=400,
            detail="Bulk sync via API is capped at 25 items. Use scripts/ingest_index_funds.py for the full universe.",
        )
    return {
        "synced": sync_all_active_index_funds(
            db,
            limit=limit,
            offset=offset,
            category=category,
            start_date=start_date,
            end_date=end_date,
            chunk_days=chunk_days,
            sleep_seconds=sleep_seconds,
            incremental=incremental,
        )
    }
