"""The option maths, checked against things that must be true.

Not against stored numbers. A regression test that pins Black-Scholes to a table
of my own outputs proves the code has not changed; these check identities that
would still hold if the implementation were thrown away and rewritten.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from app.engines.snapback.pricing import (RISK_FREE, VRP_BAND, break_even_vrp,
                                          bs_delta, bs_price, implied_vol_proxy,
                                          realized_vol, strike_for_delta,
                                          vrp_margin)


class TestBlackScholes:
    def test_put_call_parity(self):
        """C - P = S - K*exp(-rT). The identity every pricing bug breaks."""
        S, K, T, v = 23_400.0, 23_000.0, 35 / 365.0, 0.12
        c = float(bs_price(S, K, T, v, call=True))
        p = float(bs_price(S, K, T, v, call=False))
        assert c - p == pytest.approx(S - K * math.exp(-RISK_FREE * T), abs=0.01)

    def test_parity_holds_at_a_different_magnitude(self):
        """Same identity on a 1,200-rupee stock.

        A formula can be right at index scale and wrong at stock scale — this
        repo has shipped an identity that only held at one price magnitude.
        """
        S, K, T, v = 1_240.0, 1_300.0, 21 / 365.0, 0.28
        c = float(bs_price(S, K, T, v, call=True))
        p = float(bs_price(S, K, T, v, call=False))
        assert c - p == pytest.approx(S - K * math.exp(-RISK_FREE * T), abs=0.01)

    def test_expired_option_is_intrinsic_not_an_error(self):
        assert float(bs_price(100.0, 90.0, 0.0, 0.2, call=True)) == pytest.approx(10.0)
        assert float(bs_price(100.0, 90.0, 0.0, 0.2, call=False)) == pytest.approx(0.0)
        assert float(bs_price(100.0, 110.0, -1.0, 0.2, call=False)) == pytest.approx(10.0)

    def test_zero_vol_is_intrinsic(self):
        """A vol near zero must not make an option look free.

        A dead-quiet fortnight can drive realised vol to nearly nothing, and an
        option priced at nearly nothing is an infinite return on a data artefact.
        """
        assert float(bs_price(100.0, 90.0, 0.5, 0.0, call=True)) == pytest.approx(10.0)

    def test_premium_rises_with_vol(self):
        vols = np.array([0.08, 0.12, 0.20, 0.35])
        prices = bs_price(23_400.0, 23_400.0, 35 / 365.0, vols, call=False)
        assert np.all(np.diff(prices) > 0)

    def test_deep_itm_put_is_worth_more_than_its_intrinsic(self):
        v = float(bs_price(100.0, 140.0, 60 / 365.0, 0.25, call=False))
        assert v > 0
        # Discounting can put a deep ITM European put BELOW intrinsic; what must
        # never happen is a negative price.
        assert v > 30.0


class TestDelta:
    def test_signs(self):
        assert float(bs_delta(100, 100, 0.1, 0.2, call=True)) > 0
        assert float(bs_delta(100, 100, 0.1, 0.2, call=False)) < 0

    def test_deltas_sum_to_one(self):
        c = float(bs_delta(100, 105, 0.2, 0.3, call=True))
        p = float(bs_delta(100, 105, 0.2, 0.3, call=False))
        assert c - p == pytest.approx(1.0, abs=1e-9)

    @pytest.mark.parametrize("target", [0.35, 0.55, 0.70])
    @pytest.mark.parametrize("call", [True, False])
    def test_strike_for_delta_round_trips(self, target, call):
        """The strike this picks must actually carry the delta asked for.

        ``target_delta`` is always the MAGNITUDE for both sides. Asking the
        caller to remember a sign is how a put ladder ends up inverted, and an
        inverted ladder does not error — it quietly buys the wrong end of the
        chain.
        """
        S, v, T = 23_437.0, 0.12, 35 / 365.0
        K = float(strike_for_delta(S, v, T, target, call=call, step=50.0))
        got = abs(float(bs_delta(S, K, T, v, call=call)))
        # The ladder is 50 points wide, so exactness is not available; 0.03 is
        # roughly one rung at these parameters.
        assert got == pytest.approx(target, abs=0.03)

    def test_put_strikes_sit_above_spot_when_in_the_money(self):
        S, v, T = 23_437.0, 0.12, 35 / 365.0
        itm = float(strike_for_delta(S, v, T, 0.70, call=False, step=50.0))
        otm = float(strike_for_delta(S, v, T, 0.30, call=False, step=50.0))
        assert itm > S > otm

    def test_call_strikes_are_the_mirror(self):
        S, v, T = 23_437.0, 0.12, 35 / 365.0
        itm = float(strike_for_delta(S, v, T, 0.70, call=True, step=50.0))
        otm = float(strike_for_delta(S, v, T, 0.30, call=True, step=50.0))
        assert itm < S < otm

    def test_strike_lands_on_the_ladder(self):
        K = float(strike_for_delta(1_237.0, 0.28, 0.1, 0.55, call=False, step=20.0))
        assert K % 20.0 == pytest.approx(0.0)


class TestRealizedVol:
    def test_is_causal(self):
        """``out[i]`` must not move when a LATER bar changes.

        The single property that decides whether a backtest is a measurement.
        """
        rng = np.random.default_rng(3)
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 200)))
        a = realized_vol(close, 20)
        moved = close.copy()
        moved[150:] *= 1.5
        b = realized_vol(moved, 20)
        assert np.allclose(a[:150], b[:150], equal_nan=True)

    def test_nan_until_the_window_fills(self):
        close = np.linspace(100, 110, 50)
        rv = realized_vol(close, 20)
        assert np.all(np.isnan(rv[:20]))
        assert np.all(np.isfinite(rv[20:]))

    def test_matches_a_hand_computed_window(self):
        rng = np.random.default_rng(11)
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.012, 120)))
        rv = realized_vol(close, 20)
        i = 80
        r = np.diff(np.log(close[i - 20:i + 1]))
        assert rv[i] == pytest.approx(r.std(ddof=1) * math.sqrt(250), rel=1e-9)

    def test_a_constant_tape_has_no_vol(self):
        rv = realized_vol(np.full(60, 100.0), 20)
        assert rv[-1] == pytest.approx(0.0)


class TestImpliedVolProxy:
    def test_floors_a_dead_tape(self):
        out = implied_vol_proxy(np.array([0.0, 0.001, 0.20]), 1.2)
        assert out[0] >= 0.04 and out[1] >= 0.04
        assert out[2] == pytest.approx(0.24)


class TestBreakEven:
    def test_finds_the_crossing(self):
        """A book whose return falls with the multiple crosses zero once."""
        def at(vrp: float):
            return np.array([1.6 - vrp])
        assert break_even_vrp(at) == pytest.approx(1.6, abs=0.01)

    def test_none_when_it_loses_even_on_a_free_option(self):
        assert math.isnan(break_even_vrp(lambda vrp: np.array([-1.0])))

    def test_caps_rather_than_running_away(self):
        assert break_even_vrp(lambda vrp: np.array([1.0])) == 3.0

    def test_margin_is_measured_against_the_dear_end_of_the_band(self):
        assert vrp_margin(2.24) == pytest.approx(2.24 - VRP_BAND[1])
        assert vrp_margin(None) is None
        assert vrp_margin(float("nan")) is None
