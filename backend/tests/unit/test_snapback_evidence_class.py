"""A number may only prove what its source can support.

The quarantined observed replay substituted modeled prices for missing quotes
while presenting itself as observed-market evidence. Nothing in the code stopped
that, because provenance was a filename convention rather than a value. These
tests pin the ordering and, more importantly, pin the absence of any path that
strengthens a row.
"""

from __future__ import annotations

import pytest

from app.services.snapback_evidence_class import (
    BROKER_EXECUTED,
    BROKER_SHADOW,
    EVIDENCE_CLASSES,
    MODELLED,
    OBSERVED_MARKET,
    EvidenceClassError,
    parse,
    rank,
    require,
    satisfies,
    weakest,
)


def test_the_ordering_is_fixed():
    assert EVIDENCE_CLASSES == (MODELLED, OBSERVED_MARKET, BROKER_SHADOW, BROKER_EXECUTED)
    assert [rank(c) for c in EVIDENCE_CLASSES] == [0, 1, 2, 3]


def test_stronger_evidence_supports_a_weaker_claim():
    assert satisfies(BROKER_EXECUTED, MODELLED)
    assert satisfies(BROKER_EXECUTED, OBSERVED_MARKET)
    assert satisfies(BROKER_SHADOW, OBSERVED_MARKET)


def test_weaker_evidence_never_supports_a_stronger_claim():
    assert not satisfies(MODELLED, OBSERVED_MARKET)
    assert not satisfies(OBSERVED_MARKET, BROKER_EXECUTED)
    assert not satisfies(BROKER_SHADOW, BROKER_EXECUTED)


def test_no_quantity_of_modelled_evidence_becomes_economic_evidence():
    # The failure this whole concept exists to prevent: a large modeled sample
    # must not accumulate into a promotion.
    for _ in range(10_000):
        assert not satisfies(MODELLED, BROKER_EXECUTED)


def test_a_missing_class_is_refused_not_defaulted():
    with pytest.raises(EvidenceClassError) as excinfo:
        parse(None)

    assert "missing" in str(excinfo.value).lower()


def test_an_unknown_class_is_refused():
    for bogus in ("REAL", "LIVE", "", "broker", "OBSERVED"):
        with pytest.raises(EvidenceClassError):
            parse(bogus)


def test_case_and_padding_are_tolerated():
    assert parse("  broker_executed ") == BROKER_EXECUTED


def test_require_returns_the_class_or_raises():
    assert require(BROKER_EXECUTED, OBSERVED_MARKET) == BROKER_EXECUTED

    with pytest.raises(EvidenceClassError) as excinfo:
        require(MODELLED, BROKER_EXECUTED, context="promotion gate")

    assert "promotion gate" in str(excinfo.value)


def test_a_conclusion_takes_the_class_of_its_weakest_leg():
    # A real option fill hedged against a modeled future is not execution
    # evidence, however real the option leg was.
    assert weakest([BROKER_EXECUTED, MODELLED]) == MODELLED
    assert weakest([BROKER_EXECUTED, BROKER_SHADOW]) == BROKER_SHADOW
    assert weakest([BROKER_EXECUTED, BROKER_EXECUTED]) == BROKER_EXECUTED


def test_a_conclusion_from_nothing_has_no_class():
    with pytest.raises(EvidenceClassError):
        weakest([])


def test_no_function_raises_a_rows_class():
    import app.services.snapback_evidence_class as mod

    source = open(mod.__file__, encoding="utf-8").read()

    # There is deliberately no promote/upgrade/coerce path. If one is ever added,
    # this is the test that should be argued with first.
    for forbidden in ("def promote", "def upgrade", "def coerce", "def strengthen"):
        assert forbidden not in source


def test_kitelake_mirror_has_not_drifted():
    """kitelake duplicates the list because it runs in its own environment."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[3]
    text = (root / "kitelake" / "evidence.py").read_text(encoding="utf-8")
    match = re.search(r"EVIDENCE_CLASSES = \(([^)]*)\)", text)

    assert match, "kitelake/evidence.py no longer declares EVIDENCE_CLASSES"
    mirrored = tuple(re.findall(r'"([A-Z_]+)"', match.group(1)))

    assert mirrored == EVIDENCE_CLASSES
