"""The rule, and the three ways it could be quietly wrong.

The properties worth testing here are not "does it fire on this tape". They are:

* **causality** — no feature at bar ``i`` may move when a later bar changes;
* **the window is strictly prior** — including the signal bar's own high makes
  ``close > prior_high`` almost never true, which reads as "rare" rather than
  as "wrong";
* **the mirror is really a mirror** — ``fade_down`` on an inverted tape must
  fire exactly where ``fade_up`` fires on the original.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.engines.snapback import (Bars, SnapbackConfig, entry_indices, evaluate,
                                  evaluate_at, features, fires, to_bars)

DAY = 86_400.0


def tape(close, *, high=None, low=None, start=1_690_000_000.0) -> Bars:
    close = np.asarray(close, dtype=float)
    n = len(close)
    high = np.asarray(high, float) if high is not None else close * 1.004
    low = np.asarray(low, float) if low is not None else close * 0.996
    return Bars(
        time=start + np.arange(n) * DAY,
        open=close, high=np.maximum(high, close), low=np.minimum(low, close),
        close=close, volume=np.full(n, 1_000.0),
    )


def rising_then_spike(n: int = 200, spike: float = 1.10) -> Bars:
    """A quiet tape that ends with one decisive push through its own range."""
    rng = np.random.default_rng(5)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.006, n)))
    close[-1] = float(close[-2] * spike)
    return tape(close)


class TestFeaturesAreCausal:
    def test_nothing_at_bar_i_moves_when_a_later_bar_changes(self):
        cfg = SnapbackConfig()
        b = rising_then_spike(240)
        a = features(b, cfg)
        moved = tape(np.concatenate([b.close[:180], b.close[180:] * 1.3]))
        c = features(moved, cfg)
        cut = 180
        for name in ("ema", "atr", "stretch", "prior_high", "prior_low", "rv"):
            x, y = getattr(a, name)[:cut], getattr(c, name)[:cut]
            assert np.allclose(x, y, equal_nan=True), f"{name} is not causal"

    def test_prior_high_excludes_the_bar_itself(self):
        cfg = SnapbackConfig(lookback_days=5)
        b = tape([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20] * 10)
        f = features(b, cfg)
        i = 60
        assert f.prior_high[i] == pytest.approx(float(np.max(b.high[i - 5:i])))
        assert f.prior_high[i] != pytest.approx(float(np.max(b.high[i - 5:i + 1])))

    def test_stretch_is_zero_while_the_mean_is_still_seeding(self):
        cfg = SnapbackConfig()
        f = features(rising_then_spike(200), cfg)
        assert np.all(f.stretch[:cfg.mean_touch_ema] == 0.0)

    def test_a_zero_range_session_does_not_produce_an_infinite_stretch(self):
        """A halted session has a real high and low and a meaningless range.

        Dividing by it yields an infinite stretch, which passes every threshold
        there is.
        """
        cfg = SnapbackConfig()
        close = np.full(200, 100.0)
        b = Bars(time=1_690_000_000.0 + np.arange(200) * DAY, open=close,
                 high=close.copy(), low=close.copy(), close=close,
                 volume=np.zeros(200))
        f = features(b, cfg)
        assert np.all(np.isfinite(f.stretch))


class TestFiring:
    def test_needs_both_the_breakout_and_the_stretch(self):
        cfg = SnapbackConfig()
        b = rising_then_spike(240)
        f = features(b, cfg)
        i = len(b) - 1
        assert fires(f, i, "fade_up", cfg, b)
        # Raise the stretch bar above what this tape reaches and it stops.
        strict = SnapbackConfig(min_stretch_atr=99.0)
        assert not fires(features(b, strict), i, "fade_up", strict, b)

    def test_fade_down_is_off_by_default(self):
        cfg = SnapbackConfig()
        assert cfg.sides() == ("fade_up",)
        b = rising_then_spike(240, spike=0.90)
        f = features(b, cfg)
        assert not fires(f, len(b) - 1, "fade_down", cfg, b)

    def test_fade_down_is_the_exact_mirror(self):
        """Invert the tape and the other side must fire on the same bars.

        A mirror that is merely 'similar' is where a sign error hides.
        """
        cfg = SnapbackConfig(allow_fade_down=True)
        b = rising_then_spike(240)
        # Reflect prices about a constant: every high becomes a low.
        c2 = 200.0 - b.close
        inv = Bars(time=b.time, open=200.0 - b.open, high=200.0 - b.low,
                   low=200.0 - b.high, close=c2, volume=b.volume)
        up = set(int(i) for i in entry_indices(b, cfg, "fade_up"))
        down = set(int(i) for i in entry_indices(inv, cfg, "fade_down"))
        assert up and up == down

    def test_nothing_fires_before_the_warmup(self):
        cfg = SnapbackConfig()
        b = rising_then_spike(240)
        f = features(b, cfg)
        assert not any(fires(f, i, "fade_up", cfg, b)
                       for i in range(cfg.warmup_bars()))

    def test_min_atr_refuses_a_tape_too_quiet_to_pay_costs(self):
        b = rising_then_spike(240)
        loose = SnapbackConfig(min_atr_bp=0.0)
        tight = SnapbackConfig(min_atr_bp=100_000.0)
        i = len(b) - 1
        assert fires(features(b, loose), i, "fade_up", loose, b)
        assert not fires(features(b, tight), i, "fade_up", tight, b)


class TestCooldown:
    def test_a_grind_to_new_highs_is_one_entry_not_twenty(self):
        cfg = SnapbackConfig(cooldown_days=5, min_stretch_atr=0.5)
        # A relentless ramp satisfies the raw condition on every session.
        close = np.concatenate([np.full(120, 100.0),
                                100 * np.exp(np.arange(60) * 0.01)])
        b = tape(close)
        idx = entry_indices(b, cfg, "fade_up")
        assert len(idx) > 1
        assert np.all(np.diff(idx) >= cfg.cooldown_days)

    def test_zero_cooldown_lets_every_bar_through(self):
        cfg = SnapbackConfig(cooldown_days=0, min_stretch_atr=0.5)
        close = np.concatenate([np.full(120, 100.0),
                                100 * np.exp(np.arange(60) * 0.01)])
        loose = entry_indices(tape(close), cfg, "fade_up")
        strict = entry_indices(tape(close),
                               SnapbackConfig(cooldown_days=10,
                                              min_stretch_atr=0.5), "fade_up")
        assert len(loose) > len(strict)


class TestSignal:
    def test_a_fired_signal_names_the_put_and_the_mean(self):
        cfg = SnapbackConfig()
        b = rising_then_spike(240)
        sigs = evaluate(b, cfg, "NIFTY", catchup_bars=1)
        assert len(sigs) == 1
        s = sigs[0]
        assert s.symbol == "NIFTY"
        assert s.option_type == "PE" and s.direction == "BEARISH"
        assert s.side == "fade_up"
        assert s.mean_target < s.entry          # the fade is back towards the mean
        assert s.stretch >= cfg.min_stretch_atr
        assert s.realized_vol > 0 and s.assumed_iv > s.realized_vol
        assert s.distance_pct > 0
        assert len(s.reasons) == 4

    def test_catchup_finds_a_signal_the_last_bar_has_moved_past(self):
        """A daily rule fires on ONE bar. A scan that missed that day must be
        able to see it, or the replay finds signals the live engine never can."""
        cfg = SnapbackConfig()
        b = rising_then_spike(240)
        # Append two quiet sessions after the spike.
        close = np.concatenate([b.close, [b.close[-1] * 0.999,
                                          b.close[-1] * 0.998]])
        b2 = tape(close)
        assert evaluate(b2, cfg, "NIFTY", catchup_bars=1) == []
        assert len(evaluate(b2, cfg, "NIFTY", catchup_bars=3)) == 1

    def test_strength_orders_by_how_far_past_the_threshold(self):
        cfg = SnapbackConfig()
        mild = evaluate(rising_then_spike(240, spike=1.04), cfg, "X")
        hard = evaluate(rising_then_spike(240, spike=1.25), cfg, "X")
        assert mild and hard
        assert abs(hard[0].stretch) > abs(mild[0].stretch)
        assert hard[0].strength == "STRONG"

    def test_an_empty_tape_is_not_an_error(self):
        assert evaluate(to_bars([]), SnapbackConfig(), "NIFTY") == []


class TestToBars:
    def test_sorts_and_deduplicates(self):
        """A broker's history endpoint returns the boundary bar of two adjacent
        windows twice, and a duplicated session shifts every rolling window."""
        rows = [
            {"time": 300, "open": 3, "high": 3, "low": 3, "close": 3},
            {"time": 100, "open": 1, "high": 1, "low": 1, "close": 1},
            {"time": 200, "open": 2, "high": 2, "low": 2, "close": 2},
            {"time": 200, "open": 2, "high": 2, "low": 2, "close": 2},
        ]
        b = to_bars(rows)
        assert list(b.time) == [100.0, 200.0, 300.0]

    def test_millisecond_stamps_are_recognised(self):
        b = to_bars([{"time": 1_757_000_000_000, "open": 1, "high": 1,
                      "low": 1, "close": 1}])
        assert b.time[0] == pytest.approx(1_757_000_000.0)

    def test_rows_missing_a_price_are_dropped_not_zero_filled(self):
        b = to_bars([{"time": 100, "open": 1, "high": 1, "low": 1, "close": 1},
                     {"time": 200, "open": None, "high": 1, "low": 1, "close": 1}])
        assert len(b) == 1


class TestTheGateCannotOpenItself:
    """An EMA that has not formed must not answer the gate's question.

    ``market_state`` used to stamp every unfilled bar with ``False`` — "not
    above the EMA" — which under the shipped ``bearish`` filter is the OPEN
    state. Every session before the index EMA filled therefore waved the gate
    through, silently, and the more conservative the operator made the gate the
    longer the hole it opened. It surfaced as a STRICTER gate producing twice
    the trades of a looser one.
    """

    def _index(self, n: int = 400):
        import numpy as np
        from app.engines.snapback.models import Bars
        close = np.linspace(100.0, 200.0, n)          # a pure uptrend
        return Bars(time=1_690_000_000.0 + np.arange(n) * 86_400.0,
                    open=close, high=close, low=close, close=close,
                    volume=np.full(n, 1.0))

    def test_sessions_before_the_ema_fills_are_absent_not_false(self):
        from app.engines.snapback.regime import market_state
        bars = self._index()
        days = market_state(bars, ema_period=200)
        assert len(days) < len(bars), "the warm-up answered a question it cannot"
        assert all(v for v in days.values()), "a pure uptrend is above its EMA"

    def test_a_slower_gate_can_never_admit_more_days(self):
        from app.engines.snapback.regime import allowed, gate_for
        from app.engines.snapback.models import ist_day
        bars = self._index()
        tapes = {"NIFTY": bars}
        opened = []
        for ema in (50, 100, 200):
            g = gate_for(tapes, market_filter="bearish", ema_period=ema)
            opened.append(sum(1 for t in bars.time
                              if allowed(g, ist_day(float(t)))))
        assert opened == sorted(opened, reverse=True), opened

    def test_the_warmup_covers_the_market_ema(self):
        from app.engines.snapback import SnapbackConfig
        from dataclasses import replace
        c = SnapbackConfig()
        assert c.warmup_bars() > c.market_ema
        assert replace(c, market_ema=200).warmup_bars() > 200
