from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.stock import StockRead


class PortfolioCreate(BaseModel):
    portfolio_name: str = Field(min_length=2, max_length=120)
    portfolio_type: str = Field(default="manual", max_length=40)
    base_currency: str = Field(default="INR", max_length=10)
    starting_value: Decimal = Field(default=Decimal("0"), ge=0)
    cash_balance: Decimal | None = Field(default=None, ge=0)


class PortfolioRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    portfolio_name: str
    portfolio_type: str
    base_currency: str
    starting_value: Decimal
    cash_balance: Decimal
    created_at: datetime
    updated_at: datetime


class HoldingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    portfolio_id: int
    stock_id: int
    quantity: Decimal
    average_buy_price: Decimal
    total_invested: Decimal
    realized_pnl: Decimal
    last_updated_at: datetime | None = None
    stock: StockRead | None = None


class HoldingValueRead(BaseModel):
    stock_id: int
    symbol: str
    yahoo_symbol: str
    quantity: Decimal
    average_buy_price: Decimal
    total_invested: Decimal
    current_price: Decimal
    market_value: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    return_pct: Decimal


class PortfolioPerformanceRead(BaseModel):
    portfolio_id: int
    invested_value: Decimal
    market_value: Decimal
    cash_balance: Decimal
    total_value: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_return_pct: Decimal
    holdings: list[HoldingValueRead]
    snapshots: list[dict] = []


class PortfolioSnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    portfolio_id: int
    snapshot_date: date
    invested_value: Decimal
    market_value: Decimal
    cash_balance: Decimal
    total_value: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    day_pnl: Decimal
    total_return_pct: Decimal
    created_at: datetime

