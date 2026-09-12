"""Everything Snapback claims, reproduced from the store.

Run:

    .venv/bin/python -m study.snapback_research              # the whole thing
    .venv/bin/python -m study.snapback_research --part gate  # just the verdict

It calls the ENGINE's own ``replay`` and ``entry_indices``, so a result here and
a signal on the board cannot come from two different rules. The parts are
ordered so the fastest way to falsify the idea comes first.

  battery   fourteen conditions x two sides x two universes, each reported as
            an EXCESS over its own side's unconditional baseline. The window
            ends in a 2026 index drawdown, so a raw put return is mostly regime
            and only the excess is the signal's own.
  vol       realised vol AFTER these entries against the trailing vol that
            prices the option. This is the check that can kill the finding: if
            vol rises after a breakout, the real premium is dearer than modelled.
  contract  tenor, delta and slippage, scored on BREAK-EVEN VRP rather than on
            mean return, because mean return flatters short-dated contracts.
  gate      the walk-forward run and its verdict.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.engines.option_contracts import SPECS, canonical          # noqa: E402
from app.engines.snapback import (Bars, SnapbackConfig, VRP_BAND,   # noqa: E402
                                  break_even_vrp, entry_indices, features)
from app.engines.snapback.backtest import CostModel, replay, returns_at_vrp  # noqa: E402
from app.engines.snapback import walkforward as wf                  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
CALIBRATION_SPAN = "2017-09..2026-09 (2,232 sessions, 430,223 daily bars)"
INDICES = ("NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX")
STOCKS = ("RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "AXISBANK",
          "LT", "BHARTIARTL", "KOTAKBANK", "BAJFINANCE", "BAJAJFINSV",
          "ADANIENT", "ADANIPORTS", "TATASTEEL")


def _db() -> str:
    env = os.environ.get("STERLING_DB_PATH")
    if env:
        return env
    here = pathlib.Path(__file__).resolve().parents[1]
    return str(here / "sterling_paper.db")


def daily_bars(symbol: str, *, min_sessions: int = 400) -> Optional[Bars]:
    """The instrument's daily sessions.

    Real ``1d`` bars when the store has them — ``study.snapback_backfill`` pulls
    nine years of them for the whole F&O list — and a roll-up of 5-minute bars
    otherwise. The exchange's own daily bar is the better input: a roll-up
    inherits every gap in the intraday feed, and its high and low are the
    extremes of the bars that happened to arrive.
    """
    con = sqlite3.connect(_db())
    try:
        rows = con.execute(
            "SELECT time, open, high, low, close, volume FROM ohlcv "
            "WHERE symbol=? AND resolution='1d' ORDER BY time", (symbol,)).fetchall()
        if len(rows) >= min_sessions:
            a = np.array([[r[0], r[1], r[2], r[3], r[4], r[5]] for r in rows],
                         dtype=float)
            return Bars(a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5])
        rows = con.execute(
            "SELECT time, open, high, low, close, volume FROM ohlcv "
            "WHERE symbol=? AND resolution='5m' ORDER BY time", (symbol,)).fetchall()
    finally:
        con.close()
    if not rows:
        return None
    days: dict[str, list] = {}
    for t, o, h, l, c, v in rows:
        key = datetime.fromtimestamp(t, tz=IST).strftime("%Y-%m-%d")
        d = days.get(key)
        if d is None:
            days[key] = [t, o, h, l, c, v, 1]
        else:
            d[2] = max(d[2], h)
            d[3] = min(d[3], l)
            d[4] = c
            d[5] += v
            d[6] += 1
    keep = [v for _, v in sorted(days.items()) if v[6] >= 40]
    if len(keep) < 100:
        return None
    a = np.array([[r[0], r[1], r[2], r[3], r[4], r[5]] for r in keep], dtype=float)
    return Bars(a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5])


def load(names) -> dict[str, Bars]:
    out: dict[str, Bars] = {}
    for n in names:
        b = daily_bars(n)
        if b is not None:
            out[canonical(n)] = b
    return out


def stored_universe(*, min_sessions: int = 800) -> tuple[str, ...]:
    """Every instrument the store has enough daily history for AND a spec.

    Both halves matter. Without history there is nothing to measure; without a
    published lot size and strike step the engine cannot name a contract, so a
    result for it would be a number about an instrument nobody can trade.
    """
    from app.engines.option_contracts import spec_for
    con = sqlite3.connect(_db())
    try:
        rows = con.execute(
            "SELECT symbol, COUNT(*) FROM ohlcv WHERE resolution='1d' "
            "GROUP BY symbol HAVING COUNT(*) >= ?", (int(min_sessions),)).fetchall()
    finally:
        con.close()
    return tuple(sorted(s for s, _ in rows if spec_for(s) is not None))


# ----------------------------------------------------------------- part: vol

def part_vol(tapes: dict[str, Bars], cfg: SnapbackConfig, label: str) -> None:
    """Does vol RISE after these entries? If it does, the premium is underpriced.

    Every return this strategy reports is modelled at ``RV20(entry) x vrp``. If
    realised vol after a breakout systematically exceeds the trailing vol that
    priced the option, the market's real quote at that moment is dearer than the
    model charged, and the break-even is clearing a moving target.
    """
    print(f"\n=== vol after entry, {label} ===")
    print(f"{'condition':<20}{'n':>6}{'RV20 in %':>11}{'fwd10 %':>10}"
          f"{'fwd10/in':>10}")
    for side in ("fade_up", "fade_down", "ALL SESSIONS"):
        ins, fwd = [], []
        for sym, bars in tapes.items():
            f = features(bars, cfg)
            if side == "ALL SESSIONS":
                idx = np.arange(cfg.warmup_bars(), len(bars) - 12)
            else:
                idx = entry_indices(bars, cfg, side)
                idx = idx[idx < len(bars) - 12]
            for i in idx:
                v = f.rv[i]
                if not np.isfinite(v) or v <= 0:
                    continue
                r = np.diff(np.log(bars.close[i + 1:i + 11]))
                if len(r) < 5:
                    continue
                ins.append(float(v))
                fwd.append(float(r.std(ddof=1) * math.sqrt(250)))
        if len(ins) < 20:
            print(f"{side:<20}{len(ins):>6}   too few")
            continue
        ins_a, fwd_a = np.array(ins), np.array(fwd)
        print(f"{side:<20}{len(ins):>6}{np.median(ins_a) * 100:>11.1f}"
              f"{np.median(fwd_a) * 100:>10.1f}"
              f"{np.median(fwd_a / ins_a):>10.3f}")
    print("  A ratio BELOW 1 means the option is cheap at these entries: the "
          "market goes quiet\n  after an upside break, so the premium the model "
          "charges is if anything too dear.")


# ------------------------------------------------------------ part: contract

def part_contract(tapes: dict[str, Bars], base: SnapbackConfig, label: str) -> None:
    """Tenor, delta and slippage, scored on break-even VRP.

    Mean return is the flattering metric for a short-dated contract — it is
    cheaper, so the same move is a bigger percentage — and the flattery hides
    that theta per rupee of premium runs at about 1/(2T). Break-even VRP prices
    that in: it asks how dear the option can be before the trade stops paying.
    """
    from dataclasses import replace
    print(f"\n=== contract choice, {label} ===")
    print(f"{'dte':>5}{'delta':>7}{'slip%':>7}{'trades':>8}{'mean/day%':>11}"
          f"{'BE-VRP':>8}{'margin':>8}")
    for dte in (14, 21, 35, 50):
        for delta in (0.40, 0.55, 0.70):
            cfg = replace(base, min_dte=dte, target_delta=delta,
                          hold_days=min(base.hold_days, dte - 1))
            _row(tapes, cfg, CostModel(), dte, delta, 0.50)
    for slip in (0.25, 1.00, 2.00):
        _row(tapes, base, CostModel(slippage_pct=slip), base.min_dte,
             base.target_delta, slip)


def _row(tapes, cfg, cost, dte, delta, slip) -> None:
    res = replay(tapes, cfg, cost=cost)
    _, daily = res.by_day()
    if len(daily) < 20:
        return
    be = break_even_vrp(returns_at_vrp(tapes, cfg, cost))
    margin = (be - VRP_BAND[1]) if np.isfinite(be) else float("nan")
    print(f"{dte:>5}{delta:>7.2f}{slip:>7.2f}{len(res.trades):>8}"
          f"{daily.mean() * 100:>11.2f}{be:>8.2f}{margin:>+8.2f}")


# ---------------------------------------------------------------- part: gate

def part_gate(tapes: dict[str, Bars], base: SnapbackConfig, label: str,
              *, rounds: int, out: Optional[pathlib.Path],
              record: bool = False, measured_at: str = "") -> dict:
    print(f"\n=== walk-forward, {label} ===")
    report = wf.run(tapes, base, permutation_rounds=rounds)
    d = report.as_dict()
    oos = d["oos"]
    print(f"folds: {len(d['folds'])}   variants tried: {d['n_trials']}")
    for f in d["folds"]:
        sel = f["oos_selected"] or {"trades": 0, "mean_day_return_pct": float("nan")}
        print(f"  fold {f['fold']}  IS {f['in_sample'][0]}..{f['in_sample'][1]}"
              f"  chose {str(f['chosen']):<16}"
              f"  selected {sel['trades']:>3}tr {sel['mean_day_return_pct']:>+7.2f}%"
              f"   fixed {f['oos_fixed']['trades']:>3}tr "
              f"{f['oos_fixed']['mean_day_return_pct']:>+7.2f}%")
    for name, key in (("SELECTED (does parameter choice generalise?)", "selected"),
                      ("FIXED on the same windows (does the RULE generalise?)", "oos"),
                      ("FULL sample (optimistic — params were chosen on it)",
                       "full_sample")):
        b = d[key]
        print(f"\n{name}")
        print(f"  trades {b['trades']} on {b['n_days']} distinct entry days  "
              f"mean/day {b['mean_day_return_pct']:+.2f}%  t {b['t_stat']:+.2f}  "
              f"95% CI {b['ci_pct']}")
        print(f"  net {b['net']:+,.0f} (gross {b['gross']:+,.0f}, costs "
              f"{b['costs']:,.0f})  max DD {b['max_drawdown_pct']:.1f}% at "
              f"{b['allocation_pct']}% of capital per position")
        print(f"  per calendar year: {b['per_year_pct']}")
    print(f"\n  deflated Sharpe {d['deflated_sharpe']:.3f}   "
          f"permutation p (out-of-sample windows) {d['permutation_p']}   "
          f"(full sample) {d['permutation_p_full_sample']}")
    print(f"  break-even VRP {d['breakeven_vrp']} vs market {d['vrp_band']}")
    v = d["verdict"]
    print(f"\n  PROMOTED: {v['promoted']}")
    for k, ok in v["checks"].items():
        print(f"    {'PASS' if ok else 'FAIL'}  {k}")
    for r in v["reasons"]:
        print(f"    - {r}")
    if out:
        out.write_text(json.dumps(d, indent=2, default=str))
        print(f"\n  written to {out}")
    if record:
        _persist(d, tapes, measured_at)
    return d


def _persist(d: dict, tapes: dict, measured_at: str) -> None:
    """Store the verdict where the engine, the board and the settings page read it.

    ``measured_at`` is supplied by the caller rather than taken from the clock:
    re-running this against a stale dataset would otherwise stamp an old
    measurement as today's, and "promoted last week" is a claim about the data,
    not about when the script ran.
    """
    from app.services import db
    from app.services.snapback_validation import Validation, record as save
    db.init()
    oos, v = d["oos"], d["verdict"]
    ok = save(Validation(
        promoted=bool(v["promoted"]),
        measured_at=measured_at or "unknown",
        span=CALIBRATION_SPAN,
        universe=sorted(tapes),
        oos_trades=oos["trades"], oos_entry_days=oos["n_days"],
        oos_mean_day_return_pct=oos["mean_day_return_pct"],
        oos_ci_pct=list(oos["ci_pct"]), sharpe=oos["sharpe"],
        deflated_sharpe=d["deflated_sharpe"],
        permutation_p=d["permutation_p"],
        permutation_p_full_sample=d["permutation_p_full_sample"],
        breakeven_vrp=d["breakeven_vrp"],
        max_drawdown_pct=oos["max_drawdown_pct"],
        allocation_pct=oos["allocation_pct"],
        total_return_pct=oos["total_return_pct"],
        per_year_pct=dict(oos["per_year_pct"]),
        checks=dict(v["checks"]), reasons=list(v["reasons"]),
        slippage_pct=CostModel().slippage_pct,
    ))
    print("  verdict persisted — the board and the settings page now read it"
          if ok else
          "  VERDICT NOT PERSISTED — the store refused the write, so the board "
          "is still showing the PREVIOUS run. See the log.")


# ------------------------------------------------------------------- driver

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="all",
                    choices=["all", "vol", "contract", "gate"])
    ap.add_argument("--universe", default="both",
                    choices=["both", "indices", "stocks", "all", "fno"])
    ap.add_argument("--rounds", type=int, default=300)
    ap.add_argument("--out", default="")
    ap.add_argument("--record", action="store_true",
                    help="persist the verdict so the engine, the board and the "
                         "settings page read the same measurement")
    ap.add_argument("--measured-at", default="",
                    help="ISO date of this run. Passed in rather than read from "
                         "the clock so a re-run of an old dataset cannot stamp "
                         "itself as today's measurement.")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override one config field for this run, e.g. "
                         "--set market_ema=100. Validated the same way a "
                         "settings write is, so a typo is refused rather than "
                         "silently ignored — and echoed into the output, so a "
                         "result can never be read as the shipped defaults.")
    args = ap.parse_args()

    # ONE LOT, not a percentage budget. Two reasons, and neither is convenience:
    # a percentage budget on a small account cannot buy a single NIFTY lot at
    # all (2% of 1 lakh is 2,000 against a ~21,750 lot), so the measurement
    # would silently become "NIFTY never trades"; and brokerage is FLAT, so one
    # lot pays the largest fee share of any size — it is the conservative
    # choice as well as the realistic one for the account this targets.
    base = SnapbackConfig(sizing_mode="LOTS", lots=1)
    if args.set:
        from app.engines.snapback.config import validate
        over = dict(kv.split("=", 1) for kv in args.set)
        base = validate(over, base)
        print(f"OVERRIDES (this run is NOT the shipped defaults): "
              f"{', '.join(f'{k}={getattr(base, k)}' for k in over)}")
    books = {}
    if args.universe in ("both", "indices"):
        books["indices"] = load(INDICES)
    if args.universe in ("both", "stocks"):
        books["stocks"] = load(STOCKS)
    if args.universe == "fno":
        names = stored_universe()
        print(f"F&O universe from the store: {len(names)} instruments")
        books["fno"] = load(names)
    if args.universe in ("both", "all"):
        # The combined book is what an operator actually runs, and it is also
        # the only one with enough distinct entry days for the gate's clustered
        # statistics to say anything. Judging the two halves separately and
        # reporting the better one would be selection by another name.
        books["all"] = load(INDICES + STOCKS)
    for label, tapes in books.items():
        if not tapes:
            print(f"{label}: no tapes in the store — nothing to measure")
            continue
        span = sorted({wf.ist_day(float(t)) for b in tapes.values() for t in b.time})
        print(f"{label}: {len(tapes)} instruments, {len(span)} sessions "
              f"{span[0]}..{span[-1]}")
        if args.part in ("all", "vol"):
            part_vol(tapes, base, label)
        if args.part in ("all", "contract"):
            part_contract(tapes, base, label)
        if args.part in ("all", "gate"):
            out = pathlib.Path(args.out or
                               f"study/snapback_walkforward_{label}.json")
            part_gate(tapes, base, label, rounds=args.rounds, out=out,
                      record=args.record and label in ("all", "fno"),
                      measured_at=args.measured_at)


if __name__ == "__main__":
    main()
