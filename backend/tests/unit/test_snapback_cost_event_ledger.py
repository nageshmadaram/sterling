"""E25: one executed leg, one immutable cost event.

An opaque accumulated total cannot be audited: if a fee is wrong, or a leg silently
executed without being charged, nothing in the evidence says so.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from app.services.snapback_costs import (
    COST_SCHEDULE_VERSION,
    ExecutionCostEvent,
    cost_event_for_execution,
    statutory_charges,
)
from app.services.snapback_observation_warehouse import (
    EvidenceIntegrityError,
    SnapbackObservationWarehouse,
)


@pytest.fixture
def warehouse():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
        path = fh.name
    wh = SnapbackObservationWarehouse(db_path=path)
    yield wh
    if os.path.exists(path):
        os.remove(path)


def _event(**over):
    payload = dict(
        execution_event_id="PAPERFILL-OPP1", opportunity_id="OPP1",
        phase="OPTION_ENTRY", exchange="NFO", segment="OPTIONS",
        instrument="NIFTY26OCT25000PE", side="BUY", quantity=850, price=100.0,
    )
    payload.update(over)
    return cost_event_for_execution(**payload)


def test_the_factory_computes_from_the_canonical_schedule():
    event = _event()
    expected = statutory_charges(side="BUY", segment="OPTIONS", price=100.0, quantity=850)

    assert isinstance(event, ExecutionCostEvent)
    assert event.brokerage == pytest.approx(expected["brokerage"])
    assert event.stt == pytest.approx(expected["stt"])
    assert event.exchange_txn_fee == pytest.approx(expected["exchange_txn"])
    assert event.gst == pytest.approx(expected["gst"])
    assert event.stamp_duty == pytest.approx(expected["stamp_duty"])
    assert event.total_cost == pytest.approx(expected["total"])
    assert event.cost_schedule_version == COST_SCHEDULE_VERSION


def test_the_cost_id_is_owned_by_the_execution_event():
    event = _event(execution_event_id="HEDGE-OPP1-ENTRY")

    assert event.cost_id == "COST:HEDGE-OPP1-ENTRY"


def test_turnover_is_recorded():
    event = _event(quantity=850, price=100.0)

    assert event.turnover == pytest.approx(85_000.0)


def test_an_option_buy_pays_no_stt_here_either():
    assert _event(side="BUY", segment="OPTIONS").stt == pytest.approx(0.0)
    assert _event(side="SELL", segment="OPTIONS").stt > 0


def test_a_leg_persists_as_one_row(warehouse):
    warehouse.record_cost_event(_event())

    rows = warehouse.get_records_by_table("costs", opportunity_id="OPP1")

    assert len(rows) == 1
    assert rows[0]["phase"] == "OPTION_ENTRY"
    assert rows[0]["execution_event_id"] == "PAPERFILL-OPP1"
    assert rows[0]["side"] == "BUY"


def test_replaying_the_same_leg_is_a_no_op(warehouse):
    warehouse.record_cost_event(_event())
    warehouse.record_cost_event(_event())

    assert len(warehouse.get_records_by_table("costs", opportunity_id="OPP1")) == 1


def test_a_changed_leg_under_the_same_id_is_refused(warehouse):
    warehouse.record_cost_event(_event(quantity=850))

    with pytest.raises(EvidenceIntegrityError):
        warehouse.record_cost_event(_event(quantity=1700))

    rows = warehouse.get_records_by_table("costs", opportunity_id="OPP1")
    assert len(rows) == 1
    assert int(rows[0]["quantity"]) == 850


def test_every_phase_is_recorded_separately(warehouse):
    phases = [
        ("PAPERFILL-OPP1", "OPTION_ENTRY", "OPTIONS", "BUY"),
        ("HEDGE-OPP1-ENTRY", "HEDGE_ENTRY", "FUTURES", "BUY"),
        ("HEDGE-OPP1-20261018", "HEDGE_REBALANCE", "FUTURES", "SELL"),
        ("OPTIONEXIT-OPP1", "OPTION_EXIT", "OPTIONS", "SELL"),
        ("HEDGEEXIT-OPP1", "HEDGE_EXIT", "FUTURES", "SELL"),
    ]
    for execution_id, phase, segment, side in phases:
        warehouse.record_cost_event(_event(
            execution_event_id=execution_id, phase=phase, segment=segment, side=side,
            price=24500.0 if segment == "FUTURES" else 100.0,
            quantity=75 if segment == "FUTURES" else 850,
        ))

    rows = warehouse.get_records_by_table("costs", opportunity_id="OPP1")

    assert len(rows) == 5
    assert {r["phase"] for r in rows} == {p[1] for p in phases}


def test_the_ledger_sums_exactly(warehouse):
    a = _event(execution_event_id="PAPERFILL-OPP1", phase="OPTION_ENTRY")
    b = _event(execution_event_id="OPTIONEXIT-OPP1", phase="OPTION_EXIT", side="SELL")
    warehouse.record_cost_event(a)
    warehouse.record_cost_event(b)

    assert warehouse.sum_cost_events("OPP1") == pytest.approx(a.total_cost + b.total_cost)


def test_a_trade_with_no_executions_has_no_costs(warehouse):
    # NO_FILL, stale quote, insufficient depth: nothing executed, nothing charged.
    assert warehouse.sum_cost_events("OPP-NOFILL") == pytest.approx(0.0)
    assert warehouse.get_records_by_table("costs", opportunity_id="OPP-NOFILL") == []


def test_the_observed_replay_has_no_second_fee_formula():
    import inspect

    from study import snapback_observed_replay as replay

    source = inspect.getsource(replay)

    assert "def calculate_statutory_charges" not in source
    assert "snapback_costs" in source
