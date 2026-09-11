"""Walk the intraday pack forward on real 5-minute data, and judge it.

Run:

    .venv/bin/python -m study.intraday_walkforward
    .venv/bin/python -m study.intraday_walkforward --slippage 0.25 --lens option

Reads the OHLCV store, so it needs no broker session and no network. What comes
out is three verdicts — one per strategy — each either a promotion or a list of
exactly what is missing.

The result to read is ``oos``: the concatenated out-of-sample books of every
fold. Per-fold numbers are published for auditing the selector, not as findings.
A per-fold best quoted as the headline is the overfitting the split exists to
prevent, wearing a rigorous hat.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from datetime import date
from typing import Sequence

from app.engines.intraday import IntradayConfig, STRATEGY_KEYS
from app.engines.intraday.backtest import CostModel
from app.engines.intraday.walkforward import GATE, run
from app.services.intraday_validation import StrategyValidation, record

OUT_DIR = os.path.dirname(__file__)

#: The universe the harness walks. Indices first: they carry the most history
#: and the tightest books, so a strategy that cannot work here will not work on
#: a single stock with a wider spread.
DEFAULT_SYMBOLS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "RELIANCE", "HDFCBANK",
                   "ICICIBANK", "INFY", "TCS", "SBIN", "AXISBANK"]

#: One knob per strategy, three values each. Deliberately SMALL.
#:
#: Every extra variant raises the bar the deflated Sharpe sets, so a wide grid
#: does not find an edge — it buys a worse one at a higher price. These are the
#: three thresholds most likely to matter and least likely to be right by luck.
GRIDS: dict[str, list[tuple[str, dict]]] = {
    "pivot_break": [
        ("body55", {"pb_min_body_pct": 55.0}),
        ("body65", {"pb_min_body_pct": 65.0}),
        ("body75", {"pb_min_body_pct": 75.0}),
    ],
    "ma_ribbon": [
        ("spread04", {"rb_min_spread_pct": 0.04}),
        ("spread08", {"rb_min_spread_pct": 0.08}),
        ("spread16", {"rb_min_spread_pct": 0.16}),
    ],
    "vwap_supertrend": [
        ("f120", {"vs_factor": 1.20}),
        ("f146", {"vs_factor": 1.46}),
        ("f200", {"vs_factor": 2.00}),
    ],
}


def load_tapes(symbols: Sequence[str], resolution: str = "5m") -> dict[str, list]:
    from app.services.ohlcv_store import get_candles
    out: dict[str, list] = {}
    for sym in symbols:
        try:
            rows = get_candles(sym, resolution, limit=200_000) or []
        except Exception as exc:                                   # noqa: BLE001
            print(f"  {sym}: unreadable ({exc})")
            continue
        if len(rows) < 500:
            print(f"  {sym}: only {len(rows)} bars — skipped")
            continue
        out[sym] = rows
    return out


def build_grid(strategy: str, base: IntradayConfig) -> list[tuple[str, IntradayConfig]]:
    grid = []
    for label, over in GRIDS[strategy]:
        grid.append((label, dataclasses.replace(base, **over).validate()))
    return grid


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    ap.add_argument("--resolution", default="5m")
    ap.add_argument("--is-bars", type=int, default=3000,
                    help="in-sample window, in bars (~40 sessions at 5m)")
    ap.add_argument("--oos-bars", type=int, default=1500,
                    help="out-of-sample window, in bars (~20 sessions at 5m)")
    ap.add_argument("--purge-bars", type=int, default=75,
                    help="gap between the windows — one session, so no trade "
                         "straddles the boundary")
    ap.add_argument("--slippage", type=float, default=0.05,
                    help="half-spread per leg, %% of price. The default is an "
                         "INDEX-FUTURE-like 0.05%%; pass 0.5 or more for what "
                         "an intraday option book actually pays")
    ap.add_argument("--capital", type=float, default=100_000.0)
    ap.add_argument("--json", default=os.path.join(OUT_DIR, "intraday_walkforward.json"))
    ap.add_argument("--record", action="store_true",
                    help="write the verdicts to the engine's validation record, "
                         "which is what actually gates unattended execution")
    args = ap.parse_args(argv)

    print(f"Loading {args.resolution} tapes…")
    tapes = load_tapes(args.symbols, args.resolution)
    if not tapes:
        print("No usable tapes in the OHLCV store. Nothing to walk forward.")
        return 2
    n = min(len(v) for v in tapes.values())
    print(f"  {len(tapes)} symbols, shortest tape {n} bars "
          f"(~{n // 75} sessions)\n")

    base = IntradayConfig(scan_indices=("NIFTY",), scan_stocks=(),
                          close_at_session_end=True).validate()
    costs = CostModel(slippage_pct=args.slippage)
    verdicts: dict[str, StrategyValidation] = {}
    report: dict = {"config": {"symbols": sorted(tapes), "bars": n,
                               "is_bars": args.is_bars, "oos_bars": args.oos_bars,
                               "purge_bars": args.purge_bars,
                               "slippage_pct": args.slippage,
                               "capital": args.capital},
                    "gate": GATE, "strategies": {}}

    for strategy in STRATEGY_KEYS:
        print(f"── {strategy} " + "─" * (60 - len(strategy)))
        try:
            res = run(tapes, strategy, build_grid(strategy, base),
                      is_bars=args.is_bars, oos_bars=args.oos_bars,
                      purge_bars=args.purge_bars, costs=costs,
                      capital=args.capital)
        except ValueError as exc:
            print(f"  cannot evaluate: {exc}\n")
            report["strategies"][strategy] = {"error": str(exc)}
            continue
        o = res.oos
        print(f"  folds {len(res.folds)} · trials {res.n_trials}")
        print(f"  OOS trades {o.trades} · net {o.net:,.0f} · gross {o.gross:,.0f} "
              f"· costs {o.costs:,.0f} ({o.cost_share_pct}% of gross)")
        print(f"  win rate {o.win_rate} · PF {o.profit_factor} · avg R {o.avg_r}")
        print(f"  Sharpe {o.sharpe} · maxDD {o.max_drawdown_pct}% "
              f"· DSR {res.dsr:.3f} · permutation p {res.permutation_p}")
        print(f"  VERDICT: {'PROMOTED' if res.verdict.promoted else 'NOT PROMOTED'}")
        for r in res.verdict.reasons:
            print(f"    - {r}")
        print()
        report["strategies"][strategy] = res.as_dict()
        verdicts[strategy] = StrategyValidation(
            strategy=strategy, promoted=res.verdict.promoted,
            measured_at=date.today().isoformat(), oos_trades=o.trades,
            oos_net=o.net, sharpe=o.sharpe, deflated_sharpe=res.dsr,
            permutation_p=res.permutation_p,
            max_drawdown_pct=o.max_drawdown_pct,
            checks=res.verdict.checks, reasons=res.verdict.reasons,
            slippage_pct=args.slippage, symbols=sorted(tapes))

    if args.record:
        # The verdicts are what gate unattended execution, so writing them is an
        # explicit act rather than a side effect of running a report.
        record(verdicts)
        print("Validation record updated — unattended execution now follows it.")

    with open(args.json, "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    print(f"Written: {args.json}")
    promoted = [k for k, v in report["strategies"].items()
                if isinstance(v, dict) and v.get("verdict", {}).get("promoted")]
    print(f"Promoted: {promoted or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
