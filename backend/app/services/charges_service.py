"""Indian equity delivery/intraday charge model (pure arithmetic, no DB)."""

from __future__ import annotations

BROKERAGE_RATE = 0.0003
BROKERAGE_CAP = 20.0
NSE_EXCHANGE_RATE = 0.0000345
BSE_EXCHANGE_RATE = 0.0000375
SEBI_RATE = 0.0000001
GST_RATE = 0.18
STAMP_DUTY_DELIVERY_BUY = 0.00015
STAMP_DUTY_INTRADAY_BUY = 0.00003
STT_DELIVERY = 0.001
STT_INTRADAY_SELL = 0.00025


def compute_charges(
    trade_value: float,
    side: str,
    trade_type: str = "delivery",
    exchange: str = "NSE",
) -> dict:
    """
    Itemised Indian regulatory charges (Maharashtra stamp duty default).

    STT applies to trade value, not profit.
    """
    if trade_value <= 0:
        return {
            "stt": 0.0,
            "brokerage": 0.0,
            "exchange_fee": 0.0,
            "sebi_charges": 0.0,
            "gst": 0.0,
            "stamp_duty": 0.0,
            "total_charges": 0.0,
            "effective_bps": 0.0,
        }

    side_upper = side.upper()
    trade_type_lower = trade_type.lower()
    exchange_upper = exchange.upper()

    brokerage = min(BROKERAGE_CAP, trade_value * BROKERAGE_RATE)
    exchange_rate = BSE_EXCHANGE_RATE if exchange_upper == "BSE" else NSE_EXCHANGE_RATE
    exchange_fee = trade_value * exchange_rate
    sebi_charges = trade_value * SEBI_RATE

    if trade_type_lower == "intraday":
        stt = trade_value * STT_INTRADAY_SELL if side_upper == "SELL" else 0.0
        stamp_duty = trade_value * STAMP_DUTY_INTRADAY_BUY if side_upper == "BUY" else 0.0
    else:
        stt = trade_value * STT_DELIVERY
        stamp_duty = trade_value * STAMP_DUTY_DELIVERY_BUY if side_upper == "BUY" else 0.0

    gst_base = brokerage + exchange_fee + sebi_charges
    gst = gst_base * GST_RATE
    total = stt + brokerage + exchange_fee + sebi_charges + gst + stamp_duty

    return {
        "stt": round(stt, 4),
        "brokerage": round(brokerage, 4),
        "exchange_fee": round(exchange_fee, 4),
        "sebi_charges": round(sebi_charges, 4),
        "gst": round(gst, 4),
        "stamp_duty": round(stamp_duty, 4),
        "total_charges": round(total, 4),
        "effective_bps": round((total / trade_value) * 10_000, 4),
    }
