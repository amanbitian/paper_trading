from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StockBase(BaseModel):
    symbol: str
    yahoo_symbol: str
    exchange: str
    company_name: str | None = None
    sector: str | None = None
    industry: str | None = None
    currency: str = "INR"
    is_active: bool = True
    is_delisted: bool = False
    is_nifty50: bool = False
    is_nifty100: bool = False
    is_nifty200: bool = False
    is_nifty500: bool = False
    is_banknifty: bool = False
    is_finnifty: bool = False
    is_midcpnifty: bool = False
    is_sensex: bool = False
    delisted_reason: str | None = None
    delisted_detected_at: datetime | None = None


class StockRead(StockBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class StockPriceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stock_id: int
    price_datetime: datetime
    timeframe: str
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    adjusted_close: Decimal | None = None
    volume: int | None = None
    source: str
    created_at: datetime


class PriceSyncResponse(BaseModel):
    stock_id: int
    rows_saved: int
    timeframe: str
    outcome: str | None = None


class StockPerformanceRead(BaseModel):
    id: int
    symbol: str
    yahoo_symbol: str
    exchange: str
    company_name: str | None = None
    sector: str | None = None
    industry: str | None = None
    is_nifty50: bool = False
    is_nifty100: bool = False
    is_nifty200: bool = False
    is_nifty500: bool = False
    is_banknifty: bool = False
    is_finnifty: bool = False
    is_midcpnifty: bool = False
    is_sensex: bool = False
    latest_price_datetime: datetime | None = None
    latest_price: float | None = None
    latest_volume: int | None = None
    price_1m_date: datetime | None = None
    price_1m: float | None = None
    price_3m_date: datetime | None = None
    price_3m: float | None = None
    price_6m_date: datetime | None = None
    price_6m: float | None = None
    price_1y_date: datetime | None = None
    price_1y: float | None = None
    change_1m_pct: float | None = None
    change_3m_pct: float | None = None
    change_6m_pct: float | None = None
    change_1y_pct: float | None = None


class StockFundamentalsLatestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stock_id: int
    symbol: str
    exchange: str
    yahoo_ticker: str
    market_cap: Decimal | None = None
    trailing_pe: Decimal | None = None
    roe: Decimal | None = None
    debt_to_equity: Decimal | None = None
    sales_growth: Decimal | None = None
    earnings_growth: Decimal | None = None
    promoter_holding: Decimal | None = None
    dividend_yield: Decimal | None = None
    price_to_book: Decimal | None = None
    average_volume: Decimal | None = None
    currency: str | None = None
    source: str
    status: str
    error_message: str | None = None
    raw_json: dict[str, Any] | None = None
    fetched_at: datetime
    created_at: datetime
    updated_at: datetime


class FundamentalsSyncResult(BaseModel):
    status: str
    table_name: str
    columns_ingested: int
    metrics: list[str]
    selected_stocks: int
    succeeded: int
    failed: int
    rows_inserted: int
    rows_updated: int
    rows_upserted: int
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    source: str
    failed_symbols: list[dict[str, Any]] = Field(default_factory=list)
    sample_success_symbols: list[str] = Field(default_factory=list)
