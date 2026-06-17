from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class IndexFund(Base):
    __tablename__ = "index_funds"
    __table_args__ = (
        UniqueConstraint("symbol", name="uq_index_funds_symbol"),
        UniqueConstraint("yahoo_symbol", name="uq_index_funds_yahoo_symbol"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    symbol: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    yahoo_symbol: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    base_currency: Mapped[str] = mapped_column(String(10), nullable=False, default="INR")
    latest_price: Mapped[float | None] = mapped_column(Numeric(18, 4))
    value_in_inr: Mapped[float | None] = mapped_column(Numeric(18, 4))
    category: Mapped[str] = mapped_column(String(40), nullable=False, default="index", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    prices = relationship("IndexFundPrice", back_populates="index_fund", cascade="all, delete-orphan")


class IndexFundPrice(Base):
    __tablename__ = "index_fund_prices"
    __table_args__ = (
        UniqueConstraint(
            "index_fund_id",
            "price_datetime",
            "timeframe",
            name="uq_index_fund_prices_fund_dt_tf",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    index_fund_id: Mapped[int] = mapped_column(
        ForeignKey("index_funds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    price_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(20), nullable=False, default="1d")
    open: Mapped[float | None] = mapped_column(Numeric(18, 4))
    high: Mapped[float | None] = mapped_column(Numeric(18, 4))
    low: Mapped[float | None] = mapped_column(Numeric(18, 4))
    close: Mapped[float | None] = mapped_column(Numeric(18, 4))
    adjusted_close: Mapped[float | None] = mapped_column(Numeric(18, 4))
    volume: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="yfinance")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    index_fund = relationship("IndexFund", back_populates="prices")
