from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PaperOrderCreate(BaseModel):
    portfolio_id: int
    stock_id: int
    order_type: Literal["MARKET", "LIMIT", "STOP_LOSS"] = "MARKET"
    side: Literal["BUY", "SELL"]
    quantity: Decimal = Field(gt=0)
    limit_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)
    notes: str | None = Field(default=None, max_length=1000)
    slippage_bps: int | None = Field(default=None, ge=0, le=500)


class PaperOrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    portfolio_id: int
    stock_id: int
    order_type: str
    side: str
    quantity: Decimal
    limit_price: Decimal | None
    stop_price: Decimal | None
    status: str
    placed_at: datetime
    executed_at: datetime | None
    executed_price: Decimal | None
    matched_at: datetime | None = None
    matched_price: Decimal | None = None
    expires_at: datetime | None = None
    notes: str | None = None
    reason: str | None


class PaperTradeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    user_id: int
    portfolio_id: int
    stock_id: int
    side: str
    quantity: Decimal
    executed_price: Decimal
    trade_value: Decimal
    charges: Decimal
    quoted_price: Decimal | None = None
    slippage_bps: int = 0
    slippage_cost: Decimal = Decimal("0")
    charges_breakdown: dict | None = None
    executed_at: datetime

