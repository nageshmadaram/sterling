"""G24: every executed leg has exactly one cost event, and every cost event has a leg.

Summing the ledger per trade catches a wrong total. It does not catch a missing
leg whose cost happened to be small, a duplicated leg that double-charges, or an
orphan event attached to a leg that never executed. Each of those changes the
cost stress the promotion gate applies, and none of them changes the sum enough
to be obvious.

Missing, duplicate or orphan → INCONCLUSIVE, never a quietly adjusted number.
"""

from __future__ import annotations

import pytest

from study.snapback_promotion_inputs import PromotionInputError, build_promotion_input

# Authority is now explicit at the writer, so fixtures must declare it too: a row
# with no `source` is deliberately not evidence any more.
_AUTH_FIXTURE = {
    "source": "PROSPECTIVE_PAPER", "authoritative": 1,
    "runtime_build_sha": "build-1",
}


def _auth(row: dict, build: str = "") -> dict:
    """Stamp a fixture row with a complete, self-consistent authority."""
    out = dict(_AUTH_FIXTURE)
    if build:
        out["runtime_build_sha"] = build
    out.update(row)
    return out



IDENTITY = {
    "experiment_id": "E1",
    "runtime_build_sha": "build-1",
    "strategy_config_hash": "cfg",
    "strategy_rule_hash": "rule",
    "execution_cost_schedule_hash": "cost",
}


def _cost(opp, phase, cost=10.0, cost_id=None):
    return {
        "cost_id": cost_id or f"COST:{opp}:{phase}",
        "opportunity_id": opp,
        "phase": phase,
        "total_cost": cost,
        "runtime_build_sha": "build-1",
        "authoritative": 1, "source": "PROSPECTIVE_PAPER",
    }


def _outcome(opp="OPP-1", *, costs=50.0, hedged=True):
    return {
        "opportunity_id": opp,
        "actual_option_pnl": 1000.0,
        "actual_futures_pnl": 0.0,
        "actual_costs": costs,
        "actual_total_pnl": 1000.0 - costs,
        "entry_ts": "2026-10-01T09:20:00+05:30",
        "exit_ts": "2026-10-10T15:00:00+05:30",
        "runtime_build_sha": "build-1",
        "authoritative": 1, "source": "PROSPECTIVE_PAPER",
        "hedged": 1 if hedged else 0,
    }


def _records(*, outcomes, costs, rebalances=None):
    return {
        "outcomes": outcomes,
        "costs": costs,
        "hedge_rebalances": rebalances or [],
        "paper_positions": [],
        "daily_mtm": [],
        "prospective_sessions": [],
        "quote_quality_events": [
            {"required_for_economics": 1, "accepted": 1, **_AUTH_FIXTURE}
            for _ in range(10)
        ],
    }


def _build(records):
    return build_promotion_input(
        records=records, expected_identity=IDENTITY,
        source_snapshot_sha256="sha", observed_sessions=60,
        allocation_capital=1_000_000.0,
    )


def _complete_costs(opp="OPP-1"):
    return [
        _cost(opp, "OPTION_ENTRY", 10.0),
        _cost(opp, "HEDGE_ENTRY", 10.0),
        _cost(opp, "OPTION_EXIT", 15.0),
        _cost(opp, "HEDGE_EXIT", 15.0),
    ]


def test_a_complete_set_of_legs_is_accepted():
    result = _build(_records(outcomes=[_outcome()], costs=_complete_costs()))

    assert result.completed_trades == 1


@pytest.mark.parametrize("missing", [
    "OPTION_ENTRY", "HEDGE_ENTRY", "OPTION_EXIT", "HEDGE_EXIT",
])
def test_a_missing_leg_cost_is_refused(missing):
    costs = [c for c in _complete_costs() if c["phase"] != missing]
    total = sum(c["total_cost"] for c in costs)

    with pytest.raises(PromotionInputError) as exc:
        _build(_records(outcomes=[_outcome(costs=total)], costs=costs))

    assert any(missing in e for e in exc.value.errors)


@pytest.mark.parametrize("duplicated", [
    "OPTION_ENTRY", "HEDGE_ENTRY", "OPTION_EXIT", "HEDGE_EXIT",
])
def test_a_duplicated_leg_cost_is_refused(duplicated):
    costs = _complete_costs()
    extra = _cost("OPP-1", duplicated, 10.0, cost_id=f"COST:dup:{duplicated}")
    costs.append(extra)

    with pytest.raises(PromotionInputError) as exc:
        _build(_records(
            outcomes=[_outcome(costs=sum(c["total_cost"] for c in costs))], costs=costs,
        ))

    assert any(duplicated in e for e in exc.value.errors)


def test_an_unhedged_trade_must_not_carry_hedge_costs():
    """A hedge cost on a trade that never hedged is an orphan: it charges the
    strategy for a leg it did not execute."""
    costs = [
        _cost("OPP-1", "OPTION_ENTRY", 10.0),
        _cost("OPP-1", "OPTION_EXIT", 15.0),
        _cost("OPP-1", "HEDGE_ENTRY", 10.0),
    ]

    with pytest.raises(PromotionInputError) as exc:
        _build(_records(
            outcomes=[_outcome(costs=35.0, hedged=False)], costs=costs,
        ))

    assert any("HEDGE_ENTRY" in e for e in exc.value.errors)


def test_an_unhedged_trade_with_only_option_legs_is_accepted():
    costs = [
        _cost("OPP-1", "OPTION_ENTRY", 10.0),
        _cost("OPP-1", "OPTION_EXIT", 15.0),
    ]

    result = _build(_records(
        outcomes=[_outcome(costs=25.0, hedged=False)], costs=costs,
    ))

    assert result.completed_trades == 1


def test_each_rebalance_needs_its_own_cost():
    rebalances = [
        {"opportunity_id": "OPP-1", "rebalance_id": "RB-1", **_AUTH_FIXTURE},
        {"opportunity_id": "OPP-1", "rebalance_id": "RB-2", **_AUTH_FIXTURE},
    ]
    costs = _complete_costs() + [_cost("OPP-1", "HEDGE_REBALANCE", 5.0)]

    with pytest.raises(PromotionInputError) as exc:
        _build(_records(
            outcomes=[_outcome(costs=sum(c["total_cost"] for c in costs))],
            costs=costs, rebalances=rebalances,
        ))

    assert any("HEDGE_REBALANCE" in e for e in exc.value.errors)


def test_matching_rebalance_costs_are_accepted():
    rebalances = [
        {"opportunity_id": "OPP-1", "rebalance_id": "RB-1", **_AUTH_FIXTURE},
        {"opportunity_id": "OPP-1", "rebalance_id": "RB-2", **_AUTH_FIXTURE},
    ]
    costs = _complete_costs() + [
        _cost("OPP-1", "HEDGE_REBALANCE", 5.0, cost_id="COST:RB-1"),
        _cost("OPP-1", "HEDGE_REBALANCE", 5.0, cost_id="COST:RB-2"),
    ]

    result = _build(_records(
        outcomes=[_outcome(costs=sum(c["total_cost"] for c in costs))],
        costs=costs, rebalances=rebalances,
    ))

    assert result.completed_trades == 1


def test_a_rebalance_cost_without_a_rebalance_is_refused():
    costs = _complete_costs() + [_cost("OPP-1", "HEDGE_REBALANCE", 5.0)]

    with pytest.raises(PromotionInputError) as exc:
        _build(_records(
            outcomes=[_outcome(costs=sum(c["total_cost"] for c in costs))],
            costs=costs, rebalances=[],
        ))

    assert any("HEDGE_REBALANCE" in e for e in exc.value.errors)


def test_an_unknown_phase_is_refused():
    costs = _complete_costs() + [_cost("OPP-1", "MYSTERY_LEG", 5.0)]

    with pytest.raises(PromotionInputError) as exc:
        _build(_records(
            outcomes=[_outcome(costs=sum(c["total_cost"] for c in costs))], costs=costs,
        ))

    assert any("MYSTERY_LEG" in e or "phase" in e for e in exc.value.errors)


def test_the_verdict_is_inconclusive_not_a_quiet_adjustment():
    """The gate must refuse to score, rather than score a trade whose cost
    evidence is incomplete."""
    from app.services.snapback_promotion import PromotionService

    costs = [c for c in _complete_costs() if c["phase"] != "OPTION_EXIT"]

    class _Warehouse:
        def get_all_records(self):
            return _records(outcomes=[_outcome(costs=20.0)], costs=costs)

    with pytest.raises(PromotionInputError):
        _build(_records(outcomes=[_outcome(costs=20.0)], costs=costs))
