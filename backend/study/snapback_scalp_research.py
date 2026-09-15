"""Audit whether stored quotes can support a causal intraday option backtest.

Run from backend:
    .venv/bin/python -m study.snapback_scalp_research --out /tmp/snapback-scalp.json

This is deliberately a data gate before a performance test. A sampled quote's
mid is an observation, not an option candle's high/low. No option candles are
manufactured and no P&L is emitted when execution coverage is missing. All DB
access is read-only. Chronological partitions are fixed before cost diagnostics.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
from datetime import datetime, time
import json
import math
from pathlib import Path
import sqlite3

import numpy as np

from app.engines.snapback import SnapbackConfig
from app.engines.snapback.intraday import make_plan
from app.engines.snapback.models import IST

ROOT = Path(__file__).resolve().parents[2]
OPEN_MINUTE, CLOSE_MINUTE = 555, 930


def _percentile(values: list[float], pct: float) -> float | None:
    return round(float(np.percentile(values, pct)), 4) if values else None


def _longest_run(bins: set[int]) -> int:
    longest = run = 0
    previous = -2
    for value in sorted(bins):
        run = run + 1 if value == previous + 1 else 1
        longest, previous = max(longest, run), value
    return longest


def audit(db_path: Path, *, timeframe: int = 5, max_age_seconds: float = 60,
          max_gap_seconds: float = 5) -> dict:
    """Count mutually exclusive row rejects and session execution coverage.

    Availability is received_at_ms, never exchange time backdated to an earlier
    bar. Repeated contract/exchange timestamps count only at their first valid
    receipt. The five-second cadence gate is a necessary screen for a small
    target, not evidence of continuous ticks, executable depth or fills.
    """
    cfg = replace(SnapbackConfig(), trading_mode="scalp", scalp_timeframe_minutes=timeframe,
                  sizing_mode="LOTS", lots=1, max_lots=1)
    rejects: Counter = Counter()
    sessions: dict[tuple[int, str], dict] = {}
    seen: set[tuple[int, int]] = set()
    underlyings: set[str] = set()
    contracts: set[int] = set()
    total = accepted = 0
    with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as con:
        resolution_counts = [dict(zip(("resolution", "rows", "symbols"), row)) for row in con.execute(
            "SELECT resolution, COUNT(*), COUNT(DISTINCT symbol) FROM ohlcv GROUP BY resolution")]
        cursor = con.execute(
            "SELECT instrument_token, underlying, tradingsymbol, option_type, lot_size, "
            "exchange_timestamp_ms, received_at_ms, bid, ask, spot, quote_quality "
            "FROM navigator_option_snapshots ORDER BY received_at_ms, id")
        for token, symbol, contract, option_type, lot_size, exchange_ms, received_ms, bid, ask, spot, quality in cursor:
            total += 1
            contracts.add(token)
            underlyings.add(symbol)
            if not exchange_ms or not received_ms:
                rejects["missing_timestamp"] += 1
                continue
            age = (received_ms - exchange_ms) / 1000
            if age < 0:
                rejects["exchange_timestamp_in_future"] += 1
                continue
            if age > max_age_seconds:
                rejects["stale_exchange_timestamp"] += 1
                continue
            if quality != "ok":
                rejects["quote_quality_not_ok"] += 1
                continue
            if (any(x is None or not math.isfinite(float(x)) for x in (bid, ask, spot))
                    or bid <= 0 or ask < bid or spot <= 0 or not lot_size or lot_size <= 0
                    or option_type not in ("CE", "PE")):
                rejects["invalid_quote_or_contract"] += 1
                continue
            received_dt = datetime.fromtimestamp(received_ms / 1000, IST)
            exchange_dt = datetime.fromtimestamp(exchange_ms / 1000, IST)
            minute = received_dt.hour * 60 + received_dt.minute
            if (not OPEN_MINUTE <= minute < CLOSE_MINUTE or received_dt.weekday() >= 5
                    or received_dt.date() != exchange_dt.date()):
                rejects["outside_regular_session"] += 1
                continue
            identity = (token, exchange_ms)
            if identity in seen:
                rejects["repeated_contract_exchange_timestamp"] += 1
                continue
            seen.add(identity)
            accepted += 1
            day = received_dt.date().isoformat()
            key = (token, day)
            if key not in sessions:
                sessions[key] = {
                    "day": day, "symbol": symbol, "contract": contract,
                    "lot_size": lot_size, "option_type": option_type,
                    "times": [], "spreads": [], "bins": set(),
                    "first_mid": (bid + ask) / 2,
                    "first_spread": ask - bid,
                }
            session = sessions[key]
            session["times"].append(received_ms / 1000)
            session["spreads"].append(ask - bid)
            session["bins"].add((minute - OPEN_MINUTE) // timeframe)

    days = sorted({s["day"] for s in sessions.values()})
    split = max(1, len(days) * 2 // 3) if days else 0
    training_days, held_out_days = days[:split], days[split:]
    expected_bins = (CLOSE_MINUTE - OPEN_MINUTE) // timeframe
    details = []
    for s in sessions.values():
        timestamps = s["times"]
        day = datetime.fromisoformat(s["day"]).date()
        opening = datetime.combine(day, time(9, 15), IST).timestamp()
        closing = datetime.combine(day, time(15, 30), IST).timestamp()
        gap = max(np.diff(timestamps), default=0.0)
        full_bins = len(s["bins"]) == expected_bins
        cadence = (full_bins and timestamps[0] - opening <= max_gap_seconds
                   and closing - timestamps[-1] <= max_gap_seconds
                   and gap <= max_gap_seconds)
        details.append({
            "day": s["day"], "symbol": s["symbol"], "contract": s["contract"],
            "option_type": s["option_type"], "lot_size": s["lot_size"],
            "split": "held_out" if s["day"] in held_out_days else "training",
            "observations": len(timestamps), "populated_buckets": len(s["bins"]),
            "longest_contiguous_buckets": _longest_run(s["bins"]),
            "first_observation_delay_seconds": round(timestamps[0] - opening, 3),
            "last_observation_before_close_seconds": round(closing - timestamps[-1], 3),
            "maximum_internal_gap_seconds": round(float(gap), 3),
            "full_session_buckets": full_bins, "passes_cadence_screen": bool(cadence),
            "median_spread_points": _percentile(s["spreads"], 50),
            "p95_spread_points": _percentile(s["spreads"], 95),
        })
    details.sort(key=lambda s: (s["day"], s["contract"]))

    # Cost sensitivity concerns ENTRY PLANS only, not hypothetical trades.
    # Each uses its first valid observed quote. Nothing is optimized or selected
    # using realized outcomes or later quotes. Estimated fixed/variable costs
    # are the configured assumptions; no claim is made they are exchange fees.
    stress = []
    for label, partition in (("training", training_days), ("held_out", held_out_days)):
        for multiplier in (1.0, 2.0, 3.0):
            plans = []
            for s in sessions.values():
                if s["day"] not in partition:
                    continue
                variable = max(cfg.scalp_round_trip_cost_points, s["first_spread"]) * multiplier
                stress_cfg = replace(cfg, scalp_round_trip_cost_points=variable)
                plans.append(make_plan(s["first_mid"], s["lot_size"], stress_cfg,
                                       spread_points=s["first_spread"]))
            accepted_plans = [p for p in plans if p["accepted"]]
            stress.append({
                "split": label, "variable_cost_multiplier": multiplier,
                "quotes_checked": len(plans), "accepted_entry_plans": len(accepted_plans),
                "rejected_entry_plans": len(plans) - len(accepted_plans),
                "median_required_target_points": _percentile(
                    [p["effective_target_points"] for p in accepted_plans], 50),
                "realized_pnl_inr": None,
            })
    return {
        "study": "snapback_scalp_quote_coverage_v1", "database": str(db_path.resolve()),
        "research_only": True, "promoted": False,
        "verdict": "insufficient_execution_data_for_profitability_validation",
        "source": "navigator_option_snapshots; real sampled bid/ask observations, not option OHLC",
        "timeframe_minutes": timeframe, "max_quote_age_seconds": max_age_seconds,
        "max_observation_gap_seconds": max_gap_seconds,
        "raw_rows": total, "accepted_unique_quotes": accepted,
        "row_rejections": dict(sorted(rejects.items())),
        "rows_reconciled": total == accepted + sum(rejects.values()),
        "raw_contracts": len(contracts), "raw_underlyings": sorted(underlyings),
        "underlying_ohlcv": resolution_counts,
        "training_dates": training_days, "held_out_dates": held_out_days,
        "contract_days": len(details), "expected_buckets_per_session": expected_bins,
        "full_session_bucket_contract_days": sum(s["full_session_buckets"] for s in details),
        "cadence_screen_contract_days": sum(s["passes_cadence_screen"] for s in details),
        "held_out_cadence_screen_contract_days": sum(
            s["passes_cadence_screen"] and s["split"] == "held_out" for s in details),
        "full_session_examples": [s for s in details if s["full_session_buckets"]],
        "session_coverage": details,
        "cost_plan_assumptions": {
            "capital_inr": cfg.capital_inr, "lots_cap": 1,
            "stop_risk_pct": cfg.scalp_risk_pct, "requested_target_points": cfg.scalp_target_points,
            "stop_points": cfg.scalp_stop_points, "minimum_net_reward_risk": cfg.scalp_min_net_rr,
            "base_variable_round_trip_points": cfg.scalp_round_trip_cost_points,
            "fixed_round_trip_inr": cfg.scalp_fixed_cost_inr,
            "spread_floor": "first valid observed ask minus bid, charged once per round trip",
            "meaning": "Sizing/target diagnostics at observed quotes; not fills, signals or performance",
        },
        "cost_stress": stress,
        "trade_count": None, "net_pnl_inr": None, "win_rate_pct": None,
        "limitations": [
            "Minute-scale snapshots can miss stop/target excursions between observations.",
            "Bucket coverage is not proof of actual option OHLC or a fill at a bucket boundary.",
            "The cadence screen is necessary, not sufficient; no strategy promotion is available from this study.",
            "Bid/ask depth and queue priority are absent, so large-quantity execution cannot be validated.",
            "Cost multipliers stress configured estimates only; a broker/exchange-specific fee model is still required.",
            "No option candles were synthesized and no Black-Scholes historical option prices were used.",
            "Chronological partitions are data-coverage partitions, not a completed out-of-sample performance test.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "backend/sterling_paper.db")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeframe", type=int, choices=(1, 3, 5), default=5)
    parser.add_argument("--max-quote-age-seconds", type=float, default=60)
    parser.add_argument("--max-observation-gap-seconds", type=float, default=5)
    args = parser.parse_args()
    if (not math.isfinite(args.max_quote_age_seconds) or args.max_quote_age_seconds <= 0
            or not math.isfinite(args.max_observation_gap_seconds) or args.max_observation_gap_seconds <= 0):
        parser.error("Timestamp tolerances must be positive finite seconds")
    report = audit(args.db, timeframe=args.timeframe,
                   max_age_seconds=args.max_quote_age_seconds,
                   max_gap_seconds=args.max_observation_gap_seconds)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({key: report[key] for key in (
        "verdict", "raw_rows", "accepted_unique_quotes", "row_rejections", "rows_reconciled",
        "training_dates", "held_out_dates", "contract_days", "full_session_bucket_contract_days",
        "cadence_screen_contract_days", "cost_stress", "net_pnl_inr")}, indent=2))


if __name__ == "__main__":
    main()
