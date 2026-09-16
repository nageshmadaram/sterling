"""The outcome's economics must equal the ledger's.

If a cost can exist in the outcome without a leg — or a leg without a cost — then the
2x and 3x cost stress tests are measuring a number nobody can trace to an execution.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

import pytest

from app.services.snapback_costs import cost_event_for_execution
from app.services.snapback_observation_warehouse import (
    ABS_ECONOMIC_RECONCILIATION_TOLERANCE,
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


def _position(wh, opp="OPP1", accumulated=0.0):
    wh.save_paper_position(
        opportunity_id=opp, symbol="NIFTY", option_symbol="NIFTY26OCT25000PE",
        option_qty=850, option_entry_price=100.0, option_expiry="2026-10-29",
        option_strike=25000.0, futures_symbol="NIFTY26OCTFUT", futures_lot_size=75,
        current_futures_lots=1, avg_futures_entry_price=24500.0,
        realized_futures_pnl=0.0, entry_spot=24500.0,
        entry_timestamp=datetime.now(timezone.utc).isoformat(), entry_dte=45,
        entry_iv=0.2, causal_beta=1.0, accumulated_costs=accumulated,
    )
    return opp


def _entry_costs(wh, opp="OPP1"):
    events = [
        cost_event_for_execution(
            execution_event_id=f"PAPERFILL-{opp}", opportunity_id=opp,
            phase="OPTION_ENTRY", exchange="NFO", segment="OPTIONS",
            instrument="NIFTY26OCT25000PE", side="BUY", quantity=850, price=100.0,
        ),
        cost_event_for_execution(
            execution_event_id=f"HEDGE-{opp}-ENTRY", opportunity_id=opp,
            phase="HEDGE_ENTRY", exchange="NFO", segment="FUTURES",
            instrument="NIFTY26OCTFUT", side="BUY", quantity=75, price=24500.0,
        ),
    ]
    for event in events:
        wh.record_cost_event(event)
    return sum(e.total_cost for e in events)


def test_the_close_derives_costs_from_the_ledger(warehouse):
    opp = _position(warehouse)
    entry_total = _entry_costs(warehouse)

    exit_events = [
        cost_event_for_execution(
            execution_event_id=f"OPTIONEXIT-{opp}", opportunity_id=opp,
            phase="OPTION_EXIT", exchange="NFO", segment="OPTIONS",
            instrument="NIFTY26OCT25000PE", side="SELL", quantity=850, price=60.0,
        ),
        cost_event_for_execution(
            execution_event_id=f"HEDGEEXIT-{opp}", opportunity_id=opp,
            phase="HEDGE_EXIT", exchange="NFO", segment="FUTURES",
            instrument="NIFTY26OCTFUT", side="SELL", quantity=75, price=24600.0,
        ),
    ]
    expected_total = entry_total + sum(e.total_cost for e in exit_events)

    warehouse.update_paper_position_accumulated_costs(opp, entry_total)

    result = warehouse.commit_paper_close_transaction(
        opportunity_id=opp,
        outcome_data={
            "outcome_id": f"OUTCOME-{opp}", "opportunity_id": opp, "symbol": "NIFTY",
            "exit_reason": "PREMIUM_STOP",
            "entry_ts": "2026-10-01T09:20:00+05:30",
            "exit_ts": "2026-10-03T11:00:00+05:30",
            "actual_option_pnl": -34_000.0, "actual_futures_pnl": 7_500.0,
        },
        cost_events=exit_events,
    )

    outcome = warehouse.get_records_by_table("outcomes", opportunity_id=opp)[0]

    assert float(outcome["actual_costs"]) == pytest.approx(expected_total)
    assert float(outcome["actual_total_pnl"]) == pytest.approx(
        -34_000.0 + 7_500.0 - expected_total
    )
    assert result["status"] == "RECORDED"


def test_a_caller_supplied_cost_that_disagrees_is_refused(warehouse):
    opp = _position(warehouse)
    entry_total = _entry_costs(warehouse)
    warehouse.update_paper_position_accumulated_costs(opp, entry_total)

    with pytest.raises(EvidenceIntegrityError):
        warehouse.commit_paper_close_transaction(
            opportunity_id=opp,
            outcome_data={
                "outcome_id": f"OUTCOME-{opp}", "opportunity_id": opp, "symbol": "NIFTY",
                "exit_reason": "PREMIUM_STOP",
                "entry_ts": "2026-10-01T09:20:00+05:30",
                "exit_ts": "2026-10-03T11:00:00+05:30",
                "actual_option_pnl": -34_000.0, "actual_futures_pnl": 7_500.0,
                # Wildly different from the ledger.
                "actual_costs": 1.0,
            },
            cost_events=[],
        )

    assert warehouse.get_records_by_table("outcomes", opportunity_id=opp) == []


def test_the_projection_must_agree_with_the_ledger(warehouse):
    opp = _position(warehouse, accumulated=999_999.0)
    _entry_costs(warehouse)

    with pytest.raises(EvidenceIntegrityError):
        warehouse.commit_paper_close_transaction(
            opportunity_id=opp,
            outcome_data={
                "outcome_id": f"OUTCOME-{opp}", "opportunity_id": opp, "symbol": "NIFTY",
                "exit_reason": "PREMIUM_STOP",
                "entry_ts": "2026-10-01T09:20:00+05:30",
                "exit_ts": "2026-10-03T11:00:00+05:30",
                "actual_option_pnl": -1.0, "actual_futures_pnl": 0.0,
            },
            cost_events=[],
        )


def test_a_rounding_difference_inside_tolerance_is_accepted(warehouse):
    opp = _position(warehouse)
    entry_total = _entry_costs(warehouse)
    warehouse.update_paper_position_accumulated_costs(
        opp, entry_total + ABS_ECONOMIC_RECONCILIATION_TOLERANCE / 2,
    )

    warehouse.commit_paper_close_transaction(
        opportunity_id=opp,
        outcome_data={
            "outcome_id": f"OUTCOME-{opp}", "opportunity_id": opp, "symbol": "NIFTY",
            "exit_reason": "PREMIUM_STOP",
            "entry_ts": "2026-10-01T09:20:00+05:30",
            "exit_ts": "2026-10-03T11:00:00+05:30",
            "actual_option_pnl": -1.0, "actual_futures_pnl": 0.0,
        },
        cost_events=[],
    )

    assert len(warehouse.get_records_by_table("outcomes", opportunity_id=opp)) == 1


def test_a_failed_close_writes_no_cost_events(warehouse):
    opp = _position(warehouse)
    _entry_costs(warehouse)
    before = len(warehouse.get_records_by_table("costs", opportunity_id=opp))

    exit_event = cost_event_for_execution(
        execution_event_id=f"OPTIONEXIT-{opp}", opportunity_id=opp,
        phase="OPTION_EXIT", exchange="NFO", segment="OPTIONS",
        instrument="NIFTY26OCT25000PE", side="SELL", quantity=850, price=60.0,
    )

    with pytest.raises(Exception):
        warehouse.commit_paper_close_transaction(
            opportunity_id=opp,
            outcome_data={"opportunity_id": opp},  # missing required fields
            cost_events=[exit_event],
        )

    assert len(warehouse.get_records_by_table("costs", opportunity_id=opp)) == before
    assert warehouse.get_paper_position(opp)["status"] != "CLOSED"
