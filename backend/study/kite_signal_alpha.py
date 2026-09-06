"""Which chart does the signal actually work on — SPOT or the derivative's own?

``scan_source`` decides where the three SuperTrends are computed:

  * ``spot``        — on the underlying's chart, then buy a CE/PE on it. THIS IS
                      THE SHIPPED CONFIGURATION.
  * ``derivatives`` — on the contract's own premium/price chart.

The futures edge study measured the DERIVATIVES source: the SuperTrends ran on
each futures contract's own price series. That is not what the live engine does,
so this script re-measures the same thing on SPOT and puts the two side by side.

Method is the same in both cases and deliberately exit-free. Rather than replay a
strategy, measure the raw directional content of the ENTRY: the open-to-open
forward return after a fresh alignment, against the base rate of every bar in the
same series. Alpha is signal minus base. An exit can only redistribute what the
entry provides; if alpha is negative, no exit rule recovers it.

What this can and cannot say about options
------------------------------------------
It measures the UNDERLYING's move, which is what a spot-source signal claims to
predict. It cannot price the option leg — no option history exists to price it
with — and that leg only ever subtracts: a bought CE or PE is long premium, so it
pays theta and needs the underlying to move ENOUGH, not merely the right way.
Underlying alpha is therefore an upper bound on the option strategy, not an
estimate of it.

    python study/kite_signal_alpha.py --source spot    --out spot.json
    python study/kite_signal_alpha.py --source futures --out futures.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
from app.engines.sterling_kite_engine.regime import compute_regime, entry_transitions
from study.kite_futures_edge import DEFAULT_LAKE, DAILY_HOLDOUT_FROM, _load

HORIZONS = (1, 3, 5, 10, 20)

#: Alpha is reported per WINDOW, never pooled. A signal can carry a large edge in
#: the older years and none — or the opposite sign — in recent ones, and a
#: full-sample average hides exactly that. Pooling is how a dead signal keeps
#: looking alive.
WINDOWS = ("full", "in_sample", "holdout")

SOURCES = {
    # The engine scans indices and F&O-eligible equities; both are cash/spot.
    "spot": ("INDICES", "NSE", "BSE"),
    "futures": ("NFO-FUT", "BFO-FUT"),
}


def _series(lake: Path, source: str) -> List[Dict[str, Any]]:
    wanted = SOURCES[source]
    files = sorted((lake / "bars" / "interval=day").rglob("*.parquet"))
    keep = [p for p in files if any(f"segment={w}" in str(p) for w in wanted)]
    out = []
    for p in keep:
        s = _load(p)
        if not s:
            continue
        o = np.asarray(s["open"], float)
        # A contract that never traded carries zero opens; a forward return off
        # one is a division by zero, not a data point.
        if not np.all(np.isfinite(o)) or np.any(o <= 0):
            continue
        out.append(s)
    return out


def audit(lake: Path, source: str) -> Dict[str, Any]:
    from datetime import datetime, timezone
    cfg = SterlingKiteEngineConfig()
    data = _series(lake, source)
    cut = datetime.fromisoformat(DAILY_HOLDOUT_FROM).replace(
        tzinfo=timezone.utc).timestamp() * 1000
    acc = {(w, side, h): [] for w in WINDOWS
           for side in ("long", "short") for h in HORIZONS}
    base = {(w, h): [] for w in WINDOWS for h in HORIZONS}
    signals = {"long": 0, "short": 0}

    for s in data:
        o = np.asarray(s["open"], float)
        ts = np.asarray(s["ts_ms"])
        r = compute_regime(o, np.asarray(s["high"], float),
                           np.asarray(s["low"], float),
                           np.asarray(s["close"], float), cfg)
        longs, shorts = entry_transitions(r)
        n = len(o)
        signals["long"] += int(longs.sum())
        signals["short"] += int(shorts.sum())
        for h in HORIZONS:
            m = n - h - 1
            if m <= cfg.warmup:
                continue
            # Entry is the NEXT open — the signal is only known at the close — and
            # the exit is an open h bars later. Both are prices an order could get.
            fwd = np.full(n, np.nan)
            fwd[:m] = (o[h + 1:n] - o[1:m + 1]) / o[1:m + 1]
            span = np.arange(cfg.warmup, m)
            for w, sel in (("full", np.ones(len(span), bool)),
                           ("in_sample", ts[span] < cut),
                           ("holdout", ts[span] >= cut)):
                idx = span[sel]
                if not idx.size:
                    continue
                base[(w, h)].extend(fwd[idx].tolist())
                for side, mask in (("long", longs), ("short", shorts)):
                    j = idx[mask[idx]]
                    if j.size:
                        acc[(w, side, h)].extend(fwd[j].tolist())

    windows: Dict[str, Any] = {}
    for w in WINDOWS:
        rows = []
        for h in HORIZONS:
            vals = np.array(base[(w, h)])
            vals = vals[np.isfinite(vals)]
            b = float(vals.mean()) * 10_000 if vals.size else 0.0
            row: Dict[str, Any] = {"horizon_bars": h, "base_rate_bps": round(b, 1)}
            for side in ("long", "short"):
                v = np.array(acc[(w, side, h)])
                v = v[np.isfinite(v)]
                mean = float(v.mean()) * 10_000 if v.size else 0.0
                # Alpha is measured in the direction the position is taken: a short
                # profits when the underlying FALLS, so its alpha is base minus signal.
                alpha = (mean - b) if side == "long" else (b - mean)
                row[side] = {
                    "n": int(v.size), "mean_bps": round(mean, 1),
                    "alpha_bps": round(alpha, 1),
                    "hit_rate": round(float((v > 0).mean() if side == "long"
                                            else (v < 0).mean()), 4) if v.size else 0.0,
                }
            rows.append(row)
        windows[w] = rows

    hold = windows["holdout"]
    verdict = "POSITIVE_ALPHA_OUT_OF_SAMPLE" if hold and all(
        r["long"]["alpha_bps"] > 0 and r["short"]["alpha_bps"] > 0 for r in hold
    ) else "NEGATIVE_ALPHA_OUT_OF_SAMPLE"
    return {
        "lake": str(lake), "source": source,
        "holdout_from": DAILY_HOLDOUT_FROM,
        "scan_source_equivalent": ("spot — THE SHIPPED CONFIGURATION"
                                   if source == "spot" else "derivatives"),
        "series": len(data), "signals": signals,
        "method": "open-to-open forward return after a fresh 3-line alignment, "
                  "against the base rate of every bar in the same series",
        "windows": windows,
        "verdict": verdict,
        "caveats": [
            "Measures the UNDERLYING's move, not an option's P&L. No option history "
            "exists to price the leg with.",
            "A bought CE or PE is LONG PREMIUM: it pays theta and needs the "
            "underlying to move enough, not merely the right way. Underlying alpha "
            "is an upper bound on the option strategy, never an estimate of it.",
            "Exit-free by design. An exit redistributes what the entry provides; it "
            "cannot manufacture alpha the entry does not have.",
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lake", type=Path, default=DEFAULT_LAKE)
    ap.add_argument("--source", default="spot", choices=sorted(SOURCES))
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    rep = audit(args.lake, args.source)
    if args.out:
        args.out.write_text(json.dumps(rep, indent=2) + "\n")
    print(f"source={rep['source']} ({rep['scan_source_equivalent']})  "
          f"series={rep['series']}  long={rep['signals']['long']:,} "
          f"short={rep['signals']['short']:,}  -> {rep['verdict']}\n")
    print(f"{'window':<10} {'horiz':>5} {'base bps':>9} | {'LONG alpha':>11} {'hit':>7} {'n':>7} "
          f"| {'SHORT alpha':>12} {'hit':>7} {'n':>7}")
    for w, rows in rep["windows"].items():
        for r in rows:
            L, S = r["long"], r["short"]
            print(f"{w:<10} {r['horizon_bars']:>5} {r['base_rate_bps']:>9.1f} | "
                  f"{L['alpha_bps']:>+11.1f} {L['hit_rate']*100:>6.1f}% {L['n']:>7,} | "
                  f"{S['alpha_bps']:>+12.1f} {S['hit_rate']*100:>6.1f}% {S['n']:>7,}")


if __name__ == "__main__":
    main()
