"""Does the engine's own entry/exit logic make money on REAL executable futures?

Release gate P0-5 asks for expectancy on contracts an order can actually be placed
against. Cash and index bars cannot answer it, and option history does not exist to
answer it with — Kite refuses history for an expired contract, so option prices can
only be accumulated forward. Futures are what remains, in two shapes:

  * ``--interval day``    continuous, back-adjusted, 2018 onward — deep but daily.
  * ``--interval minute`` per live contract, about three months — the only way to
    reach the intraday timeframes the engine actually runs on.

What is deliberately reused rather than reimplemented
-----------------------------------------------------
``replay_premium_series`` drives the engine's own ``compute_regime``,
``entry_transitions`` and ``exits`` — the same code the live scanner runs. It enters
at the next observed raw open, fills a breached stop no better than the opening gap,
and refuses to mark a series-end exit as executable. A separate "backtest" written
for this study would measure the study, not the engine.

Predeclared BEFORE the first run, so the criterion cannot be moved afterwards
-----------------------------------------------------------------------------
1. Every cell is split into an in-sample head and an untouched holdout tail.
2. A cell PASSES only if, on its holdout and after costs:
     a. aggregate net PnL > 0, AND
     b. median per-symbol net PnL > 0 — one lucky name is not an edge, AND
     c. (a) and (b) hold at every slippage in ``SLIPPAGE_GRID``.
3. Anything else is a FAIL for that cell, and is reported as one.
4. **Cells are not independent evidence.** Sweeping sides and timeframes is
   multiple testing: with enough cells one of them passes by luck. The report
   records how many cells were run, and a single passing cell among many is
   reported as weak, not as a green light.

The parameters (21,1), (14,2), (7,3) were selected on index/spot hourly data
elsewhere in this repo, never on futures. Nothing here fits a parameter; the
holdout split is the stricter, second line of defence.

    python study/kite_futures_edge.py --interval day    --out day.json
    python study/kite_futures_edge.py --interval minute --out intraday.json
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import statistics
from typing import Any, Dict, List, Tuple

import pyarrow.parquet as pq

from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
from app.services.kite_engine.backtest import FuturesCosts, replay_premium_series

DEFAULT_LAKE = Path("/run/media/nageshmadaram/3f36ac07-fdbe-48c1-9514-ecf65c6619b0/SterlingLake")

_IST = timezone(timedelta(hours=5, minutes=30))

#: Untouched holdout for the deep daily series. A round two-year tail, chosen
#: before any result was seen.
DAILY_HOLDOUT_FROM = "2025-01-01"

#: The per-contract minute series is only about three months long, so a fixed
#: date would leave one side empty. The last 30% of bars is the holdout instead.
INTRADAY_HOLDOUT_FRACTION = 0.30

#: The one cost input that is an assumption rather than a published rate.
SLIPPAGE_GRID = (0.0001, 0.0002, 0.0005)

#: Intraday timeframes to resample minute bars onto. 1 is the raw minute series.
INTRADAY_TIMEFRAMES = (5, 15, 60)

SIDES = ("long", "short")

#: Below this a symbol cannot support the warmup plus a meaningful number of
#: trades, and its "expectancy" would be noise quoted to two decimals. The engine's
#: warmup is 21 bars, so 400 is generous for a deep daily series.
MIN_BARS = 400

#: A resampled intraday series is short by construction: a live contract carries
#: about three months, which is only ~375 hourly bars in total. Holding it to the
#: daily floor silently produced EMPTY 15-minute and 60-minute cells — a result
#: that looks like "no trades" and is really "the filter ate the data". These
#: floors are what the strategy actually needs, and a holdout this thin is
#: reported as a hint rather than a measurement.
MIN_INTRADAY_BARS = 150
MIN_INTRADAY_HOLDOUT_BARS = 80


def _load(path: Path) -> Dict[str, Any] | None:
    table = pq.read_table(path)
    meta = {k.decode(): v.decode() for k, v in (table.schema.metadata or {}).items()}
    scale = float(meta.get("price_scale") or 1)
    if table.num_rows < MIN_BARS or scale <= 0:
        return None
    import numpy as _np
    # numpy throughout. A six-month minute series is ~48,000 rows and the timeframe
    # sweep loads hundreds of them; per-row Python arithmetic here was the runtime.
    # Callers index these like sequences, which numpy arrays support.
    ts = table.column("ts").to_numpy(zero_copy_only=False).astype("datetime64[ms]")
    out = {"symbol": meta.get("tradingsymbol", path.stem),
           "ts_ms": ts.astype("int64")}
    for f in ("open", "high", "low", "close"):
        out[f] = table.column(f).to_numpy(zero_copy_only=False).astype(float) / scale
    return out


def _resample(series: Dict[str, Any], minutes: int) -> Dict[str, Any]:
    """Group minute bars into ``minutes``-wide buckets.

    Bucketed by IST calendar day AND minute-of-day, never by a rolling epoch
    window: a bucket that spanned the close and the next open would splice two
    sessions into one bar and invent a price path that never traded.
    """
    if minutes <= 1:
        return series
    out = {k: [] for k in ("ts_ms", "open", "high", "low", "close")}
    out["symbol"] = series["symbol"]
    key = None
    for i, ms in enumerate(series["ts_ms"]):
        dt = datetime.fromtimestamp(ms / 1000, _IST)
        bucket = (dt.date(), (dt.hour * 60 + dt.minute) // minutes)
        if bucket != key:
            key = bucket
            out["ts_ms"].append(ms)
            out["open"].append(series["open"][i])
            out["high"].append(series["high"][i])
            out["low"].append(series["low"][i])
            out["close"].append(series["close"][i])
        else:
            out["high"][-1] = max(out["high"][-1], series["high"][i])
            out["low"][-1] = min(out["low"][-1], series["low"][i])
            out["close"][-1] = series["close"][i]
    return out


def _tail(series: Dict[str, Any], interval: str) -> Dict[str, Any] | None:
    n = len(series["ts_ms"])
    if interval == "day":
        cut = datetime.fromisoformat(DAILY_HOLDOUT_FROM).replace(tzinfo=timezone.utc)
        cut_ms = cut.timestamp() * 1000
        start = next((i for i, t in enumerate(series["ts_ms"]) if t >= cut_ms), n)
        floor = MIN_BARS
    else:
        start = int(n * (1 - INTRADAY_HOLDOUT_FRACTION))
        floor = MIN_INTRADAY_HOLDOUT_BARS
    if n - start < floor:
        return None
    return {k: (v[start:] if isinstance(v, list) else v) for k, v in series.items()}


def _run_one(series: Dict[str, Any], cfg, costs, side: str, qty: int,
             capital: float) -> Dict[str, Any] | None:
    try:
        run = replay_premium_series(
            timestamps_ms=series["ts_ms"], premium_open=series["open"],
            premium_high=series["high"], premium_low=series["low"],
            premium_close=series["close"], cfg=cfg, trail_target=cfg.trail_target,
            exit_mode=cfg.exit_mode, qty=qty, costs=costs,
            starting_capital=capital, direction_label=side, side=side)
    except ValueError:
        return None   # rejected OHLC — never silently repaired
    trades = [t for t in run.trades if not t.exit_reason.startswith("series-end")]
    if not trades:
        return None
    return {
        "symbol": series["symbol"], "trades": len(trades),
        "net_pnl": round(sum(t.net_pnl for t in trades), 2),
        "gross_pnl": round(sum(t.gross_pnl for t in trades), 2),
        "costs": round(sum(t.costs for t in trades), 2),
        "win_rate": round(sum(1 for t in trades if t.net_pnl > 0) / len(trades), 4),
    }


def _summarise(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    nets = [r["net_pnl"] for r in rows]
    return {
        "symbols": len(rows),
        "trades": sum(r["trades"] for r in rows),
        "aggregate_net_pnl": round(sum(nets), 2),
        "aggregate_gross_pnl": round(sum(r["gross_pnl"] for r in rows), 2),
        "aggregate_costs": round(sum(r["costs"] for r in rows), 2),
        "median_symbol_net_pnl": round(statistics.median(nets), 2) if nets else 0.0,
        "profitable_symbols": sum(1 for v in nets if v > 0),
        "worst_symbol_net_pnl": round(min(nets), 2) if nets else 0.0,
        "best_symbol_net_pnl": round(max(nets), 2) if nets else 0.0,
    }


def _cell(loaded, cfg, side: str, interval: str, qty: int,
          capital: float) -> Tuple[Dict[str, Any], bool]:
    by_slippage: Dict[str, Any] = {}
    for slip in SLIPPAGE_GRID:
        costs = FuturesCosts(slippage_pct=slip)
        full, hold = [], []
        for series in loaded:
            got = _run_one(series, cfg, costs, side, qty, capital)
            if got:
                full.append(got)
            tail = _tail(series, interval)
            if tail:
                got = _run_one(tail, cfg, costs, side, qty, capital)
                if got:
                    hold.append(got)
        by_slippage[f"{slip}"] = {"full": _summarise(full), "holdout": _summarise(hold)}
    holds = [by_slippage[f"{s}"]["holdout"] for s in SLIPPAGE_GRID]
    passed = all(h["symbols"] > 0 and h["aggregate_net_pnl"] > 0
                 and h["median_symbol_net_pnl"] > 0 for h in holds)
    return by_slippage, passed


def audit(lake: Path, *, interval: str, qty: int, capital: float) -> Dict[str, Any]:
    files = sorted((lake / "bars" / f"interval={interval}").rglob("*.parquet"))
    futures = [p for p in files if "NFO-FUT" in str(p) or "BFO-FUT" in str(p)]
    base = [s for s in (_load(p) for p in futures) if s]
    cfg = SterlingKiteEngineConfig()
    timeframes = INTRADAY_TIMEFRAMES if interval == "minute" else (1,)

    cells: Dict[str, Any] = {}
    passes: List[str] = []
    for tf in timeframes:
        loaded = ([_resample(s, tf) for s in base] if tf > 1 else base)
        floor = MIN_BARS if interval == "day" else MIN_INTRADAY_BARS
        loaded = [s for s in loaded if len(s["ts_ms"]) >= floor]
        for side in SIDES:
            name = f"{side}@{tf}min" if interval == "minute" else f"{side}@day"
            result, ok = _cell(loaded, cfg, side, interval, qty, capital)
            cells[name] = {"passed": ok, "symbols_used": len(loaded),
                           "results_by_slippage": result}
            if ok:
                passes.append(name)

    return {
        "lake": str(lake),
        "interval": interval,
        "vehicle": "NFO/BFO futures"
                   + (", continuous back-adjusted" if interval == "day"
                      else ", per live contract"),
        "criterion": {
            "predeclared": True,
            "holdout": (f"from {DAILY_HOLDOUT_FROM}" if interval == "day"
                        else f"last {int(INTRADAY_HOLDOUT_FRACTION * 100)}% of bars"),
            "slippage_grid": list(SLIPPAGE_GRID),
            "requires": "holdout aggregate net > 0 AND median per-symbol net > 0, "
                        "at every slippage in the grid",
        },
        "multiple_testing": {
            "cells_tested": len(cells),
            "cells_passed": len(passes),
            "passing_cells": passes,
            "note": "Cells are not independent evidence. One passing cell out of "
                    f"{len(cells)} is what luck produces; it is a reason to test that "
                    "cell properly, not a reason to trade it.",
        },
        "engine_logic": "app.services.kite_engine.backtest.replay_premium_series "
                        "(compute_regime + entry_transitions + exits, next-open entry, "
                        "gap-aware stops, direction-aware trail fills)",
        "position": {"qty": qty, "starting_capital": capital},
        "symbols_considered": len(futures),
        "symbols_with_enough_bars": len(base),
        "cells": cells,
        "verdict": "PASS" if len(passes) == len(cells) and cells else "FAIL",
        "caveats": [
            "One fixed quantity per trade, no position sizing and no portfolio risk.",
            "Slippage is an assumption swept across a grid, not an observed fill cost.",
            "Continuous daily series are back-adjusted by the broker; roll costs are "
            "not separately modelled.",
            "The per-contract minute series covers about three months, so its holdout "
            "is weeks and an hourly series is a few hundred bars. Those cells are a "
            "hint, not a measurement, and a PASS in one of them would need re-testing "
            "on a longer series before it meant anything.",
            "Long and short are measured separately. Nothing here models running both, "
            "or the margin either would consume.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lake", type=Path, default=DEFAULT_LAKE)
    parser.add_argument("--interval", default="day", choices=["day", "minute"])
    parser.add_argument("--qty", type=int, default=1)
    parser.add_argument("--capital", type=float, default=1_000_000.0)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    report = audit(args.lake, interval=args.interval, qty=args.qty, capital=args.capital)
    if args.out:
        args.out.write_text(json.dumps(report, indent=2) + "\n")
    head = {"verdict": report["verdict"],
            "multiple_testing": report["multiple_testing"],
            "cells": {k: {"passed": v["passed"],
                          "holdout": v["results_by_slippage"][str(SLIPPAGE_GRID[1])]["holdout"]}
                      for k, v in report["cells"].items()}}
    print(json.dumps(head, indent=2))


if __name__ == "__main__":
    main()
