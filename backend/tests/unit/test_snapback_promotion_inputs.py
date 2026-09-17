"""E32: one builder turns evidence into gate inputs, and checks it first.

Every other path that assembles "promotable" numbers is a second opinion nobody
asked for, and the two will eventually disagree about whether the family may trade.
"""

from __future__ import annotations

import pytest

from study.snapback_promotion_inputs import (

    PromotionInput,
    PromotionInputError,
    build_promotion_input,
)

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
    "experiment_id": "prospective_runtime_1_1",
    "runtime_build_sha": "build-1",
    "strategy_config_hash": "cfg-1",
    "strategy_rule_hash": "rule-1",
    "execution_policy_hash": "pol-1",
    "execution_cost_schedule_hash": "zerodha_fno_costs_2026_04",
}


def _outcome(i=0, pnl=100.0, costs=25.0, **over):
    row = {
        "opportunity_id": f"OPP-{i}",
        "actual_option_pnl": pnl + costs,
        "actual_futures_pnl": 0.0,
        "actual_costs": costs,
        "actual_total_pnl": pnl,
        "entry_ts": f"2026-10-{(i % 28) + 1:02d}T09:20:00+05:30",
        "exit_ts": f"2026-11-{(i % 28) + 1:02d}T15:20:00+05:30",
        "exit_reason": "PREMIUM_STOP",
        "authoritative": 1, "source": "PROSPECTIVE_PAPER",
        "runtime_build_sha": "build-1",
    }
    row.update(over)
    return row


def _cost_rows(i=0, total=25.0, phases=("OPTION_ENTRY", "OPTION_EXIT")):
    return [
        {
            "cost_id": f"COST:{phase}-{i}", "opportunity_id": f"OPP-{i}",
            "execution_event_id": f"{phase}-{i}", "phase": phase,
            "total_cost": total / len(phases), "authoritative": 1, "source": "PROSPECTIVE_PAPER",
            "runtime_build_sha": "build-1",
        }
        for phase in phases
    ]


def _opportunity(i=0, **over):
    """The Day-T row every outcome must join back to.

    Production always writes one via ``record_signal_at_close``, carrying the
    signal's own ``assumed_iv``. Promotion now requires it, so that a trade
    priced on a T+1 fallback vol can never enter the promotable sample.
    """
    row = {
        "opportunity_id": f"OPP-{i}",
        "symbol": "NIFTY",
        "signal_iv": 0.18,
        "authoritative": 1,
        "source": "PROSPECTIVE_PAPER",
        "runtime_build_sha": "build-1",
    }
    row.update(over)
    return row


def _records(n=2, **over):
    outcomes, costs, opportunities = [], [], []
    for i in range(n):
        outcomes.append(_outcome(i))
        costs.extend(_cost_rows(i))
        opportunities.append(_opportunity(i))
    base = {
        "outcomes": outcomes,
        "costs": costs,
        "opportunities": opportunities,
        "paper_positions": [],
        "daily_mtm": [],
        "option_quotes": [],
        "quote_quality_events": [
            {"required_for_economics": 1, "accepted": 1, **_AUTH_FIXTURE} for _ in range(40)
        ],
        "prospective_sessions": [],
    }
    base.update(over)
    return base


def test_a_clean_sample_builds_inputs():
    result = build_promotion_input(
        records=_records(), expected_identity=IDENTITY,
        source_snapshot_sha256="snap-1", observed_sessions=12,
    )

    assert isinstance(result, PromotionInput)
    assert result.completed_trades == 2
    assert result.observed_sessions == 12
    assert result.trade_pnls == (100.0, 100.0)
    assert result.allocation_capital == pytest.approx(100_000.0)


def test_costs_come_from_the_ledger_not_the_outcome():
    records = _records(n=1)
    # The outcome claims a smaller cost than its legs actually charged.
    records["outcomes"][0]["actual_costs"] = 5.0

    with pytest.raises(PromotionInputError) as excinfo:
        build_promotion_input(
            records=records, expected_identity=IDENTITY,
            source_snapshot_sha256="snap-1", observed_sessions=12,
        )

    assert any("cost" in e.lower() for e in excinfo.value.errors)


def test_a_trade_with_no_cost_events_is_inadmissible():
    records = _records(n=1)
    records["costs"] = []

    with pytest.raises(PromotionInputError) as excinfo:
        build_promotion_input(
            records=records, expected_identity=IDENTITY,
            source_snapshot_sha256="snap-1", observed_sessions=12,
        )

    assert any("cost" in e.lower() for e in excinfo.value.errors)


def test_an_orphan_cost_event_is_a_data_quality_error():
    records = _records(n=1)
    records["costs"].extend(_cost_rows(i=99))

    with pytest.raises(PromotionInputError) as excinfo:
        build_promotion_input(
            records=records, expected_identity=IDENTITY,
            source_snapshot_sha256="snap-1", observed_sessions=12,
        )

    assert any("orphan" in e.lower() for e in excinfo.value.errors)


def test_a_total_that_does_not_reconcile_is_refused():
    records = _records(n=1)
    records["outcomes"][0]["actual_total_pnl"] = 999.0

    with pytest.raises(PromotionInputError):
        build_promotion_input(
            records=records, expected_identity=IDENTITY,
            source_snapshot_sha256="snap-1", observed_sessions=12,
        )


def test_a_build_mismatch_is_refused():
    records = _records(n=1)
    records["outcomes"][0]["runtime_build_sha"] = "another-build"

    with pytest.raises(PromotionInputError) as excinfo:
        build_promotion_input(
            records=records, expected_identity=IDENTITY,
            source_snapshot_sha256="snap-1", observed_sessions=12,
        )

    assert any("build" in e.lower() for e in excinfo.value.errors)


def test_unresolved_exposure_is_counted():
    records = _records(n=1, paper_positions=[{"status": "OPEN"}, {"status": "EXIT_PENDING"}])

    with pytest.raises(PromotionInputError) as excinfo:
        build_promotion_input(
            records=records, expected_identity=IDENTITY,
            source_snapshot_sha256="snap-1", observed_sessions=12,
        )

    assert any("unresolved" in e.lower() for e in excinfo.value.errors)


def test_quote_coverage_comes_only_from_attempts():
    records = _records(n=1, quote_quality_events=[])

    with pytest.raises(PromotionInputError) as excinfo:
        build_promotion_input(
            records=records, expected_identity=IDENTITY,
            source_snapshot_sha256="snap-1", observed_sessions=12,
        )

    assert any("quote" in e.lower() for e in excinfo.value.errors)


def test_the_gate_input_hash_is_deterministic():
    a = build_promotion_input(
        records=_records(), expected_identity=IDENTITY,
        source_snapshot_sha256="snap-1", observed_sessions=12,
    )
    b = build_promotion_input(
        records=_records(), expected_identity=IDENTITY,
        source_snapshot_sha256="snap-1", observed_sessions=12,
    )

    assert a.gate_input_hash == b.gate_input_hash


def test_a_different_snapshot_hashes_differently():
    a = build_promotion_input(
        records=_records(), expected_identity=IDENTITY,
        source_snapshot_sha256="snap-1", observed_sessions=12,
    )
    b = build_promotion_input(
        records=_records(n=3), expected_identity=IDENTITY,
        source_snapshot_sha256="snap-2", observed_sessions=12,
    )

    assert a.gate_input_hash != b.gate_input_hash


def test_the_hash_ignores_evaluation_time():
    inputs = build_promotion_input(
        records=_records(), expected_identity=IDENTITY,
        source_snapshot_sha256="snap-1", observed_sessions=12,
    )

    assert "evaluated_at" not in inputs.gate_input_hash
    assert len(inputs.gate_input_hash) == 64


# ─── the Day-T join is load-bearing ──────────────────────────────────────────
# These exist because the rule they guard had no negative test: every fixture
# could have been made to pass by deleting the requirement. The old T+1
# orchestration substituted `assumed_vrp * 0.15` when signal_iv was absent, so a
# trade priced on a guessed vol could otherwise look fully authoritative.

def test_an_outcome_with_no_day_t_opportunity_is_inadmissible():
    records = _records(n=1)
    records["opportunities"] = []

    with pytest.raises(PromotionInputError) as excinfo:
        build_promotion_input(
            records=records, expected_identity=IDENTITY,
            source_snapshot_sha256="snap-1", observed_sessions=12,
        )

    assert any("authoritative Day-T opportunity" in e for e in excinfo.value.errors)


def test_a_zero_signal_iv_is_inadmissible():
    """The schema defaults signal_iv to 0.0, which `or` used to convert to a vol."""
    records = _records(n=1)
    records["opportunities"] = [_opportunity(0, signal_iv=0.0)]

    with pytest.raises(PromotionInputError) as excinfo:
        build_promotion_input(
            records=records, expected_identity=IDENTITY,
            source_snapshot_sha256="snap-1", observed_sessions=12,
        )

    assert any("signal_iv" in e for e in excinfo.value.errors)


@pytest.mark.parametrize("bad", [None, -0.1, float("nan"), float("inf")])
def test_a_non_positive_or_non_finite_signal_iv_is_inadmissible(bad):
    records = _records(n=1)
    records["opportunities"] = [_opportunity(0, signal_iv=bad)]

    with pytest.raises(PromotionInputError):
        build_promotion_input(
            records=records, expected_identity=IDENTITY,
            source_snapshot_sha256="snap-1", observed_sessions=12,
        )


def test_an_opportunity_from_another_build_cannot_vouch_for_an_outcome():
    records = _records(n=1)
    records["opportunities"] = [_opportunity(0, runtime_build_sha="another-build")]

    with pytest.raises(PromotionInputError):
        build_promotion_input(
            records=records, expected_identity=IDENTITY,
            source_snapshot_sha256="snap-1", observed_sessions=12,
        )
