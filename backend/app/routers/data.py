from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.data import DataIngestionDashboardRead
from app.security import get_current_user
from app.services.data_ingestion_stats_service import get_data_ingestion_dashboard


router = APIRouter(prefix="/data", tags=["data"], dependencies=[Depends(get_current_user)])


@router.get("/ingestion-dashboard", response_model=DataIngestionDashboardRead)
def ingestion_dashboard(
    runs_limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    return get_data_ingestion_dashboard(db, runs_limit=runs_limit)
