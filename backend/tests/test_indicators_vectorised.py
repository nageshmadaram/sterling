"""The vectorised indicators against the loops they replaced.

EMA, ATR and session VWAP are shared: the Kite engine, Navigator, the intraday
pack and every study script read them. Replacing a Python loop with an IIR
filter is only safe if the numbers do not move, so the loops are kept HERE as
the reference and checked against, rather than kept in the module as dead code
nobody runs.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.engines.indicators.atr import compute_atr, true_range
from app.engines.indicators.ema import compute_ema
from app.engines.intraday.indicators import session_vwap


def loop_ema(values, period):
    n = len(values)
    ema = np.zeros(n)
    if n < period:
        return ema
    k = 2.0 / (period + 1)
    ema[period - 1] = float(np.mean(values[:period]))
    for i in range(period, n):
        ema[i] = values[i] * k + ema[i - 1] * (1.0 - k)
    return ema


def loop_atr(h, l, c, period=14):
    n = len(c)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    atr = np.zeros(n)
    if n <= period:
        return atr
    atr[period] = float(np.mean(tr[1:period + 1]))
    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def loop_vwap(h, l, c, v, starts):
    n = len(c)
    out = np.zeros(n)
    tp = (h + l + c) / 3.0
    cum_pv = cum_v = cum_tp = 0.0
    cum_n = 0
    for i in range(n):
        if starts[i]:
            cum_pv = cum_v = cum_tp = 0.0
            cum_n = 0
        vv = float(v[i]) if np.isfinite(v[i]) else 0.0
        cum_pv += tp[i] * vv
        cum_v += vv
        cum_tp += tp[i]
        cum_n += 1
        out[i] = (cum_pv / cum_v) if cum_v > 0 else (cum_tp / cum_n if cum_n else tp[i])
    return out


def ohlc(n, seed=3):
    rng = np.random.default_rng(seed)
    c = rng.normal(100, 5, n)
    return c + rng.random(n), c - rng.random(n), c


@pytest.mark.parametrize("period", [1, 2, 8, 9, 13, 21, 55, 200])
@pytest.mark.parametrize("n", [0, 1, 7, 55, 201, 400, 3000])
def test_the_ema_is_exactly_what_the_loop_produced(period, n):
    _, _, c = ohlc(n)
    got, want = compute_ema(c, period), loop_ema(c, period)
    assert got.shape == want.shape
    if n:
        # An EMA is a linear recurrence, so the filter form is not merely close.
        assert np.array_equal(got, want)


@pytest.mark.parametrize("period", [1, 2, 14, 18, 50])
@pytest.mark.parametrize("n", [0, 1, 15, 19, 400, 3000])
def test_the_atr_matches_the_loop_to_far_below_a_tick(period, n):
    h, l, c = ohlc(n)
    got, want = compute_atr(h, l, c, period), loop_atr(h, l, c, period)
    assert got.shape == want.shape
    if n:
        # NOT bit-identical: the loop divides by `period` each step where the
        # filter multiplies by 1/period. Same arithmetic, different order, so
        # the last bit or two rounds differently. Recorded rather than hidden.
        assert np.max(np.abs(got - want)) < 1e-9


@pytest.mark.parametrize("n", [0, 1, 5, 75, 400, 3000])
def test_the_session_vwap_matches_the_loop(n):
    h, l, c = ohlc(n)
    rng = np.random.default_rng(8)
    starts = [i % 75 == 0 for i in range(n)]
    for v in (rng.random(n) * 1000, np.zeros(n)):
        got, want = session_vwap(h, l, c, v, starts), loop_vwap(h, l, c, v, starts)
        assert got.shape == want.shape
        if n:
            assert np.max(np.abs(got - want)) < 1e-6


def test_the_vwap_resets_at_every_session_start():
    """The anchor is the point. A VWAP that carried yesterday's volume across
    the open is a different indicator with the same name."""
    n = 150
    h, l, c = ohlc(n)
    v = np.ones(n) * 100.0
    one = session_vwap(h, l, c, v, [i == 0 for i in range(n)])
    two = session_vwap(h, l, c, v, [i % 75 == 0 for i in range(n)])
    assert one[74] == pytest.approx(two[74])          # same first session
    assert one[149] != pytest.approx(two[149])        # second session reset


def test_the_true_range_first_bar_has_no_previous_close():
    h, l, c = ohlc(10)
    assert true_range(h, l, c)[0] == 0.0


def test_a_zero_volume_session_falls_back_to_the_typical_price_mean():
    """Index spot. A volume-weighted average of zero weights is 0/0, and the
    fallback must be a genuine session mean rather than the close — a VWAP that
    collapses onto the close makes every close-vs-VWAP test degenerate."""
    n = 40
    h, l, c = ohlc(n)
    out = session_vwap(h, l, c, np.zeros(n), [i == 0 for i in range(n)])
    tp = (h + l + c) / 3.0
    assert out[-1] == pytest.approx(float(np.mean(tp)))
    assert out[-1] != pytest.approx(float(c[-1]))
