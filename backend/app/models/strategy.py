from datetime import date

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class StrategyTemplate(Base):
    __tablename__ = "strategy_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    strategy_name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    default_parameters = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user_strategies = relationship("UserStrategy", back_populates="strategy_template")


class UserStrategy(Base):
    __tablename__ = "user_strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    strategy_template_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_templates.id", ondelete="RESTRICT"), nullable=False
    )
    strategy_name: Mapped[str] = mapped_column(String(120), nullable=False)
    parameters = mapped_column(JSONB, nullable=False, default=dict)
    risk_settings = mapped_column(JSONB, nullable=False, default=dict)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user = relationship("User", back_populates="strategies")
    portfolio = relationship("Portfolio")
    strategy_template = relationship("StrategyTemplate", back_populates="user_strategies")
    signals = relationship("StrategySignal", back_populates="user_strategy")


class StrategySignal(Base):
    __tablename__ = "strategy_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_strategy_id: Mapped[int] = mapped_column(
        ForeignKey("user_strategies.id", ondelete="CASCADE"), nullable=False
    )
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False)
    signal_date = mapped_column(DateTime(timezone=True), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(10), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    suggested_quantity: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    suggested_price: Mapped[float | None] = mapped_column(Numeric(18, 4))
    reason: Mapped[str | None] = mapped_column(Text)
    indicators = mapped_column(JSONB, nullable=False, default=dict)
    executed_as_order: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user_strategy = relationship("UserStrategy", back_populates="signals")
    stock = relationship("Stock")
    outcome = relationship("StrategySignalOutcome", back_populates="signal", uselist=False)


class StrategySignalOutcome(Base):
    __tablename__ = "strategy_signal_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    signal_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_signals.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    stock_id: Mapped[int | None] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"))
    index_fund_id: Mapped[int | None] = mapped_column(ForeignKey("index_funds.id", ondelete="CASCADE"))
    signal_type: Mapped[str | None] = mapped_column(String(10))
    signal_date: Mapped[date] = mapped_column(Date, nullable=False)
    signal_price: Mapped[float | None] = mapped_column(Numeric(18, 4))
    price_5d: Mapped[float | None] = mapped_column(Numeric(18, 4))
    price_10d: Mapped[float | None] = mapped_column(Numeric(18, 4))
    price_20d: Mapped[float | None] = mapped_column(Numeric(18, 4))
    return_5d_pct: Mapped[float | None] = mapped_column(Numeric(10, 4))
    return_10d_pct: Mapped[float | None] = mapped_column(Numeric(10, 4))
    return_20d_pct: Mapped[float | None] = mapped_column(Numeric(10, 4))
    profitable_5d: Mapped[bool | None] = mapped_column(Boolean)
    profitable_10d: Mapped[bool | None] = mapped_column(Boolean)
    profitable_20d: Mapped[bool | None] = mapped_column(Boolean)
    stop_hit: Mapped[bool | None] = mapped_column(Boolean)
    stop_hit_date: Mapped[date | None] = mapped_column(Date)
    outcome_evaluated_at = mapped_column(DateTime(timezone=True))
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    signal = relationship("StrategySignal", back_populates="outcome")


class StockStrategyExplanation(Base):
    __tablename__ = "stock_strategy_explanations"
    __table_args__ = (
        UniqueConstraint("stock_id", "strategy_type", name="uq_stock_strategy_explanations_stock_strategy"),
        Index("ix_stock_strategy_explanations_stock_calculated", "stock_id", "calculated_at"),
        Index("ix_stock_strategy_explanations_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    strategy_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    strategy_name: Mapped[str] = mapped_column(String(120), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    confidence_score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    headline: Mapped[str | None] = mapped_column(Text)
    explanation_summary: Mapped[str | None] = mapped_column(Text)
    reasons_json = mapped_column(JSONB, nullable=False, default=list)
    indicators_json = mapped_column(JSONB, nullable=False, default=dict)
    data_quality_json = mapped_column(JSONB, nullable=False, default=dict)
    price_as_of = mapped_column(DateTime(timezone=True))
    fundamentals_as_of = mapped_column(Date)
    calculated_at = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    expires_at = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    source_version: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    stock = relationship("Stock")
