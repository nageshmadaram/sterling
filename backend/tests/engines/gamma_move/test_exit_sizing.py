"""Stops, exits and size — including the three ways size can legitimately be zero."""
from __future__ import annotations

import pytest

from app.engines.gamma_move import (GammaMoveConfig, InstrumentRef, PositionState,
                                    TradeRecord, align_to_tick, exit_order_price,
                                    initial_stop, lots_for, realised_inr,
                                    risk_multiplier, should_exit, sizing_blocker,
                                    swing_low_stop, target_price, update_trail,
                                    weekday_sessions_held)
from tests.engines.gamma_move.conftest import bar

CFG = GammaMoveConfig()


def series(low=45.0, close=50.0, n=8):
    return [bar(0, i, close=close, low=low) for i in range(n)]


def test_stop_is_the_options_own_swing_low():
    assert swing_low_stop(series(low=45.0), CFG) == 45.0


def test_percent_floor_caps_a_far_swing_low():
    assert initial_stop(100.0, series(low=30.0), CFG) == 70.0


def test_swing_low_wins_when_it_is_tighter():
    assert initial_stop(100.0, series(low=90.0), CFG) == 90.0


def test_inverted_stop_is_a_rejected_setup():
    assert initial_stop(50.0, series(low=60.0), CFG) is None


def test_target_only_under_percent_target():
    assert target_price(100.0, CFG) is None
    cfg = GammaMoveConfig(exit_policy="PERCENT_TARGET", target_pct=50)
    assert target_price(100.0, cfg) == 150.0


def position(**kw):
    inst = InstrumentRef(instrument_id="1", tradingsymbol="X26SEP1CE",
                         option_type="CE", strike=100.0, expiry="2026-09-29")
    base = dict(signal_id="s", instrument=inst, entry=100.0, stop=70.0, quantity=500,
                lots=1, entered_ms=0, entry_day="2026-09-20")
    base.update(kw)
    return PositionState(**base)


def test_stop_fires_before_anything_else():
    assert should_exit(position(), 69.0, 0, "2026-09-20", CFG) == "stop"


def test_time_stop_counts_sessions_not_hours():
    pos = position()
    assert should_exit(pos, 100.0, 0, "2026-09-20", CFG) is None
    assert should_exit(pos, 100.0, 0, "2026-09-21", CFG) is None
    assert should_exit(pos, 100.0, 0, "2026-09-22", CFG) == "time_stop"


def test_weekend_is_one_session_not_three_nights():
    pos = position(entry_day="2026-09-18")
    assert weekday_sessions_held("2026-09-18", "2026-09-21") == 1
    assert should_exit(pos, 100.0, 0, "2026-09-21", CFG) is None
    assert should_exit(pos, 100.0, 0, "2026-09-22", CFG) == "time_stop"


def test_trail_only_ratchets_up():
    cfg = GammaMoveConfig(exit_policy="TRAILING_STOP", trail_pct=20, stop_percent=30)
    pos = position()
    update_trail(pos, 200.0, cfg)
    first = pos.trail
    update_trail(pos, 120.0, cfg)
    assert pos.trail == first


def test_trail_waits_for_the_start_threshold():
    cfg = GammaMoveConfig(exit_policy="TRAILING_STOP", trail_pct=20, trail_start_pct=50)
    pos = position()
    update_trail(pos, 120.0, cfg)
    assert pos.trail is None
    update_trail(pos, 160.0, cfg)
    assert pos.trail is not None


def test_exit_price_aligns_to_the_tick():
    assert exit_order_price(53.037, 0.05) == 53.05


def test_buy_limit_floors_to_the_tick():
    assert align_to_tick(53.037, 0.05, side="buy") == 53.00
    assert align_to_tick(53.00, 0.05, side="buy") == 53.00


def test_realised_is_per_unit_times_quantity():
    assert realised_inr(position(), 110.0) == 5000.0


def test_realised_uses_the_fill_not_the_intended_entry():
    pos = position(entry=100.0, fill_price=102.0, quantity=500)
    assert realised_inr(pos, 110.0) == 4000.0


class TestSizing:
    def test_risk_budget_sets_the_size(self):
        assert lots_for(50.0, 40.0, 500, CFG) == 1

    def test_premium_outlay_cap_can_bind_instead(self):
        cfg = GammaMoveConfig(max_premium_at_risk_inr=20_000)
        assert lots_for(53.0, 45.0, 500, cfg) == 0
        assert "outlay cap" in (sizing_blocker(53.0, 45.0, 500, cfg) or "")

    def test_risk_budget_blocker_names_the_budget(self):
        cfg = GammaMoveConfig(capital_inr=10_000)
        assert "risk budget" in (sizing_blocker(50.0, 40.0, 500, cfg) or "")

    def test_inverted_stop_blocker(self):
        assert "not below entry" in (sizing_blocker(40.0, 50.0, 500, CFG) or "")

    def test_no_blocker_when_the_size_is_fine(self):
        assert sizing_blocker(50.0, 40.0, 500, CFG) is None


class TestDescaleLadder:
    @staticmethod
    def rec(*pnls):
        r = TradeRecord()
        for p in pnls:
            r.record(p, "d", descale_after=CFG.descale_after_losses,
                     rescale_after=CFG.rescale_after_wins)
        return r

    def test_full_size_until_the_streak(self):
        assert risk_multiplier(self.rec(-100, -100), CFG) == 1.0

    def test_halves_at_the_threshold(self):
        assert risk_multiplier(self.rec(-100, -100, -100), CFG) == 0.5

    def test_continuing_streak_cuts_again_to_a_quarter(self):
        assert risk_multiplier(self.rec(*([-100] * 6)), CFG) == 0.25

    def test_four_losses_stay_at_half_until_the_next_packet(self):
        assert risk_multiplier(self.rec(-100, -100, -100, -100), CFG) == 0.5

    def test_one_winner_does_not_restore_full_size(self):
        assert risk_multiplier(self.rec(-100, -100, -100, 100), CFG) == 0.5

    def test_restores_after_the_required_wins(self):
        assert risk_multiplier(self.rec(-100, -100, -100, 100, 100), CFG) == 1.0

    def test_relapses_on_a_fresh_streak(self):
        r = self.rec(-100, -100, -100, 100, 100, -100, -100, -100)
        assert risk_multiplier(r, CFG) == 0.5

    def test_descaling_actually_shrinks_the_order(self):
        r = self.rec(-100, -100, -100)
        big = GammaMoveConfig(capital_inr=5_000_000, max_premium_at_risk_inr=10_000_000)
        assert lots_for(50.0, 40.0, 500, big, r) < lots_for(50.0, 40.0, 500, big)

    def test_lots_mode_also_shrinks(self):
        r = self.rec(-100, -100, -100)
        cfg = GammaMoveConfig(sizing_mode="LOTS", lots=2, max_premium_at_risk_inr=10_000_000)
        assert lots_for(50.0, 40.0, 500, cfg) == 2
        assert lots_for(50.0, 40.0, 500, cfg, r) == 1

    def test_record_round_trips(self):
        r = self.rec(-100, -100, -100)
        clone = TradeRecord.from_dict(r.as_dict())
        assert clone.descale_step == 1 and clone.descaled
        assert risk_multiplier(clone, CFG) == 0.5
