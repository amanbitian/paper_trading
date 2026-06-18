from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


IntrabarAssumption = Literal[
    "conservative",
    "optimistic",
    "open_high_low_close",
    "open_low_high_close",
]


@dataclass(frozen=True)
class FillSimulationResult:
    filled: bool
    price: Decimal | None
    event: str
    reason: str


def D(value) -> Decimal:
    return Decimal(str(value))


def simulate_long_stop_target(
    *,
    open_price,
    high_price,
    low_price,
    close_price,
    stop_loss_price=None,
    target_price=None,
    assumption: IntrabarAssumption = "conservative",
) -> FillSimulationResult:
    high = D(high_price)
    low = D(low_price)
    stop = D(stop_loss_price) if stop_loss_price is not None else None
    target = D(target_price) if target_price is not None else None

    stop_hit = stop is not None and low <= stop
    target_hit = target is not None and high >= target

    if not stop_hit and not target_hit:
        return FillSimulationResult(False, None, "none", "Neither stop-loss nor target touched")
    if stop_hit and not target_hit:
        return FillSimulationResult(True, stop, "stop_loss", "Stop-loss touched inside candle")
    if target_hit and not stop_hit:
        return FillSimulationResult(True, target, "target", "Target touched inside candle")

    if assumption in {"conservative", "open_low_high_close"}:
        return FillSimulationResult(
            True,
            stop,
            "stop_loss",
            "Both stop-loss and target touched; assumption chose stop-loss first",
        )
    return FillSimulationResult(
        True,
        target,
        "target",
        "Both stop-loss and target touched; assumption chose target first",
    )


def simulate_limit_order(
    *,
    side: Literal["BUY", "SELL"],
    limit_price,
    open_price,
    high_price,
    low_price,
) -> FillSimulationResult:
    side = side.upper()  # type: ignore[assignment]
    limit = D(limit_price)
    open_value = D(open_price)
    high = D(high_price)
    low = D(low_price)

    if side == "BUY":
        if low > limit:
            return FillSimulationResult(False, None, "none", "Buy limit was below the candle range")
        fill_price = min(open_value, limit) if open_value <= limit else limit
        return FillSimulationResult(True, fill_price, "limit_buy", "Buy limit touched")

    if high < limit:
        return FillSimulationResult(False, None, "none", "Sell limit was above the candle range")
    fill_price = max(open_value, limit) if open_value >= limit else limit
    return FillSimulationResult(True, fill_price, "limit_sell", "Sell limit touched")
