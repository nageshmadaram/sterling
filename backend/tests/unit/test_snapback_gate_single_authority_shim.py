"""Item 5: the verdict mapping lives in one place, and the shim is a delegate.

study/snapback_forward_gate.py was a second direct caller of the gate AND kept
its own copy of the promoted/sample-sufficient → PASSED/FAILED/INCONCLUSIVE
mapping, as did PromotionService. Two copies of a decision rule drift, and the
one that drifts is the one nobody is watching.
"""

from __future__ import annotations

from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]


def test_the_shim_no_longer_calls_the_gate_directly():
    text = (BACKEND / "study" / "snapback_forward_gate.py").read_text(encoding="utf-8")

    # A docstring may name it; a call may not.
    assert "evaluate_authoritative_snapback_gate(" not in text


def test_the_shim_is_not_an_allowed_gate_caller():
    from tests.unit.test_snapback_promotion_single_authority import ALLOWED_GATE_CALLERS

    assert "study/snapback_forward_gate.py" not in ALLOWED_GATE_CALLERS


@pytest.mark.parametrize("promoted,sufficient,expected", [
    (True, True, "PASSED"),
    (True, False, "PASSED"),
    (False, False, "INCONCLUSIVE"),
    (False, True, "FAILED"),
])
def test_the_verdict_mapping_is_one_function(promoted, sufficient, expected):
    from study.snapback_authoritative_gate import verdict_for

    assert verdict_for(promoted=promoted, sample_sufficient=sufficient) == expected


def test_broken_evidence_is_never_a_decision():
    from study.snapback_authoritative_gate import verdict_for

    assert verdict_for(
        promoted=True, sample_sufficient=True, data_quality_ok=False,
    ) == "INCONCLUSIVE"


def test_neither_caller_maps_the_verdict_itself():
    shim = (BACKEND / "study" / "snapback_forward_gate.py").read_text(encoding="utf-8")
    service = (BACKEND / "app" / "services" / "snapback_promotion.py").read_text(
        encoding="utf-8"
    )

    # The shim gets the verdict applied for it; the service calls the shared
    # mapping. Neither writes its own promoted/sufficient branch.
    assert "evaluate_with_verdict" in shim
    assert "verdict_for" in service

    for text, name in ((shim, "shim"), (service, "service")):
        assert 'verdict = "FAILED"' not in text, name
        assert "verdict = FAILED" not in text, name
