from __future__ import annotations

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class StockFundamentalsLatest(Base):
    __tablename__ = "stock_fundamentals_latest"
    __table_args__ = (
        UniqueConstraint("stock_id", name="uq_stock_fundamentals_latest_stock_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    stock_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    symbol: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    yahoo_ticker: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    market_cap = mapped_column(Numeric(24, 4))
    trailing_pe = mapped_column(Numeric(18, 6))
    roe = mapped_column(Numeric(18, 8))
    debt_to_equity = mapped_column(Numeric(18, 6))
    sales_growth = mapped_column(Numeric(18, 8))
    earnings_growth = mapped_column(Numeric(18, 8))
    promoter_holding = mapped_column(Numeric(18, 8))
    dividend_yield = mapped_column(Numeric(18, 8))
    price_to_book = mapped_column(Numeric(18, 6))
    average_volume = mapped_column(Numeric(24, 4))
    currency: Mapped[str | None] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="yfinance")
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    raw_json = mapped_column(JSONB)
    fetched_at = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    stock = relationship("Stock")
