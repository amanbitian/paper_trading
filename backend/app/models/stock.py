from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
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


class Stock(Base):
    __tablename__ = "stocks"
    __table_args__ = (
        UniqueConstraint("symbol", "exchange", name="uq_stocks_symbol_exchange"),
        UniqueConstraint("yahoo_symbol", name="uq_stocks_yahoo_symbol"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    yahoo_symbol: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    company_name: Mapped[str | None] = mapped_column(String(255))
    sector: Mapped[str | None] = mapped_column(String(120))
    industry: Mapped[str | None] = mapped_column(String(120))
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="INR")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_delisted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_nifty50: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_nifty100: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_nifty200: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_nifty500: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_banknifty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_finnifty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_midcpnifty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_sensex: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_bhav_index: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    delisted_reason: Mapped[str | None] = mapped_column(Text)
    delisted_detected_at = mapped_column(DateTime(timezone=True))
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    prices = relationship("StockPrice", back_populates="stock", cascade="all, delete-orphan")


class StockPrice(Base):
    __tablename__ = "stock_prices"
    __table_args__ = (
        UniqueConstraint(
            "stock_id", "price_datetime", "timeframe", name="uq_stock_prices_stock_dt_tf"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    stock_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    price_datetime = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(20), nullable=False, default="1d")
    open: Mapped[float | None] = mapped_column(Numeric(18, 4))
    high: Mapped[float | None] = mapped_column(Numeric(18, 4))
    low: Mapped[float | None] = mapped_column(Numeric(18, 4))
    close: Mapped[float | None] = mapped_column(Numeric(18, 4))
    adjusted_close: Mapped[float | None] = mapped_column(Numeric(18, 4))
    volume: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="yfinance")
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    stock = relationship("Stock", back_populates="prices")


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="yfinance")
    exchange: Mapped[str | None] = mapped_column(String(20), index=True)
    timeframe: Mapped[str] = mapped_column(String(20), nullable=False, default="1d", index=True)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="RUNNING", index=True)
    ingestion_mode: Mapped[str] = mapped_column(String(30), nullable=False, default="FULL", index=True)
    total_symbols: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    batch_offset: Mapped[int | None] = mapped_column(Integer)
    batch_limit: Mapped[int | None] = mapped_column(Integer)
    chunk_days: Mapped[int | None] = mapped_column(Integer)
    sleep_seconds: Mapped[float | None] = mapped_column(Numeric(8, 2))
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_saved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    finished_at = mapped_column(DateTime(timezone=True))


class StockPerformanceSnapshot(Base):
    __tablename__ = "stock_performance_snapshots"

    stock_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), primary_key=True
    )
    symbol: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    yahoo_symbol: Mapped[str] = mapped_column(String(80), nullable=False)
    exchange: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    company_name: Mapped[str | None] = mapped_column(String(255))
    sector: Mapped[str | None] = mapped_column(String(120))
    is_nifty50: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_nifty100: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_nifty200: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_nifty500: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_banknifty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_finnifty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_midcpnifty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_sensex: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_bhav_index: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    latest_price_datetime = mapped_column(DateTime(timezone=True))
    latest_price: Mapped[float | None] = mapped_column(Numeric(18, 4))
    latest_volume: Mapped[int | None] = mapped_column(BigInteger)
    price_1m: Mapped[float | None] = mapped_column(Numeric(18, 4))
    price_3m: Mapped[float | None] = mapped_column(Numeric(18, 4))
    price_6m: Mapped[float | None] = mapped_column(Numeric(18, 4))
    price_1y: Mapped[float | None] = mapped_column(Numeric(18, 4))
    change_1m_pct: Mapped[float | None] = mapped_column(Numeric(10, 4))
    change_3m_pct: Mapped[float | None] = mapped_column(Numeric(10, 4))
    change_6m_pct: Mapped[float | None] = mapped_column(Numeric(10, 4))
    change_1y_pct: Mapped[float | None] = mapped_column(Numeric(10, 4))
    refreshed_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MarketAnalyticsCache(Base):
    __tablename__ = "market_analytics_cache"

    cache_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    payload = mapped_column(JSONB, nullable=False, default=dict)
    refreshed_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
