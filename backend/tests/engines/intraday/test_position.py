"""The position: two price ladders, the premium trail, and which exit wins.

These are the rules that decide what a trade is worth, and every one of them
has a failure mode this repo has already paid for once — a bought PE treated as
a short, a stop that ratchets the wrong way, an R measured against a stop that
has already moved.
"""
from __future__ import annotations

import pytest

from app.engines.intraday import IntradayConfig
from app.engines.intraday.position import (ContractRef, IntradayPosition,
                                           align_to_tick, premium_stop_for,
                                           should_exit, spot_trail, update_trail)


def cfg(**over) -> IntradayConfig:
    base = dict(warmup_bars=70)
    base.update(over)
    return IntradayConfig(**base).validate()


def pos(**over) -> IntradayPosition:
    base = dict(
        strategy="pivot_break", signal_id="s1", underlying="NIFTY",
        contract=ContractRef("NIFTY26SEP24800CE", "NFO", 1234, "CE", 24800.0,
                             "2026-09-17", 75, 0.05),
        thesis="BULLISH", spot_entry=24800.0, spot_stop=24780.0,
        spot_target=24840.0, spot_risk=20.0,
        entry=100.0, fill_price=100.0, stop=70.0, initial_stop=70.0, target=160.0,
        quantity=75, lots=1, peak=100.0, status="open",
    )
    base.update(over)
    return IntradayPosition(**base)


class TestTheTwoLadders:
    def test_a_bought_put_is_still_a_long_position(self):
        """The bug this field exists to prevent: a bearish thesis is a BOUGHT
        option, and reading the thesis as the order side sells it at entry."""
        p = pos(thesis="BEARISH", contract=ContractRef("NIFTY26SEP24800PE",
                                                       option_type="PE"))
        assert p.side == "long"

    def test_r_is_measured_against_the_stop_at_entry_not_the_trailed_one(self):
        p = pos(stop=95.0)          # the trail has already moved it up
        # Risk was 30 points of premium, so 130 is +1R — not +7R against 95.
        assert p.r_multiple(130.0) == pytest.approx(1.0)

    def test_unrealised_uses_units_not_lots(self):
        # Confusing the two is a lot-size-multiple error: 75× here.
        assert pos().unrealised_inr(110.0) == pytest.approx(750.0)

    def test_the_effective_entry_is_the_fill_when_there_is_one(self):
        assert pos(fill_price=0.0, entry=100.0).effective_entry == 100.0
        assert pos(fill_price=103.5).effective_entry == 103.5


class TestThePremiumStop:
    def test_without_a_delta_it_falls_back_to_a_percentage(self):
        assert premium_stop_for(100.0, 24800.0, 24780.0, cfg(premium_stop_pct=30.0)) \
            == pytest.approx(70.0)

    def test_a_delta_converts_the_spot_stop_into_premium_points(self):
        # 20 spot points × delta 0.5 = 10 premium points, which is nearer than
        # the 30% fallback, so the nearer (safer) of the two wins.
        assert premium_stop_for(100.0, 24800.0, 24780.0,
                                cfg(premium_stop_pct=30.0), delta=0.5) \
            == pytest.approx(90.0)

    def test_a_delta_that_would_put_the_stop_below_zero_is_refused(self):
        """A spot-derived stop that assumes too much delta produces a stop the
        position can never reach, which is a position with no stop at all."""
        out = premium_stop_for(10.0, 24800.0, 24000.0, cfg(), delta=1.0)
        assert out > 0

    def test_a_nonsense_delta_is_ignored_rather_than_trusted(self):
        assert premium_stop_for(100.0, 24800.0, 24780.0, cfg(), delta=7.0) \
            == pytest.approx(70.0)


class TestTheTrail:
    def test_nothing_moves_before_the_activation_r(self):
        p = pos(peak=115.0)          # +0.5R only
        assert update_trail(p, 115.0, cfg(trail_activate_r=1.0)) == (70.0, "")

    def test_the_stop_goes_to_breakeven_at_one_r(self):
        p = pos(peak=130.0)
        stop, why = update_trail(p, 130.0, cfg(trail_activate_r=1.0,
                                               premium_trail_pct=50.0))
        assert stop == pytest.approx(100.0) and why == "breakeven"

    def test_then_it_rides_the_give_back_cap(self):
        p = pos(peak=200.0, breakeven_done=True, stop=100.0)
        stop, why = update_trail(p, 200.0, cfg(premium_trail_pct=25.0))
        assert stop == pytest.approx(150.0)       # 25% off the peak
        assert "trail" in why

    def test_the_stop_never_ratchets_backwards(self):
        p = pos(peak=200.0, breakeven_done=True, stop=180.0)
        stop, why = update_trail(p, 120.0, cfg(premium_trail_pct=25.0))
        assert stop == pytest.approx(180.0) and why == ""

    def test_a_dead_quote_moves_nothing(self):
        p = pos(peak=200.0, breakeven_done=True, stop=150.0)
        assert update_trail(p, 0.0, cfg()) == (150.0, "")


class TestWhichExitWins:
    def test_a_tick_through_both_stop_and_target_is_a_loss(self):
        """Assuming the good fill is the most common way a replay flatters
        itself, and the live path must not disagree with the replay."""
        p = pos(stop=70.0, target=160.0)
        assert should_exit(p, 70.0) == (True, "stop")

    def test_a_trailed_stop_is_named_as_one(self):
        p = pos(stop=120.0, breakeven_done=True)
        assert should_exit(p, 119.0) == (True, "trailing stop")

    def test_the_target_closes_it(self):
        assert should_exit(pos(), 161.0) == (True, "target")

    def test_the_spot_thesis_can_exit_a_stale_contract(self):
        """An illiquid option can sit at a stale premium straight through the
        level the entry was taken against."""
        assert should_exit(pos(), 100.0, spot=24775.0) == (True, "spot stop")
        assert should_exit(pos(thesis="BEARISH", spot_stop=24820.0), 100.0,
                           spot=24825.0) == (True, "spot stop")

    def test_a_bearish_thesis_is_not_exited_by_spot_falling(self):
        p = pos(thesis="BEARISH", spot_stop=24820.0)
        assert should_exit(p, 100.0, spot=24700.0)[0] is False

    def test_the_session_end_closes_what_is_left(self):
        assert should_exit(pos(), 120.0, session_over=True) == (True, "session end")

    def test_a_broken_rule_closes_it_with_its_own_reason(self):
        assert should_exit(pos(), 120.0, rule_broken="ribbon crossed back") \
            == (True, "ribbon crossed back")

    def test_a_healthy_position_stays_open(self):
        assert should_exit(pos(), 120.0, spot=24810.0) == (False, "")


def test_a_buy_rounds_up_to_the_tick_and_a_sell_rounds_down():
    """The wrong way produces a limit the exchange will not fill and a position
    sitting unprotected behind a resting order."""
    assert align_to_tick(100.02, 0.05, side="buy") == pytest.approx(100.05)
    assert align_to_tick(100.02, 0.05, side="sell") == pytest.approx(100.0)
    assert align_to_tick(100.05, 0.05, side="buy") == pytest.approx(100.05)


class TestTheSpotTrail:
    """The trail that protects the THESIS, not the money.

    An option can hold its premium on vega while the underlying walks back
    through the level the trade was taken against, and it can bleed premium to
    theta while the underlying does exactly what the entry predicted. Neither
    trail replaces the other.
    """

    def _pb(self, **over):
        base = dict(strategy="pivot_break", spot_entry=24800.0, spot_stop=24780.0,
                    spot_risk=20.0)
        base.update(over)
        return pos(**base)

    def test_nothing_moves_before_the_breakeven_r(self):
        p = self._pb()
        assert spot_trail(p, cfg(pb_breakeven_at_r=1.0), spot=24810.0,
                          atr=10.0, swing=24795.0) == (24780.0, "")

    def test_at_one_r_the_spot_stop_goes_to_the_spot_entry(self):
        p = self._pb()
        stop, why = spot_trail(p, cfg(pb_breakeven_at_r=1.0, pb_trail_mode="breakeven"),
                               spot=24825.0, atr=10.0)
        assert stop == pytest.approx(24800.0) and why == "spot breakeven"

    def test_the_atr_mode_follows_price(self):
        p = self._pb()
        stop, why = spot_trail(p, cfg(pb_trail_mode="atr", pb_trail_atr_mult=1.5),
                               spot=24900.0, atr=20.0)
        assert stop == pytest.approx(24870.0) and "ATR" in why

    def test_the_structure_mode_follows_the_swing(self):
        p = self._pb()
        stop, why = spot_trail(p, cfg(pb_trail_mode="structure"),
                               spot=24900.0, swing=24860.0)
        assert stop == pytest.approx(24860.0) and "swing" in why

    def test_none_leaves_the_stop_where_the_rule_put_it(self):
        p = self._pb()
        assert spot_trail(p, cfg(pb_trail_mode="none"), spot=24900.0,
                          atr=20.0, swing=24860.0) == (24780.0, "")

    def test_it_never_ratchets_backwards(self):
        p = self._pb(spot_stop=24880.0)
        stop, why = spot_trail(p, cfg(pb_trail_mode="structure"),
                               spot=24900.0, swing=24810.0)
        assert stop == pytest.approx(24880.0) and why == ""

    def test_the_ribbon_has_no_spot_trail_because_it_is_held_to_the_cross(self):
        p = pos(strategy="ma_ribbon", spot_entry=24800.0, spot_stop=24780.0,
                spot_risk=20.0)
        assert spot_trail(p, cfg(), spot=24900.0, atr=10.0, swing=24870.0) \
            == (24780.0, "")

    def test_vwap_supertrend_follows_vwap_once_its_points_are_banked(self):
        p = pos(strategy="vwap_supertrend", spot_entry=24800.0, spot_stop=24830.0,
                spot_risk=30.0, thesis="BEARISH")
        c = cfg(vs_trail_after_points=12.0)
        # Only 5 points in — the specification says wait.
        assert spot_trail(p, c, spot=24795.0, vwap=24815.0) == (24830.0, "")
        stop, why = spot_trail(p, c, spot=24780.0, vwap=24815.0)
        assert stop == pytest.approx(24815.0) and "VWAP" in why

    def test_a_position_with_no_spot_stop_is_left_alone(self):
        assert spot_trail(pos(spot_stop=0.0), cfg(), spot=24900.0) == (0.0, "")
