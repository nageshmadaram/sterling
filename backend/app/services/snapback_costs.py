"""Versioned statutory charge schedule for Indian F&O.

One calculator, used by entry, rebalance and exit, so a cost can never be computed
two different ways in two places. Rates are declared, dated and versioned: when they
change, a NEW version is added rather than these edited, because evidence collected
under one schedule must stay reproducible.

Schedule `zerodha_fno_costs_2026_04`, per the broker's published charges:

    STT              futures 0.05% sell side, options 0.15% sell side on premium
    exchange txn     futures 0.00183%, options 0.03553% on turnover
    GST              18% of (brokerage + SEBI + exchange transaction)
    SEBI             0.0001% of turnover
    stamp duty       buy side only: futures 0.002%, options 0.003%
    brokerage        Rs 20 per executed order, or 0.03%, whichever is lower

Slippage is NOT here. Execution quality is an observation about the market; statutory
charges are a schedule. Mixing them makes both unauditable.
"""

from __future__ import annotations

from typing import Dict

COST_SCHEDULE_VERSION = "zerodha_fno_costs_2026_04"

BROKERAGE_FLAT_INR = 20.0
BROKERAGE_PCT = 0.0003  # 0.03%

STT_SELL_FUTURES = 0.0005      # 0.05%
STT_SELL_OPTIONS = 0.0015      # 0.15% of premium

EXCHANGE_TXN_FUTURES = 0.0000183   # 0.00183%
EXCHANGE_TXN_OPTIONS = 0.0003553   # 0.03553%

SEBI_CHARGES = 0.000001        # 0.0001%
GST_RATE = 0.18

STAMP_DUTY_BUY_FUTURES = 0.00002   # 0.002%
STAMP_DUTY_BUY_OPTIONS = 0.00003   # 0.003%


def statutory_charges(
    *,
    side: str,
    segment: str,
    price: float,
    quantity: int,
) -> Dict[str, float]:
    """Charges for one executed order. `side` BUY/SELL, `segment` OPTIONS/FUTURES."""
    side = str(side).upper()
    segment = str(segment).upper()
    turnover = float(price) * int(quantity)

    if turnover <= 0:
        return {
            "version": COST_SCHEDULE_VERSION,
            "turnover": 0.0,
            "brokerage": 0.0,
            "stt": 0.0,
            "exchange_txn": 0.0,
            "sebi": 0.0,
            "gst": 0.0,
            "stamp_duty": 0.0,
            "total": 0.0,
        }

    brokerage = min(BROKERAGE_FLAT_INR, turnover * BROKERAGE_PCT)

    # STT is sell side only. Charging it on a buy overstates entry cost and hides exit
    # cost, which is exactly backwards for a long-premium strategy.
    if side == "SELL":
        stt = turnover * (STT_SELL_OPTIONS if segment == "OPTIONS" else STT_SELL_FUTURES)
    else:
        stt = 0.0

    exchange_txn = turnover * (
        EXCHANGE_TXN_OPTIONS if segment == "OPTIONS" else EXCHANGE_TXN_FUTURES
    )
    sebi = turnover * SEBI_CHARGES
    gst = (brokerage + sebi + exchange_txn) * GST_RATE

    if side == "BUY":
        stamp_duty = turnover * (
            STAMP_DUTY_BUY_OPTIONS if segment == "OPTIONS" else STAMP_DUTY_BUY_FUTURES
        )
    else:
        stamp_duty = 0.0

    total = brokerage + stt + exchange_txn + sebi + gst + stamp_duty

    return {
        "version": COST_SCHEDULE_VERSION,
        "turnover": turnover,
        "brokerage": brokerage,
        "stt": stt,
        "exchange_txn": exchange_txn,
        "sebi": sebi,
        "gst": gst,
        "stamp_duty": stamp_duty,
        "total": total,
    }


def round_trip_charges(
    *,
    segment: str,
    entry_price: float,
    exit_price: float,
    quantity: int,
    entry_side: str = "BUY",
) -> Dict[str, float]:
    """Charges for a complete round trip, entry and exit."""
    exit_side = "SELL" if str(entry_side).upper() == "BUY" else "BUY"
    entry = statutory_charges(side=entry_side, segment=segment, price=entry_price, quantity=quantity)
    exit_ = statutory_charges(side=exit_side, segment=segment, price=exit_price, quantity=quantity)
    return {
        "version": COST_SCHEDULE_VERSION,
        "entry": entry,
        "exit": exit_,
        "total": entry["total"] + exit_["total"],
    }
