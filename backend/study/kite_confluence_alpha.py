"""Does the confluence filter actually improve the signal, or just thin it?

``scan_source="confluence"`` is the engine's highest-conviction mode. From
``scanner._confluence_one``, a row is emitted only when BOTH hold on the SAME bar:

  1. the UNDERLYING fires a FRESH triple-ST entry (``row.is_fresh`` and its
     timestamp equals the latest closed bar), and
  2. the DERIVATIVE's own triple-ST confirms — ``d.is_active or d.is_fresh``, i.e.
     running or freshly entered, not a stale historical entry.

"Confluence must exist on one bar; never join an old underlying trigger to a
premium trend observed later" — so the two conditions are evaluated at the same
index, never within a window.

The live filter confirms on an OPTION's premium. No option history exists, so
this measures the futures analogue: the underlying fires fresh, and that
underlying's FUTURE is itself aligned the same way on its own chart. Direction
maps the way the vehicle does — a bull signal buys, a bear signal sells the
future, and the future's own chart must agree in that direction.

The question is narrow and worth asking on its own terms. Filtering always
reduces trade count; that is not a benefit. The filter earns its place only if
the signals it KEEPS have higher alpha than the ones it started with, out of
sample. So every number here is reported against the unfiltered spot signal on
the same bars, split by window, with a random-entry control on the holdout.

    python study/kite_confluence_alpha.py --out confluence.json
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Any, Dict, List, Tuple

import numpy as np

from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
from app.engines.sterling_kite_engine.regime import compute_regime, entry_transitions
from study.kite_futures_edge import DEFAULT_LAKE, DAILY_HOLDOUT_FROM, _load

HORIZONS = (5, 10, 20)
WINDOWS = ("in_sample", "holdout")

#: Index futures carry a different name from their spot series; equities do not.
INDEX_SPOT = {"NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK",
              "FINNIFTY": "NIFTY FIN SERVICE", "MIDCPNIFTY": "NIFTY MID SELECT",
              "SENSEX": "SENSEX", "BANKEX": "BANKEX"}


def _futures_name_map(lake: Path) -> Dict[str, str]:
    """futures tradingsymbol -> the spot series it is written on."""
    manifest = lake / "manifest" / "coverage.sqlite"
    if not manifest.exists():
        return {}
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / "coverage.sqlite"
        shutil.copy2(manifest, local)      # never write to a read-only lake
        conn = sqlite3.connect(local)
        out = {}
        for sym, name in conn.execute(
                "SELECT tradingsymbol, name FROM instruments "
                "WHERE segment IN ('NFO-FUT','BFO-FUT') AND name <> ''"):
            out[sym] = INDEX_SPOT.get(name, name)
        return out


def _load_all(lake: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    spot, fut = {}, {}
    for p in sorted((lake / "bars" / "interval=day").rglob("*.parquet")):
        s = _load(p)
        if not s:
            continue
        o = np.asarray(s["open"], float)
        if not np.all(np.isfinite(o)) or np.any(o <= 0):
            continue
        if "segment=NSE" in str(p) or "segment=INDICES" in str(p):
            spot[s["symbol"]] = s
        elif "-FUT" in str(p):
            fut[s["symbol"]] = s
    return spot, fut


def _regime(s: Dict[str, Any], cfg):
    o = np.asarray(s["open"], float)
    r = compute_regime(o, np.asarray(s["high"], float), np.asarray(s["low"], float),
                       np.asarray(s["close"], float), cfg)
    longs, shorts = entry_transitions(r)
    return o, r, longs, shorts


def audit(lake: Path) -> Dict[str, Any]:
    cfg = SterlingKiteEngineConfig()
    spot, fut = _load_all(lake)
    names = _futures_name_map(lake)
    cut = datetime.fromisoformat(DAILY_HOLDOUT_FROM).replace(
        tzinfo=timezone.utc).timestamp() * 1000

    # bucket -> list of forward returns, in the direction the position is taken
    # A strictness ladder, not two points. The shipped rule accepts a derivative
    # that is merely ALIGNED; requiring it to fire FRESH on the same bar is the
    # tightest reading of "confluence" and shows whether more selectivity is what
    # the filter is missing.
    modes = ("spot_only", "confluence", "confluence_strict")
    acc = {(w, mode, side, h): [] for w in WINDOWS
           for mode in modes for side in ("long", "short")
           for h in HORIZONS}
    base = {(w, h): [] for w in WINDOWS for h in HORIZONS}
    pairs = 0

    for fsym, spot_sym in names.items():
        f = fut.get(fsym)
        s = spot.get(spot_sym)
        if f is None or s is None:
            continue
        # Align the two series by timestamp — a future and its underlying do not
        # share a bar index, and pairing by position would compare different days.
        fi = {t: i for i, t in enumerate(f["ts_ms"])}
        shared = [(i, fi[t]) for i, t in enumerate(s["ts_ms"]) if t in fi]
        if len(shared) < cfg.warmup * 4:
            continue
        pairs += 1
        so, _sr, slongs, sshorts = _regime(s, cfg)
        _fo, fr, flongs, fshorts = _regime(f, cfg)
        ts = np.asarray(s["ts_ms"])
        n = len(so)

        for h in HORIZONS:
            m = n - h - 1
            if m <= cfg.warmup:
                continue
            fwd = np.full(n, np.nan)
            fwd[:m] = (so[h + 1:n] - so[1:m + 1]) / so[1:m + 1]
            for si, fj in shared:
                if si < cfg.warmup or si >= m or not np.isfinite(fwd[si]):
                    continue
                w = "in_sample" if ts[si] < cut else "holdout"
                base[(w, h)].append(float(fwd[si]))
                for side, fresh, confirm, strict in (
                        ("long", slongs[si], bool(fr.bull[fj]), bool(flongs[fj])),
                        ("short", sshorts[si], bool(fr.bear[fj]), bool(fshorts[fj]))):
                    if not fresh:
                        continue
                    signed = float(fwd[si]) if side == "long" else -float(fwd[si])
                    acc[(w, "spot_only", side, h)].append(signed)
                    if confirm:
                        acc[(w, "confluence", side, h)].append(signed)
                        if strict:
                            acc[(w, "confluence_strict", side, h)].append(signed)

    windows: Dict[str, Any] = {}
    for w in WINDOWS:
        rows = []
        for h in HORIZONS:
            b = np.array(base[(w, h)])
            b = b[np.isfinite(b)]
            base_bps = float(b.mean()) * 10_000 if b.size else 0.0
            row: Dict[str, Any] = {"horizon_bars": h, "base_rate_bps": round(base_bps, 1)}
            for mode in modes:
                for side in ("long", "short"):
                    v = np.array(acc[(w, mode, side, h)])
                    v = v[np.isfinite(v)]
                    mean = float(v.mean()) * 10_000 if v.size else 0.0
                    # A short profits when the underlying falls, and its returns are
                    # already sign-flipped above, so alpha is signal minus the
                    # base rate taken in the same direction.
                    ref = base_bps if side == "long" else -base_bps
                    row[f"{mode}_{side}"] = {
                        "n": int(v.size), "mean_bps": round(mean, 1),
                        "alpha_bps": round(mean - ref, 1),
                        "hit_rate": round(float((v > 0).mean()), 4) if v.size else 0.0,
                    }
            for side in ("long", "short"):
                c = row[f"spot_only_{side}"]["alpha_bps"]
                total = row[f"spot_only_{side}"]["n"]
                for mode in ("confluence", "confluence_strict"):
                    a = row[f"{mode}_{side}"]["alpha_bps"]
                    kept = row[f"{mode}_{side}"]["n"]
                    row[f"filter_{mode}_{side}"] = {
                        "kept_fraction": round(kept / total, 4) if total else 0.0,
                        "alpha_improvement_bps": round(a - c, 1),
                        "filter_helps": a > c,
                        "alpha_reaches_zero": a > 0,
                    }
            rows.append(row)
        windows[w] = rows

    hold = windows["holdout"]
    helps = sum(1 for r in hold for side in ("long", "short")
                if r[f"filter_confluence_{side}"]["filter_helps"])
    positive = sum(1 for r in hold for side in ("long", "short")
                   if r[f"confluence_{side}"]["alpha_bps"] > 0)
    return {
        "lake": str(lake), "holdout_from": DAILY_HOLDOUT_FROM,
        "definition": "underlying fires FRESH and the derivative's own triple-ST is "
                      "aligned the same way on the SAME bar (scanner._confluence_one)",
        "derivative_used": "the underlying's FUTURE, standing in for the option leg — "
                           "no option history exists to confirm against",
        "paired_underlyings": pairs,
        "windows": windows,
        "holdout_cells": len(hold) * 2,
        "holdout_cells_where_filter_helps": helps,
        "holdout_cells_with_positive_alpha": positive,
        "verdict": ("CONFLUENCE_HELPS_OUT_OF_SAMPLE"
                    if positive == len(hold) * 2 else "NO_OUT_OF_SAMPLE_EDGE"),
        "caveats": [
            "Filtering always reduces trade count; that is not a benefit. The filter "
            "earns its place only by RAISING alpha on what it keeps.",
            "Confirmed on futures, not options. The live filter reads an option's own "
            "premium, which decays; a future does not.",
            "Continuous futures are back-adjusted by the broker, which shifts levels. "
            "The ST trend state is shape-driven so it is largely preserved, but this "
            "is a real difference from the per-contract series the engine reads.",
            "Measures the UNDERLYING's move — an upper bound on a long-premium option "
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
    print(f"paired underlyings={rep['paired_underlyings']}  -> {rep['verdict']}")
    print(f"holdout cells with positive alpha: "
          f"{rep['holdout_cells_with_positive_alpha']}/{rep['holdout_cells']}   "
          f"filter helps in {rep['holdout_cells_where_filter_helps']}/{rep['holdout_cells']}\n")
    hdr = (f"{'window':<10} {'h':>3} {'side':<6} {'spot α':>8} | {'conf n':>7} "
           f"{'kept':>6} {'conf α':>8} | {'strict n':>8} {'kept':>6} {'strict α':>9}")
    print(hdr); print("-" * len(hdr))
    for w, rows in rep["windows"].items():
        for r in rows:
            for side in ("long", "short"):
                sp = r[f"spot_only_{side}"]
                cf, cfk = r[f"confluence_{side}"], r[f"filter_confluence_{side}"]
                st, stk = r[f"confluence_strict_{side}"], r[f"filter_confluence_strict_{side}"]
                print(f"{w:<10} {r['horizon_bars']:>3} {side:<6} {sp['alpha_bps']:>+8.1f} | "
                      f"{cf['n']:>7,} {cfk['kept_fraction']*100:>5.1f}% {cf['alpha_bps']:>+8.1f} | "
                      f"{st['n']:>8,} {stk['kept_fraction']*100:>5.1f}% {st['alpha_bps']:>+9.1f}")


if __name__ == "__main__":
    main()
