"""Shared building blocks for trading strategies.

Every concrete strategy (RSI, SMA crossover, breakout, MACD, advanced
proxies, ...) subclasses `BaseStrategy` and implements `generate_signal`.
The backtest engine and live signal generator both call strategies through
this same interface, so the contract here is what keeps them interchangeable.
"""

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class SignalResult:
    """The output of a single `generate_signal` call.

    Attributes:
        signal_type: One of "BUY", "SELL", or "HOLD".
        confidence_score: A 0-100 score the caller can use to rank/filter
            signals. Strategies derive this heuristically (e.g. how far RSI
            is past its threshold); it is not a probability.
        reason: Short, human-readable explanation shown in the UI and logs.
        indicators: Raw indicator values (e.g. {"rsi": 28.4}) plus any
            risk-management hints such as "stop_price"/"take_profit_price"
            that `enrich_signal_with_atr` may attach. Consumers (backtest
            engine, live execution) read these to size positions and place
            protective stops.
    """

    signal_type: str
    confidence_score: float
    reason: str
    indicators: dict = field(default_factory=dict)


class BaseStrategy:
    """Common interface and parameter handling for all strategies.

    Subclasses set `name`, `strategy_type`, and `default_parameters`, and
    implement `generate_signal`. `generate_signal` receives a price
    DataFrame with at least `open`/`high`/`low`/`close`/`volume` columns
    indexed by date, ordered oldest-to-newest, where the *last row* is the
    most recent bar the strategy is allowed to see (the backtest engine
    enforces this to prevent lookahead bias).
    """

    name = "Base Strategy"
    strategy_type = "base"
    default_parameters: dict = {}

    def generate_signal(self, prices: pd.DataFrame, parameters: dict | None = None) -> SignalResult:
        """Inspect `prices` and return a BUY/SELL/HOLD recommendation.

        Implementations should be pure functions of `prices` and
        `parameters` — no I/O, no mutation of `prices` — so the same
        strategy instance can be reused safely across backtests and live runs.
        """
        raise NotImplementedError

    def merged_parameters(self, parameters: dict | None = None) -> dict:
        """Overlay caller-supplied `parameters` on top of the strategy defaults.

        Returns a new dict; `default_parameters` is never mutated, which
        matters because it is a class-level attribute shared by every
        instance of the strategy.
        """
        merged = dict(self.default_parameters)
        if parameters:
            merged.update(parameters)
        return merged
