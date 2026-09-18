"""Regenerate the SuperTrend parity fixtures.

Run this ONLY to establish a new frozen baseline — that is, when a deliberate
behaviour change has been decided and a new strategy identity has been created
for it. Running it to make a failing parity test pass destroys the only record
of what the frozen strategy used to do.

    python -m tests.fixtures.supertrend_parity.generate
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
from app.engines.sterling_kite_engine.regime import compute_regime, entry_transitions

HERE = Path(__file__).resolve().parent


def _series(values):
    close = np.asarray(values, dtype=float)
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + 1.0
    low = np.minimum(open_, close) - 1.0
    return open_, high, low, close


def cases() -> dict[str, list[float]]:
    """Price paths chosen to exercise each thing the fixtures are meant to pin."""
    rise = list(np.linspace(100, 400, 120))
    fall = list(np.linspace(300, 150, 60))
    return {
        # A clean trend: the three lines must agree and stay agreed.
        "uptrend": rise,
        # A transition: the arrow flips, which is where an off-by-one hides.
        "bear_to_bull": fall + list(np.linspace(150, 450, 60)),
        # Chop around a level: repeated near-touches of the trail.
        "chop": [200 + 8 * np.sin(i / 3.0) for i in range(140)],
        # A gap down mid-trend: the stop and the raw-price touch must disagree
        # with the basis price, which is the whole reason both are carried.
        "gap_down": rise[:60] + [x - 60 for x in rise[60:]],
    }


def snapshot(values: list[float]) -> dict:
    opens, highs, lows, closes = _series(values)
    cfg = SterlingKiteEngineConfig()
    r = compute_regime(opens, highs, lows, closes, cfg)
    longs, shorts = entry_transitions(r)

    def _round(array):
        return [None if not np.isfinite(x) else round(float(x), 6) for x in array]

    return {
        "warmup": int(r.warmup),
        "bull": [bool(x) for x in r.bull],
        "bear": [bool(x) for x in r.bear],
        "t_fast": [int(x) for x in r.t_fast],
        "t_mid": [int(x) for x in r.t_mid],
        "t_slow": [int(x) for x in r.t_slow],
        "l_fast": _round(r.l_fast),
        "l_mid": _round(r.l_mid),
        "l_slow": _round(r.l_slow),
        "basis_close": _round(r.basis_close),
        "atr": _round(r.atr),
        "entry_long": [bool(x) for x in longs],
        "entry_short": [bool(x) for x in shorts],
    }


def main() -> None:
    payload = {name: snapshot(values) for name, values in cases().items()}
    path = HERE / "frozen.json"
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {path} ({len(payload)} cases)")


if __name__ == "__main__":
    main()
