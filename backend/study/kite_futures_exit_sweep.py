"""Is the exit what is killing it? Sweep the red-count modes on real futures.

The shipped default is ``one_red``: leave the moment ANY of the three SuperTrend
lines turns against the position. That is the tightest possible exit against a
three-line-agreement entry, and the obvious suspicion when a strategy loses money
*before costs* is that it is being stopped out of its own thesis.

So sweep the counter — and sweep the knob that decides whether the counter can
even be reached. ``config.py`` records why that second axis is mandatory:

    "Because the tightest (fast) line flips FIRST, its breach ≈ one_red, which
     pre-empts a two_red/three_red counter → the exit_mode knob is near-inert live."

With ``exit_aligned_trail`` OFF the trailing stop rides the tightest still-green
line, so it fires at roughly one_red no matter what ``exit_mode`` says, and a
sweep of exit_mode alone would measure nothing while appearing to measure
something. With it ON the stop rides the line that matches the mode, so the stop
breach and the red count coincide and the mode is real.

Multiple testing, stated up front
---------------------------------
4 modes x 2 trail settings x 2 sides = 16 cells, on top of the 8 already run. At
a 5% false-pass rate you expect roughly one winner from noise alone. A cell that
passes here is a CANDIDATE to be re-tested on the full slippage grid and the
intraday series — never a result on its own. The report says how many cells ran.

    python study/kite_futures_exit_sweep.py --interval day --out sweep.json
"""
from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path
import shutil
import sqlite3
import statistics
import tempfile
from typing import Any, Dict, List

from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
from app.services.kite_engine.backtest import FuturesCosts, replay_premium_series
from study.kite_futures_edge import (DEFAULT_LAKE, MIN_BARS, MIN_INTRADAY_BARS,
                                     SLIPPAGE_GRID, _load, _resample, _tail)

EXIT_MODES = ("one_red", "two_red", "three_red", "three_red_signal")
ALIGNED_TRAIL = (False, True)
SIDES = ("long", "short")

#: One slippage for the sweep itself — the grid is for confirming a candidate,
#: not for widening the search. 0.0002 is the middle of the grid used elsewhere.
SWEEP_SLIPPAGE = SLIPPAGE_GRID[1]


def lot_sizes(lake: Path) -> Dict[str, int]:
    """Exchange lot size per futures symbol, from the lake's instrument master.

    A futures order cannot be for one unit — the minimum tradable quantity is one
    LOT, and lots here run from 20 to 71,475 with a median of 600. This is not a
    detail: brokerage is ~Rs 20 per order plus GST, FIXED whatever the size, so a
    qty=1 study charges a whole round trip's fee against one unit of notional and
    reports a loss for a trade nobody could place. Everything else in the cost
    schedule scales with quantity; that one term does not.
    """
    manifest = lake / "manifest" / "coverage.sqlite"
    if not manifest.exists():
        return {}
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / "coverage.sqlite"
        shutil.copy2(manifest, local)      # never write to a read-only lake
        conn = sqlite3.connect(local)
        return {row[0]: int(row[1]) for row in conn.execute(
            "SELECT tradingsymbol, lot_size FROM instruments "
            "WHERE segment IN ('NFO-FUT','BFO-FUT') AND lot_size > 0")}


def _cell(loaded, cfg, side: str, costs, interval: str,
          lots: Dict[str, int] | None = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for window in ("full", "holdout"):
        rows = []
        for series in loaded:
            target = series if window == "full" else _tail(series, interval)
            if target is None:
                continue
            qty = (lots or {}).get(series["symbol"], 1) or 1
            try:
                run = replay_premium_series(
                    timestamps_ms=target["ts_ms"], premium_open=target["open"],
                    premium_high=target["high"], premium_low=target["low"],
                    premium_close=target["close"], cfg=cfg,
                    trail_target=cfg.trail_target, exit_mode=cfg.exit_mode,
                    qty=qty, costs=costs, starting_capital=1_000_000_000.0,
                    direction_label=side, side=side)
            except ValueError:
                continue
            trades = [t for t in run.trades
                      if not t.exit_reason.startswith("series-end")]
            if not trades:
                continue
            rows.append({
                "net": sum(t.net_pnl for t in trades),
                "gross": sum(t.gross_pnl for t in trades),
                "n": len(trades),
                "bars": sum(t.bars_held for t in trades),
                "wins": sum(1 for t in trades if t.net_pnl > 0),
            })
        nets = [r["net"] for r in rows]
        n = sum(r["n"] for r in rows)
        out[window] = {
            "symbols": len(rows), "trades": n,
            "gross_pnl": round(sum(r["gross"] for r in rows), 2),
            "net_pnl": round(sum(nets), 2),
            "median_symbol_net": round(statistics.median(nets), 2) if nets else 0.0,
            "profitable_symbols": sum(1 for v in nets if v > 0),
            # The diagnostic that says whether the exit is strangling the thesis:
            # a mean hold of one or two bars means the position never got to be right.
            "mean_bars_held": round(sum(r["bars"] for r in rows) / n, 2) if n else 0.0,
            "win_rate": round(sum(r["wins"] for r in rows) / n, 4) if n else 0.0,
        }
    h = out["holdout"]
    out["passed"] = bool(h["symbols"] and h["net_pnl"] > 0 and h["median_symbol_net"] > 0)
    out["gross_positive_holdout"] = bool(h["symbols"] and h["gross_pnl"] > 0)
    return out


def audit(lake: Path, *, interval: str, timeframe: int,
          use_lots: bool = True) -> Dict[str, Any]:
    files = sorted((lake / "bars" / f"interval={interval}").rglob("*.parquet"))
    futures = [p for p in files if "NFO-FUT" in str(p) or "BFO-FUT" in str(p)]
    base = [s for s in (_load(p) for p in futures) if s]
    if timeframe > 1:
        base = [_resample(s, timeframe) for s in base]
    floor = MIN_BARS if interval == "day" else MIN_INTRADAY_BARS
    loaded = [s for s in base if len(s["ts_ms"]) >= floor]
    costs = FuturesCosts(slippage_pct=SWEEP_SLIPPAGE)
    lots = lot_sizes(lake) if use_lots else {}
    sized = [s for s in loaded if not use_lots or s["symbol"] in lots]

    cells: Dict[str, Any] = {}
    for mode in EXIT_MODES:
        for aligned in ALIGNED_TRAIL:
            cfg = dataclasses.replace(SterlingKiteEngineConfig(),
                                      exit_mode=mode, exit_aligned_trail=aligned)
            for side in SIDES:
                name = f"{mode}|aligned={int(aligned)}|{side}"
                cells[name] = _cell(sized, cfg, side, costs, interval, lots)

    passed = [k for k, v in cells.items() if v["passed"]]
    gross_pos = [k for k, v in cells.items() if v["gross_positive_holdout"]]
    return {
        "lake": str(lake), "interval": interval, "timeframe_minutes": timeframe,
        "symbols": len(sized), "slippage": SWEEP_SLIPPAGE,
        "position_size": ("one exchange LOT per trade, from the instrument master"
                          if use_lots else "one unit per trade (NOT tradable)"),
        "axes": {"exit_mode": list(EXIT_MODES), "exit_aligned_trail": list(ALIGNED_TRAIL),
                 "side": list(SIDES)},
        "why_aligned_trail_is_swept": (
            "With exit_aligned_trail OFF the stop rides the tightest still-green line "
            "and fires at roughly one_red whatever exit_mode says, so sweeping the mode "
            "alone measures nothing. config.py records this."),
        "multiple_testing": {
            "cells_tested": len(cells), "cells_passed": len(passed),
            "passing_cells": passed,
            "cells_with_positive_holdout_gross": gross_pos,
            "note": "A pass here is a candidate for confirmation on the full slippage "
                    "grid and the intraday series, not a result.",
        },
        "cells": cells,
        "verdict": "CANDIDATE_FOUND" if passed else "FAIL",
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lake", type=Path, default=DEFAULT_LAKE)
    ap.add_argument("--interval", default="day", choices=["day", "minute"])
    ap.add_argument("--timeframe", type=int, default=1, help="resample minutes")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--unit-qty", action="store_true",
                    help="size 1 unit per trade instead of 1 lot (not tradable; "
                         "kept only to show what the lot correction is worth)")
    args = ap.parse_args()
    rep = audit(args.lake, interval=args.interval, timeframe=args.timeframe,
                use_lots=not args.unit_qty)
    if args.out:
        args.out.write_text(json.dumps(rep, indent=2) + "\n")
    print(f"{rep['verdict']}  symbols={rep['symbols']}  "
          f"cells={rep['multiple_testing']['cells_tested']} "
          f"passed={rep['multiple_testing']['cells_passed']}\n")
    head = f"{'cell':<34} {'trades':>7} {'gross':>11} {'net':>12} {'bars':>6} {'win%':>6} {'prof':>9}"
    print(head); print("-" * len(head))
    for name, v in rep["cells"].items():
        h = v["holdout"]
        print(f"{name:<34} {h['trades']:>7} {h['gross_pnl']:>11,.0f} {h['net_pnl']:>12,.0f} "
              f"{h['mean_bars_held']:>6.1f} {h['win_rate'] * 100:>5.1f}% "
              f"{str(h['profitable_symbols']) + '/' + str(h['symbols']):>9}")


if __name__ == "__main__":
    main()
