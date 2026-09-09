"""Wall and break-through gates from the 46:22–60:00 source segment."""
from __future__ import annotations

from datetime import date
from dataclasses import replace

from app.engines.gamma_move import (GammaMoveConfig, GammaMoveStrategy,
                                    is_chain_wall, spot_through_or_at_strike)
from tests.engines.gamma_move.conftest import BASE_MS, bar, quiet_session

TODAY = date(2026, 9, 20)


def test_ce_needs_spot_at_or_through_the_strike():
    assert spot_through_or_at_strike(1300.0, 1300.0, "CE", 1.0)
    assert spot_through_or_at_strike(1298.0, 1300.0, "CE", 1.0)
    assert not spot_through_or_at_strike(1270.0, 1300.0, "CE", 1.0)
    assert spot_through_or_at_strike(1310.0, 1300.0, "CE", 1.0)


def test_pe_needs_spot_at_or_through_the_strike():
    assert spot_through_or_at_strike(1300.0, 1300.0, "PE", 1.0)
    assert spot_through_or_at_strike(1310.0, 1300.0, "PE", 1.0)
    assert not spot_through_or_at_strike(1330.0, 1300.0, "PE", 1.0)


def test_chain_wall_skips_when_unmeasured():
    assert is_chain_wall(100, None, required=True)
    assert not is_chain_wall(100, 500, required=True)
    assert is_chain_wall(500, 500, required=True)
    assert is_chain_wall(100, 500, required=False)


def triggering():
    return quiet_session() + [bar(0, 24, oi=96_000, volume=5_000, close=53.0)]


def test_evaluate_refuses_a_strike_that_is_not_the_wall(candidate):
    s = GammaMoveStrategy(GammaMoveConfig(enabled=True, max_premium_at_risk_inr=60_000))
    far = replace(candidate, oi=100_000, chain_oi_max=6_000_000)
    sig = s.evaluate(far, triggering(), now_ms=BASE_MS, today=TODAY, regime="up")
    assert sig.state == "watching"
    assert "chain wall" in (sig.reason or "")
    assert sig.metrics is None


def test_evaluate_refuses_spot_well_below_a_call_wall(candidate):
    s = GammaMoveStrategy(GammaMoveConfig(enabled=True, max_premium_at_risk_inr=60_000))
    far = replace(candidate, spot=1200.0)
    sig = s.evaluate(far, triggering(), now_ms=BASE_MS, today=TODAY, regime="up")
    assert sig.state == "watching"
    assert "has not broken" in (sig.reason or "")


def test_evaluate_still_arms_the_wall_on_the_level(candidate):
    s = GammaMoveStrategy(GammaMoveConfig(enabled=True, max_premium_at_risk_inr=60_000))
    wall = replace(candidate, oi=6_000_000, chain_oi_max=6_000_000, spot=1298.0)
    sig = s.evaluate(wall, triggering(), now_ms=BASE_MS, today=TODAY, regime="up")
    assert sig.state == "armed"


def test_wall_flag_off_allows_a_runner_up(candidate):
    s = GammaMoveStrategy(GammaMoveConfig(
        enabled=True, max_premium_at_risk_inr=60_000, require_chain_max_oi=False))
    far = replace(candidate, oi=100_000, chain_oi_max=6_000_000)
    sig = s.evaluate(far, triggering(), now_ms=BASE_MS, today=TODAY, regime="up")
    assert sig.state == "armed"


def test_through_flag_off_allows_spot_short_of_the_wall(candidate):
    s = GammaMoveStrategy(GammaMoveConfig(
        enabled=True, max_premium_at_risk_inr=60_000,
        require_spot_through_strike=False))
    far = replace(candidate, spot=1200.0)
    sig = s.evaluate(far, triggering(), now_ms=BASE_MS, today=TODAY, regime="up")
    assert sig.state == "armed"
