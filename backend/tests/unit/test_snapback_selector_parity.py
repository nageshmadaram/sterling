"""The shared selector must reproduce frozen production exactly.

Extracting a selector is refactoring. It becomes a strategy change the moment one
fixture moves, and a strategy change during a frozen prospective experiment
invalidates the sample. The fixtures in
``tests/fixtures/snapback_selector_parity.json`` were captured from
``snapback.contracts.pick_for`` at runtime-1.5 behaviour across 840 combinations
of symbol, spot, IV, DTE and option side. They are the contract.

If one of these fails, the answer is to revert the selector, not to regenerate
the fixtures.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from app.engines.option_contracts import spec_for
from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.contracts import pick_for
from app.engines.snapback.models import SnapbackSignal
from app.services.snapback_contract_selection import (
    LISTED_NO,
    LISTED_UNKNOWN,
    LISTED_YES,
    SELECTOR_VERSION,
    SelectionError,
    SelectionInputs,
    select_snapback_contract,
)

FIXTURES = json.loads(
    (pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "snapback_selector_parity.json").read_text()
)
ROWS = FIXTURES["rows"]


def _inputs(row):
    spec = spec_for(row["symbol"])
    return SelectionInputs(
        opportunity_id="OPP-PARITY",
        underlying=row["symbol"],
        option_type=row["option_type"],
        spot=row["spot"],
        assumed_iv=row["iv"],
        target_delta=FIXTURES["config_target_delta"],
        dte_days=row["dte"],
        strike_step=spec.strike_step,
        expiry="2026-10-29",
    )


def test_the_fixture_set_is_intact():
    assert FIXTURES["count"] == 840
    assert len(ROWS) == 840
    assert FIXTURES["none_count"] == 0


@pytest.mark.parametrize("row", ROWS, ids=lambda r: f"{r['symbol']}-{r['option_type']}-{r['iv']}-{r['dte']}-{r['spot']}")
def test_shared_selector_reproduces_frozen_production(row):
    result = select_snapback_contract(_inputs(row))

    assert result.selected_strike == row["pick"]["strike"]
    assert result.computed_delta == pytest.approx(row["pick"]["delta"], abs=1e-9)


def test_live_production_still_matches_the_fixtures():
    """Guards the other direction: pick_for itself must not have moved."""
    cfg = SnapbackConfig()

    for row in ROWS:
        signal = SnapbackSignal(
            symbol=row["symbol"], side="BUY", direction="MEAN_REVERT",
            option_type=row["option_type"], timestamp_ms=1758067200000,
            entry=row["spot"], mean_target=row["spot"] * 0.98, stretch=2.1,
            atr=row["spot"] * 0.012, realized_vol=row["iv"] * 0.9,
            assumed_iv=row["iv"], level=row["spot"] * 1.01, strength="STRONG",
        )
        pick = pick_for(signal, cfg, dte=row["dte"], expiry="2026-10-29",
                        spot=row["spot"], iv=row["iv"])

        assert pick is not None
        assert pick.strike == row["pick"]["strike"], f"pick_for moved for {row['symbol']}"


def test_candidates_cannot_change_the_chosen_strike():
    """The recorded discrepancy, pinned as behaviour.

    Production selects in closed form and never consults a chain. Passing a
    candidate universe must buy a listedness verdict and nothing else, or the
    extraction has silently become a strategy change.
    """
    from app.services.snapback_candidate_universe import CandidateContract

    row = ROWS[0]
    without = select_snapback_contract(_inputs(row))

    # A universe that omits the chosen strike entirely.
    elsewhere = [
        CandidateContract(
            instrument_token=1, tradingsymbol="X", exchange="NFO", segment="NFO-OPT",
            instrument_type=row["option_type"], expiry="2026-10-29",
            strike=without.selected_strike + 12345.0, lot_size=75, tick_size=0.05,
        )
    ]
    with_candidates = select_snapback_contract(_inputs(row), elsewhere)

    assert with_candidates.selected_strike == without.selected_strike
    assert with_candidates.listed == LISTED_NO


def test_a_listed_strike_is_reported_with_its_real_identity():
    from app.services.snapback_candidate_universe import CandidateContract

    row = ROWS[0]
    computed = select_snapback_contract(_inputs(row))
    universe = [
        CandidateContract(
            instrument_token=987654, tradingsymbol="NIFTY26OCTPE", exchange="NFO",
            segment="NFO-OPT", instrument_type=row["option_type"], expiry="2026-10-29",
            strike=computed.selected_strike, lot_size=75, tick_size=0.05,
        )
    ]

    result = select_snapback_contract(_inputs(row), universe)

    assert result.listed == LISTED_YES
    assert result.selected_instrument_token == 987654
    assert result.selected_lot_size == 75


def test_no_universe_means_unknown_not_listed():
    result = select_snapback_contract(_inputs(ROWS[0]))

    # Absence of the question is not a yes.
    assert result.listed == LISTED_UNKNOWN


def test_a_wrong_expiry_does_not_count_as_listed():
    from app.services.snapback_candidate_universe import CandidateContract

    row = ROWS[0]
    computed = select_snapback_contract(_inputs(row))
    universe = [
        CandidateContract(
            instrument_token=1, tradingsymbol="X", exchange="NFO", segment="NFO-OPT",
            instrument_type=row["option_type"], expiry="2026-11-26",
            strike=computed.selected_strike, lot_size=75, tick_size=0.05,
        )
    ]

    assert select_snapback_contract(_inputs(row), universe).listed == LISTED_NO


def test_the_selector_version_travels_with_the_result():
    assert select_snapback_contract(_inputs(ROWS[0])).selector_version == SELECTOR_VERSION


def test_the_universe_hash_is_carried_through():
    inputs = _inputs(ROWS[0])
    inputs = SelectionInputs(**{**inputs.__dict__, "candidate_universe_hash": "deadbeef"})

    assert select_snapback_contract(inputs).candidate_universe_hash == "deadbeef"


# ─── no economic default survives ────────────────────────────────────────────

@pytest.mark.parametrize("field,value", [
    ("spot", 0.0), ("spot", -1.0),
    ("assumed_iv", 0.0), ("assumed_iv", None),
    ("target_delta", 0.0), ("target_delta", 1.0),
    ("dte_days", 0), ("dte_days", -5),
    ("strike_step", 0.0),
    ("option_type", "FUT"),
])
def test_a_missing_or_absurd_input_is_refused(field, value):
    base = _inputs(ROWS[0]).__dict__.copy()
    base[field] = value

    with pytest.raises(SelectionError):
        SelectionInputs(**base)
