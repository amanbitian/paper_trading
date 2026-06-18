from typing import Any

from pydantic import BaseModel, Field


class AISynthesizeSignalsRequest(BaseModel):
    symbol: str
    findings: list[dict[str, Any]]
    model: str | None = None


class AIInterpretBacktestRequest(BaseModel):
    backtest_id: int
    model: str | None = None


class AIEvaluateTradeRequest(BaseModel):
    symbol: str
    action: str
    quantity: int = Field(gt=0)
    price: float = Field(gt=0)
    notes: str = ""
    portfolio_id: int
    stock_id: int | None = None
    model: str | None = None


class AINLScreenerRequest(BaseModel):
    query: str = Field(min_length=3)
    model: str | None = None


class AIExplainRiskRequest(BaseModel):
    label: str = "Portfolio"
    beta: float | None = None
    var_1d_inr: float | None = None
    hhi: float | None = None
    max_drawdown_pct: float | None = None
    portfolio_value: float = 0
    model: str | None = None
