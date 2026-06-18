from datetime import date
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.stock import Stock, StockPrice
from app.schemas.algo import AlgoFindingRead
from app.schemas.stock import PriceSyncResponse, StockPerformanceRead, StockPriceRead, StockRead
from app.security import get_current_user
from app.services.algo_finding_service import generate_stock_algo_findings
from app.services.market_data_service import ensure_daily_interval, sync_all_active_stocks, sync_stock_prices
from app.services.search_telemetry_service import record_search_query
from app.services.stock_performance_service import (
    list_stock_index_filters,
    list_stock_industries,
    list_stock_performance,
    list_stock_sectors,
)
from app.services.ticker_service import search_stocks


router = APIRouter(prefix="/stocks", tags=["stocks"], dependencies=[Depends(get_current_user)])


@router.get("/search", response_model=list[StockRead])
def search(
    query: str = Query(min_length=1),
    exchange: str | None = None,
    index_code: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[Stock]:
    started_at = time.perf_counter()
    result_count = 0
    status = "ok"
    error_message = None
    try:
        results = search_stocks(db, query, exchange, index_code=index_code, limit=limit)
        result_count = len(results)
        return results
    except Exception as exc:
        status = "error"
        error_message = str(exc)
        raise
    finally:
        record_search_query(
            search_type="stock_search",
            query_text=query,
            filter_name="index_code" if index_code else ("exchange" if exchange else None),
            filter_value=index_code or exchange,
            result_count=result_count,
            duration_ms=(time.perf_counter() - started_at) * 1000,
            status=status,
            error_message=error_message,
        )


@router.get("/sectors", response_model=list[str])
def sectors(
    exchange: str | None = None,
    index_code: str | None = None,
    only_with_prices: bool = False,
    db: Session = Depends(get_db),
) -> list[str]:
    return list_stock_sectors(
        db,
        exchange=exchange,
        index_code=index_code,
        only_with_prices=only_with_prices,
    )


@router.get("/industries", response_model=list[str])
def industries(
    exchange: str | None = None,
    sector: str | None = None,
    index_code: str | None = None,
    only_with_prices: bool = False,
    db: Session = Depends(get_db),
) -> list[str]:
    return list_stock_industries(
        db,
        exchange=exchange,
        sector=sector,
        index_code=index_code,
        only_with_prices=only_with_prices,
    )


@router.get("/index-filters", response_model=list[dict[str, str]])
def index_filters() -> list[dict[str, str]]:
    return list_stock_index_filters()


@router.get("/performance", response_model=list[StockPerformanceRead])
def performance(
    query: str | None = None,
    exchange: str | None = None,
    sector: str | None = None,
    industry: str | None = None,
    index_code: str | None = None,
    limit: int = Query(default=5000, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    only_with_prices: bool = False,
    refresh: bool = False,
    db: Session = Depends(get_db),
) -> list[dict]:
    return list_stock_performance(
        db,
        query=query,
        exchange=exchange,
        sector=sector,
        industry=industry,
        index_code=index_code,
        limit=limit,
        offset=offset,
        only_with_prices=only_with_prices,
        refresh=refresh,
    )


@router.get("/{stock_id}", response_model=StockRead)
def get_stock(stock_id: int, db: Session = Depends(get_db)) -> Stock:
    stock = db.get(Stock, stock_id)
    if stock is None:
        raise HTTPException(status_code=404, detail="Stock not found")
    return stock


@router.get("/{stock_id}/prices", response_model=list[StockPriceRead])
def get_prices(
    stock_id: int,
    timeframe: str = "1d",
    limit: int = Query(default=250, ge=1, le=10000),
    db: Session = Depends(get_db),
) -> list[StockPrice]:
    try:
        timeframe = ensure_daily_interval(timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    stmt = (
        select(StockPrice)
        .where(StockPrice.stock_id == stock_id, StockPrice.timeframe == timeframe)
        .order_by(StockPrice.price_datetime.desc())
        .limit(limit)
    )
    return list(reversed(list(db.scalars(stmt))))


@router.get("/{stock_id}/algo-findings", response_model=list[AlgoFindingRead])
def get_algo_findings(
    stock_id: int,
    limit: int = Query(default=10000, ge=80, le=10000),
    db: Session = Depends(get_db),
) -> list[dict]:
    stock = db.get(Stock, stock_id)
    if stock is None:
        raise HTTPException(status_code=404, detail="Stock not found")
    return generate_stock_algo_findings(db, stock_id, limit=limit)


@router.post("/{stock_id}/sync-prices", response_model=PriceSyncResponse)
def sync_prices(
    stock_id: int,
    period: str = "1y",
    interval: str = "1d",
    start_date: date | None = None,
    end_date: date | None = None,
    chunk_days: int | None = Query(default=None, ge=1, le=365),
    sleep_seconds: float = Query(default=0, ge=0, le=60),
    incremental: bool = True,
    db: Session = Depends(get_db),
) -> PriceSyncResponse:
    try:
        sync_result = sync_stock_prices(
            db,
            stock_id,
            period=period,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            chunk_days=chunk_days,
            sleep_seconds=sleep_seconds,
            incremental=incremental,
        )
        rows_saved = sync_result.rows_saved
        outcome = sync_result.outcome
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Stock not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PriceSyncResponse(
        stock_id=stock_id,
        rows_saved=rows_saved,
        timeframe=ensure_daily_interval(interval),
        outcome=outcome,
    )


@router.post("/sync-all")
def sync_all(
    limit: int | None = Query(default=None, ge=1),
    offset: int | None = Query(default=None, ge=0),
    period: str = "1y",
    interval: str = "1d",
    start_date: date | None = None,
    end_date: date | None = None,
    exchange: str | None = None,
    chunk_days: int | None = Query(default=None, ge=1, le=365),
    sleep_seconds: float = Query(default=0, ge=0, le=60),
    incremental: bool = True,
    db: Session = Depends(get_db),
) -> dict:
    if limit is None or limit > 25:
        raise HTTPException(
            status_code=400,
            detail="Bulk sync via API is capped at 25 symbols. Use scripts/ingest_market_history.py for full-universe ingestion.",
        )
    try:
        synced = sync_all_active_stocks(
            db,
            limit=limit,
            offset=offset,
            period=period,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            exchange=exchange,
            chunk_days=chunk_days,
            sleep_seconds=sleep_seconds,
            incremental=incremental,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"synced": synced.get("symbol_results", synced), "summary": synced}
