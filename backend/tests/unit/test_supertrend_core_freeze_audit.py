"""Phase 5 freeze audit for the Triple-SuperTrend core.

Capturing the core means proving what the runtime does, not restating the
config. Every check below runs the real production functions — the same
``compute_regime`` and ``entry_transitions`` the live scanner and the backtest
engine both import — and compares the result against the frozen record in
``sterling_kite_engine.lanes.FROZEN_CORE``.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.core.horizon import HorizonMode
from app.engines.indicators.heikin_ashi import compute_heikin_ashi
from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
from app.engines.sterling_kite_engine.engine import SterlingKiteEngine
from app.engines.sterling_kite_engine.lanes import (
    FROZEN_CORE,
    MODE_SIGNAL_TIMEFRAMES,
    STRATEGY_VERSION,
    audit_core_parity,
    core_is_frozen,
    core_rule_hash,
    identity_for,
    lane_rule_hash,
)
from app.engines.sterling_kite_engine.regime import compute_regime, entry_transitions
from app.schemas.market import Candle

RUNTIME = "c" * 40
TAG = "supertrend-core-v1"
HOUR_MS = 3_600_000


def _series(n: int = 300, seed: int = 7) -> tuple[np.ndarray, ...]:
    """A trending-then-reversing random walk, so all three lines flip."""
    rng = np.random.default_rng(seed)
    drift = np.concatenate([np.full(n // 2, 0.6), np.full(n - n // 2, -0.6)])
    close = 20_000 + np.cumsum(drift + rng.normal(0, 8.0, n))
    high = close + rng.uniform(2, 14, n)
    low = close - rng.uniform(2, 14, n)
    open_ = np.concatenate([[close[0]], close[:-1]])
    return open_, high, low, close


def _candles(seed: int = 7, n: int = 300) -> list[Candle]:
    o, h, l, c = _series(n, seed)
    return [
        Candle(
            timestamp_ms=i * HOUR_MS,
            open=float(o[i]),
            high=float(h[i]),
            low=float(l[i]),
            close=float(c[i]),
            volume=1000.0,
        )
        for i in range(n)
    ]


# ── the frozen record vs the runtime ──────────────────────────────────────


def test_the_runtime_config_matches_the_frozen_core():
    findings = audit_core_parity()
    assert findings == [], "; ".join(str(f) for f in findings)
    assert core_is_frozen() is True


def test_the_audit_actually_detects_drift():
    """Otherwise the clean result above would prove nothing."""
    drifted = SterlingKiteEngineConfig(fast=(20, 1.0), exit_mode="three_red")
    fields = {f.field for f in audit_core_parity(drifted)}
    assert "fast" in fields
    assert "exit_mode" in fields
    assert "exit_red_count" in fields
    assert core_is_frozen(drifted) is False


def test_the_audit_reports_every_disagreement_not_just_the_first():
    drifted = SterlingKiteEngineConfig(
        fast=(20, 1.0), mid=(13, 2.0), slow=(6, 3.0), allow_short=True
    )
    fields = {f.field for f in audit_core_parity(drifted)}
    assert {"fast", "mid", "slow", "allow_short"} <= fields


def test_the_core_rule_hash_does_not_move_when_a_runtime_default_drifts():
    """Identity comes from the frozen record, not from whatever is loaded."""
    before = core_rule_hash()
    SterlingKiteEngineConfig(fast=(20, 1.0))
    assert core_rule_hash() == before


# ── candle basis ──────────────────────────────────────────────────────────


def test_the_engine_computes_on_heikin_ashi_not_raw_ohlc():
    o, h, l, c = _series()
    cfg = SterlingKiteEngineConfig()
    assert cfg.candle_basis == FROZEN_CORE["candle_basis"]

    regime = compute_regime(o, h, l, c, cfg)
    _, ha_h, ha_l, ha_c = compute_heikin_ashi(o, h, l, c)

    np.testing.assert_allclose(regime.basis_close, ha_c)
    np.testing.assert_allclose(regime.basis_high, ha_h)
    # Raw extrema are kept separately: a synthetic HA low never proves a fill.
    np.testing.assert_allclose(regime.raw_low, l)
    assert not np.allclose(regime.basis_close, c)


def test_raw_basis_is_a_genuinely_different_series():
    o, h, l, c = _series()
    raw = compute_regime(o, h, l, c, SterlingKiteEngineConfig(candle_basis="raw"))
    ha = compute_regime(o, h, l, c, SterlingKiteEngineConfig())
    np.testing.assert_allclose(raw.basis_close, c)
    assert not np.array_equal(raw.t_fast, ha.t_fast)


# ── the three lines ───────────────────────────────────────────────────────


def test_the_three_supertrend_parameters_are_the_frozen_ones():
    cfg = SterlingKiteEngineConfig()
    assert (cfg.fast, cfg.mid, cfg.slow) == (
        FROZEN_CORE["fast"],
        FROZEN_CORE["mid"],
        FROZEN_CORE["slow"],
    )


def test_the_three_lines_are_distinct_series():
    o, h, l, c = _series()
    r = compute_regime(o, h, l, c, SterlingKiteEngineConfig())
    assert not np.array_equal(r.l_fast, r.l_mid)
    assert not np.array_equal(r.l_mid, r.l_slow)


def test_warmup_is_the_longest_period_not_the_shortest():
    cfg = SterlingKiteEngineConfig()
    assert cfg.warmup == FROZEN_CORE["warmup_bars"] == 21
    assert cfg.warmup == max(cfg.fast[0], cfg.mid[0], cfg.slow[0])


# ── alignment and the fresh-transition arrow ──────────────────────────────


def test_alignment_requires_all_three_lines():
    o, h, l, c = _series()
    r = compute_regime(o, h, l, c, SterlingKiteEngineConfig())
    expected_bull = (r.t_fast == 1) & (r.t_mid == 1) & (r.t_slow == 1)
    expected_bull[: r.warmup] = False
    np.testing.assert_array_equal(r.bull, expected_bull)
    # Bull and bear can never both be true.
    assert not np.any(r.bull & r.bear)


def test_nothing_aligns_before_warmup():
    o, h, l, c = _series()
    r = compute_regime(o, h, l, c, SterlingKiteEngineConfig())
    assert not r.bull[: r.warmup].any()
    assert not r.bear[: r.warmup].any()


def test_an_arrow_marks_a_fresh_transition_not_every_aligned_bar():
    """Alignment alone would re-enter on every bar of a trend."""
    o, h, l, c = _series()
    r = compute_regime(o, h, l, c, SterlingKiteEngineConfig())
    longs, _ = entry_transitions(r)

    assert longs.any(), "test series produced no entry at all"
    assert longs.sum() < r.bull.sum(), "every aligned bar became an arrow"
    # Every arrow is an aligned bar whose predecessor was not aligned.
    for i in np.flatnonzero(longs):
        assert r.bull[i]
        assert not r.bull[i - 1]


def test_no_arrow_on_the_first_bar_after_warmup():
    o, h, l, c = _series()
    r = compute_regime(o, h, l, c, SterlingKiteEngineConfig())
    longs, shorts = entry_transitions(r)
    assert not longs[: r.warmup + 1].any()
    assert not shorts[: r.warmup + 1].any()


# ── direction ─────────────────────────────────────────────────────────────


def test_the_default_book_is_long_only():
    assert SterlingKiteEngineConfig().allow_short is FROZEN_CORE["allow_short"] is False


def test_a_bear_arrow_opens_nothing_while_long_only():
    candles = _candles()
    o, h, l, c = _series()
    r = compute_regime(o, h, l, c, SterlingKiteEngineConfig())
    _, shorts = entry_transitions(r)
    bear_bars = list(np.flatnonzero(shorts))
    assert bear_bars, "test series produced no bear arrow"

    for i in bear_bars[:5]:
        long_only = SterlingKiteEngine(SterlingKiteEngineConfig())
        assert long_only.generate(candles[: i + 1], "NIFTY") == []

        both = SterlingKiteEngine(SterlingKiteEngineConfig(allow_short=True))
        emitted = both.generate(candles[: i + 1], "NIFTY")
        assert emitted and emitted[0].direction == "short"


# ── exit counter and price stop ───────────────────────────────────────────


def test_one_red_is_the_frozen_exit_counter():
    cfg = SterlingKiteEngineConfig()
    assert cfg.exit_mode == FROZEN_CORE["exit_mode"] == "one_red"
    assert cfg.exit_red_count == 1
    assert cfg.exit_needs_signal is False


@pytest.mark.parametrize(
    "mode,count", [("one_red", 1), ("two_red", 2), ("three_red", 3), ("three_red_signal", 3)]
)
def test_the_exit_counter_thresholds(mode, count):
    cfg = SterlingKiteEngineConfig(exit_mode=mode)
    assert cfg.exit_red_count == count
    assert cfg.exit_needs_signal is (mode == "three_red_signal")


def test_the_price_stop_is_enforced_by_default():
    assert SterlingKiteEngineConfig().price_stop_exit is True
    assert FROZEN_CORE["price_stop_exit"] is True


def test_the_trail_rides_the_fast_line():
    cfg = SterlingKiteEngineConfig()
    assert cfg.trail_target == FROZEN_CORE["trail_target"] == "fast"
    assert cfg.params("fast") == cfg.fast


# ── live / backtest parity ────────────────────────────────────────────────


def test_the_engine_emits_exactly_on_the_shared_arrow_mask():
    """The scanner and the backtest engine must agree bar for bar.

    Both import compute_regime and entry_transitions from the same module; this
    proves the engine's own gate does not add or drop a bar relative to the
    mask the scanner reads.
    """
    candles = _candles()
    o, h, l, c = _series()
    r = compute_regime(o, h, l, c, SterlingKiteEngineConfig())
    longs, _ = entry_transitions(r)

    emitted = []
    for i in range(len(candles)):
        engine = SterlingKiteEngine(SterlingKiteEngineConfig())
        if engine.generate(candles[: i + 1], "NIFTY"):
            emitted.append(i)

    assert emitted == list(np.flatnonzero(longs))
    assert emitted, "parity proved on an empty set proves nothing"


def test_the_engine_holds_one_position_per_underlying():
    candles = _candles()
    o, h, l, c = _series()
    r = compute_regime(o, h, l, c, SterlingKiteEngineConfig())
    longs, _ = entry_transitions(r)
    first = int(np.flatnonzero(longs)[0])

    engine = SterlingKiteEngine(SterlingKiteEngineConfig())
    assert engine.generate(candles[: first + 1], "NIFTY")
    # Still open: a later arrow must not stack a second position.
    for i in np.flatnonzero(longs)[1:4]:
        assert engine.generate(candles[: int(i) + 1], "NIFTY") == []


def test_the_entry_stop_is_the_trail_line_at_the_entry_bar():
    candles = _candles()
    o, h, l, c = _series()
    cfg = SterlingKiteEngineConfig()
    r = compute_regime(o, h, l, c, cfg)
    longs, _ = entry_transitions(r)
    i = int(np.flatnonzero(longs)[0])

    signal = SterlingKiteEngine(cfg).generate(candles[: i + 1], "NIFTY")[0]
    assert signal.stop_loss == pytest.approx(float(r.line(cfg.trail_target)[i]))


# ── lane identities ───────────────────────────────────────────────────────


def test_every_mode_declares_its_signal_timeframe():
    assert set(MODE_SIGNAL_TIMEFRAMES) == set(HorizonMode)
    assert MODE_SIGNAL_TIMEFRAMES[HorizonMode.ULTRA_SCALPING] == "1m"
    assert MODE_SIGNAL_TIMEFRAMES[HorizonMode.SWING] == "60m"


def test_the_five_lanes_have_five_distinct_rule_hashes():
    hashes = {lane_rule_hash(mode) for mode in HorizonMode}
    assert len(hashes) == 5


def test_overnight_and_swing_differ_despite_sharing_a_timeframe():
    """Same 60m signal, different holding budget, so different experiments."""
    assert MODE_SIGNAL_TIMEFRAMES[HorizonMode.OVERNIGHT] == (
        MODE_SIGNAL_TIMEFRAMES[HorizonMode.SWING]
    )
    assert lane_rule_hash("overnight") != lane_rule_hash("swing")


def test_supertrend_and_snapback_never_share_an_identity():
    from app.engines.snapback import lanes as snapback_lanes

    st = {
        identity_for(m, runtime_sha=RUNTIME, release_tag=TAG).identity_hash
        for m in HorizonMode
    }
    snap = {
        snapback_lanes.identity_for(
            m, runtime_sha=RUNTIME, release_tag=TAG
        ).identity_hash
        for m in HorizonMode
    }
    assert len(st | snap) == 10


def test_the_identity_names_the_frozen_core():
    identity = identity_for("swing", runtime_sha=RUNTIME, release_tag=TAG)
    assert identity.strategy_version == STRATEGY_VERSION == "supertrend_core_v1"
    assert identity.lane_key == "supertrend:swing"
