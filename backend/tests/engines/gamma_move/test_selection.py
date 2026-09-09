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


def test_chain_wall_refuses_when_unmeasured():
    assert not is_chain_wall(100, None, required=True)
    assert not is_chain_wall(100, 500, required=True)
    assert is_chain_wall(500, 500, required=True)
    assert is_chain_wall(100, 500, required=False)
    assert is_chain_wall(100, None, required=False)


def triggering():
    return quiet_session() + [bar(0, 24, oi=96_000, volume=5_000, close=53.0)]


def test_evaluate_refuses_when_the_wall_was_never_measured(candidate):
    s = GammaMoveStrategy(GammaMoveConfig(enabled=True, max_premium_at_risk_inr=60_000))
    unmeasured = replace(candidate, chain_oi_max=None)
    sig = s.evaluate(unmeasured, triggering(), now_ms=BASE_MS, today=TODAY, regime="up")
    assert sig.state == "watching"
    assert "not measured" in (sig.reason or "")


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


def test_select_expiry_fallback_mid_month():
    from app.engines.gamma_move import select_expiry
    today = date(2026, 9, 10)
    expiries = ["2026-09-29", "2026-10-29"]
    cfg = GammaMoveConfig(expiry_dte_max=14, expiry_selection="nearest")
    # 2026-09-29 is 19 DTE (>14), but nearest fallback picks it
    picked = select_expiry(expiries, today, cfg)
    assert picked == "2026-09-29"


def test_is_chain_wall_tolerance():
    # 94% of chain max qualifies under 10% tolerance
    assert is_chain_wall(1_553_725, 1_652_050, required=True, tolerance=0.10)
    # 85% of chain max fails under 10% tolerance
    assert not is_chain_wall(1_400_000, 1_652_050, required=True, tolerance=0.10)


def test_signal_serialization_roundtrip(candidate):
    from app.engines.gamma_move import GammaSignal
    s = GammaMoveStrategy(GammaMoveConfig(enabled=True, max_premium_at_risk_inr=60_000))
    wall = replace(candidate, oi=6_000_000, chain_oi_max=6_000_000, spot=1298.0)
    sig = s.evaluate(wall, triggering(), now_ms=BASE_MS, today=TODAY, regime="up")
    d = sig.as_dict()
    reconstituted = GammaSignal.from_dict(d)
    assert reconstituted.id == sig.id
    assert reconstituted.state == sig.state
    assert reconstituted.candidate.underlying == sig.candidate.underlying
    assert reconstituted.candidate.oi == sig.candidate.oi
    assert reconstituted.candidate.chain_oi_max == sig.candidate.chain_oi_max
    assert reconstituted.entry == sig.entry
    assert reconstituted.stop == sig.stop
