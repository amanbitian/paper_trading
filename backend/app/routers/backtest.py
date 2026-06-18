from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models.backtest import BacktestRun, BacktestTrade
from app.models.user import User
from app.schemas.backtest import (
    BacktestRunRead,
    BacktestRunRequest,
    BacktestRunResponse,
    BacktestTradeRead,
)
from app.security import get_current_user
from app.services.backtest_service import run_backtest


router = APIRouter(prefix="/backtest", tags=["backtest"])


@router.post("/run", response_model=BacktestRunResponse)
def run(
    payload: BacktestRunRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    result = run_backtest(db, current_user, payload)
    run_model = result["run"]
    data = BacktestRunRead.model_validate(run_model).model_dump()
    data["trades"] = [BacktestTradeRead.model_validate(trade).model_dump() for trade in run_model.trades]
    data["equity_curve"] = result["equity_curve"]
    data["benchmark_curve"] = result.get("benchmark_curve", [])
    for key in (
        "walk_forward_enabled",
        "is_start_date",
        "is_end_date",
        "is_total_return_pct",
        "is_sharpe_ratio",
        "is_max_drawdown_pct",
        "is_win_rate",
        "is_num_trades",
        "oos_start_date",
        "oos_end_date",
        "oos_total_return_pct",
        "oos_sharpe_ratio",
        "oos_max_drawdown_pct",
        "oos_win_rate",
        "oos_num_trades",
        "overfitting_score",
    ):
        if key in result:
            data[key] = result[key]
    return data


@router.get("/{backtest_id}", response_model=BacktestRunResponse)
def get_backtest(
    backtest_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    run_model = db.scalar(
        select(BacktestRun)
        .where(BacktestRun.id == backtest_id, BacktestRun.user_id == current_user.id)
        .options(selectinload(BacktestRun.trades))
    )
    if run_model is None:
        raise HTTPException(status_code=404, detail="Backtest not found")
    data = BacktestRunRead.model_validate(run_model).model_dump()
    data["trades"] = [BacktestTradeRead.model_validate(trade).model_dump() for trade in run_model.trades]
    data["equity_curve"] = []
    return data


@router.get("/{backtest_id}/trades", response_model=list[BacktestTradeRead])
def get_backtest_trades(
    backtest_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[BacktestTrade]:
    run_model = db.scalar(
        select(BacktestRun).where(BacktestRun.id == backtest_id, BacktestRun.user_id == current_user.id)
    )
    if run_model is None:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return list(db.scalars(select(BacktestTrade).where(BacktestTrade.backtest_id == backtest_id)))
