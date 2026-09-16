"""End to end: can the whole story be told from the files on disk?

The test that matters is not that records appear. It is that, given only what was
persisted, the reason an opportunity never traded, partially filled, or completed
can be reconstructed without consulting any in-memory state.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.services.snapback_broker_evidence import (
    BrokerEventType, BrokerRole, build_broker_event,
)
from app.services.snapback_evidence_recorder import (
    Outcome, SnapbackEvidenceRecorder, State, derive_state, outcome_for_state,
)
from app.services.snapback_evidence_store import SnapbackEvidenceStore

NOW = datetime(2026, 9, 17, 9, 20, tzinfo=timezone.utc)
PROVENANCE = dict(
    runtime_sha="9a706f584", strategy_sha="5a13542", config_hash="6ecbeb53", rule_hash="e03ddf75",
)


@pytest.fixture
def store(tmp_path):
    return SnapbackEvidenceStore(tmp_path, session_date=date(2026, 9, 17), run_id="run01")


@pytest.fixture
def recorder(store):
    return SnapbackEvidenceRecorder(store=store, runtime_sha="9a706f584", clock=lambda: NOW)


class Sel:
    def __init__(self, listed, opportunity_id="OPP-1"):
        self.opportunity_id, self.listed_status = opportunity_id, listed
        self.computed_strike, self.matched_instrument_token = 25000.0, 987654
        if listed != "LISTED":
            self.matched_instrument_token = None

    def as_dict(self):
        return {"opportunity_id": self.opportunity_id, "listed_status": self.listed_status,
                "computed_strike": self.computed_strike}


class Universe:
    def __init__(self, opportunity_id="OPP-1"):
        self.opportunity_id = opportunity_id
        self.candidate_universe_hash = "abc123"
        self.contracts = ()

    def as_dict(self):
        return {"opportunity_id": self.opportunity_id,
                "candidate_universe_hash": self.candidate_universe_hash}


class Hedge:
    def __init__(self, opportunity_id="OPP-1", *, required=True, authoritative=True):
        self.opportunity_id, self.hedge_required = opportunity_id, required
        self.is_authoritative = authoritative
        self.hedge_reason = "SELECTED" if authoritative else "HEDGE_SELECTION_UNKNOWN"
        self.instrument_token = 5001 if authoritative else None

    def as_dict(self):
        return {"opportunity_id": self.opportunity_id, "hedge_required": self.hedge_required}


class Ready:
    def __init__(self, ready=True, reasons=()):
        self.ready, self.reasons = ready, reasons


def _broker(opportunity_id="OPP-1", **over):
    kw = dict(opportunity_id=opportunity_id, broker="zerodha", role=BrokerRole.OPTION_ENTRY,
              event_type=BrokerEventType.BROKER_ACK, received_ts=NOW,
              evidence_class="BROKER_EXECUTED", broker_order_id="ORD-1", **PROVENANCE)
    kw.update(over)
    return build_broker_event(**kw)


def _full_lifecycle(recorder, opportunity_id="OPP-1"):
    recorder.begin_opportunity({"opportunity_id": opportunity_id})
    recorder.record_candidate_universe(Universe(opportunity_id))
    recorder.record_selection(Sel("LISTED", opportunity_id))
    recorder.record_hedge_selection(Hedge(opportunity_id))
    recorder.record_market_subscription(opportunity_id, tokens=(987654, 5001))
    recorder.record_market_ready(opportunity_id, readiness=Ready(True))
    recorder.record_risk_decision(opportunity_id, approved=True)
    recorder.record_order_intent(_broker(opportunity_id,
                                         event_type=BrokerEventType.ORDER_SUBMITTED,
                                         requested_quantity=150))
    recorder.record_fill(_broker(opportunity_id, event_type=BrokerEventType.PARTIAL_FILL,
                                 requested_quantity=150, filled_quantity=75),
                         cumulative_filled=75, required=150)
    recorder.record_fill(_broker(opportunity_id, event_type=BrokerEventType.PARTIAL_FILL,
                                 requested_quantity=150, filled_quantity=150),
                         cumulative_filled=150, required=150)
    recorder.record_hedge_pending(opportunity_id)
    recorder.record_fill(_broker(opportunity_id, role=BrokerRole.HEDGE_ENTRY,
                                 broker_order_id="ORD-H",
                                 event_type=BrokerEventType.PARTIAL_FILL,
                                 requested_quantity=50, filled_quantity=50),
                         cumulative_filled=50, required=50)
    recorder.record_protection_event(opportunity_id, submitted=True)
    recorder.record_protection_event(opportunity_id, active=True)
    recorder.record_open(opportunity_id)
    recorder.record_exit_event(opportunity_id, started=True)
    recorder.record_exit_event(opportunity_id, filled=True)
    recorder.record_reconciliation(opportunity_id, clean=True)
    return opportunity_id


def test_the_happy_path_reaches_reconciled(recorder):
    opportunity_id = _full_lifecycle(recorder)

    assert recorder.current_state(opportunity_id) == State.RECONCILED
    assert recorder.outcome(opportunity_id) == Outcome.EXECUTED


def test_the_story_reconstructs_from_disk_alone(recorder, store):
    opportunity_id = _full_lifecycle(recorder)

    # Nothing in memory; only what was written.
    persisted = store.read_lifecycle_events()
    replayed = SnapbackEvidenceRecorder(store=store, runtime_sha="9a706f584", clock=lambda: NOW)
    replayed.restore(persisted)

    assert replayed.current_state(opportunity_id) == State.RECONCILED

    states = [e.state for e in replayed.events(opportunity_id)]
    for expected in (State.OPPORTUNITY_CREATED, State.LISTED_CONFIRMED,
                     State.OPTION_FILL_PENDING, State.OPTION_FILLED,
                     State.PROTECTION_PENDING, State.PROTECTION_ACTIVE,
                     State.EXIT_FILLED, State.RECONCILED):
        assert expected in states


def test_the_partial_fill_is_visible_in_the_persisted_story(recorder, store):
    _full_lifecycle(recorder)

    lifecycle = store.read("lifecycle")
    partials = [row for row in lifecycle if row["event_type"] == "PARTIAL_FILL"]

    assert partials, "a partial fill must not be erased by the later complete fill"
    assert "75/150" in partials[0]["detail"]


def test_broker_events_persist_with_their_own_quantities(recorder, store):
    _full_lifecycle(recorder)

    fills = [row for row in store.read("broker_events")
             if row["event_type"] in ("PARTIAL_FILL", "FULL_FILL")]

    assert {row["filled_quantity"] for row in fills} == {75, 150, 50}


def test_a_blocked_opportunity_persists_its_reason(recorder, store):
    recorder.begin_opportunity({"opportunity_id": "OPP-BLOCKED"})
    recorder.record_candidate_universe(Universe("OPP-BLOCKED"))
    recorder.record_selection(Sel("NOT_LISTED", "OPP-BLOCKED"))

    rows = [r for r in store.read("lifecycle") if r["opportunity_id"] == "OPP-BLOCKED"]
    final = max(rows, key=lambda r: r["sequence"])

    assert final["state"] == State.TERMINAL_NOT_LISTED
    assert final["outcome"] == Outcome.BLOCKED_SELECTION
    assert "not listed" in final["detail"]


def test_the_denominator_is_computable_from_disk(recorder, store):
    _full_lifecycle(recorder, "OPP-OK")
    for i, listed in enumerate(["NOT_LISTED", "UNKNOWN"]):
        opportunity_id = f"OPP-BAD-{i}"
        recorder.begin_opportunity({"opportunity_id": opportunity_id})
        recorder.record_candidate_universe(Universe(opportunity_id))
        recorder.record_selection(Sel(listed, opportunity_id))

    events = store.read_lifecycle_events()
    by_opportunity: dict[str, list] = {}
    for event in events:
        by_opportunity.setdefault(event.opportunity_id, []).append(event)

    outcomes = [outcome_for_state(derive_state(v)) for v in by_opportunity.values()]

    assert len(by_opportunity) == 3
    assert outcomes.count(Outcome.EXECUTED) == 1
    assert outcomes.count(Outcome.BLOCKED_SELECTION) == 2


# ─── durability ──────────────────────────────────────────────────────────────

def test_every_part_file_is_written_atomically(recorder, store, tmp_path):
    _full_lifecycle(recorder)

    staging = tmp_path / "evidence" / "_staging"
    parts = list((tmp_path / "evidence" / "date=2026-09-17" / "lifecycle").glob("part-*.json"))

    assert parts, "lifecycle parts must exist"
    # Staging must be empty: everything was moved into place.
    assert list(staging.glob("*")) == []


def test_parts_are_immutable_not_rewritten(recorder, store, tmp_path):
    _full_lifecycle(recorder)

    directory = tmp_path / "evidence" / "date=2026-09-17" / "lifecycle"
    parts = sorted(directory.glob("part-*.json"))

    # One file per event, none overwritten.
    assert len(parts) == len(store.read("lifecycle"))
    assert len({p.name for p in parts}) == len(parts)


def test_a_writer_failure_blocks_new_exposure_but_not_managing_existing(recorder, store, tmp_path):
    """Capital safety outranks evidence completeness once exposure exists."""
    store._root = Path("/proc/nonexistent-evidence-root")

    store.append_lifecycle_event(type("E", (), {"as_dict": lambda self: {"x": 1}})())

    assert store.health.healthy is False
    assert store.health.may_open_new_exposure is False
    # A dead disk must never strand an open position.
    assert store.health.may_manage_existing_exposure is True
    assert store.health.failed_writes == 1


def test_a_write_failure_does_not_raise_into_the_trading_path(store):
    store._root = Path("/proc/nonexistent-evidence-root")

    # No exception: a storage fault must not propagate into whatever was recording.
    result = store.append_broker_event(type("E", (), {"as_dict": lambda self: {"x": 1}})())

    assert result is None
    assert store.health.last_error


def test_health_recovers_when_the_destination_returns(store, tmp_path):
    original = store._root
    store._root = Path("/proc/nonexistent-evidence-root")
    store.append_lifecycle_event(type("E", (), {"as_dict": lambda self: {"x": 1}})())
    assert store.health.healthy is False

    store._root = original
    store.append_lifecycle_event(type("E", (), {"as_dict": lambda self: {"x": 2}})())

    assert store.health.healthy is True
    assert store.health.failed_writes == 1
