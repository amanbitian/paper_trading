from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import desc, select
from sqlalchemy.orm import Session, joinedload

from app.models.stock import StockPrice
from app.models.portfolio import Portfolio
from app.models.strategy import StrategySignal, StrategyTemplate, UserStrategy
from app.models.user import User
from app.schemas.paper_trading import PaperOrderCreate
from app.schemas.strategy import GenerateSignalRequest, StrategyPreviewRequest, UserStrategyCreate
from app.services.market_data_service import (
    DAILY_TIMEFRAME,
    get_latest_price,
    prices_to_dataframe,
    sync_stock_prices,
)
from app.services.index_fund_service import (
    get_latest_index_price,
    index_prices_to_dataframe,
    sync_index_fund_prices,
)
from app.models.index_fund import IndexFundPrice
from app.services.paper_trading_service import place_paper_order
from app.services.portfolio_service import D, calculate_portfolio_value, _lock_portfolio, _paper_cash
from app.strategies import (
    AvellanedaStoikovStrategy,
    BreakoutStrategy,
    GARCHVolatilityStrategy,
    ImplementationShortfallStrategy,
    KalmanFilterStrategy,
    MACDStrategy,
    OrderBookImbalanceStrategy,
    OUProcessStrategy,
    PairsCointegrationStrategy,
    RSIStrategy,
    SARIMAXBaselineStrategy,
    SMACrossoverStrategy,
    SectorRotationStrategy,
    SequentialDeepLearningProxyStrategy,
    TWAPStrategy,
    TreeEnsembleProxyStrategy,
    VWAPStrategy,
)
from app.strategies.risk_management import calculate_position_size
from app.services.signal_outcome_service import create_signal_outcome_stub
from app.utils.observability import timed


STRATEGY_CLASSES = {
    "rsi": RSIStrategy,
    "sma_crossover": SMACrossoverStrategy,
    "macd": MACDStrategy,
    "breakout": BreakoutStrategy,
    "sector_rotation": SectorRotationStrategy,
    "vwap": VWAPStrategy,
    "twap": TWAPStrategy,
    "implementation_shortfall": ImplementationShortfallStrategy,
    "pairs_cointegration": PairsCointegrationStrategy,
    "ou_process": OUProcessStrategy,
    "kalman_filter": KalmanFilterStrategy,
    "sarimax": SARIMAXBaselineStrategy,
    "garch": GARCHVolatilityStrategy,
    "avellaneda_stoikov": AvellanedaStoikovStrategy,
    "order_book_imbalance": OrderBookImbalanceStrategy,
    "tree_ensemble": TreeEnsembleProxyStrategy,
    "sequential_deep_learning": SequentialDeepLearningProxyStrategy,
}

MAX_SIGNAL_PRICE_ROWS = 400


def get_strategy_instance(strategy_type: str):
    strategy_cls = STRATEGY_CLASSES.get(strategy_type)
    if strategy_cls is None:
        raise HTTPException(status_code=400, detail=f"Unsupported strategy type: {strategy_type}")
    return strategy_cls()


def _strategy_price_lookback(parameters: dict, strategy_type: str) -> int:
    if strategy_type == "rsi":
        return int(parameters.get("rsi_period", 14)) + 5
    if strategy_type == "sma_crossover":
        return int(parameters.get("long_window", 50)) + 5
    if strategy_type == "macd":
        return int(parameters.get("min_bars", 60))
    if strategy_type == "sector_rotation":
        return 30
    if strategy_type == "breakout":
        return int(parameters.get("lookback_period", 20)) + 5
    if strategy_type == "vwap":
        return int(parameters.get("vwap_window", 20)) + 5
    if strategy_type == "twap":
        return int(parameters.get("twap_window", 20)) + 5
    if strategy_type == "implementation_shortfall":
        return max(
            int(parameters.get("arrival_window", 5)) + 1,
            int(parameters.get("trend_window", 20)),
        ) + 5
    if strategy_type == "pairs_cointegration":
        return int(parameters.get("lookback_window", 120)) + 5
    if strategy_type == "ou_process":
        return int(parameters.get("mean_window", 60)) + 5
    if strategy_type == "kalman_filter":
        return int(parameters.get("lookback_window", 120)) + 5
    if strategy_type == "sarimax":
        return max(
            int(parameters.get("short_return_window", 20)),
            int(parameters.get("long_return_window", 60)),
        ) + 10
    if strategy_type == "garch":
        return max(
            int(parameters.get("short_vol_window", 20)),
            int(parameters.get("long_vol_window", 60)),
            int(parameters.get("momentum_window", 20)),
        ) + 10
    if strategy_type == "tree_ensemble":
        return max(
            int(parameters.get("momentum_short_window", 20)),
            int(parameters.get("momentum_long_window", 60)),
            int(parameters.get("trend_window", 50)),
            60,
        ) + 10
    if strategy_type == "sequential_deep_learning":
        return max(
            int(parameters.get("sequence_window", 20)),
            int(parameters.get("ema_slow_span", 26)),
        ) + 10
    return 120


def _run_strategy_signal(
    db: Session,
    strategy,
    strategy_type: str,
    dataframe,
    parameters: dict,
    stock_id: int | None = None,
):
    if strategy_type == "sector_rotation":
        return strategy.generate_signal(dataframe, parameters, db=db, stock_id=stock_id)
    return strategy.generate_signal(dataframe, parameters)


@timed("strategy.create_user_strategy")
def create_user_strategy(db: Session, user: User, payload: UserStrategyCreate) -> UserStrategy:
    template = db.get(StrategyTemplate, payload.strategy_template_id)
    if template is None or not template.is_active:
        raise HTTPException(status_code=404, detail="Strategy template not found")
    portfolio = db.scalar(
        select(Portfolio).where(Portfolio.id == payload.portfolio_id, Portfolio.user_id == user.id)
    )
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    parameters = dict(template.default_parameters or {})
    parameters.update(payload.parameters or {})
    strategy = UserStrategy(
        user_id=user.id,
        portfolio_id=payload.portfolio_id,
        strategy_template_id=template.id,
        strategy_name=payload.strategy_name or template.strategy_name,
        parameters=parameters,
        risk_settings=payload.risk_settings or {"risk_per_trade_pct": 1},
        is_enabled=payload.is_enabled,
    )
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    return strategy


def _load_price_dataframe(db: Session, stock_id: int, row_limit: int):
    prices = list(
        db.scalars(
            select(StockPrice)
            .where(StockPrice.stock_id == stock_id, StockPrice.timeframe == DAILY_TIMEFRAME)
            .order_by(desc(StockPrice.price_datetime))
            .limit(row_limit)
        )
    )
    prices.reverse()
    if not prices:
        sync_stock_prices(db, stock_id, commit=False)
        prices = list(
            db.scalars(
                select(StockPrice)
                .where(StockPrice.stock_id == stock_id, StockPrice.timeframe == DAILY_TIMEFRAME)
                .order_by(desc(StockPrice.price_datetime))
                .limit(row_limit)
            )
        )
        prices.reverse()
    return prices_to_dataframe(prices)


def _load_index_price_dataframe(db: Session, index_fund_id: int, row_limit: int):
    prices = list(
        db.scalars(
            select(IndexFundPrice)
            .where(IndexFundPrice.index_fund_id == index_fund_id, IndexFundPrice.timeframe == DAILY_TIMEFRAME)
            .order_by(desc(IndexFundPrice.price_datetime))
            .limit(row_limit)
        )
    )
    prices.reverse()
    if not prices:
        sync_index_fund_prices(db, index_fund_id, commit=False)
        prices = list(
            db.scalars(
                select(IndexFundPrice)
                .where(IndexFundPrice.index_fund_id == index_fund_id, IndexFundPrice.timeframe == DAILY_TIMEFRAME)
                .order_by(desc(IndexFundPrice.price_datetime))
                .limit(row_limit)
            )
        )
        prices.reverse()
    return index_prices_to_dataframe(prices)


def _load_preview_dataframe(db: Session, payload: StrategyPreviewRequest, row_limit: int):
    if payload.instrument_type == "index_fund":
        if payload.index_fund_id is None:
            raise HTTPException(status_code=400, detail="index_fund_id is required")
        return _load_index_price_dataframe(db, payload.index_fund_id, row_limit)
    if payload.stock_id is None:
        raise HTTPException(status_code=400, detail="stock_id is required")
    return _load_price_dataframe(db, payload.stock_id, row_limit)


@timed("strategy.generate_signal")
def generate_signal(db: Session, user: User, payload: GenerateSignalRequest) -> StrategySignal:
    user_strategy = db.scalar(
        select(UserStrategy).where(
            UserStrategy.id == payload.user_strategy_id,
            UserStrategy.user_id == user.id,
            UserStrategy.is_enabled.is_(True),
        )
    )
    if user_strategy is None:
        raise HTTPException(status_code=404, detail="User strategy not found")
    template = db.get(StrategyTemplate, user_strategy.strategy_template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Strategy template not found")

    parameters = dict(user_strategy.parameters or {})
    lookback = min(MAX_SIGNAL_PRICE_ROWS, _strategy_price_lookback(parameters, template.strategy_type))
    dataframe = _load_price_dataframe(db, payload.stock_id, lookback)
    strategy = get_strategy_instance(template.strategy_type)
    result = _run_strategy_signal(
        db, strategy, template.strategy_type, dataframe, parameters, stock_id=payload.stock_id
    )
    latest_price = get_latest_price(db, payload.stock_id)

    portfolio = _lock_portfolio(db, user.id, user_strategy.portfolio_id)
    portfolio_values = calculate_portfolio_value(db, user_strategy.portfolio_id)
    risk_settings = user_strategy.risk_settings or {}
    stop_loss_pct = user_strategy.parameters.get("stop_loss_pct", 5)
    max_position_size_pct = user_strategy.parameters.get("max_position_size_pct", 10)
    atr_stop = None
    if result.indicators.get("stop_pct") is not None:
        atr_stop = {
            "stop_pct": result.indicators["stop_pct"],
            "stop_price": result.indicators.get("stop_price"),
        }
    suggested_quantity = 0
    if result.signal_type == "BUY" and latest_price:
        suggested_quantity = calculate_position_size(
            portfolio_values["total_value"],
            _paper_cash(portfolio),
            latest_price,
            risk_settings.get("risk_per_trade_pct", 1),
            stop_loss_pct,
            max_position_size_pct,
            atr_stop=atr_stop,
        )

    signal = StrategySignal(
        user_strategy_id=user_strategy.id,
        stock_id=payload.stock_id,
        signal_date=datetime.now(UTC),
        signal_type=result.signal_type,
        confidence_score=Decimal(str(round(result.confidence_score, 2))),
        suggested_quantity=Decimal(str(suggested_quantity)),
        suggested_price=latest_price,
        reason=result.reason,
        indicators=result.indicators,
        executed_as_order=False,
    )
    db.add(signal)
    db.flush()
    if result.signal_type in {"BUY", "SELL"}:
        create_signal_outcome_stub(
            db,
            signal.id,
            payload.stock_id,
            result.signal_type,
            signal.signal_date,
            latest_price,
        )
    db.commit()
    db.refresh(signal)
    return signal


@timed("strategy.preview_signal")
def preview_signal(db: Session, payload: StrategyPreviewRequest) -> dict:
    template = db.get(StrategyTemplate, payload.strategy_template_id)
    if template is None or not template.is_active:
        raise HTTPException(status_code=404, detail="Strategy template not found")

    parameters = dict(template.default_parameters or {})
    parameters.update(payload.parameters or {})
    lookback = min(MAX_SIGNAL_PRICE_ROWS, _strategy_price_lookback(parameters, template.strategy_type))
    dataframe = _load_preview_dataframe(db, payload, lookback)
    strategy = get_strategy_instance(template.strategy_type)
    try:
        result = _run_strategy_signal(
            db,
            strategy,
            template.strategy_type,
            dataframe,
            parameters,
            stock_id=payload.stock_id,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid strategy parameters: {exc}") from exc

    return {
        "stock_id": payload.stock_id if payload.instrument_type == "stock" else None,
        "index_fund_id": payload.index_fund_id if payload.instrument_type == "index_fund" else None,
        "instrument_type": payload.instrument_type,
        "strategy_template_id": template.id,
        "strategy_name": template.strategy_name,
        "strategy_type": template.strategy_type,
        "signal_type": result.signal_type,
        "confidence_score": round(float(result.confidence_score), 2),
        "suggested_price": (
            get_latest_index_price(db, payload.index_fund_id)
            if payload.instrument_type == "index_fund" and payload.index_fund_id is not None
            else get_latest_price(db, payload.stock_id)
            if payload.stock_id is not None
            else None
        ),
        "reason": result.reason,
        "indicators": result.indicators,
        "parameters": parameters,
    }


@timed("strategy.execute_signal_as_paper_order")
def execute_signal_as_paper_order(db: Session, user: User, signal_id: int):
    signal = db.scalar(
        select(StrategySignal)
        .options(joinedload(StrategySignal.user_strategy))
        .join(UserStrategy)
        .where(StrategySignal.id == signal_id, UserStrategy.user_id == user.id)
    )
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    if signal.signal_type not in {"BUY", "SELL"}:
        raise HTTPException(status_code=400, detail="Only BUY or SELL signals can be executed")
    if D(signal.suggested_quantity) <= 0:
        raise HTTPException(status_code=400, detail="Signal suggested quantity is zero")
    user_strategy = signal.user_strategy
    order = place_paper_order(
        db,
        user,
        PaperOrderCreate(
            portfolio_id=user_strategy.portfolio_id,
            stock_id=signal.stock_id,
            order_type="MARKET",
            side=signal.signal_type,
            quantity=D(signal.suggested_quantity),
        ),
    )
    signal.executed_as_order = order.status == "EXECUTED"
    db.commit()
    db.refresh(signal)
    return order
