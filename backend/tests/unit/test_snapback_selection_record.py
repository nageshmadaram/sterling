"""Two truths, kept apart, and what each one authorizes.

The record must never let "Snapback computed this contract" be read as "this
contract existed". Those are separate facts, and only the second one can
authorize an entry.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.snapback_candidate_universe import CandidateContract
from app.services.snapback_contract_selection import (
    LISTED_NO,
    LISTED_UNKNOWN,
    LISTED_YES,
    SelectionInputs,
    select_snapback_contract,
)
from app.services.snapback_selection_record import (
    SELECTION_REALITY_FAILURE,
    SELECTION_REALITY_INCONCLUSIVE,
    SelectionRecordError,
    build_selection_record,
    selection_block_reason,
    selection_is_authoritative,
)

NOW = datetime(2026, 9, 17, 9, 20, tzinfo=timezone.utc)
PROVENANCE = dict(
    runtime_sha="9a706f584",
    strategy_sha="5a1354202e2c960c66b7003fce9cb80abd152008",
    config_hash="6ecbeb53e9768a91",
    rule_hash="e03ddf75f29463a8",
)


def _inputs(**over):
    kw = dict(
        opportunity_id="OPP-1", underlying="NIFTY", option_type="PE",
        spot=25137.0, assumed_iv=0.22, target_delta=0.70, dte_days=42,
        strike_step=50.0, expiry="2026-10-29", candidate_universe_hash="cafe1234",
    )
    kw.update(over)
    return SelectionInputs(**kw)


def _contract(strike, **over):
    kw = dict(
        instrument_token=987654, tradingsymbol="NIFTY26OCT25000PE", exchange="NFO",
        segment="NFO-OPT", instrument_type="PE", expiry="2026-10-29",
        strike=strike, lot_size=75, tick_size=0.05,
    )
    kw.update(over)
    return CandidateContract(**kw)


def _record(result, **over):
    kw = dict(min_dte=40, max_dte=60, created_at=NOW, **PROVENANCE)
    kw.update(over)
    return build_selection_record(result, **kw)


def _computed_strike():
    return select_snapback_contract(_inputs()).selected_strike


# ─── the two truths stay separate ────────────────────────────────────────────

def test_computed_and_matched_are_distinct_fields():
    strike = _computed_strike()
    result = select_snapback_contract(_inputs(), [_contract(strike)])

    record = _record(result)

    assert record.computed_strike == strike
    assert record.matched_instrument_token == 987654
    assert record.listed_status == LISTED_YES


def test_a_computed_contract_that_does_not_exist_is_still_recorded():
    """The rule's choice is evidence even when the exchange never listed it."""
    result = select_snapback_contract(_inputs(), [_contract(_computed_strike() + 500.0)])

    record = _record(result)

    assert record.computed_strike == _computed_strike()
    assert record.listed_status == LISTED_NO
    assert record.matched_instrument_token is None


# ─── authority ───────────────────────────────────────────────────────────────

def test_only_listed_is_authoritative():
    assert selection_is_authoritative(LISTED_YES) is True
    assert selection_is_authoritative(LISTED_NO) is False
    assert selection_is_authoritative(LISTED_UNKNOWN) is False


def test_the_two_failures_block_equally_but_read_differently():
    assert selection_block_reason(LISTED_YES) is None
    assert selection_block_reason(LISTED_NO) == SELECTION_REALITY_FAILURE
    assert selection_block_reason(LISTED_UNKNOWN) == SELECTION_REALITY_INCONCLUSIVE


def test_an_unknown_status_raises_rather_than_passing():
    with pytest.raises(SelectionRecordError):
        selection_block_reason("PROBABLY")


def test_not_listed_blocks_entry():
    record = _record(select_snapback_contract(_inputs(), [_contract(_computed_strike() + 500.0)]))

    assert record.is_authoritative is False
    assert record.block_reason == SELECTION_REALITY_FAILURE


def test_no_universe_is_inconclusive_not_permissive():
    record = _record(select_snapback_contract(_inputs(candidate_universe_hash=None)))

    assert record.listed_status == LISTED_UNKNOWN
    assert record.is_authoritative is False
    assert record.block_reason == SELECTION_REALITY_INCONCLUSIVE


def test_listed_is_authoritative():
    record = _record(select_snapback_contract(_inputs(), [_contract(_computed_strike())]))

    assert record.is_authoritative is True
    assert record.block_reason is None


# ─── no silent repair ────────────────────────────────────────────────────────

def test_nothing_snaps_to_the_nearest_listed_strike():
    """Repairing NOT_LISTED would change which contract Snapback buys."""
    computed = _computed_strike()
    near = _contract(computed + 50.0, instrument_token=111)

    result = select_snapback_contract(_inputs(), [near])
    record = _record(result)

    assert record.computed_strike == computed
    assert record.listed_status == LISTED_NO
    assert record.matched_instrument_token is None


# ─── the record cannot lie about itself ──────────────────────────────────────

@pytest.mark.parametrize("field", ["runtime_sha", "strategy_sha", "config_hash", "rule_hash"])
def test_blank_provenance_is_refused(field):
    result = select_snapback_contract(_inputs(), [_contract(_computed_strike())])

    with pytest.raises(SelectionRecordError) as excinfo:
        _record(result, **{field: ""})

    assert field in str(excinfo.value)


def test_listed_without_a_matched_instrument_is_refused():
    import dataclasses

    result = select_snapback_contract(_inputs(), [_contract(_computed_strike())])
    forged = dataclasses.replace(result, selected_instrument_token=None)

    with pytest.raises(SelectionRecordError):
        _record(forged)


def test_a_non_match_carrying_an_instrument_is_refused():
    import dataclasses

    result = select_snapback_contract(_inputs(), [_contract(_computed_strike() + 500.0)])
    forged = dataclasses.replace(result, selected_instrument_token=42)

    with pytest.raises(SelectionRecordError):
        _record(forged)


def test_a_verdict_without_the_universe_it_was_decided_against_is_refused():
    import dataclasses

    result = select_snapback_contract(_inputs(), [_contract(_computed_strike())])
    forged = dataclasses.replace(result, candidate_universe_hash=None)

    with pytest.raises(SelectionRecordError):
        _record(forged)


# ─── what a later audit needs ────────────────────────────────────────────────

def test_the_record_carries_everything_needed_to_replay_the_selection():
    result = select_snapback_contract(_inputs(), [_contract(_computed_strike())])

    record = _record(result, valuation_ts=NOW, instrument_master_snapshot_id="snap-2026-09-17T09:15")

    replayed = select_snapback_contract(
        SelectionInputs(
            opportunity_id=record.opportunity_id, underlying="NIFTY",
            option_type=record.option_type, spot=record.spot,
            assumed_iv=record.assumed_iv, target_delta=record.target_delta,
            dte_days=42, strike_step=50.0, expiry=record.computed_expiry,
            candidate_universe_hash=record.candidate_universe_hash,
        )
    )

    assert replayed.selected_strike == record.computed_strike
    assert replayed.computed_delta == record.computed_delta
    assert record.instrument_master_snapshot_id == "snap-2026-09-17T09:15"
    assert record.matched_tick_size == 0.05


def test_the_record_serialises_whole():
    record = _record(select_snapback_contract(_inputs(), [_contract(_computed_strike())]))

    blob = record.as_dict()

    for key in ("computed_strike", "listed_status", "candidate_universe_hash",
                "selector_version", "runtime_sha", "rule_hash", "created_at"):
        assert key in blob
