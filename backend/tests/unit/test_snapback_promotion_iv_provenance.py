"""A completed trade cannot become promotable if its Day-T IV was fabricated or absent."""

import pytest

from study.snapback_promotion_inputs import PromotionInputError, build_promotion_input


def _base_records(signal_iv):
    opp = "OPP-1"
    return {
        "opportunities": [{
            "opportunity_id": opp,
            "signal_iv": signal_iv,
            "source": "PROSPECTIVE_PAPER",
            "runtime_build_sha": "sha",
            "authoritative": 1,
        }],
        "outcomes": [{
            "opportunity_id": opp,
            "entry_ts": "2026-09-17T09:20:00+00:00",
            "actual_option_pnl": 100.0,
            "actual_futures_pnl": 0.0,
            "actual_costs": 10.0,
            "actual_total_pnl": 90.0,
            "runtime_build_sha": "sha",
            "source": "PROSPECTIVE_PAPER",
            "authoritative": 1,
            "hedged": 0,
        }],
        "costs": [
            {"opportunity_id": opp, "phase": "OPTION_ENTRY", "total_cost": 5.0,
             "runtime_build_sha": "sha", "source": "PROSPECTIVE_PAPER", "authoritative": 1},
            {"opportunity_id": opp, "phase": "OPTION_EXIT", "total_cost": 5.0,
             "runtime_build_sha": "sha", "source": "PROSPECTIVE_PAPER", "authoritative": 1},
        ],
        "paper_positions": [],
        "paper_fills": [],
        "hedge_rebalances": [],
        "quote_quality_events": [{
            "opportunity_id": opp, "required_for_economics": 1, "accepted": 1,
            "runtime_build_sha": "sha", "source": "PROSPECTIVE_PAPER", "authoritative": 1,
        }],
        "daily_mtm": [],
        "prospective_sessions": [],
    }


def _identity():
    return {
        "experiment_id": "exp",
        "runtime_build_sha": "sha",
        "strategy_config_hash": "cfg",
        "strategy_rule_hash": "rule",
        "execution_policy_hash": "policy",
        "execution_cost_schedule_hash": "cost",
    }


@pytest.mark.parametrize("signal_iv", [None, 0.0, -0.1, float("nan")])
def test_bad_day_t_iv_blocks_promotion_input(signal_iv):
    with pytest.raises(PromotionInputError) as exc:
        build_promotion_input(
            records=_base_records(signal_iv),
            expected_identity=_identity(),
            source_snapshot_sha256="snap",
            observed_sessions=70,
            allocation_capital=1_000_000.0,
        )
    assert any("signal_iv" in error for error in exc.value.errors)


def test_positive_day_t_iv_is_not_rejected_by_the_iv_guard():
    try:
        build_promotion_input(
            records=_base_records(0.25),
            expected_identity=_identity(),
            source_snapshot_sha256="snap",
            observed_sessions=70,
            allocation_capital=1_000_000.0,
        )
    except PromotionInputError as exc:
        assert not any("signal_iv" in error for error in exc.errors)
