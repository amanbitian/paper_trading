from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class IndexFundRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    yahoo_symbol: str
    base_currency: str
    latest_price: Decimal | None = None
    value_in_inr: Decimal | None = None
    category: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class IndexFundPriceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    index_fund_id: int
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


class IndexFundSyncResponse(BaseModel):
    index_fund_id: int
    rows_saved: int
    timeframe: str


class IndexFundPerformanceRead(BaseModel):
    id: int
    symbol: str
    yahoo_symbol: str
    base_currency: str
    category: str
    latest_price_datetime: datetime | None = None
    latest_price: float | None = None
    latest_volume: int | None = None
    price_1m: float | None = None
    price_3m: float | None = None
    price_6m: float | None = None
    price_1y: float | None = None
    change_1m_pct: float | None = None
    change_3m_pct: float | None = None
    change_6m_pct: float | None = None
    change_1y_pct: float | None = None


class IndexFundReturnSeries(BaseModel):
    id: int
    symbol: str
    yahoo_symbol: str
    base_currency: str
    points: list[dict]
