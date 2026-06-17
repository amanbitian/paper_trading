from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class MarketIndex(Base):
    __tablename__ = "market_indices"
    __table_args__ = (UniqueConstraint("index_code", name="uq_market_indices_index_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    index_code: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    index_name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="NSE")
    exchange: Mapped[str] = mapped_column(String(20), nullable=False, default="NSE")
    yahoo_symbol: Mapped[str | None] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    memberships = relationship(
        "StockIndexMembership",
        back_populates="market_index",
        cascade="all, delete-orphan",
    )


class StockIndexMembership(Base):
    __tablename__ = "stock_index_memberships"
    __table_args__ = (
        UniqueConstraint("index_id", "stock_id", name="uq_stock_index_memberships_index_stock"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    index_id: Mapped[int] = mapped_column(
        ForeignKey("market_indices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stock_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    symbol: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(20), nullable=False, default="NSE", index=True)
    company_name: Mapped[str | None] = mapped_column(String(255))
    industry: Mapped[str | None] = mapped_column(String(120))
    series: Mapped[str | None] = mapped_column(String(20))
    isin: Mapped[str | None] = mapped_column(String(20))
    weight: Mapped[float | None] = mapped_column(Numeric(10, 4))
    effective_date: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(80), nullable=False, default="manual")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    market_index = relationship("MarketIndex", back_populates="memberships")
    stock = relationship("Stock")
