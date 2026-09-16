"""Exchange-aware Zerodha F&O cost schedule.

The previous schedule (`snapback_costs.py`) had two defects that both inflated
paper P&L:

  - it took no exchange, while the default universe includes SENSEX, whose
    options resolve on BFO. NSE and BSE charge different option transaction
    rates, and BSE futures carry no exchange transaction charge at all;
  - it applied `min(Rs 20, 0.03%)` to BOTH segments. Zerodha charges options a
    FLAT Rs 20 per executed order. A one-lot option order of Rs 10,000 turnover
    was charged Rs 3 instead of Rs 20 — money the strategy never had.

The old module is left exactly as it is. A corrected schedule is a new version,
never an edit to the old one: an earlier result must stay reproducible, and the
version string is part of the evidence identity.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Dict, Optional

COST_SCHEDULE_VERSION_V2 = "zerodha_fno_costs_2026_04_exchange_aware_v2"

EXCHANGE_NFO = "NFO"   # NSE derivatives
EXCHANGE_BFO = "BFO"   # BSE derivatives (SENSEX, BANKEX)

SUPPORTED_EXCHANGES = frozenset({EXCHANGE_NFO, EXCHANGE_BFO})

# Options are a flat fee per executed order, whatever the size.
BROKERAGE_OPTIONS_FLAT_INR = 20.0
# Futures are the lower of Rs 20 or 0.03% of turnover.
BROKERAGE_FUTURES_FLAT_INR = 20.0
BROKERAGE_FUTURES_PCT = 0.0003

# Securities transaction tax, sell side only.
STT_SELL_FUTURES = 0.0005      # 0.05%
STT_SELL_OPTIONS = 0.0015      # 0.15% of premium

# Exchange transaction charges differ by venue.
EXCHANGE_TXN = {
    (EXCHANGE_NFO, "OPTIONS"): 0.0003553,   # 0.03553%
    (EXCHANGE_NFO, "FUTURES"): 0.0000183,   # 0.00183%
    (EXCHANGE_BFO, "OPTIONS"): 0.000325,    # 0.0325%
    # BSE futures currently carry no exchange transaction charge. Zero here is a
    # measured rate, not a missing one.
    (EXCHANGE_BFO, "FUTURES"): 0.0,
}

SEBI_CHARGES = 0.000001        # 0.0001%
GST_RATE = 0.18

STAMP_DUTY_BUY_FUTURES = 0.00002   # 0.002%
STAMP_DUTY_BUY_OPTIONS = 0.00003   # 0.003%

_SEGMENTS = frozenset({"OPTIONS", "FUTURES"})


def statutory_charges_v2(
    *,
    exchange: str,
    segment: str,
    side: str,
    price: float,
    quantity: int,
) -> Dict[str, float]:
    """Charges for one executed order on one exchange.

    An unknown exchange raises. Falling back to an NSE rate would silently
    misprice an entire venue, and the error would look like strategy
    performance.
    """
    exchange = str(exchange).upper()
    segment = str(segment).upper()
    side = str(side).upper()

    if exchange not in SUPPORTED_EXCHANGES:
        raise ValueError(
            f"unsupported derivatives exchange {exchange!r}; "
            f"expected one of {sorted(SUPPORTED_EXCHANGES)}"
        )
    if segment not in _SEGMENTS:
        raise ValueError(f"unsupported segment {segment!r}")

    turnover = float(price) * int(quantity)

    if turnover <= 0:
        return {
            "version": COST_SCHEDULE_VERSION_V2,
            "exchange": exchange,
            "segment": segment,
            "turnover": 0.0,
            "brokerage": 0.0,
            "stt": 0.0,
            "exchange_txn": 0.0,
            "sebi": 0.0,
            "gst": 0.0,
            "stamp_duty": 0.0,
            "total": 0.0,
        }

    if segment == "OPTIONS":
        brokerage = BROKERAGE_OPTIONS_FLAT_INR
    else:
        brokerage = min(BROKERAGE_FUTURES_FLAT_INR, turnover * BROKERAGE_FUTURES_PCT)

    # STT is sell side only. Charging it on a buy overstates entry cost and hides
    # exit cost, which is exactly backwards for a long-premium strategy.
    if side == "SELL":
        stt = turnover * (STT_SELL_OPTIONS if segment == "OPTIONS" else STT_SELL_FUTURES)
    else:
        stt = 0.0

    exchange_txn = turnover * EXCHANGE_TXN[(exchange, segment)]
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
        "version": COST_SCHEDULE_VERSION_V2,
        "exchange": exchange,
        "segment": segment,
        "turnover": turnover,
        "brokerage": brokerage,
        "stt": stt,
        "exchange_txn": exchange_txn,
        "sebi": sebi,
        "gst": gst,
        "stamp_duty": stamp_duty,
        "total": total,
    }


@dataclass
class ExecutionCostEventV2:
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
    schedule_version: str
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
            "quantity": self.quantity,
            "price": self.price,
            "turnover": self.turnover,
            "brokerage": self.brokerage,
            "stt": self.stt,
            "exchange_txn_fee": self.exchange_txn_fee,
            "sebi_fee": self.sebi_fee,
            "gst": self.gst,
            "stamp_duty": self.stamp_duty,
            "total_cost": self.total_cost,
            "cost_schedule_version": self.schedule_version,
            "payload_hash": self.payload_hash,
        }


def cost_event_for_execution_v2(
    *,
    execution_event_id: str,
    opportunity_id: str,
    phase: str,
    exchange: str,
    segment: str,
    side: str,
    price: float,
    quantity: int,
    instrument: str = "",
) -> ExecutionCostEventV2:
    """One immutable cost leg, priced for the exchange it actually executed on.

    The charges are computed here rather than accepted from a caller: a cost
    passed in can be wrong in a way nothing downstream can detect.
    """
    charges = statutory_charges_v2(
        exchange=exchange, segment=segment, side=side, price=price, quantity=quantity,
    )

    event = ExecutionCostEventV2(
        cost_id=f"COST:{execution_event_id}",
        execution_event_id=execution_event_id,
        opportunity_id=opportunity_id,
        phase=str(phase).upper(),
        exchange=charges["exchange"],
        segment=charges["segment"],
        instrument=instrument,
        side=str(side).upper(),
        quantity=int(quantity),
        price=float(price),
        turnover=charges["turnover"],
        brokerage=charges["brokerage"],
        stt=charges["stt"],
        exchange_txn_fee=charges["exchange_txn"],
        sebi_fee=charges["sebi"],
        gst=charges["gst"],
        stamp_duty=charges["stamp_duty"],
        total_cost=charges["total"],
        schedule_version=COST_SCHEDULE_VERSION_V2,
    )
    event.payload_hash = _payload_hash(event)
    return event


def _payload_hash(event: ExecutionCostEventV2) -> str:
    payload = {k: v for k, v in event.as_row().items() if k != "payload_hash"}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


def exchange_for_instrument(
    *, option_exchange: str = "", underlying: str = "",
) -> Optional[str]:
    """Which derivatives venue an instrument trades on.

    Returns None rather than guessing: the caller must fail closed instead of
    pricing a BSE contract at NSE rates.
    """
    declared = str(option_exchange or "").upper()
    if declared in SUPPORTED_EXCHANGES:
        return declared
    return None
