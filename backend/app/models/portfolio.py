from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Portfolio(Base):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    portfolio_name: Mapped[str] = mapped_column(String(120), nullable=False)
    portfolio_type: Mapped[str] = mapped_column(String(40), nullable=False, default="manual")
    base_currency: Mapped[str] = mapped_column(String(10), nullable=False, default="INR")
    starting_value: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    cash_balance: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user = relationship("User", back_populates="portfolios")
    holdings = relationship("PortfolioHolding", back_populates="portfolio", cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="portfolio")
    paper_orders = relationship("PaperOrder", back_populates="portfolio")
    paper_trades = relationship("PaperTrade", back_populates="portfolio")


class PortfolioHolding(Base):
    __tablename__ = "portfolio_holdings"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "stock_id", name="uq_holdings_portfolio_stock"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    average_buy_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    total_invested: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    realized_pnl: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    last_updated_at = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    portfolio = relationship("Portfolio", back_populates="holdings")
    stock = relationship("Stock")


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    stock_id: Mapped[int | None] = mapped_column(ForeignKey("stocks.id", ondelete="SET NULL"))
    transaction_type: Mapped[str] = mapped_column(String(30), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    gross_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    charges: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    net_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    transaction_date = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="manual")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="transactions")
    portfolio = relationship("Portfolio", back_populates="transactions")
    stock = relationship("Stock")


class PaperOrder(Base):
    __tablename__ = "paper_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False)
    order_type: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    limit_price: Mapped[float | None] = mapped_column(Numeric(18, 4))
    stop_price: Mapped[float | None] = mapped_column(Numeric(18, 4))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    placed_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = mapped_column(DateTime(timezone=True))
    matched_at = mapped_column(DateTime(timezone=True))
    matched_price: Mapped[float | None] = mapped_column(Numeric(18, 4))
    executed_at = mapped_column(DateTime(timezone=True))
    executed_price: Mapped[float | None] = mapped_column(Numeric(18, 4))
    reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    user = relationship("User", back_populates="paper_orders")
    portfolio = relationship("Portfolio", back_populates="paper_orders")
    stock = relationship("Stock")
    trade = relationship("PaperTrade", back_populates="order", uselist=False)


class PaperTrade(Base):
    __tablename__ = "paper_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("paper_orders.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    executed_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    trade_value: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    charges: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    quoted_price: Mapped[float | None] = mapped_column(Numeric(18, 4))
    slippage_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    slippage_cost: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    charges_breakdown = mapped_column(JSONB)
    executed_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    order = relationship("PaperOrder", back_populates="trade")
    user = relationship("User", back_populates="paper_trades")
    portfolio = relationship("Portfolio", back_populates="paper_trades")
    stock = relationship("Stock")


class PortfolioDailySnapshot(Base):
    __tablename__ = "portfolio_daily_snapshot"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "snapshot_date", name="uq_snapshot_portfolio_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_date = mapped_column(Date, nullable=False, index=True)
    invested_value: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    market_value: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    cash_balance: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    total_value: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    realized_pnl: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    unrealized_pnl: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    day_pnl: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    total_return_pct: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, default=0)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    portfolio = relationship("Portfolio")

