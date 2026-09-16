"""The lifecycle journal: every opportunity leaves a trace, including the dead ones.

An opportunity that never reaches the broker is the one most likely to vanish,
because nothing downstream ever handles it. That loses the denominator. Without
it Sterling can count 86 completed trades but cannot say whether 100 signals
produced 86 trades or 400 did, and "the strategy fired and reality refused" is
indistinguishable from "the strategy never fired".

State is derived, never assigned. The recorder appends transitions and folds them
to get the current state, so a later write cannot erase what happened — which is
what a mutable ``row.state = "OPEN"`` does the moment two code paths disagree.
Restart recovery, duplicate detection and causal ordering all fall out of the
same log.

The recorder observes. Trading decisions stay in the services that own them; this
module contains no strategy logic and no execution authority, and its semantic
methods exist so that illegal transitions are refused in one place rather than
trusted to every caller.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Any, Optional

__all__ = [
    "LIFECYCLE_SCHEMA_VERSION",
    "State",
    "Outcome",
    "LEGAL_TRANSITIONS",
    "TERMINAL_STATES",
    "BLOCKED_STATES",
    "LifecycleError",
    "EvidenceLifecycleEvent",
    "SnapbackEvidenceRecorder",
    "derive_state",
    "outcome_for_state",
]

LIFECYCLE_SCHEMA_VERSION = "1"


class State:
    OPPORTUNITY_CREATED = "OPPORTUNITY_CREATED"
    CANDIDATE_UNIVERSE_CAPTURED = "CANDIDATE_UNIVERSE_CAPTURED"
    SELECTION_RECORDED = "SELECTION_RECORDED"
    LISTED_CONFIRMED = "LISTED_CONFIRMED"
    HEDGE_SELECTION_RECORDED = "HEDGE_SELECTION_RECORDED"
    MARKET_SUBSCRIBED = "MARKET_SUBSCRIBED"
    MARKET_EVIDENCE_READY = "MARKET_EVIDENCE_READY"
    ENTRY_INTENT_RECORDED = "ENTRY_INTENT_RECORDED"
    BROKER_SUBMITTED = "BROKER_SUBMITTED"
    BROKER_ACKNOWLEDGED = "BROKER_ACKNOWLEDGED"
    OPTION_FILL_PENDING = "OPTION_FILL_PENDING"
    OPTION_FILLED = "OPTION_FILLED"
    HEDGE_FILL_PENDING = "HEDGE_FILL_PENDING"
    HEDGE_FILLED = "HEDGE_FILLED"
    HEDGE_WAIVED = "HEDGE_WAIVED"
    PROTECTION_PENDING = "PROTECTION_PENDING"
    PROTECTION_ACTIVE = "PROTECTION_ACTIVE"
    OPEN = "OPEN"
    EXITING = "EXITING"
    EXIT_FILLED = "EXIT_FILLED"
    RECONCILED = "RECONCILED"

    # ─── terminal, blocked before any exposure exists ────────────────────────
    TERMINAL_NOT_LISTED = "TERMINAL_NOT_LISTED"
    TERMINAL_SELECTION_UNKNOWN = "TERMINAL_SELECTION_UNKNOWN"
    TERMINAL_HEDGE_UNKNOWN = "TERMINAL_HEDGE_UNKNOWN"
    TERMINAL_NO_MARKET_EVIDENCE = "TERMINAL_NO_MARKET_EVIDENCE"
    TERMINAL_RISK_REJECTED = "TERMINAL_RISK_REJECTED"
    TERMINAL_BROKER_REJECTED = "TERMINAL_BROKER_REJECTED"
    TERMINAL_ABORTED_SYSTEM = "TERMINAL_ABORTED_SYSTEM"

    # ─── problem states that still carry exposure ────────────────────────────
    PROTECTION_FAILED = "PROTECTION_FAILED"
    EXIT_UNRESOLVED = "EXIT_UNRESOLVED"
    RECONCILIATION_FAILED = "RECONCILIATION_FAILED"


#: Reached one of these and the opportunity is over. Nothing leads out.
TERMINAL_STATES = frozenset({
    State.TERMINAL_NOT_LISTED,
    State.TERMINAL_SELECTION_UNKNOWN,
    State.TERMINAL_HEDGE_UNKNOWN,
    State.TERMINAL_NO_MARKET_EVIDENCE,
    State.TERMINAL_RISK_REJECTED,
    State.TERMINAL_BROKER_REJECTED,
    State.TERMINAL_ABORTED_SYSTEM,
    State.RECONCILED,
})

#: Terminal states in which no exposure was ever taken. These are the
#: denominator: the signal fired and reality refused it.
BLOCKED_STATES = TERMINAL_STATES - {State.RECONCILED}


class Outcome:
    """Why an opportunity ended where it did."""

    EXECUTED = "EXECUTED"
    IN_FLIGHT = "IN_FLIGHT"
    BLOCKED_SELECTION = "BLOCKED_SELECTION"
    BLOCKED_HEDGE = "BLOCKED_HEDGE"
    BLOCKED_MARKET_DATA = "BLOCKED_MARKET_DATA"
    BLOCKED_RISK = "BLOCKED_RISK"
    BLOCKED_BROKER = "BLOCKED_BROKER"
    ABORTED_SYSTEM = "ABORTED_SYSTEM"
    UNRESOLVED = "UNRESOLVED"


_OUTCOME_BY_STATE = {
    State.TERMINAL_NOT_LISTED: Outcome.BLOCKED_SELECTION,
    State.TERMINAL_SELECTION_UNKNOWN: Outcome.BLOCKED_SELECTION,
    State.TERMINAL_HEDGE_UNKNOWN: Outcome.BLOCKED_HEDGE,
    State.TERMINAL_NO_MARKET_EVIDENCE: Outcome.BLOCKED_MARKET_DATA,
    State.TERMINAL_RISK_REJECTED: Outcome.BLOCKED_RISK,
    State.TERMINAL_BROKER_REJECTED: Outcome.BLOCKED_BROKER,
    State.TERMINAL_ABORTED_SYSTEM: Outcome.ABORTED_SYSTEM,
    State.RECONCILED: Outcome.EXECUTED,
    State.PROTECTION_FAILED: Outcome.UNRESOLVED,
    State.EXIT_UNRESOLVED: Outcome.UNRESOLVED,
    State.RECONCILIATION_FAILED: Outcome.UNRESOLVED,
}


def outcome_for_state(state: str) -> str:
    return _OUTCOME_BY_STATE.get(state, Outcome.IN_FLIGHT)


#: Every legal edge, enumerated. Anything absent here is refused, so a new path
#: has to be declared rather than discovered in production.
LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    State.OPPORTUNITY_CREATED: frozenset({
        State.CANDIDATE_UNIVERSE_CAPTURED,
        State.TERMINAL_SELECTION_UNKNOWN,
        State.TERMINAL_ABORTED_SYSTEM,
    }),
    State.CANDIDATE_UNIVERSE_CAPTURED: frozenset({
        State.SELECTION_RECORDED, State.TERMINAL_ABORTED_SYSTEM,
    }),
    State.SELECTION_RECORDED: frozenset({
        State.LISTED_CONFIRMED,
        State.TERMINAL_NOT_LISTED,
        State.TERMINAL_SELECTION_UNKNOWN,
        State.TERMINAL_ABORTED_SYSTEM,
    }),
    State.LISTED_CONFIRMED: frozenset({
        State.HEDGE_SELECTION_RECORDED,
        State.TERMINAL_HEDGE_UNKNOWN,
        State.TERMINAL_ABORTED_SYSTEM,
    }),
    State.HEDGE_SELECTION_RECORDED: frozenset({
        State.MARKET_SUBSCRIBED,
        State.TERMINAL_HEDGE_UNKNOWN,
        State.TERMINAL_ABORTED_SYSTEM,
    }),
    State.MARKET_SUBSCRIBED: frozenset({
        State.MARKET_EVIDENCE_READY,
        State.TERMINAL_NO_MARKET_EVIDENCE,
        State.TERMINAL_ABORTED_SYSTEM,
    }),
    State.MARKET_EVIDENCE_READY: frozenset({
        State.ENTRY_INTENT_RECORDED,
        State.TERMINAL_RISK_REJECTED,
        State.TERMINAL_NO_MARKET_EVIDENCE,
        State.TERMINAL_ABORTED_SYSTEM,
    }),
    State.ENTRY_INTENT_RECORDED: frozenset({
        State.BROKER_SUBMITTED, State.TERMINAL_ABORTED_SYSTEM,
    }),
    State.BROKER_SUBMITTED: frozenset({
        State.BROKER_ACKNOWLEDGED,
        State.TERMINAL_BROKER_REJECTED,
        State.OPTION_FILL_PENDING,
    }),
    State.BROKER_ACKNOWLEDGED: frozenset({
        State.OPTION_FILL_PENDING,
        State.TERMINAL_BROKER_REJECTED,
    }),
    # A partial fill leaves the state where it is; only cumulative completion moves it.
    State.OPTION_FILL_PENDING: frozenset({
        State.OPTION_FILL_PENDING,
        State.OPTION_FILLED,
        State.TERMINAL_BROKER_REJECTED,
        State.EXIT_UNRESOLVED,
    }),
    State.OPTION_FILLED: frozenset({
        State.HEDGE_FILL_PENDING, State.HEDGE_WAIVED, State.PROTECTION_FAILED,
    }),
    State.HEDGE_FILL_PENDING: frozenset({
        State.HEDGE_FILL_PENDING, State.HEDGE_FILLED, State.PROTECTION_FAILED,
    }),
    State.HEDGE_FILLED: frozenset({State.PROTECTION_PENDING, State.PROTECTION_FAILED}),
    State.HEDGE_WAIVED: frozenset({State.PROTECTION_PENDING, State.PROTECTION_FAILED}),
    State.PROTECTION_PENDING: frozenset({State.PROTECTION_ACTIVE, State.PROTECTION_FAILED}),
    State.PROTECTION_ACTIVE: frozenset({State.OPEN, State.EXITING, State.PROTECTION_FAILED}),
    State.OPEN: frozenset({State.EXITING, State.PROTECTION_FAILED}),
    State.EXITING: frozenset({State.EXITING, State.EXIT_FILLED, State.EXIT_UNRESOLVED}),
    State.EXIT_FILLED: frozenset({State.RECONCILED, State.RECONCILIATION_FAILED}),
    # Exposure exists here, so exiting and reconciling must stay reachable.
    State.PROTECTION_FAILED: frozenset({
        State.EXITING, State.PROTECTION_ACTIVE, State.EXIT_UNRESOLVED,
    }),
    State.EXIT_UNRESOLVED: frozenset({
        State.EXITING, State.EXIT_FILLED, State.RECONCILIATION_FAILED,
    }),
    State.RECONCILIATION_FAILED: frozenset({State.RECONCILED, State.EXIT_UNRESOLVED}),
}


class LifecycleError(RuntimeError):
    """An illegal transition, or a caller trying to reanimate a dead opportunity."""


def _payload_hash(payload: Optional[dict[str, Any]]) -> str:
    blob = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvidenceLifecycleEvent:
    """One transition. Append-only; the sequence orders them causally."""

    schema_version: str
    event_id: str
    opportunity_id: str
    sequence: int
    previous_state: Optional[str]
    state: str
    event_type: str
    outcome: str
    detail: Optional[str]
    occurred_at: str
    received_at: str
    payload_hash: str
    runtime_sha: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def derive_state(events: list[EvidenceLifecycleEvent]) -> Optional[str]:
    """Current state is a fold over the log, not a stored field."""
    if not events:
        return None
    return max(events, key=lambda e: e.sequence).state


def _lifecycle_event_id(opportunity_id: str, sequence: int, state: str, payload_hash: str) -> str:
    blob = f"{opportunity_id}|{sequence}|{state}|{payload_hash}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class SnapbackEvidenceRecorder:
    """Appends the lifecycle of one runtime's opportunities. Decides nothing.

    ``store`` is any object exposing the append methods of
    :class:`SnapbackEvidenceStore`; the recorder deliberately knows nothing about
    Parquet, so a failing writer is a store concern and the log stays testable.
    """

    def __init__(self, *, store: Any, runtime_sha: str, clock: Optional[Any] = None) -> None:
        if not runtime_sha:
            raise LifecycleError("runtime_sha is required; an unattributed journal proves nothing")
        self._store = store
        self._runtime_sha = runtime_sha
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._events: dict[str, list[EvidenceLifecycleEvent]] = {}
        self._broker_event_ids: set[str] = set()

    # ─── reading ─────────────────────────────────────────────────────────────

    def current_state(self, opportunity_id: str) -> Optional[str]:
        return derive_state(self._events.get(opportunity_id, []))

    def outcome(self, opportunity_id: str) -> Optional[str]:
        state = self.current_state(opportunity_id)
        return None if state is None else outcome_for_state(state)

    def events(self, opportunity_id: str) -> tuple[EvidenceLifecycleEvent, ...]:
        return tuple(sorted(self._events.get(opportunity_id, []), key=lambda e: e.sequence))

    def is_terminal(self, opportunity_id: str) -> bool:
        return self.current_state(opportunity_id) in TERMINAL_STATES

    # ─── the single transition path ──────────────────────────────────────────

    def _transition(
        self,
        opportunity_id: str,
        state: str,
        *,
        event_type: str = "STATE_TRANSITION",
        detail: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
        occurred_at: Optional[datetime] = None,
    ) -> EvidenceLifecycleEvent:
        log = self._events.setdefault(opportunity_id, [])
        previous = derive_state(log)

        if previous is None:
            if state != State.OPPORTUNITY_CREATED:
                raise LifecycleError(
                    f"{opportunity_id} has no journal; it must begin at OPPORTUNITY_CREATED, not {state}"
                )
        else:
            if previous in TERMINAL_STATES:
                # A terminal opportunity is finished. Retrying requires a new
                # opportunity_id, so that the refusal stays in the record.
                raise LifecycleError(
                    f"{opportunity_id} is terminal in {previous}; a new attempt needs a new opportunity_id"
                )
            allowed = LEGAL_TRANSITIONS.get(previous, frozenset())
            if state not in allowed:
                raise LifecycleError(f"illegal transition {previous} -> {state} for {opportunity_id}")

        now = self._clock()
        digest = _payload_hash(payload)
        sequence = len(log)
        event = EvidenceLifecycleEvent(
            schema_version=LIFECYCLE_SCHEMA_VERSION,
            event_id=_lifecycle_event_id(opportunity_id, sequence, state, digest),
            opportunity_id=opportunity_id,
            sequence=sequence,
            previous_state=previous,
            state=state,
            event_type=event_type,
            outcome=outcome_for_state(state),
            detail=detail,
            occurred_at=(occurred_at or now).isoformat(),
            received_at=now.isoformat(),
            payload_hash=digest,
            runtime_sha=self._runtime_sha,
        )
        log.append(event)
        self._store.append_lifecycle_event(event)
        return event

    # ─── semantic methods ────────────────────────────────────────────────────
    # Callers never name a state directly, so illegal paths are impossible to
    # express rather than merely refused.

    def begin_opportunity(self, envelope: Any) -> EvidenceLifecycleEvent:
        opportunity_id = getattr(envelope, "opportunity_id", None) or envelope["opportunity_id"]
        self._store.append_opportunity(envelope)
        return self._transition(
            opportunity_id, State.OPPORTUNITY_CREATED, event_type="OPPORTUNITY_CREATED",
            payload={"opportunity_id": opportunity_id},
        )

    def record_candidate_universe(self, universe: Any) -> EvidenceLifecycleEvent:
        self._store.append_candidate_universe(universe)
        return self._transition(
            universe.opportunity_id, State.CANDIDATE_UNIVERSE_CAPTURED,
            event_type="CANDIDATE_UNIVERSE_CAPTURED",
            payload={"hash": universe.candidate_universe_hash, "count": len(universe.contracts)},
        )

    def record_selection(self, selection: Any) -> EvidenceLifecycleEvent:
        """Records the selection, then resolves listedness into the journal."""
        from app.services.snapback_contract_selection import LISTED_NO, LISTED_UNKNOWN, LISTED_YES

        self._store.append_selection(selection)
        self._transition(
            selection.opportunity_id, State.SELECTION_RECORDED, event_type="SELECTION_RECORDED",
            payload={"strike": selection.computed_strike, "listed": selection.listed_status},
        )

        if selection.listed_status == LISTED_YES:
            return self._transition(
                selection.opportunity_id, State.LISTED_CONFIRMED, event_type="LISTED_CONFIRMED",
                payload={"token": selection.matched_instrument_token},
            )
        if selection.listed_status == LISTED_NO:
            return self._transition(
                selection.opportunity_id, State.TERMINAL_NOT_LISTED,
                event_type="SELECTION_REALITY_FAILURE",
                detail="the computed contract was not listed in the observed master",
                payload={"strike": selection.computed_strike},
            )
        if selection.listed_status == LISTED_UNKNOWN:
            return self._transition(
                selection.opportunity_id, State.TERMINAL_SELECTION_UNKNOWN,
                event_type="SELECTION_UNIVERSE_UNKNOWN",
                detail="no authoritative instrument master; listedness is unknown",
            )
        raise LifecycleError(f"unknown listed_status {selection.listed_status!r}")

    def record_hedge_selection(self, hedge: Any) -> EvidenceLifecycleEvent:
        if hedge.hedge_required and not hedge.is_authoritative:
            return self._transition(
                hedge.opportunity_id, State.TERMINAL_HEDGE_UNKNOWN,
                event_type="HEDGE_SELECTION_UNKNOWN",
                detail=hedge.hedge_reason,
            )
        self._store.append_hedge_selection(hedge)
        return self._transition(
            hedge.opportunity_id, State.HEDGE_SELECTION_RECORDED,
            event_type="HEDGE_SELECTION_RECORDED",
            payload={"required": hedge.hedge_required, "token": hedge.instrument_token},
        )

    def record_market_subscription(self, opportunity_id: str, *, tokens: tuple[int, ...]) -> EvidenceLifecycleEvent:
        return self._transition(
            opportunity_id, State.MARKET_SUBSCRIBED, event_type="MARKET_SUBSCRIBED",
            payload={"tokens": list(tokens)},
        )

    def record_market_ready(self, opportunity_id: str, *, readiness: Any) -> EvidenceLifecycleEvent:
        ready = getattr(readiness, "ready", getattr(readiness, "authoritative", False))
        reasons = tuple(getattr(readiness, "reasons", ()) or ())
        if not ready:
            return self._transition(
                opportunity_id, State.TERMINAL_NO_MARKET_EVIDENCE,
                event_type="MARKET_EVIDENCE_NOT_AUTHORITATIVE",
                detail=", ".join(reasons) or "no qualifying quote before the decision",
            )
        return self._transition(
            opportunity_id, State.MARKET_EVIDENCE_READY, event_type="MARKET_EVIDENCE_READY",
        )

    def record_risk_decision(self, opportunity_id: str, *, approved: bool, detail: str = "") -> EvidenceLifecycleEvent:
        if not approved:
            return self._transition(
                opportunity_id, State.TERMINAL_RISK_REJECTED, event_type="RISK_REJECTED",
                detail=detail or "risk approval refused",
            )
        return self._transition(
            opportunity_id, State.ENTRY_INTENT_RECORDED, event_type="RISK_APPROVED",
        )

    def record_order_intent(self, event: Any) -> EvidenceLifecycleEvent:
        self.record_broker_event(event)
        return self._transition(
            event.opportunity_id, State.BROKER_SUBMITTED, event_type="ORDER_SUBMITTED",
            payload={"requested_quantity": event.requested_quantity},
        )

    def record_broker_event(self, event: Any) -> str:
        """Append a broker fact. Returns RECORDED or ALREADY_RECORDED.

        Deduplication happens on the broker's own derived id, so a retried
        callback cannot become a second fill.
        """
        if event.event_id in self._broker_event_ids:
            return "ALREADY_RECORDED"
        self._broker_event_ids.add(event.event_id)
        self._store.append_broker_event(event)
        return "RECORDED"

    def record_fill(self, event: Any, *, cumulative_filled: int, required: int) -> EvidenceLifecycleEvent:
        """Advance on cumulative broker quantity, never on status text.

        ``cumulative_filled`` is the broker's number. A partial fill re-enters the
        pending state rather than advancing, so a position half the intended size
        can never present as complete.
        """
        from app.services.snapback_broker_evidence import BrokerRole

        self.record_broker_event(event)
        opportunity_id = event.opportunity_id
        state = self.current_state(opportunity_id)

        if event.role == BrokerRole.OPTION_ENTRY:
            if state == State.BROKER_SUBMITTED or state == State.BROKER_ACKNOWLEDGED:
                self._transition(opportunity_id, State.OPTION_FILL_PENDING,
                                 event_type="OPTION_FILL_PENDING")
            complete, pending = State.OPTION_FILLED, State.OPTION_FILL_PENDING
        elif event.role == BrokerRole.HEDGE_ENTRY:
            complete, pending = State.HEDGE_FILLED, State.HEDGE_FILL_PENDING
        else:
            raise LifecycleError(f"record_fill does not handle role {event.role!r}")

        if int(cumulative_filled) >= int(required) and int(required) > 0:
            return self._transition(
                opportunity_id, complete, event_type=f"{complete}",
                payload={"cumulative_filled": int(cumulative_filled), "required": int(required)},
            )
        return self._transition(
            opportunity_id, pending, event_type="PARTIAL_FILL",
            detail=f"{int(cumulative_filled)}/{int(required)} filled",
            payload={"cumulative_filled": int(cumulative_filled), "required": int(required)},
        )

    def record_broker_rejection(self, event: Any, *, detail: str = "") -> EvidenceLifecycleEvent:
        self.record_broker_event(event)
        return self._transition(
            event.opportunity_id, State.TERMINAL_BROKER_REJECTED, event_type="BROKER_REJECTED",
            detail=detail or (event.broker_status or "broker rejected the order"),
        )

    def record_hedge_pending(self, opportunity_id: str) -> EvidenceLifecycleEvent:
        return self._transition(opportunity_id, State.HEDGE_FILL_PENDING,
                                event_type="HEDGE_FILL_PENDING")

    def record_hedge_waived(self, opportunity_id: str, *, reason: str) -> EvidenceLifecycleEvent:
        return self._transition(opportunity_id, State.HEDGE_WAIVED,
                                event_type="HEDGE_WAIVED", detail=reason)

    def record_protection_event(
        self, opportunity_id: str, *, submitted: bool = False,
        active: bool = False, failed: bool = False, detail: str = "",
    ) -> EvidenceLifecycleEvent:
        """Submitted is not active. Only a broker confirmation makes it active."""
        if failed:
            return self._transition(
                opportunity_id, State.PROTECTION_FAILED, event_type="PROTECTION_FAILED",
                detail=detail or "protection could not be established",
            )
        if active:
            return self._transition(opportunity_id, State.PROTECTION_ACTIVE,
                                    event_type="PROTECTION_ACTIVE")
        if submitted:
            return self._transition(opportunity_id, State.PROTECTION_PENDING,
                                    event_type="PROTECTION_SUBMITTED")
        raise LifecycleError("a protection event must be submitted, active or failed")

    def record_open(self, opportunity_id: str) -> EvidenceLifecycleEvent:
        return self._transition(opportunity_id, State.OPEN, event_type="OPEN")

    def record_exit_event(
        self, opportunity_id: str, *, started: bool = False,
        filled: bool = False, unresolved: bool = False, detail: str = "",
    ) -> EvidenceLifecycleEvent:
        if unresolved:
            return self._transition(opportunity_id, State.EXIT_UNRESOLVED,
                                    event_type="EXIT_UNRESOLVED", detail=detail)
        if filled:
            return self._transition(opportunity_id, State.EXIT_FILLED, event_type="EXIT_FILLED")
        if started:
            return self._transition(opportunity_id, State.EXITING, event_type="EXITING")
        raise LifecycleError("an exit event must be started, filled or unresolved")

    def record_reconciliation(
        self, opportunity_id: str, *, clean: bool, detail: str = "",
    ) -> EvidenceLifecycleEvent:
        state = State.RECONCILED if clean else State.RECONCILIATION_FAILED
        return self._transition(
            opportunity_id, state,
            event_type="RECONCILED" if clean else "RECONCILIATION_FAILED", detail=detail,
        )

    def mark_terminal_system_abort(self, opportunity_id: str, *, detail: str) -> EvidenceLifecycleEvent:
        """For failures of Sterling itself, before any exposure was taken."""
        return self._transition(
            opportunity_id, State.TERMINAL_ABORTED_SYSTEM,
            event_type="ABORTED_SYSTEM", detail=detail,
        )

    # ─── restart ─────────────────────────────────────────────────────────────

    def restore(self, events: list[EvidenceLifecycleEvent]) -> None:
        """Rebuild in-memory state from a persisted log.

        The journal is what happened; it is not authority over the broker. Callers
        must still reconcile against broker reality, which outranks this log
        wherever the two disagree.
        """
        for event in events:
            self._events.setdefault(event.opportunity_id, []).append(event)

    def non_terminal_opportunities(self) -> tuple[str, ...]:
        """What a restarting process must reconcile against the broker."""
        return tuple(
            opportunity_id
            for opportunity_id in sorted(self._events)
            if self.current_state(opportunity_id) not in TERMINAL_STATES
        )
