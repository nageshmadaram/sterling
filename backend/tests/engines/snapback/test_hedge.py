"""The hedge, and the two ways it could quietly stop being one.

A hedge that is not exact stops being a hedge and becomes a second position. The
first version of this used a bought index CALL and beat an exact hedge by almost
a point — because a call is convex and the sample rose. These check the
properties that keep the linear version honest.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.engines.snapback import Bars
from app.engines.snapback.hedge import (BETA_BOUNDS, BETA_WINDOW, FuturesCost,
                                        apply, clear_beta_cache, hedge_notional,
                                        market_pnl, rolling_beta)

DAY = 86_400.0


def tape(close: np.ndarray, start: float = 1_600_000_000.0) -> Bars:
    close = np.asarray(close, dtype=float)
    n = len(close)
    return Bars(time=start + np.arange(n) * DAY, open=close,
                high=close * 1.004, low=close * 0.996, close=close,
                volume=np.full(n, 1000.0))


class TestBeta:
    def test_a_name_that_IS_the_market_has_a_beta_of_one(self):
        rng = np.random.default_rng(3)
        m = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300)))
        b = rolling_beta(tape(m.copy()), tape(m))
        assert b
        assert all(abs(v - 1.0) < 1e-6 for v in b.values())

    def test_a_name_that_moves_twice_the_market_has_a_beta_of_two(self):
        rng = np.random.default_rng(4)
        mr = rng.normal(0, 0.01, 300)
        m = 100 * np.exp(np.cumsum(mr))
        s = 100 * np.exp(np.cumsum(2 * mr))
        b = rolling_beta(tape(s), tape(m))
        assert all(abs(v - 2.0) < 1e-6 for v in b.values())

    def test_is_causal(self):
        """A beta stamped on a session must not move when a LATER bar changes.

        The property that decides whether the hedge was sizeable at the time.
        """
        rng = np.random.default_rng(5)
        mr = rng.normal(0, 0.01, 400)
        m = 100 * np.exp(np.cumsum(mr))
        s = 100 * np.exp(np.cumsum(0.8 * mr + rng.normal(0, 0.005, 400)))
        clear_beta_cache()
        a = rolling_beta(tape(s), tape(m))
        moved = s.copy()
        moved[300:] *= 1.5
        clear_beta_cache()
        c = rolling_beta(tape(moved), tape(m))
        # Scaling the LEVEL from bar 300 creates a return AT bar 300, which
        # legitimately enters the trailing window of bars 301 onwards. Causality
        # says bars up to and including i — so only sessions at index <= 300 are
        # expected to be untouched, and the keys start at index window+1.
        safe = sorted(a)[:300 - BETA_WINDOW]
        assert len(safe) > 200
        for d in safe:
            assert a[d] == pytest.approx(c[d], abs=1e-9)

    def test_is_CLAMPED(self):
        """An unclamped regression on a quiet window yields betas above three,
        and a hedge sized off one is bigger than the trade it neutralises."""
        rng = np.random.default_rng(6)
        mr = rng.normal(0, 0.002, 300)
        m = 100 * np.exp(np.cumsum(mr))
        s = 100 * np.exp(np.cumsum(8 * mr))
        b = rolling_beta(tape(s), tape(m))
        lo, hi = BETA_BOUNDS
        assert b and all(lo <= v <= hi for v in b.values())

    def test_aligns_on_the_CALENDAR_not_the_bar_index(self):
        """Two instruments listed at different times have different bar counts
        for the same session; pairing by position regresses one name's June on
        another's July."""
        rng = np.random.default_rng(7)
        mr = rng.normal(0, 0.01, 400)
        m = 100 * np.exp(np.cumsum(mr))
        # The name starts 100 sessions later, on the SAME calendar.
        s = 50 * np.exp(np.cumsum(2 * mr[100:]))
        b = rolling_beta(tape(s, start=1_600_000_000.0 + 100 * DAY), tape(m))
        assert b
        assert all(abs(v - 2.0) < 1e-6 for v in b.values())

    def test_too_short_a_tape_is_empty_not_an_error(self):
        assert rolling_beta(tape(np.full(10, 100.0)), tape(np.full(10, 100.0))) == {}

    def test_the_cache_is_transparent(self):
        rng = np.random.default_rng(8)
        m = tape(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300))))
        s = tape(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300))))
        clear_beta_cache()
        first = dict(rolling_beta(s, m))
        assert rolling_beta(s, m) == first


class TestMarketPnl:
    def test_a_flat_market_contributes_nothing(self):
        assert market_pnl(delta=-0.7, beta=1.2, spot_in=1000, qty=500,
                          market_in=23000, market_out=23000) == 0.0

    def test_a_put_LOSES_when_the_market_rises(self):
        v = market_pnl(delta=-0.7, beta=1.2, spot_in=1000, qty=500,
                       market_in=23000, market_out=23460)
        assert v < 0

    def test_scales_with_beta(self):
        args = dict(delta=-0.7, spot_in=1000, qty=500,
                    market_in=23000, market_out=23460)
        assert market_pnl(beta=2.0, **args) == pytest.approx(
            2 * market_pnl(beta=1.0, **args))

    def test_hedge_notional_is_unsigned(self):
        assert hedge_notional(delta=-0.7, beta=1.2, spot_in=1000, qty=500) > 0


class TestApply:
    def test_removes_exactly_the_market_component(self):
        """A LINEAR hedge offsets by construction. That is the whole reason it
        is a future and not a bought call: a call is convex and over a rising
        sample gains MORE than the put's market loss, which makes it a second
        position rather than a hedge."""
        args = dict(delta=-0.7, beta=1.2, spot_in=1000, qty=500,
                    market_in=23000, market_out=23460)
        removed_expected = market_pnl(**args)
        net, removed, charged = apply(10_000.0, cost=FuturesCost(), **args)
        assert removed == pytest.approx(removed_expected)
        assert net == pytest.approx(10_000.0 - removed_expected - charged)

    def test_a_falling_market_is_removed_TOO(self):
        """The hedge is symmetric. One that only gave back losses and kept the
        gains would be a claim, not a hedge."""
        _, removed, _ = apply(0.0, delta=-0.7, beta=1.0, spot_in=1000, qty=500,
                              market_in=23000, market_out=22000)
        assert removed > 0                     # the put GAINED from the fall
        net, _, charged = apply(5_000.0, delta=-0.7, beta=1.0, spot_in=1000,
                                qty=500, market_in=23000, market_out=22000)
        assert net < 5_000.0 - charged

    def test_the_hedge_is_never_free(self):
        _, _, charged = apply(0.0, delta=-0.7, beta=1.2, spot_in=1000, qty=500,
                              market_in=23000, market_out=23000)
        assert charged > 0


class TestFuturesCost:
    def test_is_orders_of_magnitude_cheaper_per_rupee_than_the_option_schedule(self):
        """Charging the option schedule against an index NOTIONAL overstates the
        cost by roughly two orders of magnitude. This repo has shipped a number
        from that mistake."""
        from app.engines.snapback.backtest import CostModel
        notional = 1_000_000.0
        fut = FuturesCost().round_trip(notional) / notional
        opt = CostModel().charges(200.0, 200.0, 5000) / notional
        assert fut < opt

    def test_scales_with_notional(self):
        c = FuturesCost()
        assert c.round_trip(2_000_000) > c.round_trip(1_000_000)

    def test_zero_notional_costs_nothing(self):
        assert FuturesCost().round_trip(0) == 0.0
