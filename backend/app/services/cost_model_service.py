from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal


CostModelName = Literal[
    "basic",
    "zerodha_equity_delivery",
    "zerodha_intraday",
    "custom",
    "zero",
]

D0 = Decimal("0")
BROKERAGE_RATE = Decimal("0.0003")
BROKERAGE_CAP = Decimal("20")
NSE_EXCHANGE_RATE = Decimal("0.0000345")
BSE_EXCHANGE_RATE = Decimal("0.0000375")
SEBI_RATE = Decimal("0.0000001")
GST_RATE = Decimal("0.18")
STAMP_DUTY_DELIVERY_BUY = Decimal("0.00015")
STAMP_DUTY_INTRADAY_BUY = Decimal("0.00003")
STT_DELIVERY = Decimal("0.001")
STT_INTRADAY_SELL = Decimal("0.00025")


def D(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def q(value: Decimal, places: str = "0.0001") -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def _rate_from_bps(custom: dict[str, Any], key: str, default: Decimal = D0) -> Decimal:
    return D(custom.get(key, default)) / Decimal("10000")


def calculate_trade_cost(
    *,
    trade_value,
    side: str,
    cost_model: CostModelName | str = "zerodha_equity_delivery",
    exchange: str = "NSE",
    slippage_cost=0,
    spread_cost=0,
    custom: dict[str, Any] | None = None,
) -> dict[str, Decimal | str]:
    value = D(trade_value)
    slip = D(slippage_cost)
    spread = D(spread_cost)
    model = (cost_model or "zerodha_equity_delivery").lower()
    side_upper = side.upper()
    exchange_rate = BSE_EXCHANGE_RATE if exchange.upper() == "BSE" else NSE_EXCHANGE_RATE

    if value <= 0 or model == "zero":
        return {
            "cost_model": model,
            "brokerage": D0,
            "stt": D0,
            "exchange_transaction_charge": D0,
            "sebi_charge": D0,
            "stamp_duty": D0,
            "gst": D0,
            "slippage_cost": q(slip),
            "spread_cost": q(spread),
            "total_charges": D0,
            "total_cost": q(slip + spread),
        }

    brokerage = D0
    stt = D0
    exchange_charge = value * exchange_rate
    sebi_charge = value * SEBI_RATE
    stamp_duty = D0

    if model == "basic":
        brokerage = min(BROKERAGE_CAP, value * BROKERAGE_RATE)
    elif model == "zerodha_intraday":
        brokerage = min(BROKERAGE_CAP, value * BROKERAGE_RATE)
        stt = value * STT_INTRADAY_SELL if side_upper == "SELL" else D0
        stamp_duty = value * STAMP_DUTY_INTRADAY_BUY if side_upper == "BUY" else D0
    elif model == "custom":
        custom = custom or {}
        brokerage = min(D(custom.get("brokerage_cap", BROKERAGE_CAP)), value * _rate_from_bps(custom, "brokerage_bps"))
        stt_rate_key = "stt_bps_sell" if side_upper == "SELL" else "stt_bps_buy"
        stt = value * _rate_from_bps(custom, stt_rate_key)
        exchange_charge = value * _rate_from_bps(custom, "exchange_bps", exchange_rate * Decimal("10000"))
        sebi_charge = value * _rate_from_bps(custom, "sebi_bps", SEBI_RATE * Decimal("10000"))
        stamp_duty = value * _rate_from_bps(custom, "stamp_bps_buy") if side_upper == "BUY" else D0
    else:
        # Zerodha equity delivery: zero brokerage, delivery STT on buy and sell.
        stt = value * STT_DELIVERY
        stamp_duty = value * STAMP_DUTY_DELIVERY_BUY if side_upper == "BUY" else D0

    gst = (brokerage + exchange_charge + sebi_charge) * GST_RATE
    total_charges = brokerage + stt + exchange_charge + sebi_charge + stamp_duty + gst
    total_cost = total_charges + slip + spread

    return {
        "cost_model": model,
        "brokerage": q(brokerage),
        "stt": q(stt),
        "exchange_transaction_charge": q(exchange_charge),
        "sebi_charge": q(sebi_charge),
        "stamp_duty": q(stamp_duty),
        "gst": q(gst),
        "slippage_cost": q(slip),
        "spread_cost": q(spread),
        "total_charges": q(total_charges),
        "total_cost": q(total_cost),
    }


def calculate_round_trip_pnl(
    *,
    entry_quoted_price,
    exit_quoted_price,
    entry_executed_price,
    exit_executed_price,
    quantity,
    buy_cost: dict[str, Decimal | str],
    sell_cost: dict[str, Decimal | str],
) -> dict[str, Decimal]:
    qty = D(quantity)
    gross_pnl = (D(exit_quoted_price) - D(entry_quoted_price)) * qty
    execution_pnl = (D(exit_executed_price) - D(entry_executed_price)) * qty
    total_charges = D(buy_cost["total_charges"]) + D(sell_cost["total_charges"])
    slippage_cost = D(buy_cost["slippage_cost"]) + D(sell_cost["slippage_cost"])
    net_pnl = execution_pnl - total_charges
    return {
        "gross_pnl": q(gross_pnl),
        "total_charges": q(total_charges),
        "slippage_cost": q(slippage_cost),
        "net_pnl": q(net_pnl),
    }
