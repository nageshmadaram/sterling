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
from app.engines.intraday.contracts import SPECS
from app.engines.intraday.walkforward import GATE, run
from app.services.intraday_validation import StrategyValidation, record

OUT_DIR = os.path.dirname(__file__)

#: The universe the harness walks. Indices first: they carry the most history
#: and the tightest books, so a strategy that cannot work here will not work on
#: a single stock with a wider spread.
DEFAULT_SYMBOLS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "RELIANCE", "HDFCBANK",
                   "ICICIBANK", "INFY", "TCS", "SBIN", "AXISBANK"]

#: Units per lot, read from the engine's own contract registry rather than
#: repeated here.
#:
#: It WAS repeated here, which is the recurring bug class in this codebase: one
#: fact stored twice, honoured in one place. A lot size that disagreed between
#: the harness and the live engine would make the backtest price a trade the
#: engine would never place, and nothing would have said so.
#:
#: Why it matters at all: a flat per-order brokerage against ONE unit of an
#: index makes the brokerage the entire result. The first run of this harness
#: did that and reported an average of -14.5R per trade, arithmetically
#: impossible for a book whose stop is 1R — the number was the cost model.
LOT_SIZES = {name: spec.lot_size for name, spec in SPECS.items()}
DEFAULT_LOT = 100

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
    ap.add_argument("--resolution", default="5m",
                    help="the stored series to read")
    ap.add_argument("--sweep-timeframes", default=None,
                    help="comma-separated timeframes to search, e.g. "
                         "'5m,15m,30m'. Every configuration tried at EVERY "
                         "timeframe counts toward the deflation, because "
                         "searching three timeframes for one that looks good "
                         "is a search — running them as separate invocations "
                         "and quoting the best is how a result gets a deflated "
                         "Sharpe it has not earned")
    ap.add_argument("--timeframe", default=None,
                    help="evaluate on THIS timeframe, resampling the stored "
                         "series up to it. The whole pack is specified on 5m, "
                         "but 92%% of one strategy's gross edge went to costs "
                         "there — fewer, larger trades is the one change the "
                         "measurement actually points at")
    ap.add_argument("--is-bars", type=int, default=3000,
                    help="in-sample window, in bars (~40 sessions at 5m)")
    ap.add_argument("--oos-bars", type=int, default=1500,
                    help="out-of-sample window, in bars (~20 sessions at 5m)")
    ap.add_argument("--purge-bars", type=int, default=75,
                    help="gap between the windows — one session, so no trade "
                         "straddles the boundary")
    ap.add_argument("--lens", choices=("underlying", "option"), default="underlying",
                    help="what is traded. 'underlying' measures the SIGNAL on a "
                         "delta-1 proxy at futures costs; 'option' applies the "
                         "options schedule to a premium")
    ap.add_argument("--slippage", type=float, default=None,
                    help="half-spread per leg, %% of price. Defaults to the "
                         "lens's own: 0.01%% delta-1, 0.5%% options")
    ap.add_argument("--capital", type=float, default=100_000.0)
    ap.add_argument("--json", default=os.path.join(OUT_DIR, "intraday_walkforward.json"))
    ap.add_argument("--record", action="store_true",
                    help="write the verdicts to the engine's validation record, "
                         "which is what actually gates unattended execution")
    args = ap.parse_args(argv)

    print(f"Loading {args.resolution} tapes…")
    raw_tapes = load_tapes(args.symbols, args.resolution)
    if not raw_tapes:
        print("No usable tapes in the OHLCV store. Nothing to walk forward.")
        return 2
    timeframes = ([t.strip() for t in args.sweep_timeframes.split(",") if t.strip()]
                  if args.sweep_timeframes else [args.timeframe or args.resolution])
    if len(timeframes) > 1:
        print(f"  sweeping {len(timeframes)} timeframes — every configuration "
              "tried at every one of them counts toward the deflation")
    print(f"  {len(raw_tapes)} symbols\n")

    costs = CostModel.for_lens(args.lens, slippage_pct=args.slippage)
    verdicts: dict[str, StrategyValidation] = {}
    report: dict = {"config": {"symbols": sorted(raw_tapes),
                               "timeframes": timeframes,
                               "lens": args.lens,
                               "slippage_pct": costs.slippage_pct,
                               "capital": args.capital},
                    "gate": GATE, "strategies": {}, "by_timeframe": {}}

    # The Sharpe of every configuration tried so far, PER STRATEGY, carried
    # across timeframes. Searching three timeframes for one that looks good is
    # a search, and deflating only against the last leg of it reports a number
    # the result has not earned.
    seen_sharpes: dict[str, list[float]] = {k: [] for k in STRATEGY_KEYS}
    best: dict[str, tuple[str, object]] = {}

    for timeframe in timeframes:
        tapes = raw_tapes
        if timeframe != args.resolution:
            from app.engines.intraday import resample
            mins = {"1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15,
                    "30m": 30}[timeframe]
            tapes = {k: resample(v, mins) for k, v in raw_tapes.items()}
        # Windows are in BARS, so a slower timeframe needs proportionally fewer
        # of them to cover the same calendar span. Scaling keeps folds
        # comparable instead of silently shortening the history.
        scale = 5 / {"1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15,
                     "30m": 30}[timeframe]
        is_bars = max(400, int(args.is_bars * scale))
        oos_bars = max(200, int(args.oos_bars * scale))
        purge_bars = max(10, int(args.purge_bars * scale))
        n = min(len(v) for v in tapes.values())
        base = IntradayConfig(scan_indices=("NIFTY",), scan_stocks=(),
                              timeframe=timeframe,
                              close_at_session_end=True).validate()
        print(f"══ {timeframe} · {n} bars · IS {is_bars} / OOS {oos_bars} "
              f"/ purge {purge_bars}")
        report["by_timeframe"][timeframe] = {}

        for strategy in STRATEGY_KEYS:
            print(f"── {strategy} " + "─" * (56 - len(strategy)))
            try:
                res = run(tapes, strategy, build_grid(strategy, base),
                          is_bars=is_bars, oos_bars=oos_bars,
                          purge_bars=purge_bars, costs=costs,
                          capital=args.capital,
                          prior_sharpes=seen_sharpes[strategy],
                          lot_sizes={s: LOT_SIZES.get(s, DEFAULT_LOT) for s in tapes})
            except ValueError as exc:
                print(f"  cannot evaluate: {exc}\n")
                report["by_timeframe"][timeframe][strategy] = {"error": str(exc)}
                continue
            o = res.oos
            # Carry this leg's own trials into the next timeframe's deflation.
            # `res.n_trials` already counts the ones carried IN, so only this
            # leg's grid scores are appended — adding them twice would deflate
            # by a search larger than the one actually run.
            seen_sharpes[strategy] = list(seen_sharpes[strategy]) + [
                v for fold in res.folds for v in fold.in_sample.values()]
            print(f"  folds {len(res.folds)} · trials {res.n_trials} "
                  f"(whole search so far)")
            cost_note = (f"{o.cost_share_pct}% of gross" if o.gross_positive
                         else "gross was negative BEFORE costs")
            print(f"  OOS trades {o.trades} · net {o.net:,.0f} "
                  f"· gross {o.gross:,.0f} · costs {o.costs:,.0f} ({cost_note})")
            print(f"  win rate {o.win_rate} · PF {o.profit_factor} "
                  f"· avg R {o.avg_r}")
            print(f"  Sharpe {o.sharpe} · maxDD {o.max_drawdown_r}R "
                  f"· DSR {res.dsr:.3f} · permutation p {res.permutation_p}")
            print(f"  VERDICT: "
                  f"{'PROMOTED' if res.verdict.promoted else 'NOT PROMOTED'}")
            for r in res.verdict.reasons:
                print(f"    - {r}")
            print()
            report["by_timeframe"][timeframe][strategy] = res.as_dict()
            prev = best.get(strategy)
            if prev is None or res.dsr > prev[1].dsr:
                best[strategy] = (timeframe, res)

    for strategy, (timeframe, res) in best.items():
        o = res.oos
        report["strategies"][strategy] = {**res.as_dict(), "timeframe": timeframe}
        verdicts[strategy] = StrategyValidation(
            strategy=strategy, promoted=res.verdict.promoted,
            measured_at=date.today().isoformat(), oos_trades=o.trades,
            oos_net=o.net, sharpe=o.sharpe, deflated_sharpe=res.dsr,
            permutation_p=res.permutation_p,
            max_drawdown_pct=o.max_drawdown_pct,
            checks=res.verdict.checks, reasons=res.verdict.reasons,
            slippage_pct=costs.slippage_pct, symbols=sorted(raw_tapes))

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
