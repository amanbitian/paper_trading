from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.portfolio import PaperOrder, PaperTrade
from app.models.user import User
from app.schemas.paper_trading import PaperOrderCreate, PaperOrderRead, PaperTradeRead
from app.security import get_current_user
from app.services.paper_trading_service import cancel_paper_order, match_pending_orders, place_paper_order


router = APIRouter(tags=["paper trading"])


@router.post("/paper-orders", response_model=PaperOrderRead)
def create_order(
    payload: PaperOrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PaperOrder:
    return place_paper_order(
        db,
        current_user,
        payload,
        slippage_bps_override=payload.slippage_bps,
    )


@router.post("/paper-orders/match")
def match_orders(
    stock_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    return match_pending_orders(db, stock_id=stock_id)


@router.get("/paper-orders", response_model=list[PaperOrderRead])
def list_orders(
    portfolio_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[PaperOrder]:
    stmt = select(PaperOrder).where(PaperOrder.user_id == current_user.id)
    if portfolio_id:
        stmt = stmt.where(PaperOrder.portfolio_id == portfolio_id)
    return list(db.scalars(stmt.order_by(PaperOrder.placed_at.desc())))


@router.post("/paper-orders/{order_id}/cancel", response_model=PaperOrderRead)
def cancel_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PaperOrder:
    return cancel_paper_order(db, current_user, order_id)


@router.get("/paper-trades", response_model=list[PaperTradeRead])
def list_trades(
    portfolio_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[PaperTrade]:
    stmt = select(PaperTrade).where(PaperTrade.user_id == current_user.id)
    if portfolio_id:
        stmt = stmt.where(PaperTrade.portfolio_id == portfolio_id)
    return list(db.scalars(stmt.order_by(PaperTrade.executed_at.desc())))

