"""Closing a position and recording its economics must be one transaction.

If the position is marked CLOSED before the outcome is written, a crash in between
erases the trade's economics while removing it from the open book — the worst possible
combination for evidence.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

import pytest

from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse


@pytest.fixture
def warehouse():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
        path = fh.name
    wh = SnapbackObservationWarehouse(db_path=path)
    yield wh
    if os.path.exists(path):
        os.remove(path)


def _open_position(wh, opp="OPP-1"):
    wh.save_paper_position(
        opportunity_id=opp, symbol="NIFTY", option_symbol="NIFTY26OCT25000PE",
        option_qty=25, option_entry_price=100.0, option_expiry="2026-10-29",
        option_strike=25000.0, futures_symbol="NIFTY26OCTFUT", futures_lot_size=25,
        current_futures_lots=1, avg_futures_entry_price=24500.0,
        realized_futures_pnl=0.0, entry_spot=24500.0,
        entry_timestamp=datetime.now(timezone.utc).isoformat(), entry_dte=45,
        entry_iv=0.2, causal_beta=1.0,
    )
    return opp


def _outcome(opp="OPP-1"):
    return {
        "outcome_id": f"OUT-{opp}",
        "opportunity_id": opp,
        "symbol": "NIFTY",
        "exit_reason": "PREMIUM_STOP",
        "entry_ts": "2026-10-01T09:20:00+05:30",
        "exit_ts": "2026-10-03T11:00:00+05:30",
        "actual_option_pnl": -1000.0,
        "actual_futures_pnl": 200.0,
        # Costs now come from the ledger; an empty ledger means zero cost here.
        "actual_costs": 0.0,
        "actual_total_pnl": -800.0,
        "modeled_option_pnl": -900.0,
        "modeled_futures_pnl": 200.0,
        "modeled_costs": 100.0,
        "modeled_total_pnl": -800.0,
        "observed_vs_model_error": -120.0,
    }


def _costs(opp="OPP-1"):
    return [
        {"cost_id": f"COST-{opp}-OPTION-EXIT", "opportunity_id": opp, "symbol": "NIFTY",
         "phase": "OPTION_EXIT", "total_cost": 80.0},
        {"cost_id": f"COST-{opp}-HEDGE-EXIT", "opportunity_id": opp, "symbol": "NIFTY",
         "phase": "HEDGE_EXIT", "total_cost": 40.0},
    ]


def test_successful_close_writes_everything_once(warehouse):
    opp = _open_position(warehouse)

    warehouse.commit_paper_close_transaction(
        opportunity_id=opp, outcome_data=_outcome(), cost_events=_costs(),
    )

    assert warehouse.get_paper_position(opp)["status"] == "CLOSED"
    outcomes = warehouse.get_records_by_table("outcomes", opportunity_id=opp)
    costs = warehouse.get_records_by_table("costs", opportunity_id=opp)
    assert len(outcomes) == 1
    assert len(costs) == 2


def test_a_failed_outcome_write_leaves_the_position_open(warehouse):
    opp = _open_position(warehouse)

    broken = _outcome()
    broken.pop("outcome_id")  # forces the insert to fail

    with pytest.raises(Exception):
        warehouse.commit_paper_close_transaction(
            opportunity_id=opp, outcome_data=broken, cost_events=_costs(),
        )

    # Nothing may be half-applied.
    assert warehouse.get_paper_position(opp)["status"] != "CLOSED"
    assert warehouse.get_records_by_table("outcomes", opportunity_id=opp) == []
    assert warehouse.get_records_by_table("costs", opportunity_id=opp) == []


def test_a_failed_cost_write_rolls_back_the_close(warehouse):
    opp = _open_position(warehouse)

    bad_costs = [{"opportunity_id": opp}]  # missing cost_id

    with pytest.raises(Exception):
        warehouse.commit_paper_close_transaction(
            opportunity_id=opp, outcome_data=_outcome(), cost_events=bad_costs,
        )

    assert warehouse.get_paper_position(opp)["status"] != "CLOSED"
    assert warehouse.get_records_by_table("outcomes", opportunity_id=opp) == []


def test_exit_pending_position_can_be_closed_atomically(warehouse):
    opp = _open_position(warehouse)
    warehouse.set_paper_position_pending_exit(
        opportunity_id=opp, pending_exit_reason="PREMIUM_STOP",
        pending_exit_option_bid=60.0,
        pending_exit_ts=datetime.now(timezone.utc).isoformat(),
    )

    warehouse.commit_paper_close_transaction(
        opportunity_id=opp, outcome_data=_outcome(), cost_events=_costs(),
    )

    assert warehouse.get_paper_position(opp)["status"] == "CLOSED"


def test_opportunity_is_closed_in_the_same_transaction(warehouse):
    opp = _open_position(warehouse)
    warehouse.record_opportunity(
        opportunity_id=opp, symbol="NIFTY", signal_type="SNAPBACK_FADE_UP",
        spot_price=24500.0, trend="BEARISH",
    )

    warehouse.commit_paper_close_transaction(
        opportunity_id=opp, outcome_data=_outcome(), cost_events=_costs(),
    )

    row = warehouse.get_records_by_table("opportunities", opportunity_id=opp)[0]
    assert row["status"] == "CLOSED"


def test_close_is_used_by_the_collector():
    import inspect

    from app.services import snapback_prospective_collector as mod

    source = inspect.getsource(mod.SnapbackProspectiveCollector.close_opportunity)

    assert "commit_paper_close_transaction" in source
