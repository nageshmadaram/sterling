"""Quote attempts are recorded before any rejection.

Today's rehearsal queried 25 contracts and stored zero quotes, because the futures
freshness rejection returned before the observations were persisted. The gate then
cannot measure the missing evidence it exists to police: coverage computed over
persisted quotes only ever divides good rows by good rows.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse
from app.services.snapback_quote_evidence import (
    coverage_from_events,
    record_quote_attempt,
)


@pytest.fixture
def warehouse():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
        path = fh.name
    wh = SnapbackObservationWarehouse(db_path=path)
    yield wh
    if os.path.exists(path):
        os.remove(path)


def _attempt(wh, **kw):
    payload = dict(
        opportunity_id="OPP-1",
        phase="ENTRY",
        leg="OPTION",
        contract_id="NIFTY26OCT25000PE",
        required_for_economics=True,
        quote_present=True,
        bid=100.0,
        ask=102.0,
        provider_timestamp="2026-09-17T09:20:00+05:30",
        age_ms=500,
        accepted=True,
        reason_codes=[],
    )
    payload.update(kw)
    record_quote_attempt(wh, **payload)


def test_a_missing_quote_is_still_recorded(warehouse):
    _attempt(warehouse, quote_present=False, bid=None, ask=None, accepted=False,
             reason_codes=["missing_quote"])

    rows = warehouse.get_records_by_table("quote_quality_events", opportunity_id="OPP-1")

    assert len(rows) == 1
    assert rows[0]["quote_present"] == 0
    assert rows[0]["accepted"] == 0
    assert "missing_quote" in rows[0]["reason_codes"]


def test_a_stale_quote_is_recorded_before_the_rejection(warehouse):
    _attempt(warehouse, leg="FUTURES", accepted=False,
             reason_codes=["future_exchange_timestamp"], age_ms=999999)

    rows = warehouse.get_records_by_table("quote_quality_events", opportunity_id="OPP-1")

    assert len(rows) == 1
    assert rows[0]["accepted"] == 0
    assert rows[0]["leg"] == "FUTURES"
    assert "future_exchange_timestamp" in rows[0]["reason_codes"]


def test_events_carry_build_provenance(warehouse):
    _attempt(warehouse)

    row = warehouse.get_records_by_table("quote_quality_events", opportunity_id="OPP-1")[0]

    assert row["runtime_build_sha"]
    assert row["runtime_build_sha"] != ""


def test_coverage_counts_required_attempts_not_stored_quotes():
    events = [
        {"required_for_economics": 1, "accepted": 1},
        {"required_for_economics": 1, "accepted": 0},
        {"required_for_economics": 1, "accepted": 0},
        {"required_for_economics": 1, "accepted": 1},
        # Not required: must not flatter the ratio.
        {"required_for_economics": 0, "accepted": 0},
    ]

    coverage = coverage_from_events(events)

    assert coverage["required"] == 4
    assert coverage["accepted"] == 2
    assert coverage["coverage_pct"] == pytest.approx(50.0)


def test_coverage_is_unknown_without_any_required_attempt():
    coverage = coverage_from_events([])

    # No attempts is not 100% coverage.
    assert coverage["coverage_pct"] is None
    assert coverage["required"] == 0


def test_all_rejected_is_zero_coverage_not_perfect_coverage():
    events = [
        {"required_for_economics": 1, "accepted": 0},
        {"required_for_economics": 1, "accepted": 0},
    ]

    coverage = coverage_from_events(events)

    assert coverage["coverage_pct"] == pytest.approx(0.0)


def test_gate_uses_attempt_based_coverage(warehouse):
    from study.snapback_forward_gate import build_gate_inputs

    records = {
        "outcomes": [],
        "paper_positions": [],
        "daily_mtm": [],
        # Two persisted option quotes, both good — the old ratio would say 100%.
        "option_quotes": [
            {"bid": 10.0, "ask": 11.0, "is_stale": 0},
            {"bid": 12.0, "ask": 13.0, "is_stale": 0},
        ],
        # But four required observations were attempted and two were refused.
        "quote_quality_events": [
            {"required_for_economics": 1, "accepted": 1},
            {"required_for_economics": 1, "accepted": 1},
            {"required_for_economics": 1, "accepted": 0},
            {"required_for_economics": 1, "accepted": 0},
        ],
    }

    inputs = build_gate_inputs(records=records)

    assert inputs["quote_coverage_pct"] == pytest.approx(50.0)
