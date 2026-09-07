"""The engine scans on 1H. Everything measured so far was daily.

``scanner._fetch_candles`` calls ``get_candles(inst, "1H", 320)`` on every path —
spot and confluence alike — so 1H is the timeframe the three SuperTrends actually
run on, and a daily measurement answers a question nobody asked.

Bars are built from the lake's minute series, anchored to the SESSION OPEN rather
than the clock. Kite's 1H candles run 09:15-10:14, 10:15-11:14 and so on; bucketing
by clock hour would cut them at 10:00 and 11:00, producing bars that are off by 45
minutes and an ST that flips on different bars than the live one.

Coverage is the honest constraint here. Minute history in the lake runs
2026-02-13..2026-08-14 for spot and 2026-07-01..2026-09-04 for futures, so this is
about six months of 1H for the signal and roughly six WEEKS of overlap for
confluence. A deeper 1H pull needs a live Kite session; the stored one has expired.

Same method as the daily studies: open-to-open forward return after a fresh
alignment, against the base rate of every bar in the same series, split into an
in-sample head and an untouched holdout tail, reported per window and never pooled.

    python study/kite_1h_alpha.py --out 1h.json
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
from app.engines.sterling_kite_engine.regime import compute_regime, entry_transitions
from study.kite_confluence_alpha import INDEX_SPOT, _futures_name_map
from study.kite_futures_edge import DEFAULT_LAKE, _load

_IST = timezone(timedelta(hours=5, minutes=30))

#: NSE/BSE continuous trading opens at 09:15 IST. Kite's 1H bars are anchored here.
SESSION_OPEN_MINUTE = 9 * 60 + 15

HORIZONS = (1, 3, 7, 14)     # 1H bars: ~1 bar, half a day, a day, two days
HOLDOUT_FRACTION = 0.30
MIN_BARS = 300               # engine warmup is 21; 300 leaves room for real trades


def resample_1h(series: Dict[str, Any]) -> Dict[str, Any]:
    """Minute bars -> session-anchored 1H bars.

    Bucketed by (IST date, hours since the session open). Two properties matter:
    a bucket never spans the close and the next open, and the first bar of every
    session starts at 09:15 exactly as Kite's does.
    """
    out = {k: [] for k in ("ts_ms", "open", "high", "low", "close")}
    out["symbol"] = series["symbol"]
    key = None
    for i, ms in enumerate(series["ts_ms"]):
        dt = datetime.fromtimestamp(ms / 1000, _IST)
        minute_of_day = dt.hour * 60 + dt.minute
        bucket = (dt.date(), (minute_of_day - SESSION_OPEN_MINUTE) // 60)
        if bucket != key:
            key = bucket
            out["ts_ms"].append(ms)
            for f in ("open", "high", "low", "close"):
                out[f].append(series[f][i])
        else:
            out["high"][-1] = max(out["high"][-1], series["high"][i])
            out["low"][-1] = min(out["low"][-1], series["low"][i])
            out["close"][-1] = series["close"][i]
    return out


def _load_1h(lake: Path, wanted: Tuple[str, ...]) -> Dict[str, Dict[str, Any]]:
    out = {}
    root = lake / "bars" / "interval=minute"
    for p in sorted(root.rglob("*.parquet")):
        if not any(f"segment={w}" in str(p) for w in wanted):
            continue
        s = _load(p)
        if not s:
            continue
        h = resample_1h(s)
        o = np.asarray(h["open"], float)
        if len(o) < MIN_BARS or not np.all(np.isfinite(o)) or np.any(o <= 0):
            continue
        out[h["symbol"]] = h
    return out


def _regime(s, cfg):
    o = np.asarray(s["open"], float)
    r = compute_regime(o, np.asarray(s["high"], float), np.asarray(s["low"], float),
                       np.asarray(s["close"], float), cfg)
    longs, shorts = entry_transitions(r)
    return o, r, longs, shorts


def _summarise(acc, base, modes) -> List[Dict[str, Any]]:
    rows = []
    for h in HORIZONS:
        b = np.array(base[h])
        b = b[np.isfinite(b)]
        base_bps = float(b.mean()) * 10_000 if b.size else 0.0
        row: Dict[str, Any] = {"horizon_bars": h, "base_rate_bps": round(base_bps, 1)}
        for mode in modes:
            for side in ("long", "short"):
                v = np.array(acc[(mode, side, h)])
                v = v[np.isfinite(v)]
                mean = float(v.mean()) * 10_000 if v.size else 0.0
                ref = base_bps if side == "long" else -base_bps
                row[f"{mode}_{side}"] = {
                    "n": int(v.size), "mean_bps": round(mean, 1),
                    "alpha_bps": round(mean - ref, 1),
                    "hit_rate": round(float((v > 0).mean()), 4) if v.size else 0.0,
                }
        rows.append(row)
    return rows


def audit(lake: Path) -> Dict[str, Any]:
    cfg = SterlingKiteEngineConfig()
    spot = _load_1h(lake, ("NSE", "INDICES", "BSE"))
    fut = _load_1h(lake, ("NFO-FUT", "BFO-FUT"))
    names = _futures_name_map(lake)
    modes = ("spot_only", "confluence", "confluence_strict")
    windows = ("in_sample", "holdout")
    acc = {(w, m, s, h): [] for w in windows for m in modes
           for s in ("long", "short") for h in HORIZONS}
    base = {(w, h): [] for w in windows for h in HORIZONS}
    paired = 0

    # Every spot series contributes to spot_only. Only those with a futures
    # counterpart can contribute to the confluence modes, so the two are reported
    # with their own counts rather than pretending one universe.
    fut_for_spot: Dict[str, Any] = {}
    for fsym, spot_sym in names.items():
        if fsym in fut and spot_sym in spot and spot_sym not in fut_for_spot:
            fut_for_spot[spot_sym] = fut[fsym]

    for sym, s in spot.items():
        so, _sr, slongs, sshorts = _regime(s, cfg)
        ts = np.asarray(s["ts_ms"])
        n = len(so)
        cut_i = int(n * (1 - HOLDOUT_FRACTION))
        f = fut_for_spot.get(sym)
        if f is not None:
            paired += 1
            fi = {t: i for i, t in enumerate(f["ts_ms"])}
            _fo, fr, flongs, fshorts = _regime(f, cfg)
        for h in HORIZONS:
            m = n - h - 1
            if m <= cfg.warmup:
                continue
            fwd = np.full(n, np.nan)
            fwd[:m] = (so[h + 1:n] - so[1:m + 1]) / so[1:m + 1]
            for i in range(cfg.warmup, m):
                if not np.isfinite(fwd[i]):
                    continue
                w = "in_sample" if i < cut_i else "holdout"
                base[(w, h)].append(float(fwd[i]))
                fj = fi.get(int(ts[i])) if f is not None else None
                for side, fresh in (("long", slongs[i]), ("short", sshorts[i])):
                    if not fresh:
                        continue
                    signed = float(fwd[i]) if side == "long" else -float(fwd[i])
                    acc[(w, "spot_only", side, h)].append(signed)
                    if fj is None:
                        continue
                    aligned = bool(fr.bull[fj] if side == "long" else fr.bear[fj])
                    strict = bool(flongs[fj] if side == "long" else fshorts[fj])
                    if aligned:
                        acc[(w, "confluence", side, h)].append(signed)
                        if strict:
                            acc[(w, "confluence_strict", side, h)].append(signed)

    out_windows = {}
    for w in windows:
        out_windows[w] = _summarise(
            {(m, s, h): acc[(w, m, s, h)] for m in modes
             for s in ("long", "short") for h in HORIZONS},
            {h: base[(w, h)] for h in HORIZONS}, modes)

    hold = out_windows["holdout"]
    positive = sum(1 for r in hold for m in modes for side in ("long", "short")
                   if r[f"{m}_{side}"]["n"] and r[f"{m}_{side}"]["alpha_bps"] > 0)
    total = sum(1 for r in hold for m in modes for side in ("long", "short")
                if r[f"{m}_{side}"]["n"])
    return {
        "lake": str(lake), "timeframe": "1H, session-anchored at 09:15 IST",
        "engine_timeframe_confirmed": 'scanner._fetch_candles -> get_candles(inst, "1H", 320)',
        "spot_series": len(spot), "futures_series": len(fut),
        "paired_for_confluence": paired,
        "holdout": f"last {int(HOLDOUT_FRACTION * 100)}% of each series' bars",
        "coverage_note": "spot minute 2026-02-13..2026-08-14, futures minute "
                         "2026-07-01..2026-09-04 — about six months of 1H for the "
                         "signal and six WEEKS of overlap for confluence",
        "windows": out_windows,
        "holdout_cells_positive": positive, "holdout_cells": total,
        "verdict": "POSITIVE_ALPHA_OUT_OF_SAMPLE" if total and positive == total
                   else "NEGATIVE_ALPHA_OUT_OF_SAMPLE",
        "caveats": [
            "Six months of 1H is a fraction of the 2018-2026 span the daily studies "
            "used. A result here is weaker evidence, in both directions.",
            "The confluence overlap is about six weeks. Treat those cells as a hint.",
            "Measures the UNDERLYING's move — an upper bound on a long-premium CE/PE "
            "position, never an estimate of one.",
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lake", type=Path, default=DEFAULT_LAKE)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    rep = audit(args.lake)
    if args.out:
        args.out.write_text(json.dumps(rep, indent=2) + "\n")
    print(f"1H  spot={rep['spot_series']}  futures={rep['futures_series']}  "
          f"paired={rep['paired_for_confluence']}  -> {rep['verdict']}")
    print(f"holdout cells with positive alpha: "
          f"{rep['holdout_cells_positive']}/{rep['holdout_cells']}\n")
    hdr = (f"{'window':<10} {'h':>3} {'base':>7} | {'mode':<18} {'side':<6} "
           f"{'n':>7} {'alpha':>8} {'hit':>7}")
    print(hdr); print("-" * len(hdr))
    for w, rows in rep["windows"].items():
        for r in rows:
            for m in ("spot_only", "confluence", "confluence_strict"):
                for side in ("long", "short"):
                    c = r[f"{m}_{side}"]
                    if not c["n"]:
                        continue
                    print(f"{w:<10} {r['horizon_bars']:>3} {r['base_rate_bps']:>7.1f} | "
                          f"{m:<18} {side:<6} {c['n']:>7,} {c['alpha_bps']:>+8.1f} "
                          f"{c['hit_rate']*100:>6.1f}%")


if __name__ == "__main__":
    main()
