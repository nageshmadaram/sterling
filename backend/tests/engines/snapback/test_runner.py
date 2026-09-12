"""Holding past the horizon, and the four ways that invents money if it is wrong.

The motivation is the return distribution rather than a preference: the top 1%
of this book's trades carry 148% of its P&L, so a fixed horizon closes the few
trades that pay for all the rest. The danger is the mirror image — a rule that
"lets winners run" is trivially profitable in a backtest if it also quietly
lets the trade outlive its contract, or if it decides to run using a price the
session had not printed yet.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from app.engines.snapback import Bars, SnapbackConfig
from app.engines.snapback.backtest import replay

DAY = 86_400.0
HOLD = SnapbackConfig().hold_days


def falling_tape(n: int = 240, spike: float = 1.12, after: float = 0.985,
                 tail: int = 60) -> Bars:
    """Noise, one upside break, then a steady fade — a put's best case."""
    rng = np.random.default_rng(5)
    close = 1_200 * np.exp(np.cumsum(rng.normal(0.0002, 0.006, n)))
    close[-1] = close[-2] * spike
    close = np.concatenate([close, close[-1] * np.power(after, np.arange(1, tail + 1))])
    high, low = close * 1.004, close * 0.996
    return Bars(time=1_690_000_000.0 + np.arange(len(close)) * DAY,
                open=close, high=np.maximum(high, close),
                low=np.minimum(low, close), close=close,
                volume=np.full(len(close), 1000.0))


CFG = SnapbackConfig(sizing_mode="LOTS", lots=1, scan_stocks=("RELIANCE",),
                     scan_indices=(), hedge_mode="none", premium_stop_pct=100.0)


def last(cfg, bars, spike_at: int = 240) -> object:
    """The trade the SPIKE produced.

    Not ``trades[-1]``: a tape long enough to hold a runner is long enough to
    fire again, and a later entry that simply ran out of tape would be judged
    instead of the one under test.
    """
    res = replay({"RELIANCE": bars}, cfg)
    want = int(bars.time[spike_at] * 1000)
    hit = [t for t in res.trades if t.entry_ms == want]
    assert hit, f"the spike at bar {spike_at} produced no trade"
    return hit[0]


class TestItOnlyRunsWhenTold:
    def test_the_shipped_runner_is_the_measured_one(self):
        c = SnapbackConfig()
        assert (c.runner_mult, c.runner_trail_pct) == (1.5, 25.0)

    def test_with_the_runner_off_the_horizon_is_the_exit(self):
        t = last(replace(CFG, runner_mult=0.0), falling_tape())
        assert t.reason == "horizon"
        assert t.held_days == HOLD

    def test_a_winner_runs_and_a_horizon_exit_would_have_left_money(self):
        base = last(replace(CFG, runner_mult=0.0), falling_tape())
        run = last(CFG, falling_tape())
        assert run.reason == "runner"
        assert run.held_days > base.held_days
        assert run.net > base.net

    def test_a_trade_below_the_multiple_still_stops_at_the_horizon(self):
        """The rule must not become 'hold everything longer'. A flat tape leaves
        the put near what it cost, so nothing qualifies."""
        flat = falling_tape(after=1.0)
        t = last(replace(CFG, runner_mult=1.3), flat)
        assert t.reason == "horizon"
        assert t.held_days == HOLD


class TestItCannotInventMoney:
    def test_a_runner_never_outlives_its_contract(self):
        cfg = replace(CFG, runner_mult=1.05, runner_trail_pct=0.0)
        t = last(cfg, falling_tape(after=0.97, tail=90))
        assert t.held_days <= cfg.min_dte - 5, (
            "a trade held into the last five days of its contract is priced on "
            "the expiry cliff, which is gamma rather than the drift it is for")

    def test_the_ratchet_closes_on_the_sessions_OWN_worst_price(self):
        """A give-back exit filled at the session close would be a fill at a
        price the ratchet had already been breached before."""
        spike_then_reverse = falling_tape(after=0.97, tail=30)
        c = np.array(spike_then_reverse.close)
        c[-12:] = c[-13] * np.power(1.06, np.arange(1, 13))   # violent reversal
        rev = Bars(spike_then_reverse.time, c, np.maximum(c * 1.004, c),
                   np.minimum(c * 0.996, c), c, spike_then_reverse.volume)
        t = last(replace(CFG, runner_mult=1.2, runner_trail_pct=20.0), rev)
        assert t.reason == "runner"
        assert t.held_days > HOLD

    def test_the_multiple_is_of_what_the_trade_COST(self):
        """A multiple below 1.0 would let every losing trade run, which is the
        opposite rule. Refused rather than clamped."""
        from app.engines.snapback.config import validate
        with pytest.raises(ValueError, match="ABOVE 1.0"):
            validate({"runner_mult": 0.9})
        with pytest.raises(ValueError, match="ABOVE 1.0"):
            validate({"runner_mult": 1.0})
