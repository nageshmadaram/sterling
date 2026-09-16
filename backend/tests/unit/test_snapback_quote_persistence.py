"""The stored quote must be the decision that was actually made.

A row written with is_stale=False beside a decision that said "stale" is worse than
no row: it is evidence that disagrees with the runtime.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.intraday_models import DepthLevel, RawQuoteEvent
from app.services.snapback_market_data import evaluate_quote_quality, is_stale_decision
from app.services.snapback_observation_warehouse import (
    EvidenceIntegrityError,
    SnapbackObservationWarehouse,
)

NOW = 1_800_000_000_000


@pytest.fixture
def warehouse():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
        path = fh.name
    wh = SnapbackObservationWarehouse(db_path=path)
    yield wh
    if os.path.exists(path):
        os.remove(path)


def _event(ts=NOW, bid=100.0, ask=102.0):
    return RawQuoteEvent(
        contract_id="NIFTY26OCT25000PE", exchange_timestamp_ms=ts, received_at_ms=ts,
        best_bid=bid, best_ask=ask, bid_quantity=500, ask_quantity=500,
        bid_depth=(DepthLevel(bid, 500),), ask_depth=(DepthLevel(ask, 500),),
    )


def _attempt(wh, event_id, **over):
    payload = dict(
        event_id=event_id, opportunity_id="OPP-1", phase="ENTRY", leg="OPTION",
        contract_id="NIFTY26OCT25000PE", required_for_economics=1, quote_present=1,
        bid=100.0, ask=102.0, age_ms=10, accepted=1, reason_codes="",
    )
    payload.update(over)
    wh.record_quote_quality_event(**payload)


def test_a_stale_decision_is_stored_as_stale():
    decision = evaluate_quote_quality(_event(ts=NOW - 30_000), SnapbackConfig(), now_ms=NOW)

    # The stored flag is derived from the decision, never hard-coded.
    assert is_stale_decision(decision) is True


def test_a_crossed_decision_is_not_stored_as_stale():
    decision = evaluate_quote_quality(_event(bid=102.0, ask=100.0), SnapbackConfig(), now_ms=NOW)

    assert is_stale_decision(decision) is False
    assert "crossed_book" in decision.reason_codes


def test_no_production_write_hardcodes_the_stale_flag():
    import inspect

    from app.services import snapback_prospective_collector as collector

    source = inspect.getsource(collector)

    assert "is_stale=False" not in source.replace(" ", "")
    assert "is_stale=0," not in source.replace(" ", "")


def test_an_identical_event_replay_is_a_no_op(warehouse):
    _attempt(warehouse, "QE-1")
    _attempt(warehouse, "QE-1")

    rows = warehouse.get_records_by_table("quote_quality_events", opportunity_id="OPP-1")
    assert len(rows) == 1


def test_a_changed_payload_under_the_same_id_is_an_integrity_violation(warehouse):
    _attempt(warehouse, "QE-1", bid=100.0)

    with pytest.raises(EvidenceIntegrityError):
        _attempt(warehouse, "QE-1", bid=55.0)

    rows = warehouse.get_records_by_table("quote_quality_events", opportunity_id="OPP-1")
    assert len(rows) == 1
    assert float(rows[0]["bid"]) == pytest.approx(100.0)


def test_quote_events_record_the_execution_view(warehouse):
    warehouse.record_quote_quality_event(
        event_id="QE-2", opportunity_id="OPP-1", phase="ENTRY", leg="OPTION",
        contract_id="NIFTY26OCT25000PE", required_for_economics=1, quote_present=1,
        bid=100.0, ask=102.0, age_ms=10, accepted=0,
        reason_codes="insufficient_visible_depth",
        required_quantity=850, visible_quantity=300, raw_vwap=None,
        execution_price=None, spread_pct=1.98,
    )

    row = warehouse.get_records_by_table("quote_quality_events", opportunity_id="OPP-1")[0]

    assert row["required_quantity"] == 850
    assert row["visible_quantity"] == 300
    assert row["spread_pct"] == pytest.approx(1.98)
    assert "insufficient_visible_depth" in row["reason_codes"]
