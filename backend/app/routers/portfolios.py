from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models.portfolio import Portfolio, PortfolioDailySnapshot, PortfolioHolding
from app.models.user import User
from app.schemas.portfolio import (
    HoldingRead,
    PortfolioCreate,
    PortfolioPerformanceRead,
    PortfolioRead,
)
from app.security import get_current_user
from app.services.portfolio_service import calculate_portfolio_value, generate_daily_snapshot
from app.services.risk_service import get_portfolio_risk_metrics


router = APIRouter(prefix="/portfolios", tags=["portfolios"])


@router.post("", response_model=PortfolioRead)
def create_portfolio(
    payload: PortfolioCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Portfolio:
    data = payload.model_dump()
    cash_balance = data.pop("cash_balance", None)
    portfolio = Portfolio(user_id=current_user.id, **data)
    if portfolio.portfolio_type == "paper":
        portfolio.cash_balance = cash_balance if cash_balance is not None else portfolio.starting_value
    else:
        portfolio.cash_balance = Decimal("0")
    db.add(portfolio)
    db.commit()
    db.refresh(portfolio)
    return portfolio


@router.get("", response_model=list[PortfolioRead])
def list_portfolios(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> list[Portfolio]:
    return list(
        db.scalars(select(Portfolio).where(Portfolio.user_id == current_user.id).order_by(Portfolio.id.asc()))
    )


@router.get("/{portfolio_id}", response_model=PortfolioRead)
def get_portfolio(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Portfolio:
    portfolio = db.scalar(
        select(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.user_id == current_user.id)
    )
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return portfolio


@router.get("/{portfolio_id}/holdings", response_model=list[HoldingRead])
def get_holdings(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[PortfolioHolding]:
    portfolio = db.scalar(
        select(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.user_id == current_user.id)
    )
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return list(
        db.scalars(
            select(PortfolioHolding)
            .where(PortfolioHolding.portfolio_id == portfolio_id)
            .options(selectinload(PortfolioHolding.stock))
        )
    )


@router.get("/{portfolio_id}/performance", response_model=PortfolioPerformanceRead)
def get_performance(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    portfolio = db.scalar(
        select(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.user_id == current_user.id)
    )
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    values = calculate_portfolio_value(db, portfolio_id)
    snapshot = generate_daily_snapshot(db, portfolio_id)
    if snapshot is not None:
        db.commit()
    snapshots = list(
        db.scalars(
            select(PortfolioDailySnapshot)
            .where(PortfolioDailySnapshot.portfolio_id == portfolio_id)
            .order_by(PortfolioDailySnapshot.snapshot_date.asc())
            .limit(365)
        )
    )
    values["snapshots"] = [
        {
            "snapshot_date": item.snapshot_date.isoformat(),
            "total_value": float(item.total_value),
            "market_value": float(item.market_value),
            "cash_balance": float(item.cash_balance),
            "day_pnl": float(item.day_pnl),
        }
        for item in snapshots
    ]
    if not values["snapshots"] and snapshot is not None:
        values["snapshots"] = [
            {
                "snapshot_date": snapshot.snapshot_date.isoformat(),
                "total_value": float(snapshot.total_value),
                "market_value": float(snapshot.market_value),
                "cash_balance": float(snapshot.cash_balance),
                "day_pnl": float(snapshot.day_pnl),
            }
        ]
    return values


@router.get("/{portfolio_id}/risk-metrics")
def risk_metrics(
    portfolio_id: int,
    lookback_days: int = 252,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    portfolio = db.scalar(
        select(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.user_id == current_user.id)
    )
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return get_portfolio_risk_metrics(db, portfolio_id, lookback_days=lookback_days)

