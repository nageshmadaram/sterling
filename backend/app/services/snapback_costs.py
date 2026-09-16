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

import hashlib
import json
from dataclasses import dataclass
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


# --------------------------------------------------------------------------- #
# Execution-leg cost events. One executed leg, one immutable event.
# --------------------------------------------------------------------------- #

# Deterministic phases. A cost belongs to a leg, never to "the entry" in general.
PHASE_OPTION_ENTRY = "OPTION_ENTRY"
PHASE_HEDGE_ENTRY = "HEDGE_ENTRY"
PHASE_HEDGE_REBALANCE = "HEDGE_REBALANCE"
PHASE_OPTION_EXIT = "OPTION_EXIT"
PHASE_HEDGE_EXIT = "HEDGE_EXIT"
PHASE_FUTURES_ROLL = "FUTURES_ROLL"

COST_PHASES = frozenset({
    PHASE_OPTION_ENTRY, PHASE_HEDGE_ENTRY, PHASE_HEDGE_REBALANCE,
    PHASE_OPTION_EXIT, PHASE_HEDGE_EXIT, PHASE_FUTURES_ROLL,
})


@dataclass(frozen=True)
class ExecutionCostEvent:
    cost_id: str
    execution_event_id: str
    opportunity_id: str
    phase: str
    exchange: str
    segment: str
    instrument: str
    side: str
    quantity: int
    price: float
    turnover: float
    brokerage: float
    stt: float
    exchange_txn_fee: float
    sebi_fee: float
    gst: float
    stamp_duty: float
    total_cost: float
    cost_schedule_version: str
    payload_hash: str = ""

    def as_row(self) -> Dict[str, object]:
        return {
            "cost_id": self.cost_id,
            "execution_event_id": self.execution_event_id,
            "opportunity_id": self.opportunity_id,
            "phase": self.phase,
            "exchange": self.exchange,
            "segment": self.segment,
            "instrument": self.instrument,
            "side": self.side,
            "quantity": int(self.quantity),
            "price": float(self.price),
            "turnover": float(self.turnover),
            "brokerage": float(self.brokerage),
            "stt": float(self.stt),
            "exchange_txn_fee": float(self.exchange_txn_fee),
            "sebi_fee": float(self.sebi_fee),
            "gst": float(self.gst),
            "stamp_duty": float(self.stamp_duty),
            "total_cost": float(self.total_cost),
            "cost_schedule_version": self.cost_schedule_version,
        }


def _payload_hash(payload: Dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def cost_event_for_execution(
    *,
    execution_event_id: str,
    opportunity_id: str,
    phase: str,
    exchange: str,
    segment: str,
    instrument: str,
    side: str,
    quantity: int,
    price: float,
) -> ExecutionCostEvent:
    """Build the cost event for one executed leg.

    The charges are computed HERE from the single versioned schedule; callers cannot
    pass precomputed taxes, so a persisted fee can always be recomputed from the leg.
    """
    if phase not in COST_PHASES:
        raise ValueError(f"unknown cost phase {phase!r}")

    charges = statutory_charges(
        side=side, segment=segment, price=price, quantity=quantity,
    )

    event = ExecutionCostEvent(
        cost_id=f"COST:{execution_event_id}",
        execution_event_id=execution_event_id,
        opportunity_id=opportunity_id,
        phase=phase,
        exchange=exchange,
        segment=segment,
        instrument=instrument,
        side=str(side).upper(),
        quantity=int(quantity),
        price=float(price),
        turnover=float(charges["turnover"]),
        brokerage=float(charges["brokerage"]),
        stt=float(charges["stt"]),
        exchange_txn_fee=float(charges["exchange_txn"]),
        sebi_fee=float(charges["sebi"]),
        gst=float(charges["gst"]),
        stamp_duty=float(charges["stamp_duty"]),
        total_cost=float(charges["total"]),
        cost_schedule_version=COST_SCHEDULE_VERSION,
    )
    return ExecutionCostEvent(**{**event.__dict__, "payload_hash": _payload_hash(event.as_row())})
