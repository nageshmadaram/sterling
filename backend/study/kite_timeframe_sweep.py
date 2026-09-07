"""Which timeframe does the signal work on, if any? 3m to 1H, all three scan sources.

``scanner._fetch_candles`` calls ``get_candles(inst, "1H", 320)``, so 1H is what the
live engine reads — every earlier study in this repo measured daily, which answers
a question nobody asked. This sweeps the timeframes an operator would plausibly
switch to, and for each one measures all three scan sources side by side:

  spot_only          the underlying fires a FRESH triple-ST entry
  confluence         ...and the derivative's own triple-ST is ALIGNED (shipped rule)
  confluence_strict  ...and the derivative fires FRESH on the same bar

Bars are session-anchored to 09:15 IST, not clock-aligned: Kite's 1H candles run
09:15-10:14, and bucketing by clock hour would cut them at 10:00, shifting every
bar 45 minutes and flipping the SuperTrends on different bars than the live engine.

Universe is the engine's own — the four indices from ``universe.json`` plus the
F&O-eligible equities, not every listed scrip. Alpha is the open-to-open forward
return after a signal minus the base rate of every bar in the same series, split
into an in-sample head and an untouched holdout tail and reported per window.
Pooling windows is how a dead signal keeps looking alive.

    python study/kite_timeframe_sweep.py --out sweep.json
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
from study.kite_confluence_alpha import _futures_name_map
from study.kite_futures_edge import DEFAULT_LAKE, _load

_IST = timezone(timedelta(hours=5, minutes=30))
SESSION_OPEN_MINUTE = 9 * 60 + 15          # NSE/BSE continuous open

#: 45 minutes is a resample only — Kite serves 1, 3, 5, 10, 15, 30, 60 and day.
TIMEFRAMES = (3, 5, 15, 30, 45, 60)

#: Horizons in BARS, so they mean the same thing structurally at every timeframe.
HORIZONS = (1, 3, 7, 14)

HOLDOUT_FRACTION = 0.30
MODES = ("spot_only", "confluence", "confluence_strict")
WINDOWS = ("in_sample", "holdout")

#: A series must carry the 21-bar warmup plus enough bars for the horizon and a
#: meaningful number of signals. Below this the "alpha" is one or two trades.
MIN_BARS = 250

#: The CONFIRMING series gets a lower floor. A futures contract lives about three
#: months, so at 1H it carries ~250-450 bars and at 30m ~500-900 — and confluence
#: only needs its trend state at the signal bar, plus its own warmup. Holding it to
#: the spot floor is what emptied every confluence cell in the first native run.
MIN_DERIV_BARS = 120


_DAY_MS = 86_400_000
_IST_OFFSET_MS = int(5.5 * 3_600_000)


def resample(series: Dict[str, Any], minutes: int) -> Dict[str, Any]:
    """Minute bars -> session-anchored ``minutes``-wide bars.

    Bucketed by (IST date, whole buckets since the session open), so a bucket can
    never span the close and the next open, and the first bar of every session
    starts at 09:15 exactly as the broker's does.

    Vectorised deliberately. The universe is ~350 series of six months of minute
    bars, so a per-row ``datetime.fromtimestamp`` is ~17 million constructions —
    slow enough that the sweep never finished.
    """
    ts = np.asarray(series["ts_ms"], dtype=np.int64)
    if ts.size == 0:
        return {"symbol": series["symbol"], "ts_ms": np.array([], np.int64),
                "open": np.array([]), "high": np.array([]),
                "low": np.array([]), "close": np.array([])}
    ist = ts + _IST_OFFSET_MS
    day = ist // _DAY_MS
    minute_of_day = (ist % _DAY_MS) // 60_000
    slot = (minute_of_day - SESSION_OPEN_MINUTE) // minutes
    # Distinct per (day, slot); day dominates so slots never collide across days.
    key = day * 100_000 + slot
    starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    ends = np.r_[starts[1:] - 1, len(ts) - 1]
    return {
        "symbol": series["symbol"],
        "ts_ms": ts[starts],
        "open": np.asarray(series["open"], float)[starts],
        "high": np.maximum.reduceat(np.asarray(series["high"], float), starts),
        "low": np.minimum.reduceat(np.asarray(series["low"], float), starts),
        "close": np.asarray(series["close"], float)[ends],
    }


#: universe.json lists four; the operator also trades MIDCPNIFTY, whose spot
#: series is named differently again.
EXTRA_INDICES = ("NIFTY MID SELECT", "NIFTY MIDCAP SELECT", "NIFTY MIDCAP 50")


def _engine_universe(lake: Path) -> Tuple[set, Dict[str, str]]:
    """The symbols the engine actually scans, and futures -> spot pairing."""
    names = _futures_name_map(lake)          # futures symbol -> spot series name
    import json as _json
    reg = _json.loads((Path(__file__).resolve().parents[1] / "app" / "services" /
                       "kite_engine" / "universe.json").read_text())
    wanted = {i["spot_symbol"] for i in reg["indices"]}
    wanted |= set(EXTRA_INDICES)
    wanted |= set(names.values())            # F&O-eligible equities and indices
    return wanted, names


def _series_for(lake: Path, wanted: set, interval: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Load only the universe's series at one stored interval.

    Filenames are ``<token>__<TRADINGSYMBOL>.parquet``, so the symbol is known
    without opening the file. Deciding AFTER a full parse meant reading all 12,246
    minute series — 231 million rows — to keep about 350 of them, which is why an
    earlier run never finished.
    """
    root = lake / "bars" / f"interval={interval}"
    if not root.exists():
        return {}, {}
    spot, fut = {}, {}
    for p in sorted(root.rglob("*.parquet")):
        text = str(p)
        is_fut = "-FUT" in text
        is_spot = ("segment=NSE" in text or "segment=INDICES" in text
                   or "segment=BSE" in text)
        if not (is_fut or is_spot):
            continue
        symbol = p.stem.split("__", 1)[-1]
        if not is_fut and symbol not in wanted:
            continue
        s = _load(p, min_rows=MIN_DERIV_BARS if is_fut else MIN_BARS)
        if not s:
            continue
        (fut if is_fut else spot)[s["symbol"]] = s
    return spot, fut


def _source_for(lake: Path, wanted: set, tf: int):
    """Native bars at this timeframe when the lake holds them, else minute+resample.

    Native and resampled are the same bars arithmetically, but the native pulls
    cover a different span than the stored minute series, so which one was used is
    reported per timeframe rather than assumed.
    """
    spot, fut = _series_for(lake, wanted, f"{tf}minute")
    if spot:
        return spot, fut, "native"
    spot, fut = _series_for(lake, wanted, "minute")
    return spot, fut, "resampled_from_minute"


def _regime(s, cfg):
    o = np.asarray(s["open"], float)
    r = compute_regime(o, np.asarray(s["high"], float), np.asarray(s["low"], float),
                       np.asarray(s["close"], float), cfg)
    longs, shorts = entry_transitions(r)
    return o, r, longs, shorts


def _sweep_one(tf: int, spot_min, fut_min, names, cfg, source: str) -> Dict[str, Any]:
    acc = {(w, m, s, h): [] for w in WINDOWS for m in MODES
           for s in ("long", "short") for h in HORIZONS}
    base = {(w, h): [] for w in WINDOWS for h in HORIZONS}

    native = source == "native"
    spot = {}
    for sym, s in spot_min.items():
        b = s if native else resample(s, tf)
        if len(b["ts_ms"]) >= MIN_BARS:
            spot[sym] = b
    fut_for_spot: Dict[str, Any] = {}
    for fsym, spot_sym in names.items():
        if fsym in fut_min and spot_sym in spot and spot_sym not in fut_for_spot:
            b = fut_min[fsym] if native else resample(fut_min[fsym], tf)
            if len(b["ts_ms"]) >= MIN_DERIV_BARS:
                fut_for_spot[spot_sym] = b

    paired = 0
    for sym, s in spot.items():
        so, _sr, slongs, sshorts = _regime(s, cfg)
        n = len(so)
        cut = int(n * (1 - HOLDOUT_FRACTION))
        f = fut_for_spot.get(sym)
        conf_l = conf_s = strict_l = strict_s = None
        if f is not None:
            paired += 1
            _fo, fr, flongs, fshorts = _regime(f, cfg)
            # Map each spot bar to its futures bar by TIMESTAMP; the two series do
            # not share an index and pairing by position compares different bars.
            fi = {t: j for j, t in enumerate(f["ts_ms"])}
            j = np.array([fi.get(int(t), -1) for t in s["ts_ms"]])
            ok = j >= 0
            conf_l = np.zeros(n, bool); conf_s = np.zeros(n, bool)
            strict_l = np.zeros(n, bool); strict_s = np.zeros(n, bool)
            conf_l[ok] = fr.bull[j[ok]]; conf_s[ok] = fr.bear[j[ok]]
            strict_l[ok] = flongs[j[ok]] & conf_l[ok]
            strict_s[ok] = fshorts[j[ok]] & conf_s[ok]

        for h in HORIZONS:
            m = n - h - 1
            if m <= cfg.warmup:
                continue
            fwd = np.full(n, np.nan)
            fwd[:m] = (so[h + 1:n] - so[1:m + 1]) / so[1:m + 1]
            span = np.zeros(n, bool)
            span[cfg.warmup:m] = True
            span &= np.isfinite(fwd)
            for w, sel in (("in_sample", span & (np.arange(n) < cut)),
                           ("holdout", span & (np.arange(n) >= cut))):
                if not sel.any():
                    continue
                base[(w, h)].append(fwd[sel])
                for side, fresh, cmask, smask in (
                        ("long", slongs, conf_l, strict_l),
                        ("short", sshorts, conf_s, strict_s)):
                    sign = 1.0 if side == "long" else -1.0
                    hit = sel & fresh
                    if hit.any():
                        acc[(w, "spot_only", side, h)].append(sign * fwd[hit])
                    if cmask is None:
                        continue
                    hc = hit & cmask
                    if hc.any():
                        acc[(w, "confluence", side, h)].append(sign * fwd[hc])
                    hs = hit & smask
                    if hs.any():
                        acc[(w, "confluence_strict", side, h)].append(sign * fwd[hs])

    windows: Dict[str, Any] = {}
    for w in WINDOWS:
        rows = []
        for h in HORIZONS:
            b = np.concatenate(base[(w, h)]) if base[(w, h)] else np.array([])
            base_bps = float(b.mean()) * 10_000 if b.size else 0.0
            row: Dict[str, Any] = {"horizon_bars": h,
                                   "horizon_minutes": h * tf,
                                   "base_rate_bps": round(base_bps, 1)}
            for mode in MODES:
                for side in ("long", "short"):
                    chunks = acc[(w, mode, side, h)]
                    v = np.concatenate(chunks) if chunks else np.array([])
                    mean = float(v.mean()) * 10_000 if v.size else 0.0
                    ref = base_bps if side == "long" else -base_bps
                    row[f"{mode}_{side}"] = {
                        "n": int(v.size), "mean_bps": round(mean, 1),
                        "alpha_bps": round(mean - ref, 1),
                        "hit_rate": round(float((v > 0).mean()), 4) if v.size else 0.0,
                    }
            rows.append(row)
        windows[w] = rows
    span = ""
    if spot:
        any_s = next(iter(spot.values()))
        span = (f"{str(np.datetime64(int(any_s['ts_ms'][0]), 'ms'))[:10]}"
                f"..{str(np.datetime64(int(any_s['ts_ms'][-1]), 'ms'))[:10]}")
    return {"timeframe_minutes": tf, "bar_source": source, "sample_span": span,
            "spot_series": len(spot), "paired_for_confluence": paired,
            "windows": windows}


def audit(lake: Path, timeframes) -> Dict[str, Any]:
    cfg = SterlingKiteEngineConfig()
    wanted, names = _engine_universe(lake)
    results = {}
    for tf in timeframes:
        spot_s, fut_s, source = _source_for(lake, wanted, tf)
        results[str(tf)] = _sweep_one(tf, spot_s, fut_s, names, cfg, source)

    cells = pos = 0
    for tf, rep in results.items():
        for r in rep["windows"]["holdout"]:
            for m in MODES:
                for side in ("long", "short"):
                    c = r[f"{m}_{side}"]
                    if c["n"] >= 30:          # ignore cells too thin to mean anything
                        cells += 1
                        pos += c["alpha_bps"] > 0
    return {
        "lake": str(lake),
        "engine_timeframe": 'scanner._fetch_candles -> get_candles(inst, "1H", 320)',
        "timeframes_swept": list(timeframes),
        "note_45min": "45 minutes is a resample only; Kite serves 1/3/5/10/15/30/60 "
                      "and day.",
        "bars": "native broker bars where the lake holds them, otherwise minute "
                "bars resampled session-anchored to 09:15 IST",
        "bar_source_per_timeframe": {tf: r["bar_source"] for tf, r in results.items()},
        "sample_span_per_timeframe": {tf: r["sample_span"] for tf, r in results.items()},
        "universe": "universe.json indices plus F&O-eligible equities",
        "holdout": f"last {int(HOLDOUT_FRACTION*100)}% of each series' bars",
        "coverage": "spot minute 2026-02-13..2026-08-14; futures minute "
                    "2026-07-01..2026-09-04, so confluence cells rest on ~6 weeks",
        "results": results,
        "holdout_cells_with_positive_alpha": pos,
        "holdout_cells_counted": cells,
        "verdict": "POSITIVE_ALPHA_SOMEWHERE" if pos else "NO_POSITIVE_ALPHA_ANYWHERE",
        "caveats": [
            "Six months of intraday is a fraction of the 2018-2026 daily span. "
            "Evidence here is weaker in both directions.",
            "Confluence rests on ~6 weeks of overlap. Those cells are a hint.",
            "Measures the UNDERLYING's move — an upper bound on a long-premium CE/PE "
            "position, never an estimate of one.",
            "Many cells means multiple testing: a scattering of positives is what "
            "noise produces. Consistency across timeframes is the thing to look for.",
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lake", type=Path, default=DEFAULT_LAKE)
    ap.add_argument("--timeframes", default=",".join(str(t) for t in TIMEFRAMES))
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    tfs = [int(x) for x in args.timeframes.split(",") if x.strip()]
    rep = audit(args.lake, tfs)
    if args.out:
        args.out.write_text(json.dumps(rep, indent=2) + "\n")
    print(f"{rep['verdict']}   holdout cells with positive alpha: "
          f"{rep['holdout_cells_with_positive_alpha']}/{rep['holdout_cells_counted']}\n")
    hdr = (f"{'tf':>4} {'win':<10} {'h':>3} {'base':>7} | {'mode':<18} {'side':<6} "
           f"{'n':>7} {'alpha':>8} {'hit':>7}")
    print(hdr); print("-" * len(hdr))
    for tf, r in rep["results"].items():
        for w, rows in r["windows"].items():
            for row in rows:
                for m in MODES:
                    for side in ("long", "short"):
                        c = row[f"{m}_{side}"]
                        if c["n"] < 30:
                            continue
                        print(f"{tf:>4} {w:<10} {row['horizon_bars']:>3} "
                              f"{row['base_rate_bps']:>7.1f} | {m:<18} {side:<6} "
                              f"{c['n']:>7,} {c['alpha_bps']:>+8.1f} "
                              f"{c['hit_rate']*100:>6.1f}%")


if __name__ == "__main__":
    main()
