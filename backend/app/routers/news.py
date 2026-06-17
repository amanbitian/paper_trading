from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.news import NewsIngestionRun
from app.schemas.news import (
    NewsArticleRead,
    NewsIngestionRunRead,
    NewsPriorityRefreshRead,
    NewsRefreshRead,
    NewsSummaryRead,
)
from app.security import get_current_user
from app.services.news_service import (
    list_stock_news,
    news_database_summary,
    refresh_priority_news,
    refresh_stock_news,
)


router = APIRouter(prefix="/news", tags=["news"], dependencies=[Depends(get_current_user)])


@router.get("/summary", response_model=NewsSummaryRead)
def summary(db: Session = Depends(get_db)) -> dict:
    return news_database_summary(db)


@router.get("/stocks/{stock_id}", response_model=list[NewsArticleRead])
def stock_news(
    stock_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[dict]:
    return list_stock_news(db, stock_id, limit=limit)


@router.post("/stocks/{stock_id}/refresh", response_model=NewsRefreshRead)
def refresh_stock_news_endpoint(
    stock_id: int,
    force: bool = False,
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return refresh_stock_news(db, stock_id, force=force, limit=limit, mode="api")
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/refresh-priority", response_model=NewsPriorityRefreshRead)
def refresh_priority_news_endpoint(
    limit_stocks: int = Query(default=25, ge=1, le=100),
    force: bool = False,
    db: Session = Depends(get_db),
) -> dict:
    return refresh_priority_news(db, limit_stocks=limit_stocks, force=force)


@router.get("/runs", response_model=list[NewsIngestionRunRead])
def runs(
    limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[NewsIngestionRun]:
    return list(
        db.scalars(
            select(NewsIngestionRun)
            .order_by(desc(NewsIngestionRun.started_at), desc(NewsIngestionRun.id))
            .limit(limit)
        )
    )
