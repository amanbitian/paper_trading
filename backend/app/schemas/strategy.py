from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrategyTemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    strategy_name: str
    strategy_type: str
    description: str | None = None
    default_parameters: dict
    is_active: bool
    created_at: datetime


class UserStrategyCreate(BaseModel):
    portfolio_id: int
    strategy_template_id: int
    strategy_name: str | None = None
    parameters: dict = Field(default_factory=dict)
    risk_settings: dict = Field(default_factory=dict)
    is_enabled: bool = True


class UserStrategyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    portfolio_id: int
    strategy_template_id: int
    strategy_name: str
    parameters: dict
    risk_settings: dict
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class GenerateSignalRequest(BaseModel):
    user_strategy_id: int
    stock_id: int


class StrategyPreviewRequest(BaseModel):
    stock_id: int | None = None
    index_fund_id: int | None = None
    instrument_type: Literal["stock", "index_fund"] = "stock"
    strategy_template_id: int
    parameters: dict = Field(default_factory=dict)


class StrategyPreviewRead(BaseModel):
    stock_id: int | None = None
    index_fund_id: int | None = None
    instrument_type: str = "stock"
    strategy_template_id: int
    strategy_name: str
    strategy_type: str
    signal_type: str
    confidence_score: float
    suggested_price: Decimal | None = None
    reason: str | None = None
    indicators: dict
    parameters: dict


class StrategySignalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_strategy_id: int
    stock_id: int
    signal_date: datetime
    signal_type: str
    confidence_score: Decimal
    suggested_quantity: Decimal
    suggested_price: Decimal | None
    reason: str | None
    indicators: dict
    executed_as_order: bool
    created_at: datetime
