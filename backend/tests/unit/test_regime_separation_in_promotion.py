"""A promotion report must not present one number without saying whose it is.

Paper, shadow and broker rows answer three different questions, and the most
permissive of the three is paper. Pooling them silently lets a paper sample
carry a lane toward a gate that was meant to be about real execution.
"""
from __future__ import annotations

import pytest

from app.core.evidence import EvidenceClass
from app.core.execution_regime import (
    POOLED_FORWARD,
    SEPARATE_REGIMES,
    PoolingNotDeclared,
    assert_pooling_declared,
)
from app.core.lane_promotion import evaluate_all_lanes, evaluate_lane_regimes

LANE = "snapback:swing"


def _row(evidence_class: str, pnl: float, date: str, ident: str = "id-1"):
    return {
        "authoritative": 1,
        "lane_key": LANE,
        "identity_hash": ident,
        "evidence_class": evidence_class,
        "observed_pnl": pnl,
        "actual_total_pnl": pnl,
        "statutory_costs": 0.0,
        "actual_costs": 0.0,
        "entry_date": date,
    }


class TestTheReportSeparatesTheRegimes:
    def test_every_lane_carries_a_per_regime_breakdown(self):
        report = evaluate_all_lanes([])
        for lane_key, verdict in report.items():
            assert verdict["evidence_scope"] == "all promotable classes", lane_key
            assert set(verdict["regimes"]["by_regime"]) == {"paper", "shadow", "broker"}

    def test_each_regime_is_counted_on_its_own(self):
        rows = [
            _row("paper", 100.0, "2026-09-01"),
            _row("paper", 120.0, "2026-09-02"),
            _row("shadow", -50.0, "2026-09-03"),
        ]
        result = evaluate_lane_regimes(rows, LANE)
        stats = result["statistics"]
        assert stats["paper"]["trades"] == 2
        assert stats["shadow"]["trades"] == 1
        assert stats["broker"]["trades"] == 0

    def test_a_paper_sample_does_not_appear_in_the_broker_regime(self):
        result = evaluate_lane_regimes([_row("paper", 100.0, "2026-09-01")], LANE)
        assert result["by_regime"]["broker"]["lane"]["eligible_trades"] == 0
        assert result["by_regime"]["paper"]["lane"]["eligible_trades"] == 1

    def test_each_regime_verdict_says_which_regime_it_is(self):
        result = evaluate_lane_regimes([_row("shadow", 10.0, "2026-09-01")], LANE)
        for name, verdict in result["by_regime"].items():
            assert verdict["evidence_scope"] == name


class TestPoolingHasToBeDeclared:
    def test_the_default_policy_produces_no_pooled_verdict(self):
        result = evaluate_lane_regimes([_row("paper", 10.0, "2026-09-01")], LANE)
        assert result["policy"]["policy_id"] == SEPARATE_REGIMES.policy_id
        assert result["pooled"] is None

    def test_a_declared_policy_produces_one_and_labels_it(self):
        result = evaluate_lane_regimes(
            [_row("paper", 10.0, "2026-09-01")], LANE, policy=POOLED_FORWARD)
        assert result["pooled"] is not None
        assert result["pooled"]["evidence_scope"].startswith("POOLED: ")
        assert "paper" in result["pooled"]["evidence_scope"]

    def test_pooling_two_regimes_without_a_policy_is_an_error(self):
        with pytest.raises(PoolingNotDeclared):
            assert_pooling_declared(
                SEPARATE_REGIMES, {EvidenceClass.PAPER, EvidenceClass.BROKER})

    def test_one_regime_is_never_pooling(self):
        # A single class is a sample, not a pool; the rule must not fire on it.
        assert_pooling_declared(SEPARATE_REGIMES, {EvidenceClass.PAPER})


class TestTheQuestionsAreNamed:
    def test_each_regime_states_the_question_it_answers(self):
        stats = evaluate_lane_regimes([], LANE)["statistics"]
        assert "observed market data" in stats["paper"]["question"]
        assert "executed" in stats["shadow"]["question"]
        assert "real capital" in stats["broker"]["question"]
