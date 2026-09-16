"""The selector's menu, recorded so the selection can be audited later.

Storing only the neighbourhood of the chosen contract lets the selector define
the evidence that judges it. These tests pin the opposite: the whole eligible
listed universe, from a real master, hashed so a later edit is detectable.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.services.snapback_candidate_universe import (
    SOURCE_INSTRUMENT_MASTER,
    CandidateUniverseError,
    build_candidate_universe,
    universe_hash,
)

AS_OF = date(2026, 9, 17)
NEAR = date(2026, 10, 29)      # 42 DTE — inside 40-60
FAR = date(2026, 11, 26)       # 70 DTE — outside
SOON = date(2026, 9, 24)       # 7 DTE — outside


def _row(strike, *, expiry=NEAR, token=None, typ="PE", name="NIFTY", **over):
    row = {
        "instrument_token": token if token is not None else int(strike),
        "tradingsymbol": f"NIFTY{int(strike) if strike else 0}{typ}",
        "exchange": "NFO",
        "segment": "NFO-OPT",
        "instrument_type": typ,
        "name": name,
        "expiry": expiry,
        "strike": float(strike),
        "lot_size": 75,
        "tick_size": 0.05,
    }
    row.update(over)
    return row


def _rows(strikes=(24800, 24900, 25000, 25100), **kw):
    return [_row(s, **kw) for s in strikes]


def _build(instruments, **over):
    kw = dict(
        opportunity_id="OPP-1",
        underlying="NIFTY",
        option_type="PE",
        as_of=AS_OF,
        instruments=instruments,
        instrument_master_date=AS_OF,
        min_dte=40,
        max_dte=60,
    )
    kw.update(over)
    return build_candidate_universe(**kw)


def test_the_whole_eligible_universe_is_kept_not_a_neighbourhood():
    universe = _build(_rows(range(24000, 26050, 50)))

    # 41 strikes span the grid; none is dropped for being far from any target.
    assert len(universe.contracts) == 41
    assert universe.strikes[0] == 24000.0
    assert universe.strikes[-1] == 26000.0


def test_contracts_outside_the_dte_window_are_excluded():
    rows = _rows(expiry=NEAR) + _rows(expiry=FAR) + _rows(expiry=SOON)

    universe = _build(rows)

    assert universe.expiries == (NEAR.isoformat(),)


def test_the_other_option_side_is_excluded():
    universe = _build(_rows() + _rows(typ="CE"))

    assert {c.instrument_type for c in universe.contracts} == {"PE"}


def test_another_underlying_is_excluded():
    universe = _build(_rows() + _rows(name="BANKNIFTY"))

    assert len(universe.contracts) == 4


def test_exchanges_can_be_restricted():
    rows = _rows() + _rows(strikes=(25200,), exchange="BFO", segment="BFO-OPT")

    universe = _build(rows, exchanges=["NFO"])

    assert {c.exchange for c in universe.contracts} == {"NFO"}


def test_an_incomplete_row_is_dropped_not_repaired():
    rows = _rows() + [
        {**_row(25200), "expiry": None},
        {**_row(25300), "strike": None},
        {**_row(25400), "lot_size": None},
        {**_row(25500), "instrument_token": None},
    ]

    universe = _build(rows)

    # A contract we cannot fully describe cannot prove the menu was right.
    assert len(universe.contracts) == 4


def test_an_unparseable_expiry_is_dropped_not_guessed():
    universe = _build(_rows() + [_row(25200, expiry="last thursday")])

    assert len(universe.contracts) == 4


def test_a_string_expiry_is_accepted_as_published():
    universe = _build(_rows(strikes=(25000,), expiry="2026-10-29"))

    assert universe.expiries == ("2026-10-29",)


def test_a_datetime_expiry_is_accepted():
    universe = _build(_rows(strikes=(25000,), expiry=datetime(2026, 10, 29, 15, 30)))

    assert universe.expiries == ("2026-10-29",)


def test_duplicate_tokens_are_collapsed():
    universe = _build(_rows() + _rows())

    assert len(universe.contracts) == 4


def test_an_empty_universe_raises_rather_than_reading_as_clean():
    with pytest.raises(CandidateUniverseError) as excinfo:
        _build(_rows(expiry=FAR))

    assert "inconclusive" in str(excinfo.value).lower()


def test_a_master_with_no_date_is_refused():
    with pytest.raises(CandidateUniverseError):
        _build(_rows(), instrument_master_date=None)


def test_an_inverted_dte_window_is_refused():
    with pytest.raises(CandidateUniverseError):
        _build(_rows(), min_dte=60, max_dte=40)


def test_a_bad_option_type_is_refused():
    with pytest.raises(CandidateUniverseError):
        _build(_rows(), option_type="FUT")


def test_provenance_is_recorded_and_validated():
    universe = _build(_rows())

    assert universe.source == SOURCE_INSTRUMENT_MASTER
    assert universe.evidence_class == "OBSERVED_MARKET"

    with pytest.raises(Exception):
        _build(_rows(), evidence_class="REAL")


# ─── the hash ────────────────────────────────────────────────────────────────

def test_the_hash_is_independent_of_row_order():
    rows = _rows(range(24000, 25050, 50))

    a = _build(rows)
    b = _build(list(reversed(rows)))

    assert a.candidate_universe_hash == b.candidate_universe_hash


def test_a_removed_contract_changes_the_hash():
    full = _build(_rows((24800, 24900, 25000, 25100)))
    trimmed = _build(_rows((24800, 24900, 25000)))

    assert full.candidate_universe_hash != trimmed.candidate_universe_hash


def test_an_edited_lot_size_changes_the_hash():
    a = _build(_rows())
    b = _build([_row(s, lot_size=50) for s in (24800, 24900, 25000, 25100)])

    assert a.candidate_universe_hash != b.candidate_universe_hash


def test_the_eligibility_rule_is_inside_the_hash():
    """Same contracts, different rule, is not the same evidence."""
    rows = _rows()

    a = _build(rows, min_dte=40, max_dte=60)
    b = _build(rows, min_dte=30, max_dte=60)

    assert a.candidate_universe_hash != b.candidate_universe_hash


def test_a_tampered_universe_fails_verification():
    universe = _build(_rows())

    assert universe.verify_hash() is True

    forged = universe.__class__(**{**universe.as_dict_fields(), "contracts": universe.contracts[:2]}) \
        if hasattr(universe, "as_dict_fields") else None
    if forged is None:
        import dataclasses
        forged = dataclasses.replace(universe, contracts=universe.contracts[:2])

    assert forged.verify_hash() is False


def test_hash_helper_matches_the_stored_hash():
    universe = _build(_rows())

    recomputed = universe_hash(
        universe.contracts,
        underlying=universe.underlying,
        option_type=universe.option_type,
        as_of=universe.as_of,
        min_dte=universe.min_dte,
        max_dte=universe.max_dte,
    )

    assert recomputed == universe.candidate_universe_hash


# ─── the question this exists to answer ──────────────────────────────────────

def test_whether_a_computed_strike_was_actually_listed():
    """``pick_for`` computes a strike and assumes the step grid means listed."""
    universe = _build(_rows((24800, 24900, 25000, 25100)))

    assert universe.contains_strike(25000) is True
    assert universe.contains_strike(25050) is False
    assert universe.contains_strike(25000, expiry=NEAR.isoformat()) is True
    assert universe.contains_strike(25000, expiry=FAR.isoformat()) is False
