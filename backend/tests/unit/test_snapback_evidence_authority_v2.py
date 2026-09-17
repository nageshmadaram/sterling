"""1.5 P0-EVIDENCE: authority fails closed everywhere, and promotion proves identity.

Four defects that share one philosophy — a missing fact was treated as a
permissive one:

  - is_authoritative_row() defaulted a missing flag to 1 and a missing source to
    PROSPECTIVE_PAPER, and only excluded two known-bad sources. An empty dict
    counted as authoritative evidence.
  - the promotion builder checked runtime_build_sha on outcomes but not on the
    cost, rebalance, quote or MTM rows beside them, so a 1.4 outcome could be
    priced by a 1.3 cost event.
  - G24 inferred whether a hedge existed from futures P&L, so a hedge that
    opened and closed flat looked like no hedge at all and its two cost events
    stopped being required.
  - the 60-session gate counted distinct trade entry days, so a fully observed
    session that produced no trade did not count.
"""

from __future__ import annotations

import pytest

from app.services.snapback_authority import is_authoritative_row


# ------------------------------------------------------ the row-level default


def test_an_empty_row_is_not_authoritative():
    """Omission must not confer authority. This is the 1.3 lesson, one layer out."""
    assert is_authoritative_row({}) is False


def test_a_row_without_a_source_is_not_authoritative():
    assert is_authoritative_row({"authoritative": 1}) is False


def test_a_row_without_the_flag_is_not_authoritative():
    assert is_authoritative_row({"source": "PROSPECTIVE_PAPER"}) is False


def test_an_unknown_source_is_not_authoritative():
    """Allow-list, not deny-list. A source nobody has classified is unknown,
    and unknown is not evidence."""
    assert is_authoritative_row(
        {"source": "SOME_NEW_BACKFILL", "authoritative": 1}
    ) is False


def test_a_complete_prospective_row_is_authoritative():
    assert is_authoritative_row(
        {"source": "PROSPECTIVE_PAPER", "authoritative": 1}
    ) is True


def test_an_unknown_source_is_reported_as_a_data_quality_problem():
    from app.services.snapback_authority import classify_row_authority

    verdict = classify_row_authority({"source": "SOME_NEW_BACKFILL", "authoritative": 1})

    assert verdict.authoritative is False
    assert any("unknown_source" in r for r in verdict.reasons)


def test_a_known_replay_source_is_excluded_without_a_data_quality_error():
    from app.services.snapback_authority import classify_row_authority

    verdict = classify_row_authority(
        {"source": "LIVE_CATCHUP_REPLAY", "authoritative": 0}
    )

    assert verdict.authoritative is False
    assert not any("unknown_source" in r for r in verdict.reasons)


# ------------------------------------------------------ the schema default


def test_the_schema_defaults_authoritative_to_zero():
    """A writer that forgets the column must produce non-evidence, not evidence."""
    import re
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "app" / "services" / "snapback_observation_warehouse.py"
    ).read_text(encoding="utf-8")

    defaults = re.findall(r"authoritative\s+INTEGER NOT NULL DEFAULT (\d)", source)

    assert defaults, "no authoritative column found"
    assert set(defaults) == {"0"}, f"fail-open defaults remain: {set(defaults)}"


def test_contradiction_scanning_covers_every_gate_relevant_table(tmp_path):
    from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

    wh = SnapbackObservationWarehouse(db_path=str(tmp_path / "w.db"))

    scanned = wh.authority_scanned_tables()

    for table in ("opportunities", "outcomes", "costs", "daily_mtm",
                  "hedge_rebalances", "quote_quality_events"):
        assert table in scanned


# ----------------------------------------------------- build identity in gate


IDENTITY = {
    "experiment_id": "E1",
    "runtime_build_sha": "build-1-4",
    "strategy_config_hash": "cfg",
    "strategy_rule_hash": "rule",
    "execution_cost_schedule_hash": "cost",
}


def _row(build="build-1-4", **over):
    base = {
        "source": "PROSPECTIVE_PAPER", "authoritative": 1,
        "runtime_build_sha": build,
    }
    base.update(over)
    return base


def _outcome(build="build-1-4", **over):
    base = _row(build)
    base.update(
        opportunity_id="OPP-1", actual_option_pnl=1000.0, actual_futures_pnl=0.0,
        actual_costs=50.0, actual_total_pnl=950.0,
        entry_ts="2026-10-01T09:20:00+05:30", exit_ts="2026-10-10T15:00:00+05:30",
        hedged=0,
    )
    base.update(over)
    return base


def _cost(phase, build="build-1-4", cost=25.0):
    base = _row(build)
    base.update(
        cost_id=f"COST:OPP-1:{phase}", opportunity_id="OPP-1",
        phase=phase, total_cost=cost,
    )
    return base


def _opportunities_for(outcomes):
    """The Day-T rows production always writes, one per outcome."""
    return [
        {
            "opportunity_id": o["opportunity_id"], "symbol": "NIFTY",
            "signal_iv": 0.18, "authoritative": 1,
            "source": o.get("source", "PROSPECTIVE_PAPER"),
            "runtime_build_sha": o.get("runtime_build_sha", "build-1"),
        }
        for o in outcomes
    ]


def _records(outcomes, costs, **over):
    base = {
        "outcomes": outcomes, "costs": costs, "hedge_rebalances": [],
        "opportunities": _opportunities_for(outcomes),
        "paper_positions": [], "daily_mtm": [], "prospective_sessions": [],
        "quote_quality_events": [
            _row() | {"required_for_economics": 1, "accepted": 1} for _ in range(10)
        ],
    }
    base.update(over)
    return base


def _build(records, observed_sessions=60):
    from study.snapback_promotion_inputs import build_promotion_input

    return build_promotion_input(
        records=records, expected_identity=IDENTITY,
        source_snapshot_sha256="sha", observed_sessions=observed_sessions,
        allocation_capital=1_000_000.0,
    )


def test_a_matching_build_is_accepted():
    result = _build(_records(
        [_outcome()], [_cost("OPTION_ENTRY"), _cost("OPTION_EXIT")],
    ))

    assert result.completed_trades == 1


def test_a_cost_event_from_another_build_is_refused():
    """A 1.4 outcome priced by a 1.3 cost event is not one experiment."""
    from study.snapback_promotion_inputs import PromotionInputError

    with pytest.raises(PromotionInputError):
        _build(_records(
            [_outcome()],
            [_cost("OPTION_ENTRY"), _cost("OPTION_EXIT", build="build-1-3")],
        ))


def test_a_quote_event_from_another_build_is_refused():
    from study.snapback_promotion_inputs import PromotionInputError

    records = _records([_outcome()], [_cost("OPTION_ENTRY"), _cost("OPTION_EXIT")])
    records["quote_quality_events"] = [
        _row(build="build-1-3") | {"required_for_economics": 1, "accepted": 1}
    ]

    with pytest.raises(PromotionInputError):
        _build(records)


def test_an_mtm_row_from_another_build_is_refused():
    from study.snapback_promotion_inputs import PromotionInputError

    records = _records([_outcome()], [_cost("OPTION_ENTRY"), _cost("OPTION_EXIT")])
    records["daily_mtm"] = [
        _row(build="build-1-3") | {"opportunity_id": "OPP-1", "mtm_pnl": 10.0,
                                   "session_date": "2026-10-02"}
    ]

    with pytest.raises(PromotionInputError):
        _build(records)


# ------------------------------------------------------- G24 from executions


def test_a_hedge_that_closed_flat_still_requires_its_cost_events():
    """The defect: futures P&L of exactly zero made a real hedge invisible, and
    its HEDGE_ENTRY/HEDGE_EXIT costs stopped being required."""
    from study.snapback_promotion_inputs import PromotionInputError

    outcome = _outcome(actual_futures_pnl=0.0, hedged=1)

    with pytest.raises(PromotionInputError) as exc:
        _build(_records([outcome], [_cost("OPTION_ENTRY"), _cost("OPTION_EXIT")]))

    assert any("HEDGE_ENTRY" in e for e in exc.value.errors)


def test_hedging_is_read_from_execution_events_not_pnl():
    """An execution artifact is the evidence a leg happened."""
    from study.snapback_promotion_inputs import PromotionInputError

    outcome = _outcome(actual_futures_pnl=0.0)
    outcome.pop("hedged")
    records = _records(
        [outcome], [_cost("OPTION_ENTRY"), _cost("OPTION_EXIT")],
        paper_fills=[
            _row() | {"opportunity_id": "OPP-1", "leg": "HEDGE_ENTRY", "quantity": 75},
        ],
    )

    with pytest.raises(PromotionInputError) as exc:
        _build(records)

    assert any("HEDGE" in e for e in exc.value.errors)


def test_no_hedge_execution_means_no_hedge_costs_required():
    outcome = _outcome(actual_futures_pnl=0.0)
    outcome.pop("hedged")

    result = _build(_records(
        [outcome], [_cost("OPTION_ENTRY"), _cost("OPTION_EXIT")], paper_fills=[],
    ))

    assert result.completed_trades == 1


def test_futures_pnl_alone_no_longer_implies_a_hedge():
    """The old fallback would have demanded hedge costs here purely because the
    futures P&L was non-zero, with no execution record to justify it."""
    outcome = _outcome(actual_futures_pnl=250.0)
    outcome.pop("hedged")
    outcome["actual_total_pnl"] = 1000.0 + 250.0 - 50.0

    result = _build(_records(
        [outcome], [_cost("OPTION_ENTRY"), _cost("OPTION_EXIT")], paper_fills=[],
    ))

    assert result.completed_trades == 1


# --------------------------------------------------------- the session count


def test_the_sixty_session_check_uses_observed_sessions():
    """A fully observed session that produced no trade is still a held-out
    session. Counting trade entry days silently shrinks the denominator."""
    from study.snapback_authoritative_gate import evaluate_authoritative_snapback_gate

    verdict = evaluate_authoritative_snapback_gate(
        trade_pnls=[10.0] * 5,
        entry_dates=["2026-10-01"] * 5,          # five trades, one entry day
        statutory_costs=[1.0] * 5,
        daily_mtm_equity_series=[100.0, 101.0],
        entry_sessions_count=61,                  # sixty-one observed sessions
    )

    assert verdict.checks["independent_sessions_ge_60"] is True
    assert verdict.total_sessions == 61


def test_too_few_observed_sessions_still_fails_the_check():
    from study.snapback_authoritative_gate import evaluate_authoritative_snapback_gate

    verdict = evaluate_authoritative_snapback_gate(
        trade_pnls=[10.0] * 5,
        entry_dates=[f"2026-10-{d:02d}" for d in range(1, 6)],
        statutory_costs=[1.0] * 5,
        daily_mtm_equity_series=[100.0, 101.0],
        entry_sessions_count=12,
    )

    assert verdict.checks["independent_sessions_ge_60"] is False


def test_the_bootstrap_still_clusters_on_entry_days():
    """Entry dates keep their job: they are what the day-clustered CI resamples."""
    import inspect

    from study import snapback_authoritative_gate

    source = inspect.getsource(
        snapback_authoritative_gate.evaluate_authoritative_snapback_gate
    )

    assert "day_to_pnls" in source
