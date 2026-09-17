"""The hedge is held to the option's standard, not to the standard of whatever
data happened to be available.

The quarantined replay substituted spot for a missing futures quote and defaulted
the NIFTY lot size to 50 across a window in which the real lot size changed
repeatedly. Both produced hedge P&L that looked like a measurement. These tests
pin the refusals that make that impossible here.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.services.snapback_hedge_evidence import (
    HEDGE_SELECTION_UNKNOWN,
    HEDGE_SELECTOR_VERSION,
    HedgeEvidenceError,
    build_hedge_selection_record,
    hedge_evidence_is_authoritative,
    hedge_not_required_record,
)

NOW = datetime(2026, 9, 17, 9, 20, tzinfo=timezone.utc)
OPT_EXPIRY = date(2026, 10, 29)
PROVENANCE = dict(
    runtime_sha="9a706f584",
    strategy_sha="5a1354202e2c960c66b7003fce9cb80abd152008",
    config_hash="6ecbeb53e9768a91",
    rule_hash="e03ddf75f29463a8",
)


def _fut(expiry, *, token=5001, lot=75, name="NIFTY", symbol=None):
    return {
        "instrument_token": token,
        "tradingsymbol": symbol or f"NIFTY{expiry:%y%b}FUT".upper(),
        "instrument_type": "FUT",
        "name": name,
        "expiry": expiry,
        "lot_size": lot,
        "exchange": "NFO",
    }


def _build(chain, **over):
    kw = dict(
        opportunity_id="OPP-1", underlying="NIFTY", option_expiry=OPT_EXPIRY,
        chain=chain, selected_at=NOW, **PROVENANCE,
    )
    kw.update(over)
    return build_hedge_selection_record(**kw)


# ─── the hedge must outlive the option ───────────────────────────────────────

def test_the_nearest_covering_future_is_selected():
    chain = [
        _fut(date(2026, 9, 24), token=1),
        _fut(date(2026, 10, 29), token=2),
        _fut(date(2026, 11, 26), token=3),
    ]

    record = _build(chain)

    assert record.instrument_token == 2
    assert record.expiry == "2026-10-29"
    assert record.is_authoritative is True


def test_a_future_expiring_before_the_option_is_never_chosen():
    """It would leave the position unhedged at futures expiry."""
    record = _build([_fut(date(2026, 9, 24), token=1)])

    assert record.hedge_reason == HEDGE_SELECTION_UNKNOWN
    assert record.is_authoritative is False


def test_a_record_whose_hedge_expires_first_is_not_authoritative():
    import dataclasses

    record = _build([_fut(date(2026, 11, 26), token=3)])
    forged = dataclasses.replace(record, expiry="2026-09-24")

    assert hedge_evidence_is_authoritative(forged) is False


# ─── unknown is recorded, never dropped ──────────────────────────────────────

def test_an_empty_chain_is_unknown_not_an_exception():
    record = _build([])

    assert record.hedge_required is True
    assert record.hedge_reason == HEDGE_SELECTION_UNKNOWN
    assert record.instrument_token is None
    assert record.is_authoritative is False


def test_unknown_still_carries_full_provenance():
    record = _build([])

    assert record.runtime_sha == PROVENANCE["runtime_sha"]
    assert record.option_expiry == "2026-10-29"
    assert record.selector_version == HEDGE_SELECTOR_VERSION


def test_a_contract_with_no_lot_size_is_not_usable():
    record = _build([_fut(date(2026, 11, 26), lot=0)])

    assert record.hedge_reason == HEDGE_SELECTION_UNKNOWN


def test_a_zero_lot_size_never_falls_back_to_a_default():
    """The replay defaulted NIFTY to 50 and mis-sized every hedge."""
    record = _build([_fut(date(2026, 11, 26), lot=0)])

    assert record.lot_size is None
    assert record.lot_size != 50


def test_a_contract_with_no_token_is_not_usable():
    record = _build([_fut(date(2026, 11, 26), token=0)])

    assert record.hedge_reason == HEDGE_SELECTION_UNKNOWN


def test_another_underlyings_future_is_not_borrowed():
    record = _build([_fut(date(2026, 11, 26), name="BANKNIFTY", token=9)])

    assert record.hedge_reason == HEDGE_SELECTION_UNKNOWN


# ─── "not required" is a decision, not an absence ────────────────────────────

def test_a_waiver_needs_an_accepted_reason():
    record = hedge_not_required_record(
        opportunity_id="OPP-1", underlying="NIFTY", reason="FROZEN_RULE_UNHEDGED",
        selected_at=NOW, **PROVENANCE,
    )

    assert record.hedge_required is False
    assert record.is_authoritative is True


def test_free_text_cannot_waive_the_hedge():
    with pytest.raises(HedgeEvidenceError):
        hedge_not_required_record(
            opportunity_id="OPP-1", underlying="NIFTY", reason="not needed today",
            selected_at=NOW, **PROVENANCE,
        )


def test_a_waiver_with_an_unaccepted_reason_is_not_authoritative():
    import dataclasses

    record = hedge_not_required_record(
        opportunity_id="OPP-1", underlying="NIFTY", reason="FROZEN_RULE_UNHEDGED",
        selected_at=NOW, **PROVENANCE,
    )
    forged = dataclasses.replace(record, hedge_reason="because")

    assert hedge_evidence_is_authoritative(forged) is False


def test_missing_and_waived_are_distinguishable():
    missing = _build([])
    waived = hedge_not_required_record(
        opportunity_id="OPP-1", underlying="NIFTY", reason="FROZEN_RULE_UNHEDGED",
        selected_at=NOW, **PROVENANCE,
    )

    # Both have no contract; only one of them is a decision.
    assert missing.instrument_token is waived.instrument_token is None
    assert missing.hedge_required is True and waived.hedge_required is False
    assert missing.is_authoritative is False and waived.is_authoritative is True


# ─── provenance ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field", ["runtime_sha", "strategy_sha", "config_hash", "rule_hash"])
def test_blank_provenance_is_refused(field):
    with pytest.raises(HedgeEvidenceError) as excinfo:
        _build([_fut(date(2026, 11, 26))], **{field: ""})

    assert field in str(excinfo.value)


def test_the_record_serialises_whole():
    blob = _build([_fut(date(2026, 11, 26))]).as_dict()

    for key in ("hedge_required", "hedge_reason", "instrument_token", "lot_size",
                "option_expiry", "selector_version", "runtime_sha", "selected_at"):
        assert key in blob


def test_production_selector_is_the_only_one():
    """A recorder-specific hedge selector would drift, exactly as the replay did."""
    import app.services.snapback_hedge_evidence as mod

    source = open(mod.__file__, encoding="utf-8").read()

    assert "select_hedge_future" in source
    for forbidden in ("def _select_hedge", "def select_hedge_future(", "sorted(candidates"):
        assert forbidden not in source
