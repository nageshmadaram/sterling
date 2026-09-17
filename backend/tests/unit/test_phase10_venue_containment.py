"""The SENSEX/BFO containment, and the condition for lifting it.

SENSEX is addressed correctly as BSE/BFO on entry, but the later MTM and
intraday-risk path still rebuilds quote keys as ``f"NFO:{symbol}"``. Opening a
SENSEX position today would therefore create evidence Sterling cannot observe
for its own lifetime, so admission refuses it.

The containment must not be lifted before that reconstruction is gone. These
tests make the two facts move together: the count of remaining reconstruction
sites is pinned, and so is the containment. Removing the containment while
sites remain fails here, and so does fixing the sites without updating the
count — which is the prompt to re-check whether the containment can now go.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.snapback_capacity import _LIFECYCLE_UNSUPPORTED_UNDERLYINGS
from app.services.snapback_instrument_identity import (
    VALID_CASH_EXCHANGES,
    IdentityError,
    UnderlyingIdentity,
    identity_from_opportunity,
)

BACKEND = Path(__file__).resolve().parents[2]
LIFECYCLE_PATH = BACKEND / "app" / "services" / "snapback.py"

#: Quote keys rebuilt from a canonical name instead of the stored identity.
_RECONSTRUCTION = re.compile(r'f"(?:NFO|NSE|BFO|BSE):\{')

#: Sites remaining in the Snapback lifecycle today. Lower this as they are
#: fixed. It must reach 0 before the containment below may be lifted.
KNOWN_RECONSTRUCTION_SITES = 17


def _count_reconstructions(path: Path) -> int:
    return len(_RECONSTRUCTION.findall(path.read_text(encoding="utf-8")))


# ── the containment ───────────────────────────────────────────────────────


def test_sensex_is_still_contained():
    assert "SENSEX" in _LIFECYCLE_UNSUPPORTED_UNDERLYINGS


def test_the_containment_covers_only_what_it_must():
    """Containment is an operability constraint, not a universe change."""
    assert _LIFECYCLE_UNSUPPORTED_UNDERLYINGS == frozenset({"SENSEX"})


def test_the_lifecycle_still_rebuilds_quote_keys():
    """The reason the containment exists, measured rather than asserted."""
    found = _count_reconstructions(LIFECYCLE_PATH)
    assert found == KNOWN_RECONSTRUCTION_SITES, (
        f"{LIFECYCLE_PATH.name} now has {found} rebuilt quote keys, not "
        f"{KNOWN_RECONSTRUCTION_SITES}. If this went down, update the count and "
        "re-check whether the SENSEX containment can be lifted."
    )


def test_the_containment_may_not_be_lifted_while_sites_remain():
    """The two facts are tied together on purpose.

    Removing SENSEX from the unsupported set while the lifecycle still rebuilds
    NSE/NFO keys would let Sterling open a position on BFO and then mark it
    against an instrument that does not exist.
    """
    contained = "SENSEX" in _LIFECYCLE_UNSUPPORTED_UNDERLYINGS
    sites_remain = _count_reconstructions(LIFECYCLE_PATH) > 0
    assert contained or not sites_remain, (
        "SENSEX containment was lifted while the lifecycle still rebuilds "
        "exchange-prefixed quote keys"
    )


# ── stored identity ───────────────────────────────────────────────────────


def _opportunity(**over):
    row = {
        "symbol": "SENSEX",
        "cash_exchange": "BSE",
        "cash_tradingsymbol": "SENSEX",
        "cash_instrument_token": 265,
        "option_exchange": "BFO",
        "option_underlying_name": "SENSEX",
    }
    row.update(over)
    return row


def test_a_bse_underlying_keeps_its_bfo_option_venue():
    identity = identity_from_opportunity(_opportunity())
    assert identity.cash_exchange == "BSE"
    assert identity.option_exchange == "BFO"


def test_an_nse_underlying_keeps_nfo():
    identity = identity_from_opportunity(
        _opportunity(
            symbol="NIFTY",
            cash_exchange="NSE",
            cash_tradingsymbol="NIFTY 50",
            option_exchange="NFO",
            option_underlying_name="NIFTY",
        )
    )
    assert identity.option_exchange == "NFO"
    # The provider symbol is not the canonical name, which is the whole point.
    assert identity.cash_tradingsymbol == "NIFTY 50"


def test_only_the_two_known_cash_exchanges_are_valid():
    assert VALID_CASH_EXCHANGES == {"NSE", "BSE"}


@pytest.mark.parametrize(
    "broken",
    [
        {"cash_exchange": ""},
        {"cash_tradingsymbol": ""},
        {"cash_instrument_token": 0},
        {"cash_exchange": "MCX"},
    ],
)
def test_a_missing_or_unknown_identity_raises_rather_than_being_rebuilt(broken):
    with pytest.raises(IdentityError):
        identity_from_opportunity(_opportunity(**broken))


def test_identity_is_frozen_once_captured():
    identity = identity_from_opportunity(_opportunity())
    assert isinstance(identity, UnderlyingIdentity)
    with pytest.raises(Exception):
        identity.option_exchange = "NFO"  # type: ignore[misc]
