"""Post-merge acceptance hardening: findings and vendor probes stay separate."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from study.snapback_live_evidence_acceptance import _roundtrip_root, _vendor_probe


@dataclass(frozen=True)
class _Contract:
    instrument_token: int
    tradingsymbol: str
    strike: float
    expiry: str


class _Universe:
    def __init__(self, contracts):
        self.contracts = tuple(contracts)


class _Selection:
    def __init__(self, listed, strike, token=None):
        self.listed = listed
        self.selected_strike = strike
        self.selected_instrument_token = token


def test_listed_selection_is_the_vendor_probe_without_substitution():
    selection = _Selection("LISTED", 24500.0, token=101)
    universe = _Universe([_Contract(101, "NIFTY24500PE", 24500.0, "2026-10-29")])

    token, detail = _vendor_probe(selection, universe, "2026-10-29")

    assert token == 101
    assert detail["source"] == "FROZEN_THEORETICAL_SELECTION"
    assert detail["not_strategy_substitute"] is False


def test_not_listed_remains_a_finding_but_gets_a_real_schema_probe():
    selection = _Selection("NOT_LISTED", 24525.0)
    universe = _Universe([
        _Contract(201, "NIFTY24500PE", 24500.0, "2026-10-29"),
        _Contract(202, "NIFTY24550PE", 24550.0, "2026-10-29"),
    ])

    token, detail = _vendor_probe(selection, universe, "2026-10-29")

    assert token == 201
    assert detail["source"] == "LISTED_VENDOR_SCHEMA_PROBE"
    assert detail["theoretical_target_listedness"] == "NOT_LISTED"
    assert detail["computed_strike"] == 24525.0
    assert detail["not_strategy_substitute"] is True


def test_schema_probe_prefers_the_same_expiry_before_nearest_other_expiry():
    selection = _Selection("NOT_LISTED", 24500.0)
    universe = _Universe([
        _Contract(301, "OTHER", 24500.0, "2026-11-26"),
        _Contract(302, "SAME", 24400.0, "2026-10-29"),
    ])

    token, detail = _vendor_probe(selection, universe, "2026-10-29")

    assert token == 302
    assert detail["expiry"] == "2026-10-29"


def test_acceptance_roundtrip_store_is_unique_per_invocation(tmp_path):
    first = _roundtrip_root(tmp_path)
    second = _roundtrip_root(tmp_path)

    assert first != second
    assert first.parent == Path(tmp_path) / "roundtrip"
    assert second.parent == Path(tmp_path) / "roundtrip"
