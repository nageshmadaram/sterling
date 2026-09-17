"""SENSEX must not open a position Sterling cannot observe afterwards.

Day-T identity capture is venue-correct: SENSEX cash quotes on BSE and its
options trade on BFO. The post-entry paths are not. `snapback.py` rebuilds
`NFO:{symbol}` and `NSE:{symbol}` from the canonical name in the T+1 futures
quote, the intraday option/futures/spot quotes, and the exit path — eight sites
at the time of writing.

So a SENSEX position could enter correctly through BFO and then have Sterling
ask NFO for its mark-to-market, its risk checks and its exit. An unobservable
open position is worse than a missed trade, so entry is refused until the
lifecycle is venue-aware.

These tests pin both halves: the refusal works, and the reason for the refusal
still exists. If someone makes the lifecycle venue-aware, the second half fails
and tells them to lift the containment rather than leaving it in place forever.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.services.snapback_capacity import (
    _LIFECYCLE_UNSUPPORTED_UNDERLYINGS,
    ObservedHedgeMargin,
    evaluate_capacity,
)


def _capacity(underlying: str, **over):
    kw = dict(
        capital=5_000_000.0,
        reserved_margin=0.0,
        option_premium_cash=50_000.0,
        hedge_margin=ObservedHedgeMargin(100_000.0),
        fee_reserve=1_000.0,
        open_positions=0,
        max_open_positions=5,
        underlying=underlying,
        open_underlyings=set(),
    )
    kw.update(over)
    return evaluate_capacity(**kw)


# ─── the containment works ───────────────────────────────────────────────────

def test_sensex_is_refused_before_a_position_exists():
    decision = _capacity("SENSEX")

    assert decision.allowed is False
    assert decision.status == "INCONCLUSIVE_LIFECYCLE_VENUE"


def test_the_refusal_is_case_insensitive():
    assert _capacity("sensex").allowed is False


def test_nifty_is_unaffected():
    decision = _capacity("NIFTY")

    assert decision.status != "INCONCLUSIVE_LIFECYCLE_VENUE"


def test_the_refusal_precedes_every_funding_question():
    """Even a trivially fundable SENSEX entry is refused on venue grounds."""
    decision = _capacity("SENSEX", capital=10_000_000_000.0, option_premium_cash=1.0)

    assert decision.status == "INCONCLUSIVE_LIFECYCLE_VENUE"


def test_an_unfundable_sensex_entry_is_still_refused_for_venue():
    """The venue refusal is not masked by a capital refusal, or vice versa."""
    decision = _capacity("SENSEX", capital=0.0)

    assert decision.allowed is False


# ─── the reason for the containment still exists ─────────────────────────────

def _snapback_source() -> str:
    root = pathlib.Path(__file__).resolve().parents[2]
    return (root / "app" / "services" / "snapback.py").read_text(encoding="utf-8")


def test_the_post_entry_lifecycle_still_reconstructs_nfo_and_nse():
    """The containment exists because of these. When they go, lift it.

    This test is deliberately the inverse of the usual shape: it asserts a
    defect is still present. A containment kept after its cause is fixed is a
    silently narrowed strategy universe, which is its own kind of dishonesty.
    """
    source = _snapback_source()

    reconstructions = re.findall(r'f"(?:NFO|NSE):\{', source)

    assert reconstructions, (
        "snapback.py no longer rebuilds NFO:/NSE: from canonical names. If the "
        "post-entry lifecycle is now venue-aware, remove SENSEX from "
        "_LIFECYCLE_UNSUPPORTED_UNDERLYINGS and delete this test."
    )


def test_sensex_is_the_declared_unsupported_underlying():
    assert "SENSEX" in _LIFECYCLE_UNSUPPORTED_UNDERLYINGS


def test_the_containment_names_why_it_exists():
    """A future reader must not have to guess whether this is arbitrary."""
    import app.services.snapback_capacity as mod

    source = open(mod.__file__, encoding="utf-8").read()
    block = source[source.index("_LIFECYCLE_UNSUPPORTED_UNDERLYINGS") - 1200:
                   source.index("_LIFECYCLE_UNSUPPORTED_UNDERLYINGS") + 200]

    assert "BFO" in source or "BFO" in block
