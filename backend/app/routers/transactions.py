from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.portfolio import Portfolio, Transaction
from app.models.user import User
from app.schemas.transaction import ManualBuyRequest, ManualSellRequest, TransactionRead
from app.security import get_current_user
from app.services.portfolio_service import add_manual_buy, add_manual_sell


router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.post("/manual-buy", response_model=TransactionRead)
def manual_buy(
    payload: ManualBuyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Transaction:
    transaction = add_manual_buy(db, user=current_user, **payload.model_dump())
    db.commit()
    db.refresh(transaction)
    return transaction


@router.post("/manual-sell", response_model=TransactionRead)
def manual_sell(
    payload: ManualSellRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Transaction:
    transaction = add_manual_sell(db, user=current_user, **payload.model_dump())
    db.commit()
    db.refresh(transaction)
    return transaction


@router.get("", response_model=list[TransactionRead])
def list_transactions(
    portfolio_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[Transaction]:
    stmt = select(Transaction).join(Portfolio).where(Transaction.user_id == current_user.id)
    if portfolio_id:
        stmt = stmt.where(Transaction.portfolio_id == portfolio_id, Portfolio.user_id == current_user.id)
    return list(db.scalars(stmt.order_by(Transaction.transaction_date.desc())))

