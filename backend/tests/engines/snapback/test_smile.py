"""The volatility skew, and the two traps it exists to close.

This repository has already shipped a number from the FLAT-VOL TRAP once: a
far-out-of-the-money wing priced at ATM vol looked like a +455% edge and became
-79.5% under a realistic smile. Snapback walked into both halves of it in one
afternoon:

* **The out-of-the-money half.** A 0.20-delta put measured +8.31% per entry day
  flat and **-5.97%** at a realistic skew — the cheap wing was cheap only in the
  model. Every low-delta configuration was an artefact.
* **The in-the-money half.** Applying the same steepness ABOVE spot hands an
  in-the-money put a discount no market gives. At 0.60 delta that alone moved
  the book from +1.79% to +4.04% per entry day. This engine BUYS in-the-money
  puts, so a symmetric slope is a flattering assumption, not a neutral one.

Hence two slopes, both swept, neither assumed.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.engines.snapback.pricing import (DEFAULT_SMILE_SLOPE, bs_price,
                                          smile_vol, strike_for_delta)

S, ATM, T = 23_400.0, 0.12, 35 / 365


def put_strike(delta: float) -> float:
    return float(strike_for_delta(S, ATM, T, delta, call=False, step=50.0))


class TestShape:
    def test_zero_slope_is_exactly_the_flat_model(self):
        """The sweep is only meaningful if one code path answers both."""
        for d in (0.2, 0.5, 0.8):
            K = put_strike(d)
            assert float(smile_vol(S, K, ATM, 0.0)) == pytest.approx(ATM)

    def test_an_out_of_the_money_put_costs_MORE_than_flat(self):
        K = put_strike(0.25)
        assert K < S
        assert float(smile_vol(S, K, ATM, DEFAULT_SMILE_SLOPE)) > ATM
        assert float(bs_price(S, K, T, smile_vol(S, K, ATM, DEFAULT_SMILE_SLOPE),
                              call=False)) > float(bs_price(S, K, T, ATM, call=False))

    def test_an_in_the_money_put_costs_LESS_than_flat(self):
        """Equity skew falls as the strike rises. That is the real shape, and it
        is why the shipped 0.70 delta gets cheaper rather than dearer."""
        K = put_strike(0.75)
        assert K > S
        assert float(smile_vol(S, K, ATM, DEFAULT_SMILE_SLOPE)) < ATM

    def test_vol_decreases_monotonically_with_strike(self):
        strikes = [put_strike(d) for d in (0.85, 0.7, 0.5, 0.3, 0.15)]
        vols = [float(smile_vol(S, k, ATM, DEFAULT_SMILE_SLOPE)) for k in strikes]
        assert strikes == sorted(strikes, reverse=True)
        assert vols == sorted(vols)

    def test_the_two_wings_are_independent(self):
        """A flatter in-the-money wing must not change the out-of-the-money one."""
        otm, itm = put_strike(0.25), put_strike(0.75)
        a = float(smile_vol(S, otm, ATM, 1.6, itm_slope=0.0))
        b = float(smile_vol(S, otm, ATM, 1.6, itm_slope=1.6))
        assert a == pytest.approx(b)
        c = float(smile_vol(S, itm, ATM, 1.6, itm_slope=0.0))
        d = float(smile_vol(S, itm, ATM, 1.6, itm_slope=1.6))
        assert c > d                       # flatter wing = less discount

    def test_a_flat_in_the_money_wing_leaves_ATM_vol_alone(self):
        assert float(smile_vol(S, put_strike(0.75), ATM, 1.6, itm_slope=0.0)) \
            == pytest.approx(ATM)


class TestClamp:
    def test_vol_cannot_run_away_in_either_direction(self):
        """A linear-in-log-moneyness skew is a LOCAL approximation. Extrapolated
        it prices a deep contract at a fraction of ATM or a multiple of it, and
        a strike choice must never be rewarded by an unquotable price."""
        for K in (S * 0.4, S * 0.6, S * 1.6, S * 2.5):
            v = float(smile_vol(S, K, ATM, 3.0))
            assert 0.5 * ATM - 1e-9 <= v <= 2.0 * ATM + 1e-9

    def test_the_floor_binds_before_zero(self):
        assert float(smile_vol(S, S * 3, 0.02, 3.0)) > 0


class TestTheTrapItself:
    def test_the_skew_reverses_which_strike_looks_cheap(self):
        """Under flat vol the 0.20-delta put is by far the cheapest per unit of
        delta. Under a realistic skew it is the dearest. That reversal is the
        whole reason this module exists."""
        def per_delta(d: float, slope: float) -> float:
            K = put_strike(d)
            v = float(smile_vol(S, K, ATM, slope))
            return float(bs_price(S, K, T, v, call=False)) / d

        flat_cheap = per_delta(0.20, 0.0) < per_delta(0.70, 0.0)
        skew_cheap = per_delta(0.20, DEFAULT_SMILE_SLOPE) < \
            per_delta(0.70, DEFAULT_SMILE_SLOPE)
        assert flat_cheap, "flat vol should make the wing look cheap"
        assert not skew_cheap, "a realistic skew should reverse that"
