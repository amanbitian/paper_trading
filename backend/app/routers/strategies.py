from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.stock import Stock
from app.models.strategy import StrategySignal, StrategyTemplate, UserStrategy
from app.models.user import User
from app.schemas.paper_trading import PaperOrderRead
from app.schemas.strategy import (
    GenerateSignalRequest,
    StrategyPreviewRead,
    StrategyPreviewRequest,
    StrategySignalRead,
    StrategyTemplateRead,
    UserStrategyCreate,
    UserStrategyRead,
)
from app.security import get_current_user
from app.services.signal_outcome_service import get_strategy_accuracy
from app.services.email_service import send_signal_alert
from app.services.strategy_service import (
    create_user_strategy,
    execute_signal_as_paper_order,
    generate_signal,
    preview_signal,
)


router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.get("/signal-accuracy")
def signal_accuracy(
    lookback_days: int = 90,
    strategy_template_id: int | None = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    return get_strategy_accuracy(
        db,
        strategy_template_id=strategy_template_id,
        lookback_days=lookback_days,
    )


@router.get("/templates", response_model=list[StrategyTemplateRead])
def templates(db: Session = Depends(get_db)) -> list[StrategyTemplate]:
    return list(
        db.scalars(
            select(StrategyTemplate)
            .where(StrategyTemplate.is_active.is_(True))
            .order_by(StrategyTemplate.strategy_name.asc())
        )
    )


@router.post("/user-strategy", response_model=UserStrategyRead)
def create_strategy(
    payload: UserStrategyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserStrategy:
    return create_user_strategy(db, current_user, payload)


@router.get("/user-strategy", response_model=list[UserStrategyRead])
def user_strategies(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> list[UserStrategy]:
    return list(
        db.scalars(select(UserStrategy).where(UserStrategy.user_id == current_user.id).order_by(UserStrategy.id.asc()))
    )


@router.post("/generate-signal", response_model=StrategySignalRead)
def create_signal(
    payload: GenerateSignalRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StrategySignal:
    signal = generate_signal(db, current_user, payload)
    if signal.signal_type in {"BUY", "SELL"} and current_user.email_alerts_enabled:
        stock = db.get(Stock, signal.stock_id)
        user_strategy = db.get(UserStrategy, signal.user_strategy_id)
        template = (
            db.get(StrategyTemplate, user_strategy.strategy_template_id) if user_strategy else None
        )
        background_tasks.add_task(
            send_signal_alert,
            current_user.email,
            current_user.name,
            signal.signal_type,
            stock.symbol if stock else str(signal.stock_id),
            template.strategy_name if template else "Strategy",
            float(signal.confidence_score),
            signal.reason or "",
            int(signal.suggested_quantity),
            float(signal.suggested_price or 0),
            signal.indicators or {},
        )
    return signal


@router.post("/preview-signal", response_model=StrategyPreviewRead)
def preview_strategy_signal(
    payload: StrategyPreviewRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    return preview_signal(db, payload)


@router.get("/signals", response_model=list[StrategySignalRead])
def signals(
    user_strategy_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[StrategySignal]:
    stmt = select(StrategySignal).join(UserStrategy).where(UserStrategy.user_id == current_user.id)
    if user_strategy_id:
        stmt = stmt.where(StrategySignal.user_strategy_id == user_strategy_id)
    return list(db.scalars(stmt.order_by(StrategySignal.created_at.desc())))


@router.post("/signals/{signal_id}/execute-paper-order", response_model=PaperOrderRead)
def execute_signal(
    signal_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return execute_signal_as_paper_order(db, current_user, signal_id)
