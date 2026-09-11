"""The three intraday strategies, against tape built to make each rule bite.

Each strategy gets a positive case and the negative that its own rule exists to
refuse — a partial ribbon cross, a doji poking through a pivot, a VWAP with no
volume behind it. A test that only proves the happy path proves nothing about a
filter, and every one of these filters is the reason a strategy is not a
coin flip.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.engines.intraday import (IntradayConfig, evaluate_ma_ribbon,
                                  evaluate_pivot_break, evaluate_vwap_supertrend,
                                  fib_pivots, resample, ribbon_should_exit, to_bars)

IST = timezone(timedelta(hours=5, minutes=30))


def ts(day: str, hh: int, mm: int) -> float:
    y, mo, d = (int(x) for x in day.split("-"))
    return datetime(y, mo, d, hh, mm, tzinfo=IST).timestamp()


def bar(t: float, o: float, h: float, l: float, c: float, v: float = 1000.0) -> dict:
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v}


#: NSE cash hours are 09:15-15:30 — 75 five-minute bars. Tape that runs past
#: that is not merely unrealistic, it silently lands every late bar outside the
#: engine's own entry window and makes a rule look broken when it is not.
BARS_PER_SESSION = 75


def session(day: str, closes: list[float], *, start=(9, 15), step_min=5,
            volume: float = 1000.0, spread: float = 2.0) -> list[dict]:
    """Five-minute bars around a close path, rolling to the next weekday at 15:30."""
    out = []
    cur_day, i_in_day = day, 0
    prev = closes[0]
    for c in closes:
        if i_in_day >= BARS_PER_SESSION:
            cur_day, i_in_day = _next_weekday(cur_day), 0
        o = prev
        h = max(o, c) + spread
        l = min(o, c) - spread
        t = ts(cur_day, start[0], start[1]) + i_in_day * step_min * 60
        out.append(bar(t, o, h, l, c, volume))
        prev = c
        i_in_day += 1
    return out


def _next_weekday(day: str) -> str:
    from datetime import date, timedelta
    d = date(*(int(x) for x in day.split("-"))) + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()


def flat(day: str, level: float, n: int, **kw) -> list[dict]:
    return session(day, [level] * n, **kw)


def cfg(**over) -> IntradayConfig:
    base = dict(warmup_bars=70, session_start="09:15", no_entry_after="15:10")
    base.update(over)
    return IntradayConfig(**base).validate()


# ------------------------------------------------------------------- pivots

def test_fibonacci_pivots_match_the_published_formula():
    p = fib_pivots(110.0, 90.0, 100.0)
    assert p is not None
    assert p.p == pytest.approx(100.0)
    assert p.r1 == pytest.approx(100.0 + 0.382 * 20)
    assert p.r2 == pytest.approx(100.0 + 0.618 * 20)
    assert p.r3 == pytest.approx(120.0)
    assert p.s1 == pytest.approx(100.0 - 0.382 * 20)


def test_a_zero_range_prior_session_yields_no_pivots():
    # A holiday or a halted instrument. Seven identical levels would be
    # "broken" by every tick.
    assert fib_pivots(100.0, 100.0, 100.0) is None


# -------------------------------------------------------------- pivot_break

def _pivot_break_tape(*, body: float = 0.9, doji: bool = False) -> list[dict]:
    """Prior session H=105 L=99 C=103 → P=102.33, R1=104.62. Today crawls at 104.

    The break candle clears R1 by a body the size of a real 5-minute move, so
    the stop (its own low) is under a percent of price — the case the strategy
    is meant to take. A body wide enough to clear the level by 10% would be
    refused by ``pb_max_stop_pct``, which is a different test.
    """
    # A calm prior session: down to 99, up to 105, settling at 103. Built as a
    # smooth path rather than a zig-zag, because a zig-zag prior day inflates
    # ATR into the next session and the body-vs-ATR filter then refuses a
    # perfectly ordinary break.
    path = ([103.0 - i * 0.16 for i in range(25)]       # 103 -> 99
            + [99.0 + i * 0.24 for i in range(25)]      # 99 -> 105
            + [105.0 - i * 0.08 for i in range(25)])    # 105 -> 103
    prior = session("2026-09-09", path, spread=0.0)
    prior[-1]["close"] = 103.0
    # A quiet pre-break drift, so ATR reflects the tape the break happens in.
    warm = flat("2026-09-10", 104.0, 30, spread=0.1)
    o = 104.0
    c = o + (0.1 if doji else body)
    breaker = bar(warm[-1]["time"] + 300, o,
                  c + (1.0 if doji else 0.1), o - 0.1, c)
    return [*prior, *warm, breaker]


def test_pivot_break_fires_on_a_strong_candle_through_ema_and_pivot():
    ev = evaluate_pivot_break(to_bars(_pivot_break_tape()), cfg(), "NIFTY")
    assert ev.signal is not None, ev.blockers
    s = ev.signal
    assert s.option_type == "CE" and s.direction == "BULLISH"
    # Stop is the signal candle's low, and the targets are 1:2 and 1:3 of it.
    assert s.stop == pytest.approx(103.9)   # the break candle's own low
    assert s.rr1 == pytest.approx(2.0, abs=1e-6)
    assert s.target2 == pytest.approx(s.entry + 3.0 * s.risk)
    assert s.metrics["level_kind"] in {"P", "R1", "R2", "R3"}


def test_pivot_break_refuses_a_doji_that_merely_pokes_through():
    ev = evaluate_pivot_break(to_bars(_pivot_break_tape(doji=True)), cfg(), "NIFTY")
    assert ev.signal is None
    assert any("body" in b for b in ev.blockers), ev.blockers


def test_pivot_break_will_not_re_enter_an_old_break():
    tape = _pivot_break_tape()
    # One more bar that also closes above the level but crosses nothing.
    last = tape[-1]
    tape.append(bar(last["time"] + 300, last["close"], last["close"] + 1.0,
                    last["close"] - 0.1, last["close"] + 0.9))
    ev = evaluate_pivot_break(to_bars(tape), cfg(), "NIFTY")
    assert ev.signal is None
    assert any("no pivot level broken" in b for b in ev.blockers), ev.blockers


def test_pivot_break_rejects_a_candle_too_wide_to_risk():
    ev = evaluate_pivot_break(to_bars(_pivot_break_tape()), cfg(pb_max_stop_pct=0.01),
                              "NIFTY")
    assert ev.signal is None
    assert any("too wide" in b for b in ev.blockers), ev.blockers


def test_pivot_break_is_silent_outside_the_entry_window():
    tape = _pivot_break_tape()
    ev = evaluate_pivot_break(to_bars(tape), cfg(session_start="12:00"), "NIFTY")
    assert ev.signal is None
    assert any("12:00" in b for b in ev.blockers), ev.blockers


# ---------------------------------------------------------------- ma_ribbon

def _ribbon_tape(down_len: int = 40) -> list[dict]:
    """A long climb (the 55 under everything) then a drop that puts it on top."""
    up = [100.0 + i * 0.6 for i in range(90)]
    down = [up[-1] - (i + 1) * 1.2 for i in range(down_len)]
    return session("2026-09-10", up + down)


def test_ma_ribbon_fires_when_the_55_clears_the_whole_ribbon():
    # Falling tape: the 55 ends ABOVE all three faster lines → bearish → PE.
    ev = None
    for n in range(12, 40):
        e = evaluate_ma_ribbon(to_bars(_ribbon_tape(n)), cfg(), "NIFTY")
        if e.signal:
            ev = e
            break
    assert ev is not None and ev.signal is not None
    assert ev.signal.option_type == "PE" and ev.signal.direction == "BEARISH"
    assert ev.signal.stop > ev.signal.entry      # stop above a short
    assert ev.signal.target < ev.signal.entry


def test_ma_ribbon_refuses_a_partial_cross():
    # Early in the turn the 55 is through some of the ribbon but not all of it.
    seen_partial = False
    for n in range(1, 25):
        ev = evaluate_ma_ribbon(to_bars(_ribbon_tape(n)), cfg(), "NIFTY")
        if ev.signal is None and any("part of the ribbon" in b for b in ev.blockers):
            seen_partial = True
            break
    assert seen_partial, "expected a bar where the 55 has crossed only part of the ribbon"


def test_ma_ribbon_refuses_a_braided_ribbon():
    ev = evaluate_ma_ribbon(to_bars(session("2026-09-10", [100.0] * 130)), cfg(), "NIFTY")
    assert ev.signal is None
    assert ev.blockers


def test_ribbon_exit_is_the_opposite_full_cross():
    bars = to_bars(_ribbon_tape(40))
    done, why = ribbon_should_exit(bars, cfg(), "BULLISH")
    assert done and "ribbon" in why
    assert ribbon_should_exit(bars, cfg(), "BEARISH")[0] is False


# ---------------------------------------------------------- vwap_supertrend

def _vs_tape(volume: float = 1000.0) -> list[dict]:
    """A climb, then a drop steep enough that the flip and the VWAP loss coincide.

    That coincidence is the strategy: a SuperTrend flip whose own bar is already
    on the far side of VWAP. A gentle drop flips SuperTrend several bars before
    price reaches VWAP, and the freshness rule correctly refuses it.
    """
    up = [100.0 + i * 0.5 for i in range(80)]
    down = [up[-1] - (i + 1) * 14.0 for i in range(10)]
    return session("2026-09-10", up + down, volume=volume)


def _first_vs_signal(**over):
    for n in range(2, 14):
        e = evaluate_vwap_supertrend(to_bars(_vs_tape()[: 80 + n]),
                                     cfg(vs_max_stop_points=400.0, **over), "NIFTY")
        if e.signal:
            return e
    return None


def test_vwap_supertrend_fires_on_a_red_flip_closing_below_vwap():
    ev = _first_vs_signal(vs_dynamic_target=False, dynamic_targets=False,
                          dynamic_stops=False)
    assert ev is not None and ev.signal is not None, "expected a red flip below VWAP"
    s = ev.signal
    assert s.option_type == "PE" and s.direction == "BEARISH"
    assert s.stop == pytest.approx(ev.metrics["vwap"])      # VWAP is the stop
    assert s.target == pytest.approx(s.entry - 20.0)        # the stated objective


def test_the_dynamic_target_lifts_a_fixed_objective_and_never_lowers_it():
    """20 points is a whole move on one index and a rounding error on another."""
    fixed = _first_vs_signal(vs_dynamic_target=False, dynamic_targets=False)
    live = _first_vs_signal()
    assert fixed and live and fixed.signal and live.signal
    assert abs(live.signal.target - live.signal.entry) >= 20.0
    # And a quiet tape leaves the stated objective exactly where it was.
    calm = evaluate_vwap_supertrend(
        to_bars(_vs_tape()), cfg(vs_max_stop_points=400.0, vs_target_atr_mult=0.0,
                                 target_atr_mult=0.0), "NIFTY")
    if calm.signal:
        assert abs(calm.signal.target - calm.signal.entry) == pytest.approx(20.0)


def test_vwap_supertrend_refuses_a_session_with_no_volume():
    # The index case. A zero-weight VWAP is a session mean, and this engine says
    # so instead of trading close-vs-itself.
    rows = _vs_tape(volume=0.0)
    ev = evaluate_vwap_supertrend(to_bars(rows), cfg(vs_max_stop_points=400.0), "NIFTY")
    assert ev.signal is None
    assert any("no volume" in b for b in ev.blockers), ev.blockers


def test_vwap_supertrend_refuses_a_stop_the_target_cannot_pay_for():
    found = False
    for n in range(2, 14):
        ev = evaluate_vwap_supertrend(to_bars(_vs_tape()[: 80 + n]),
                                      cfg(vs_min_stop_points=0.5, vs_max_stop_points=1.0), "NIFTY")
        if any("does not pay for it" in b for b in ev.blockers):
            found = True
            break
    assert found, "expected the stop-distance cap to refuse at least one bar"


# ----------------------------------------------------------------- resample

def test_resample_buckets_on_the_ist_clock_not_the_epoch():
    one_min = [bar(ts("2026-09-10", 9, 15) + i * 60, 100, 101, 99, 100 + i)
               for i in range(10)]
    five = resample(one_min, 5)
    assert len(five) == 2
    first = datetime.fromtimestamp(five[0]["time"], tz=IST)
    assert (first.hour, first.minute) == (9, 15)
    second = datetime.fromtimestamp(five[1]["time"], tz=IST)
    assert (second.hour, second.minute) == (9, 20)
    assert five[0]["close"] == 104 and five[0]["volume"] == 5 * 1000.0


def test_resample_leaves_coarser_bars_alone():
    rows = session("2026-09-10", [100, 101, 102])
    assert len(resample(rows, 5)) == 3


# ---------------------------------------------------------- dynamic levels

def test_a_stop_inside_the_noise_is_widened_to_the_atr_floor():
    """A structural stop two ticks from the close is not a stop, it is a fee."""
    from app.engines.intraday.strategies import apply_dynamic_levels
    m: dict = {}
    stop, targets = apply_dynamic_levels(
        100.0, 99.98, [104.0], atr=2.0, bullish=True,
        cfg=cfg(stop_atr_floor_mult=0.5, target_atr_mult=0.0), metrics=m)
    assert stop == pytest.approx(99.0)          # 0.5 × ATR below entry
    assert m["stop_widened_to_atr_floor"] is True
    assert targets == [104.0]


def test_a_structural_stop_wide_enough_already_is_left_alone():
    from app.engines.intraday.strategies import apply_dynamic_levels
    m: dict = {}
    stop, _ = apply_dynamic_levels(100.0, 95.0, [110.0], atr=2.0, bullish=True,
                                   cfg=cfg(), metrics=m)
    # Never TIGHTENED — tightening a rule's own stop trades a different strategy.
    assert stop == pytest.approx(95.0)
    assert "stop_widened_to_atr_floor" not in m


def test_the_short_side_widens_the_other_way():
    from app.engines.intraday.strategies import apply_dynamic_levels
    m: dict = {}
    stop, targets = apply_dynamic_levels(
        100.0, 100.02, [96.0], atr=2.0, bullish=False,
        cfg=cfg(stop_atr_floor_mult=0.5, target_atr_mult=3.0), metrics=m)
    assert stop == pytest.approx(101.0)
    assert targets[0] == pytest.approx(94.0)    # 3 × ATR, further than 96


def test_a_target_volatility_has_overtaken_is_extended_not_cut():
    from app.engines.intraday.strategies import apply_dynamic_levels
    m: dict = {}
    _, targets = apply_dynamic_levels(100.0, 95.0, [101.0, 130.0], atr=4.0,
                                      bullish=True, cfg=cfg(target_atr_mult=2.0),
                                      metrics=m)
    assert targets[0] == pytest.approx(108.0)   # lifted to 2 × ATR
    assert targets[1] == pytest.approx(130.0)   # already further — untouched


def test_dynamic_levels_are_inert_without_an_atr():
    from app.engines.intraday.strategies import apply_dynamic_levels
    m: dict = {}
    stop, targets = apply_dynamic_levels(100.0, 99.99, [100.5], atr=0.0,
                                         bullish=True, cfg=cfg(), metrics=m)
    assert (stop, targets) == (99.99, [100.5])
    assert m == {}


def test_a_stop_beyond_the_atr_cap_is_reported_not_silently_moved():
    from app.engines.intraday.strategies import apply_dynamic_levels
    m: dict = {}
    stop, _ = apply_dynamic_levels(100.0, 80.0, [110.0], atr=2.0, bullish=True,
                                   cfg=cfg(stop_atr_cap_mult=4.0), metrics=m)
    assert stop == pytest.approx(80.0)
    assert m["stop_beyond_atr_cap"] is True
    assert m["stop_atr_multiple"] == pytest.approx(10.0)


# ------------------------------------------------------- is the thesis dead?

def test_the_ribbon_thesis_dies_on_the_opposite_full_cross():
    """This strategy's stated exit IS the idea failing, not a price. Nothing
    else evaluates it, so without this the trail was the only exit."""
    from app.engines.intraday import thesis_broken
    bars = to_bars(_ribbon_tape(40))
    done, why = thesis_broken(bars, cfg(), "ma_ribbon", "BULLISH")
    assert done and "ribbon" in why
    assert thesis_broken(bars, cfg(), "ma_ribbon", "BEARISH")[0] is False


def test_a_pivot_break_thesis_is_never_second_guessed_here():
    """Its stated exit is the price stop. A second opinion would close a trade
    the strategy says is still on."""
    from app.engines.intraday import thesis_broken
    bars = to_bars(_ribbon_tape(40))
    assert thesis_broken(bars, cfg(), "pivot_break", "BULLISH") == (False, "")


def test_the_vwap_thesis_needs_BOTH_legs_to_reverse():
    from app.engines.intraday import thesis_broken
    import numpy as np
    from app.engines.intraday.models import Bars

    rows = _vs_tape()
    bars = to_bars(rows)
    # A long held through a tape that ends falling and below VWAP is dead.
    done, why = thesis_broken(bars, cfg(), "vwap_supertrend", "BULLISH")
    assert done and "VWAP" in why
    # The same tape does not kill a short — that IS the short working.
    assert thesis_broken(bars, cfg(), "vwap_supertrend", "BEARISH")[0] is False


def test_nothing_is_decided_before_warmup():
    from app.engines.intraday import thesis_broken
    short = to_bars(session("2026-09-10", [100.0] * 10))
    assert thesis_broken(short, cfg(), "ma_ribbon", "BULLISH") == (False, "")
