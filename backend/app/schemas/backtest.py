from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional


ExecutionMode = Literal[
    "signal_on_close_execute_next_open",
    "signal_on_close_execute_next_close",
    "signal_on_open_execute_same_open",
]
IntrabarAssumption = Literal[
    "conservative",
    "optimistic",
    "open_high_low_close",
    "open_low_high_close",
]
CostModel = Literal[
    "basic",
    "zerodha_equity_delivery",
    "zerodha_intraday",
    "custom",
    "zero",
]
BenchmarkCode = Literal[
    "buy_and_hold",
    "cash",
    "nifty50",
    "nifty500",
    "sector",
]

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BacktestRunRequest(BaseModel):
    stock_id: int | None = None
    index_fund_id: int | None = None
    instrument_type: Literal["stock", "index_fund"] = "stock"
    strategy_id: int
    start_date: date
    end_date: date
    initial_capital: Decimal = Field(default=Decimal("100000"), gt=0)
    parameters: dict = Field(default_factory=dict)
    walk_forward: bool = False
    execution_mode: ExecutionMode = "signal_on_close_execute_next_open"
    intrabar_assumption: IntrabarAssumption = "conservative"
    cost_model: CostModel = "zerodha_equity_delivery"
    benchmark_code: BenchmarkCode = "buy_and_hold"

    @model_validator(mode="after")
    def validate_date_range(self) -> "BacktestRunRequest":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if self.instrument_type == "stock" and self.stock_id is None:
            raise ValueError("stock_id is required for stock backtests")
        if self.instrument_type == "index_fund" and self.index_fund_id is None:
            raise ValueError("index_fund_id is required for index fund backtests")
        return self


class BacktestRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    user_strategy_id: int | None
    stock_id: int | None = None
    index_fund_id: int | None = None
    start_date: date
    end_date: date
    initial_capital: Decimal
    final_value: Decimal
    total_return_pct: Decimal
    max_drawdown_pct: Decimal
    sharpe_ratio: Decimal
    win_rate: Decimal
    total_trades: int
    execution_mode: str = "signal_on_close_execute_next_open"
    intrabar_assumption: str = "conservative"
    cost_model: str = "zerodha_equity_delivery"
    gross_pnl: Decimal = Decimal("0")
    total_charges: Decimal = Decimal("0")
    slippage_cost: Decimal = Decimal("0")
    net_pnl: Decimal = Decimal("0")
    gross_return_pct: Decimal = Decimal("0")
    net_return_pct: Decimal = Decimal("0")
    benchmark_code: str | None = None
    benchmark_symbol: str | None = None
    benchmark_name: str | None = None
    benchmark_return: Decimal | None = None
    excess_return: Decimal | None = None
    alpha: Decimal | None = None
    beta: Decimal | None = None
    tracking_error: Decimal | None = None
    information_ratio: Decimal | None = None
    upside_capture: Decimal | None = None
    downside_capture: Decimal | None = None
    benchmark_warnings: list[str] | None = None
    created_at: datetime


class BacktestTradeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    backtest_id: int
    stock_id: int | None = None
    index_fund_id: int | None = None
    side: str
    quantity: Decimal
    price: Decimal
    trade_date: date
    pnl: Decimal
    signal_date: date | None = None
    quoted_price: Decimal | None = None
    gross_pnl: Decimal = Decimal("0")
    charges: Decimal = Decimal("0")
    slippage_cost: Decimal = Decimal("0")
    net_pnl: Decimal = Decimal("0")
    charges_breakdown: dict | None = None
    reason: str | None = None


class BacktestRunResponse(BacktestRunRead):
    trades: list[BacktestTradeRead] = Field(default_factory=list)
    equity_curve: list[dict] = Field(default_factory=list)
    benchmark_curve: list[dict] = Field(default_factory=list)
    walk_forward_enabled: bool = False
    is_start_date: Optional[date] = None
    is_end_date: Optional[date] = None
    is_total_return_pct: Optional[Decimal] = None
    is_sharpe_ratio: Optional[Decimal] = None
    is_max_drawdown_pct: Optional[Decimal] = None
    is_win_rate: Optional[Decimal] = None
    is_num_trades: Optional[int] = None
    oos_start_date: Optional[date] = None
    oos_end_date: Optional[date] = None
    oos_total_return_pct: Optional[Decimal] = None
    oos_sharpe_ratio: Optional[Decimal] = None
    oos_max_drawdown_pct: Optional[Decimal] = None
    oos_win_rate: Optional[Decimal] = None
    oos_num_trades: Optional[int] = None
    overfitting_score: Optional[Decimal] = None
