"""``tape_ended`` has to mean the tape ran out, not "held fewer days than the
horizon".

The board reads this field to decide whether a row is a POSITION the operator
may be holding or a closed trade with a realised exit. The old test — held fewer
sessions than ``hold_days`` — happened to agree with that while the horizon was
the only way out. It stops agreeing the moment a trade may legitimately outlive
its horizon, and it was wrong in the other direction too: a stop that fired on
the tape's last session was relabelled as though no rule had run.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np

from app.engines.snapback import Bars, SnapbackConfig
from app.engines.snapback.backtest import replay

DAY = 86_400.0


def tape(n_noise: int = 240, after: float = 0.985, tail: int = 60) -> Bars:
    rng = np.random.default_rng(5)
    close = 1_200 * np.exp(np.cumsum(rng.normal(0.0002, 0.006, n_noise)))
    close[-1] = close[-2] * 1.12
    close = np.concatenate([close,
                            close[-1] * np.power(after, np.arange(1, tail + 1))])
    return Bars(time=1_690_000_000.0 + np.arange(len(close)) * DAY,
                open=close, high=close * 1.004, low=close * 0.996, close=close,
                volume=np.full(len(close), 1000.0))


CFG = SnapbackConfig(sizing_mode="LOTS", lots=1, scan_stocks=("RELIANCE",),
                     scan_indices=(), hedge_mode="none", premium_stop_pct=100.0,
                     runner_mult=0.0)


def spike_trade(cfg, bars, at: int = 240):
    res = replay({"RELIANCE": bars}, cfg)
    hit = [t for t in res.trades if t.entry_ms == int(bars.time[at] * 1000)]
    assert hit
    return hit[0]


def test_a_runner_cut_short_by_the_tape_is_still_open():
    """Two sessions of tail after the horizon: the runner qualifies and then
    the tape stops. That is an open position, whatever its held count."""
    cfg = replace(CFG, runner_mult=1.2, runner_trail_pct=0.0)
    t = spike_trade(cfg, tape(after=0.97, tail=cfg.hold_days + 2))
    assert t.held_days > cfg.hold_days
    assert t.reason == "tape_ended"


def test_a_completed_runner_is_NOT_marked_tape_ended():
    cfg = replace(CFG, runner_mult=1.2, runner_trail_pct=20.0)
    t = spike_trade(cfg, tape(after=0.97, tail=80))
    assert t.reason in ("runner", "premium_stop")
    assert t.reason != "tape_ended"


def test_a_horizon_exit_on_a_short_tape_is_not_relabelled():
    """The horizon fired. The tape happening to end there changes nothing."""
    t = spike_trade(CFG, tape(after=1.0, tail=CFG.hold_days))
    assert t.held_days == CFG.hold_days
    assert t.reason == "horizon"
