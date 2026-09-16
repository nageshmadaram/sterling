"""Tick-driven stop observation for open Snapback positions.

A 30-second REST poll cannot see a breach that opens and closes between two polls:
bid 70 at 10:00:00, 64 at 10:00:05, 73 at 10:00:12 — the poll sees 70 then 73 and the
stop never happened as far as the evidence is concerned. That is an irreversible
economic error, so the stop is evaluated on accepted tick events.

Every observation is recorded as quote evidence before it decides anything, and a
position that stops being observed is marked compromised rather than assumed safe: a
later REST snapshot proves what the price is now, never that no breach occurred while
nobody was looking.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))

# An open position must be observed at least this often while the market is open.
STOP_OBSERVATION_TOLERANCE_S = 60

PREMIUM_STOP_RATIO = 0.65      # 35% premium stop
RUNNER_TRAIL_RATIO = 0.75      # 25% give-back from the stored end-of-day peak


@dataclass
class PositionMonitorState:
    opportunity_id: str
    contract: str = ""
    subscription_active: bool = False
    last_tick_at: Optional[datetime] = None
    last_valid_stop_quote_at: Optional[datetime] = None
    data_gap_started_at: Optional[datetime] = None
    compromised: bool = False


def _dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=_IST)
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_IST)


def _f(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


class SnapbackStopMonitor:
    """Evaluates the frozen stop rules against accepted tick observations."""

    def __init__(self, *, warehouse=None) -> None:
        if warehouse is None:
            from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

            warehouse = SnapbackObservationWarehouse()
        self.warehouse = warehouse
        self._states: Dict[str, PositionMonitorState] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ subscriptions

    def mark_subscribed(self, opportunity_id: str, contract: str) -> None:
        with self._lock:
            state = self._states.setdefault(
                opportunity_id, PositionMonitorState(opportunity_id=opportunity_id)
            )
            state.contract = contract
            state.subscription_active = True

    def mark_unsubscribed(self, opportunity_id: str) -> None:
        with self._lock:
            state = self._states.setdefault(
                opportunity_id, PositionMonitorState(opportunity_id=opportunity_id)
            )
            state.subscription_active = False

    def state(self, opportunity_id: str) -> Dict[str, Any]:
        with self._lock:
            state = self._states.get(opportunity_id)
            if state is None:
                return {"subscription_active": False, "last_tick_at": None}
            return {
                "opportunity_id": state.opportunity_id,
                "contract": state.contract,
                "subscription_active": state.subscription_active,
                "last_tick_at": state.last_tick_at,
                "last_valid_stop_quote_at": state.last_valid_stop_quote_at,
                "data_gap_started_at": state.data_gap_started_at,
                "compromised": state.compromised,
            }

    # ------------------------------------------------------------------- ticks

    def _open_positions(self) -> List[Dict[str, Any]]:
        try:
            return [dict(p) for p in (self.warehouse.get_active_paper_positions() or [])]
        except Exception as exc:
            log.warning("Stop monitor: could not read positions: %s", exc)
            return []

    def on_tick(self, tick: Dict[str, Any], *, source: str = "WEBSOCKET") -> None:
        """Observe one tick for a subscribed contract and apply the frozen stop rules."""
        contract = str(tick.get("tradingsymbol") or tick.get("contract") or "")
        observed_at = _dt(tick.get("exchange_timestamp")) or datetime.now(_IST)
        bid = _f(tick.get("bid"))
        ask = _f(tick.get("ask"))

        for pos in self._open_positions():
            if str(pos.get("option_symbol") or "") != contract:
                continue

            opp = str(pos["opportunity_id"])
            accepted, reasons = self._acceptable(bid, ask, tick)

            self._record_observation(
                opp, pos, bid, ask, observed_at, accepted, reasons, source,
            )

            with self._lock:
                state = self._states.setdefault(
                    opp, PositionMonitorState(opportunity_id=opp, contract=contract)
                )
                state.last_tick_at = observed_at
                if accepted:
                    previous = state.last_valid_stop_quote_at
                    # A gap that already opened is not closed by a later quote: the
                    # unobserved interval stays unobserved.
                    if previous is not None and (
                        observed_at - previous
                    ).total_seconds() > STOP_OBSERVATION_TOLERANCE_S:
                        state.compromised = True
                        state.data_gap_started_at = previous
                        log.warning(
                            "Stop monitor: %s went unobserved for %.0fs; evidence compromised",
                            opp, (observed_at - previous).total_seconds(),
                        )
                    state.last_valid_stop_quote_at = observed_at

            if not accepted:
                continue

            if str(pos.get("status") or "") == "EXIT_PENDING" or pos.get("pending_exit_reason"):
                # Already latched: a decision made is not revisited.
                continue

            self._evaluate_stop(pos, bid, observed_at)

    def _acceptable(self, bid, ask, tick) -> tuple:
        reasons: List[str] = []
        if bid is None or bid <= 0:
            reasons.append("missing_bid")
        if ask is None or ask <= 0:
            reasons.append("missing_ask")
        if bid is not None and ask is not None and ask < bid:
            reasons.append("crossed_book")
        if int(tick.get("bid_quantity") or 0) <= 0:
            reasons.append("no_bid_depth")
        return (not reasons), reasons

    def _record_observation(self, opp, pos, bid, ask, observed_at, accepted, reasons, source) -> None:
        from app.services.snapback_quote_evidence import record_quote_attempt

        record_quote_attempt(
            self.warehouse,
            opportunity_id=opp,
            phase="INTRADAY_STOP",
            leg="OPTION",
            contract_id=str(pos.get("option_symbol") or ""),
            required_for_economics=True,
            quote_present=bid is not None,
            bid=bid,
            ask=ask,
            provider_timestamp=observed_at.isoformat(),
            age_ms=None,
            accepted=accepted,
            reason_codes=reasons + ([source] if source != "WEBSOCKET" else []),
            symbol=str(pos.get("symbol") or ""),
        )

    def _evaluate_stop(self, pos: Dict[str, Any], bid: float, observed_at: datetime) -> None:
        opp = str(pos["opportunity_id"])
        entry = _f(pos.get("option_entry_price")) or 0.0
        is_runner = int(pos.get("is_runner") or 0) == 1

        reason = None
        if is_runner:
            # The trail is measured against the STORED end-of-day peak. A tick may
            # trigger the give-back but must never raise the peak.
            peak = _f(pos.get("peak_option_bid")) or entry
            if peak > 0 and bid <= peak * RUNNER_TRAIL_RATIO:
                reason = "RUNNER_TRAIL_STOP"
        else:
            if entry > 0 and bid <= entry * PREMIUM_STOP_RATIO:
                reason = "PREMIUM_STOP"

        if not reason:
            return

        self.warehouse.set_paper_position_pending_exit(
            opportunity_id=opp,
            pending_exit_reason=reason,
            pending_exit_option_bid=bid,
            pending_exit_ts=observed_at.astimezone(timezone.utc).isoformat(),
        )
        log.warning(
            "Stop monitor latched %s for %s at bid %.2f (%s)",
            reason, opp, bid, observed_at.isoformat(),
        )

    # -------------------------------------------------------------- data gaps

    def data_gaps(self, *, now: Optional[datetime] = None) -> List[str]:
        """Open positions with no accepted observation inside the tolerance."""
        now = now or datetime.now(_IST)
        stale: List[str] = []
        for pos in self._open_positions():
            opp = str(pos["opportunity_id"])
            with self._lock:
                state = self._states.get(opp)
            last = state.last_valid_stop_quote_at if state else None
            if last is None or (now - last).total_seconds() > STOP_OBSERVATION_TOLERANCE_S:
                stale.append(opp)
                if state is not None and state.data_gap_started_at is None:
                    state.data_gap_started_at = last or now
                    state.compromised = True
        return stale

    def monitoring_healthy(self, *, now: Optional[datetime] = None) -> bool:
        """False whenever any exposed position is unobserved: fail closed."""
        return not self.data_gaps(now=now)

    def compromised_positions(self) -> List[str]:
        with self._lock:
            return [opp for opp, state in self._states.items() if state.compromised]


_monitor: Optional[SnapbackStopMonitor] = None


def get_stop_monitor() -> SnapbackStopMonitor:
    global _monitor
    if _monitor is None:
        _monitor = SnapbackStopMonitor()
    return _monitor
