"""An unknown economic result must never become ₹0.

The promotion collector used to read every money field as
``float(row.get(x) or 0.0)``. That spelling makes three different facts
indistinguishable: nobody recorded the result, the trade broke even, and the
field holds NaN. A sample built from it reports a number for evidence that was
never observed, and the direction of the error is the dangerous one — unmeasured
trades enter as break-even, inflating the count while dragging the measured
effect toward zero.
"""
from __future__ import annotations

import pytest

from app.core.lane_promotion import (
    INCONCLUSIVE,
    EconomicRowError,
    Exclusion,
    collect_lane_evidence,
    evaluate_lane,
    evaluate_lane_identities,
    lane_identities,
    validated_economic_row,
)

LANE = "snapback:swing"
IDENTITY = "aaaabbbbccccdddd"


def _row(**overrides):
    row = {
        "lane_key": LANE,
        "authoritative": 1,
        "evidence_class": "paper",
        "identity_hash": IDENTITY,
        "actual_total_pnl": 1_250.0,
        "actual_costs": 95.0,
        "entry_date": "2026-09-11",
    }
    row.update(overrides)
    return row


def test_a_complete_row_is_read_exactly():
    observation = validated_economic_row(_row(), identity_hash=IDENTITY)

    assert observation.pnl == 1_250.0
    assert observation.cost == 95.0
    assert observation.entry_date == "2026-09-11"


def test_a_genuine_zero_is_still_a_result():
    """Break-even is an observation. Only an absence is an absence."""
    observation = validated_economic_row(
        _row(actual_total_pnl=0.0, actual_costs=0.0), identity_hash=IDENTITY
    )

    assert observation.pnl == 0.0
    assert observation.cost == 0.0


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"actual_total_pnl": None}, Exclusion.MISSING_ACTUAL_PNL),
        ({"actual_total_pnl": ""}, Exclusion.MISSING_ACTUAL_PNL),
        ({"actual_total_pnl": float("nan")}, Exclusion.NONFINITE_ACTUAL_PNL),
        ({"actual_total_pnl": float("inf")}, Exclusion.NONFINITE_ACTUAL_PNL),
        ({"actual_total_pnl": float("-inf")}, Exclusion.NONFINITE_ACTUAL_PNL),
        ({"actual_costs": None}, Exclusion.MISSING_ACTUAL_COST),
        ({"actual_costs": float("nan")}, Exclusion.NONFINITE_ACTUAL_COST),
        ({"actual_costs": -1.0}, Exclusion.NEGATIVE_ACTUAL_COST),
        ({"entry_date": "", "entry_ts": ""}, Exclusion.MISSING_ENTRY_DATE),
        ({"identity_hash": ""}, Exclusion.MISSING_IDENTITY),
        ({"identity_hash": "9999888877776666"}, Exclusion.IDENTITY_MISMATCH),
    ],
)
def test_every_unknown_is_refused_with_its_own_code(overrides, code):
    with pytest.raises(EconomicRowError) as excinfo:
        validated_economic_row(_row(**overrides), identity_hash=IDENTITY)

    assert excinfo.value.code == code


def test_an_unreadable_row_is_reported_not_silently_dropped():
    evidence = collect_lane_evidence(
        [_row(), _row(actual_total_pnl=None), _row(actual_costs=None)],
        LANE,
        identity_hash=IDENTITY,
    )

    assert evidence.eligible == 1
    assert evidence.unreadable_rows == 2
    assert evidence.unreadable[Exclusion.MISSING_ACTUAL_PNL] == 1
    assert evidence.unreadable[Exclusion.MISSING_ACTUAL_COST] == 1
    assert evidence.economics_readable is False


def test_a_lane_with_unreadable_economics_is_inconclusive_not_scored():
    """Never evaluate the readable subset: it is not a random sample.

    Whatever broke the recording may correlate with the outcome, so scoring what
    survived answers a question nobody asked.
    """
    rows = [_row(actual_total_pnl=float(i)) for i in range(400)]
    rows[7] = _row(actual_total_pnl=None)

    verdict = evaluate_lane(collect_lane_evidence(rows, LANE, identity_hash=IDENTITY))

    assert verdict["verdict"] == INCONCLUSIVE
    assert verdict["data_quality"] == "UNREADABLE_ECONOMICS"
    assert any(Exclusion.MISSING_ACTUAL_PNL in reason for reason in verdict["reasons"])


def test_a_foreign_lane_row_is_excluded_before_its_economics_are_read():
    evidence = collect_lane_evidence(
        [_row(lane_key="snapback:scalping"), _row()], LANE, identity_hash=IDENTITY
    )

    assert evidence.eligible == 1
    assert evidence.excluded_other_lane == 1
    assert evidence.economics_readable is True


def test_two_revisions_of_one_lane_are_two_experiments():
    """A re-frozen lane must not inherit its predecessor's sample."""
    other = "1111222233334444"
    rows = [_row(), _row(), _row(identity_hash=other)]

    assert lane_identities(rows, LANE) == (other, IDENTITY)

    v1 = collect_lane_evidence(rows, LANE, identity_hash=IDENTITY)
    v2 = collect_lane_evidence(rows, LANE, identity_hash=other)

    assert v1.eligible == 2
    assert v2.eligible == 1

    per_identity = evaluate_lane_identities(rows, LANE)
    assert set(per_identity) == {IDENTITY, other}


def test_pooling_every_revision_is_only_possible_without_an_identity():
    """The un-scoped call still exists for coverage reports, and says so."""
    rows = [_row(), _row(identity_hash="1111222233334444")]

    pooled = collect_lane_evidence(rows, LANE)

    assert pooled.eligible == 2
    assert pooled.identity_hash == ""
