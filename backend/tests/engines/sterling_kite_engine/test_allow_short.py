"""The direction gate, and the trap in implementing it.

MEASURED (study/kite_st_best.py, real 7.5y 1H, four indices, delta-1, costs and
slippage included): over 1102 short trades the short book netted -4,911 rupees
while the long book netted +3,143,906. Dropping shorts leaves slightly MORE money
on half the trades with a quarter less drawdown, so `allow_short` defaults False.

The trap these tests exist to pin: the tidy-looking fix is to zero the `shorts`
mask inside `entry_transitions`, because every caller then inherits the gate in
one line. It breaks the engine. `resolve_exit` also reads `shorts` — the red
counter uses a fresh counter-signal to decide when a LONG closes — so zeroing it
would leave long positions unable to exit. The gate belongs at the ENTRY sites
only, and `test_long_exits_still_work_with_shorts_disabled` is what catches a
regression back to the tidy version.
"""
import numpy as np
import pytest

from app.domain.models import Candle
from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
from app.engines.sterling_kite_engine.engine import SterlingKiteEngine
from app.engines.sterling_kite_engine.regime import compute_regime, entry_transitions
from app.services.kite_engine.scanner import build_setup_chart, evaluate_item
from app.services.kite_engine.universe import UniverseItem


def _candles(close_path, start_ms=0):
    c = np.asarray(close_path, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    out = []
    for i in range(len(c)):
        hi = max(o[i], c[i]) + 1.0
        lo = max(0.01, min(o[i], c[i]) - 1.0)
        out.append(Candle(timestamp_ms=start_ms + i * 3_600_000, open=float(o[i]),
                          high=float(hi), low=float(lo), close=float(c[i]), volume=1.0))
    return out


def _down_up_down(n=80, base=20_000.0, step=25.0):
    """Down, then up, then down.

    It has to OPEN in a downtrend. `entry_transitions` fires on `bull & ~prev_bull`
    — a fresh flip INTO alignment — so a series that simply ramps up from bar one
    is already aligned by the time warmup ends and never produces a transition at
    all. The leading down leg is what makes the first bull flip a real event, and
    the trailing one does the same for the bear flip.
    """
    down1 = base - np.arange(n) * step
    up = down1[-1] + np.arange(1, n + 1) * step
    down2 = up[-1] - np.arange(1, n + 1) * step
    return np.concatenate([down1, up, down2])


@pytest.fixture
def path():
    return _down_up_down()


def _transitions(path_, cfg):
    cs = _candles(path_)
    o = np.array([c.open for c in cs], float)
    h = np.array([c.high for c in cs], float)
    l = np.array([c.low for c in cs], float)
    c = np.array([c.close for c in cs], float)
    r = compute_regime(o, h, l, c, cfg)
    return cs, entry_transitions(r)


# ── The default ──────────────────────────────────────────────────────────────

def test_allow_short_defaults_to_false():
    assert SterlingKiteEngineConfig().allow_short is False


def test_entry_transitions_itself_is_unchanged(path):
    """The mask is raw market structure and must stay that way.

    Both masks are still produced in full regardless of the flag; the gate is
    applied by the CALLERS. If this ever fails, someone moved the gate into
    `entry_transitions` and the exit path has silently lost its counter-signal.
    """
    _, (longs_off, shorts_off) = _transitions(path, SterlingKiteEngineConfig(allow_short=False))
    _, (longs_on, shorts_on) = _transitions(path, SterlingKiteEngineConfig(allow_short=True))
    assert np.array_equal(longs_off, longs_on)
    assert np.array_equal(shorts_off, shorts_on)
    assert shorts_off.any(), "fixture must contain a bear transition to be meaningful"


# ── The scanner ──────────────────────────────────────────────────────────────

def _item():
    return UniverseItem(name="NIFTY", tradingsymbol="NIFTY", token=256265,
                        exchange="NSE", option_exchange="NFO", is_index=True)


def _rows(path_, *, allow_short):
    cfg = SterlingKiteEngineConfig(allow_short=allow_short)
    return evaluate_item(SterlingKiteEngine(cfg), _item(), _candles(path_), cfg)


def test_scanner_emits_no_short_rows_when_shorts_are_disabled(path):
    rows = _rows(path, allow_short=False)
    assert rows, "the bull leg must still produce rows"
    assert all(r.direction == "long" for r in rows)


def test_scanner_emits_short_rows_when_shorts_are_enabled(path):
    rows = _rows(path, allow_short=True)
    assert any(r.direction == "short" for r in rows), (
        "with the flag on, the bear leg must still be tradeable")


def test_the_long_rows_are_byte_identical_either_way(path):
    """Turning shorts off must not perturb a single long row.

    A gate that also changed the longs would be changing the strategy, not
    removing a book.
    """
    off = [r for r in _rows(path, allow_short=False) if r.direction == "long"]
    on = [r for r in _rows(path, allow_short=True) if r.direction == "long"]
    assert len(off) == len(on) and off
    for a, b in zip(off, on):
        assert a.model_dump() == b.model_dump()


# ── The trap ─────────────────────────────────────────────────────────────────

def test_the_red_counter_needs_the_shorts_mask_to_close_a_long(path):
    """THE trap, tested at the exact line it would break.

    Under `three_red_signal` a long is closed by three reds AND a fresh
    counter-entry arrow — and that arrow IS an entry in the `shorts` mask
    (`exits.red_count_exit_index`, the `shorts[j]` branch). Hand that function a
    zeroed mask, which is what gating inside `entry_transitions` would do, and
    the long never closes.

    If this ever stops failing on the zeroed mask, the counter-signal exit has
    been removed and the rest of this file is no longer guarding anything.
    """
    from app.engines.sterling_kite_engine import exits

    cfg = SterlingKiteEngineConfig(allow_short=False, exit_mode="three_red_signal")
    cs, (longs, shorts) = _transitions(path, cfg)
    o = np.array([c.open for c in cs], float)
    h = np.array([c.high for c in cs], float)
    l = np.array([c.low for c in cs], float)
    c = np.array([c.close for c in cs], float)
    r = compute_regime(o, h, l, c, cfg)
    entry_i = int(np.where(longs)[0][0])
    last = len(c) - 1

    with_mask = exits.red_count_exit_index(r, "long", entry_i, last, cfg, longs, shorts)
    without = exits.red_count_exit_index(r, "long", entry_i, last, cfg, longs,
                                         np.zeros_like(shorts))
    assert with_mask is not None, "the long must close on a counter-signal"
    assert without is None, (
        "zeroing the shorts mask left the long unable to close — this is exactly "
        "what gating inside entry_transitions would do")


def test_a_long_still_exits_end_to_end_with_shorts_disabled(path):
    """The same guarantee through the scanner, not the unit."""
    cfg = SterlingKiteEngineConfig(allow_short=False, exit_mode="three_red_signal")
    rows = evaluate_item(SterlingKiteEngine(cfg), _item(), _candles(path), cfg)
    longs = [r for r in rows if r.direction == "long"]
    assert longs, "fixture must open a long"
    assert any(r.exit_reason for r in longs), "no long ever exited"


def test_long_exit_reasons_match_with_shorts_on_and_off(path):
    """Same fixture, same exits. The flag decides what OPENS, never what CLOSES."""
    cfg_off = SterlingKiteEngineConfig(allow_short=False, exit_mode="three_red_signal")
    cfg_on = SterlingKiteEngineConfig(allow_short=True, exit_mode="three_red_signal")
    def longs_of(cfg):
        rows = evaluate_item(SterlingKiteEngine(cfg), _item(), _candles(path), cfg)
        return [(r.timestamp_ms, r.exit_reason) for r in rows if r.direction == "long"]

    off, on = longs_of(cfg_off), longs_of(cfg_on)
    assert off == on and off


# ── The engine ───────────────────────────────────────────────────────────────

def _first_bear_bar(path_, cfg):
    cs, (_, shorts) = _transitions(path_, cfg)
    idx = np.where(shorts)[0]
    assert len(idx), "fixture must contain a fresh bear transition"
    return cs, int(idx[0])


def test_engine_declines_a_bear_transition_when_shorts_are_disabled(path):
    cfg = SterlingKiteEngineConfig(allow_short=False)
    cs, bear_i = _first_bear_bar(path, cfg)
    assert SterlingKiteEngine(cfg).generate(cs[: bear_i + 1], underlying="NIFTY") == []


def test_engine_takes_a_bear_transition_when_shorts_are_enabled(path):
    cfg = SterlingKiteEngineConfig(allow_short=True)
    cs, bear_i = _first_bear_bar(path, cfg)
    sigs = SterlingKiteEngine(cfg).generate(cs[: bear_i + 1], underlying="NIFTY")
    assert sigs, "with the flag on the engine must still take the short"


def test_engine_still_takes_longs_when_shorts_are_disabled(path):
    cfg = SterlingKiteEngineConfig(allow_short=False)
    cs, (longs, _) = _transitions(path, cfg)
    idx = np.where(longs)[0]
    assert len(idx), "fixture must contain a fresh bull transition"
    bull_i = int(idx[0])
    assert SterlingKiteEngine(cfg).generate(cs[: bull_i + 1], underlying="NIFTY")


# ── The chart ────────────────────────────────────────────────────────────────

class _StubClient:
    """Just enough client for `build_setup_chart` — it only fetches candles."""

    def __init__(self, candles):
        self._candles = candles

    async def get_candles(self, *_args, **_kwargs):
        return self._candles


def test_chart_entry_marker_follows_the_same_gate(path):
    """The chart must not mark a trade the engine will not take."""
    import asyncio

    cs = _candles(path)

    def chart(allow_short):
        return asyncio.run(build_setup_chart(
            _StubClient(cs), 256265, "NIFTY",
            SterlingKiteEngineConfig(allow_short=allow_short)))

    off, on = chart(False), chart(True)
    assert on.entry_index is not None
    # The series ends in the bear leg, so the last fresh alignment is a short:
    # with shorts off the marker must fall back to the last BULL transition.
    assert off.entry_index != on.entry_index
