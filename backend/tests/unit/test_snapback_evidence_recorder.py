"""Every opportunity leaves a trace, including the ones that never reach a broker.

The blocked opportunities are the denominator. Without them Sterling can count
completed trades but cannot say whether 100 signals produced 86 trades or 400
did, and "the strategy never fired" looks identical to "the strategy fired and
reality refused".
"""

from __future__ import annotations

import itertools
from datetime import datetime, timezone

import pytest

from app.services.snapback_broker_evidence import (
    BrokerEventType, BrokerRole, build_broker_event,
)
from app.services.snapback_evidence_recorder import (
    BLOCKED_STATES,
    LEGAL_TRANSITIONS,
    TERMINAL_STATES,
    LifecycleError,
    Outcome,
    SnapbackEvidenceRecorder,
    State,
    derive_state,
    outcome_for_state,
)

NOW = datetime(2026, 9, 17, 9, 20, tzinfo=timezone.utc)
PROVENANCE = dict(
    runtime_sha="9a706f584", strategy_sha="5a13542", config_hash="6ecbeb53", rule_hash="e03ddf75",
)


class FakeStore:
    """Records what it was asked to persist; can be told to start failing."""

    def __init__(self):
        self.lifecycle, self.broker, self.other = [], [], []
        self.failing = False

    def _guard(self):
        if self.failing:
            raise OSError("evidence destination unavailable")

    def append_lifecycle_event(self, event):
        self._guard()
        self.lifecycle.append(event)

    def append_broker_event(self, event):
        self._guard()
        self.broker.append(event)

    def append_opportunity(self, obj):
        self._guard(); self.other.append(("opportunity", obj))

    def append_candidate_universe(self, obj):
        self._guard(); self.other.append(("universe", obj))

    def append_selection(self, obj):
        self._guard(); self.other.append(("selection", obj))

    def append_hedge_selection(self, obj):
        self._guard(); self.other.append(("hedge", obj))


class Sel:
    def __init__(self, listed, opportunity_id="OPP-1"):
        self.opportunity_id = opportunity_id
        self.listed_status = listed
        self.computed_strike = 25000.0
        self.matched_instrument_token = 987654 if listed == "LISTED" else None


class Hedge:
    def __init__(self, *, required=True, authoritative=True, opportunity_id="OPP-1"):
        self.opportunity_id = opportunity_id
        self.hedge_required = required
        self.is_authoritative = authoritative
        self.hedge_reason = "SELECTED" if authoritative else "HEDGE_SELECTION_UNKNOWN"
        self.instrument_token = 5001 if authoritative else None


class Ready:
    def __init__(self, ready=True, reasons=()):
        self.ready = ready
        self.reasons = reasons


@pytest.fixture
def recorder():
    return SnapbackEvidenceRecorder(store=FakeStore(), runtime_sha="9a706f584",
                                    clock=lambda: NOW)


def _begin(rec, opportunity_id="OPP-1"):
    rec.begin_opportunity({"opportunity_id": opportunity_id})
    return opportunity_id


def _to_listed(rec, opportunity_id="OPP-1"):
    _begin(rec, opportunity_id)
    rec.record_candidate_universe(type("U", (), {
        "opportunity_id": opportunity_id, "candidate_universe_hash": "h", "contracts": ()})())
    rec.record_selection(Sel("LISTED", opportunity_id))
    return opportunity_id


def _to_market_ready(rec, opportunity_id="OPP-1"):
    _to_listed(rec, opportunity_id)
    rec.record_hedge_selection(Hedge(opportunity_id=opportunity_id))
    rec.record_market_subscription(opportunity_id, tokens=(1, 2))
    rec.record_market_ready(opportunity_id, readiness=Ready(True))
    return opportunity_id


def _broker(**over):
    kw = dict(opportunity_id="OPP-1", broker="zerodha", role=BrokerRole.OPTION_ENTRY,
              event_type=BrokerEventType.BROKER_ACK, received_ts=NOW,
              evidence_class="BROKER_EXECUTED", broker_order_id="ORD-1", **PROVENANCE)
    kw.update(over)
    return build_broker_event(**kw)


# ─── state is derived, not assigned ──────────────────────────────────────────

def test_state_is_a_fold_over_the_log(recorder):
    opportunity_id = _to_listed(recorder)

    assert recorder.current_state(opportunity_id) == State.LISTED_CONFIRMED
    assert derive_state(list(recorder.events(opportunity_id))) == State.LISTED_CONFIRMED


def test_the_log_is_append_only_and_sequenced(recorder):
    opportunity_id = _to_listed(recorder)

    events = recorder.events(opportunity_id)
    assert [e.sequence for e in events] == list(range(len(events)))
    assert events[0].previous_state is None
    assert all(e.previous_state == events[i].state for i, e in enumerate(events[1:]))


def test_every_event_carries_a_payload_hash_and_runtime(recorder):
    opportunity_id = _to_listed(recorder)

    for event in recorder.events(opportunity_id):
        assert len(event.payload_hash) == 64
        assert event.runtime_sha == "9a706f584"


def test_an_opportunity_must_begin_at_the_beginning(recorder):
    with pytest.raises(LifecycleError):
        recorder.record_open("OPP-NEVER-CREATED")


# ─── blocked opportunities are durable ───────────────────────────────────────

def test_a_not_listed_contract_leaves_a_durable_record(recorder):
    _begin(recorder)
    recorder.record_candidate_universe(type("U", (), {
        "opportunity_id": "OPP-1", "candidate_universe_hash": "h", "contracts": ()})())

    recorder.record_selection(Sel("NOT_LISTED"))

    assert recorder.current_state("OPP-1") == State.TERMINAL_NOT_LISTED
    assert recorder.outcome("OPP-1") == Outcome.BLOCKED_SELECTION
    assert len(recorder._store.lifecycle) >= 3


def test_an_unknown_universe_leaves_a_durable_record(recorder):
    _begin(recorder)
    recorder.record_candidate_universe(type("U", (), {
        "opportunity_id": "OPP-1", "candidate_universe_hash": "h", "contracts": ()})())

    recorder.record_selection(Sel("UNKNOWN"))

    assert recorder.current_state("OPP-1") == State.TERMINAL_SELECTION_UNKNOWN
    assert recorder.outcome("OPP-1") == Outcome.BLOCKED_SELECTION


def test_an_unavailable_hedge_leaves_a_durable_record(recorder):
    _to_listed(recorder)

    recorder.record_hedge_selection(Hedge(required=True, authoritative=False))

    assert recorder.current_state("OPP-1") == State.TERMINAL_HEDGE_UNKNOWN
    assert recorder.outcome("OPP-1") == Outcome.BLOCKED_HEDGE


def test_stale_market_data_leaves_a_durable_record(recorder):
    _to_listed(recorder)
    recorder.record_hedge_selection(Hedge())
    recorder.record_market_subscription("OPP-1", tokens=(1,))

    recorder.record_market_ready("OPP-1", readiness=Ready(False, ("OPTION_STALE",)))

    assert recorder.current_state("OPP-1") == State.TERMINAL_NO_MARKET_EVIDENCE
    assert recorder.outcome("OPP-1") == Outcome.BLOCKED_MARKET_DATA
    assert "OPTION_STALE" in recorder.events("OPP-1")[-1].detail


def test_a_risk_rejection_leaves_a_durable_record(recorder):
    _to_market_ready(recorder)

    recorder.record_risk_decision("OPP-1", approved=False, detail="premium budget")

    assert recorder.current_state("OPP-1") == State.TERMINAL_RISK_REJECTED
    assert recorder.outcome("OPP-1") == Outcome.BLOCKED_RISK


def test_a_broker_rejection_leaves_a_durable_record(recorder):
    _to_market_ready(recorder)
    recorder.record_risk_decision("OPP-1", approved=True)
    recorder.record_order_intent(_broker(event_type=BrokerEventType.ORDER_SUBMITTED,
                                         requested_quantity=75))

    recorder.record_broker_rejection(_broker(event_type=BrokerEventType.REJECTED,
                                             broker_status="insufficient margin"))

    assert recorder.current_state("OPP-1") == State.TERMINAL_BROKER_REJECTED
    assert recorder.outcome("OPP-1") == Outcome.BLOCKED_BROKER


def test_the_denominator_is_recoverable(recorder):
    """Signals fired vs signals that reached the broker."""
    for i, listed in enumerate(["LISTED", "NOT_LISTED", "LISTED", "UNKNOWN"]):
        opportunity_id = f"OPP-{i}"
        _begin(recorder, opportunity_id)
        recorder.record_candidate_universe(type("U", (), {
            "opportunity_id": opportunity_id, "candidate_universe_hash": "h", "contracts": ()})())
        recorder.record_selection(Sel(listed, opportunity_id))

    states = [recorder.current_state(f"OPP-{i}") for i in range(4)]

    assert sum(1 for s in states if s in BLOCKED_STATES) == 2
    assert sum(1 for s in states if s == State.LISTED_CONFIRMED) == 2


# ─── terminal means terminal ─────────────────────────────────────────────────

@pytest.mark.parametrize("listed,expected", [
    ("NOT_LISTED", State.TERMINAL_NOT_LISTED),
    ("UNKNOWN", State.TERMINAL_SELECTION_UNKNOWN),
])
def test_a_blocked_opportunity_can_never_become_executable(recorder, listed, expected):
    _begin(recorder)
    recorder.record_candidate_universe(type("U", (), {
        "opportunity_id": "OPP-1", "candidate_universe_hash": "h", "contracts": ()})())
    recorder.record_selection(Sel(listed))

    assert recorder.current_state("OPP-1") == expected

    for attempt in (
        lambda: recorder.record_market_subscription("OPP-1", tokens=(1,)),
        lambda: recorder.record_market_ready("OPP-1", readiness=Ready(True)),
        lambda: recorder.record_open("OPP-1"),
        lambda: recorder.record_hedge_selection(Hedge()),
    ):
        with pytest.raises(LifecycleError) as excinfo:
            attempt()
        assert "new opportunity_id" in str(excinfo.value)


def test_a_retry_needs_a_new_opportunity_id(recorder):
    _begin(recorder)
    recorder.record_candidate_universe(type("U", (), {
        "opportunity_id": "OPP-1", "candidate_universe_hash": "h", "contracts": ()})())
    recorder.record_selection(Sel("NOT_LISTED"))

    # A fresh id works, and the refusal for the old one stays in the record.
    _to_listed(recorder, "OPP-2")

    assert recorder.current_state("OPP-1") == State.TERMINAL_NOT_LISTED
    assert recorder.current_state("OPP-2") == State.LISTED_CONFIRMED


# ─── transitions ─────────────────────────────────────────────────────────────

def test_every_declared_transition_target_is_a_known_state():
    known = {v for k, v in vars(State).items() if not k.startswith("_") and isinstance(v, str)}

    for source, targets in LEGAL_TRANSITIONS.items():
        assert source in known
        for target in targets:
            assert target in known, f"{source} -> {target} is not a declared state"


def test_no_transition_leads_out_of_a_terminal_state():
    for terminal in TERMINAL_STATES:
        assert LEGAL_TRANSITIONS.get(terminal, frozenset()) == frozenset()


def test_states_carrying_exposure_can_always_still_exit():
    """Losing protection or reconciliation must never strand a position."""
    for state in (State.PROTECTION_FAILED, State.EXIT_UNRESOLVED):
        reachable = LEGAL_TRANSITIONS[state]
        assert State.EXITING in reachable or State.EXIT_FILLED in reachable


def test_an_illegal_transition_is_refused(recorder):
    _begin(recorder)

    # CANDIDATE_UNIVERSE_CAPTURED cannot jump to OPEN.
    with pytest.raises(LifecycleError) as excinfo:
        recorder.record_open("OPP-1")

    assert "illegal transition" in str(excinfo.value)


def test_illegal_transitions_are_refused_exhaustively(recorder):
    """Every pair not in the table must fail, not merely the ones we thought of."""
    all_states = sorted({s for s in LEGAL_TRANSITIONS} | {
        t for targets in LEGAL_TRANSITIONS.values() for t in targets})

    checked = 0
    for source, target in itertools.product(all_states, all_states):
        if target in LEGAL_TRANSITIONS.get(source, frozenset()):
            continue
        rec = SnapbackEvidenceRecorder(store=FakeStore(), runtime_sha="x", clock=lambda: NOW)
        rec._events["O"] = [type("E", (), {"sequence": 0, "state": source})()]
        with pytest.raises(LifecycleError):
            rec._transition("O", target)
        checked += 1

    assert checked > 300


# ─── partial fills ───────────────────────────────────────────────────────────

def test_a_partial_fill_does_not_mark_the_option_filled(recorder):
    _to_market_ready(recorder)
    recorder.record_risk_decision("OPP-1", approved=True)
    recorder.record_order_intent(_broker(event_type=BrokerEventType.ORDER_SUBMITTED,
                                         requested_quantity=150))

    recorder.record_fill(
        _broker(event_type=BrokerEventType.PARTIAL_FILL, requested_quantity=150,
                filled_quantity=75, broker_ts=NOW),
        cumulative_filled=75, required=150,
    )

    assert recorder.current_state("OPP-1") == State.OPTION_FILL_PENDING


def test_cumulative_completion_marks_the_option_filled(recorder):
    _to_market_ready(recorder)
    recorder.record_risk_decision("OPP-1", approved=True)
    recorder.record_order_intent(_broker(event_type=BrokerEventType.ORDER_SUBMITTED,
                                         requested_quantity=150))
    recorder.record_fill(_broker(event_type=BrokerEventType.PARTIAL_FILL, requested_quantity=150,
                                 filled_quantity=75), cumulative_filled=75, required=150)

    recorder.record_fill(_broker(event_type=BrokerEventType.PARTIAL_FILL, requested_quantity=150,
                                 filled_quantity=150), cumulative_filled=150, required=150)

    assert recorder.current_state("OPP-1") == State.OPTION_FILLED


def test_a_duplicate_broker_callback_is_recorded_once(recorder):
    _to_market_ready(recorder)
    recorder.record_risk_decision("OPP-1", approved=True)
    event = _broker(event_type=BrokerEventType.BROKER_ACK)

    first = recorder.record_broker_event(event)
    second = recorder.record_broker_event(event)

    assert first == "RECORDED"
    assert second == "ALREADY_RECORDED"
    assert len(recorder._store.broker) == 1


# ─── protection ──────────────────────────────────────────────────────────────

def test_submitted_protection_is_not_active(recorder):
    opportunity_id = _open_position(recorder, stop_before_active=True)

    assert recorder.current_state(opportunity_id) == State.PROTECTION_PENDING
    assert recorder.current_state(opportunity_id) != State.PROTECTION_ACTIVE


def test_protection_becomes_active_only_on_confirmation(recorder):
    opportunity_id = _open_position(recorder, stop_before_active=True)

    recorder.record_protection_event(opportunity_id, active=True)

    assert recorder.current_state(opportunity_id) == State.PROTECTION_ACTIVE


def test_a_protection_failure_after_entry_keeps_exit_reachable(recorder):
    opportunity_id = _open_position(recorder)
    recorder.record_protection_event(opportunity_id, failed=True, detail="GTT rejected")

    assert recorder.current_state(opportunity_id) == State.PROTECTION_FAILED

    # Exposure exists, so exiting must remain possible.
    recorder.record_exit_event(opportunity_id, started=True)
    assert recorder.current_state(opportunity_id) == State.EXITING


def _open_position(recorder, opportunity_id="OPP-1", *, stop_before_active=False):
    _to_market_ready(recorder, opportunity_id)
    recorder.record_risk_decision(opportunity_id, approved=True)
    recorder.record_order_intent(_broker(opportunity_id=opportunity_id,
                                         event_type=BrokerEventType.ORDER_SUBMITTED,
                                         requested_quantity=75))
    recorder.record_fill(_broker(opportunity_id=opportunity_id,
                                 event_type=BrokerEventType.PARTIAL_FILL,
                                 requested_quantity=75, filled_quantity=75),
                         cumulative_filled=75, required=75)
    recorder.record_hedge_pending(opportunity_id)
    recorder.record_fill(_broker(opportunity_id=opportunity_id, role=BrokerRole.HEDGE_ENTRY,
                                 event_type=BrokerEventType.PARTIAL_FILL, broker_order_id="ORD-H",
                                 requested_quantity=50, filled_quantity=50),
                         cumulative_filled=50, required=50)
    recorder.record_protection_event(opportunity_id, submitted=True)
    if stop_before_active:
        return opportunity_id
    recorder.record_protection_event(opportunity_id, active=True)
    recorder.record_open(opportunity_id)
    return opportunity_id


# ─── the whole happy path ────────────────────────────────────────────────────

def test_a_complete_lifecycle_reaches_reconciled_once(recorder):
    opportunity_id = _open_position(recorder)
    recorder.record_exit_event(opportunity_id, started=True)
    recorder.record_exit_event(opportunity_id, filled=True)
    recorder.record_reconciliation(opportunity_id, clean=True)

    assert recorder.current_state(opportunity_id) == State.RECONCILED
    assert recorder.outcome(opportunity_id) == Outcome.EXECUTED

    with pytest.raises(LifecycleError):
        recorder.record_reconciliation(opportunity_id, clean=True)


def test_outcome_mapping_covers_every_terminal_state():
    for state in TERMINAL_STATES:
        assert outcome_for_state(state) != Outcome.IN_FLIGHT


# ─── restart ─────────────────────────────────────────────────────────────────

def test_restore_rebuilds_state_from_events_alone(recorder):
    opportunity_id = _open_position(recorder)
    persisted = list(recorder.events(opportunity_id))

    fresh = SnapbackEvidenceRecorder(store=FakeStore(), runtime_sha="9a706f584", clock=lambda: NOW)
    fresh.restore(persisted)

    assert fresh.current_state(opportunity_id) == State.OPEN


def test_restart_lists_what_must_be_reconciled(recorder):
    _open_position(recorder, "OPP-OPEN")
    _begin(recorder, "OPP-DEAD")
    recorder.record_candidate_universe(type("U", (), {
        "opportunity_id": "OPP-DEAD", "candidate_universe_hash": "h", "contracts": ()})())
    recorder.record_selection(Sel("NOT_LISTED", "OPP-DEAD"))

    fresh = SnapbackEvidenceRecorder(store=FakeStore(), runtime_sha="x", clock=lambda: NOW)
    fresh.restore([e for oid in ("OPP-OPEN", "OPP-DEAD") for e in recorder.events(oid)])

    # The terminal one needs no broker query; the open one does.
    assert fresh.non_terminal_opportunities() == ("OPP-OPEN",)
