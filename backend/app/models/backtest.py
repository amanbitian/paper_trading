from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    user_strategy_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_strategies.id", ondelete="SET NULL")
    )
    stock_id: Mapped[int | None] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"))
    index_fund_id: Mapped[int | None] = mapped_column(
        ForeignKey("index_funds.id", ondelete="CASCADE"), index=True
    )
    start_date = mapped_column(Date, nullable=False)
    end_date = mapped_column(Date, nullable=False)
    initial_capital: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    final_value: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    total_return_pct: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, default=0)
    max_drawdown_pct: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, default=0)
    sharpe_ratio: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, default=0)
    win_rate: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, default=0)
    total_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    walk_forward_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_sharpe_ratio: Mapped[float | None] = mapped_column(Numeric(10, 4))
    oos_sharpe_ratio: Mapped[float | None] = mapped_column(Numeric(10, 4))
    oos_total_return_pct: Mapped[float | None] = mapped_column(Numeric(10, 4))
    oos_max_drawdown_pct: Mapped[float | None] = mapped_column(Numeric(10, 4))
    overfitting_score: Mapped[float | None] = mapped_column(Numeric(10, 4))
    execution_mode: Mapped[str] = mapped_column(
        String(50), nullable=False, default="signal_on_close_execute_next_open"
    )
    intrabar_assumption: Mapped[str] = mapped_column(
        String(40), nullable=False, default="conservative"
    )
    cost_model: Mapped[str] = mapped_column(
        String(40), nullable=False, default="zerodha_equity_delivery"
    )
    gross_pnl: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    total_charges: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    slippage_cost: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    net_pnl: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    gross_return_pct: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, default=0)
    net_return_pct: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, default=0)
    benchmark_code: Mapped[str | None] = mapped_column(String(40))
    benchmark_symbol: Mapped[str | None] = mapped_column(String(80))
    benchmark_name: Mapped[str | None] = mapped_column(String(120))
    benchmark_return: Mapped[float | None] = mapped_column(Numeric(10, 4))
    excess_return: Mapped[float | None] = mapped_column(Numeric(10, 4))
    alpha: Mapped[float | None] = mapped_column(Numeric(10, 4))
    beta: Mapped[float | None] = mapped_column(Numeric(10, 4))
    tracking_error: Mapped[float | None] = mapped_column(Numeric(10, 4))
    information_ratio: Mapped[float | None] = mapped_column(Numeric(10, 4))
    upside_capture: Mapped[float | None] = mapped_column(Numeric(10, 4))
    downside_capture: Mapped[float | None] = mapped_column(Numeric(10, 4))
    benchmark_warnings: Mapped[list | None] = mapped_column(JSONB)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User")
    user_strategy = relationship("UserStrategy")
    stock = relationship("Stock")
    index_fund = relationship("IndexFund")
    trades = relationship("BacktestTrade", back_populates="backtest", cascade="all, delete-orphan")


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    backtest_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False
    )
    stock_id: Mapped[int | None] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"))
    index_fund_id: Mapped[int | None] = mapped_column(
        ForeignKey("index_funds.id", ondelete="CASCADE"), index=True
    )
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    trade_date = mapped_column(Date, nullable=False)
    pnl: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    signal_date = mapped_column(Date)
    quoted_price: Mapped[float | None] = mapped_column(Numeric(18, 4))
    gross_pnl: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    charges: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    slippage_cost: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    net_pnl: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    charges_breakdown: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)

    backtest = relationship("BacktestRun", back_populates="trades")
    stock = relationship("Stock")
    index_fund = relationship("IndexFund")
