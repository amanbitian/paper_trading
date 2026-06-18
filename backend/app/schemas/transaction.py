from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ManualBuyRequest(BaseModel):
    portfolio_id: int
    stock_id: int
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    transaction_date: datetime
    charges: Decimal = Field(default=Decimal("0"), ge=0)
    notes: str | None = None


class ManualSellRequest(BaseModel):
    portfolio_id: int
    stock_id: int
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    transaction_date: datetime
    charges: Decimal = Field(default=Decimal("0"), ge=0)
    notes: str | None = None


class TransactionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    portfolio_id: int
    stock_id: int | None
    transaction_type: str
    quantity: Decimal
    price: Decimal
    gross_amount: Decimal
    charges: Decimal
    net_amount: Decimal
    transaction_date: datetime
    source: str
    notes: str | None = None
    created_at: datetime

