"""Adapter: frozen prospective evidence -> the existing authoritative economic gate.

This module only selects and shapes observed records. It has no authority to alter
any strategy parameter, and it does not implement a second gate: the verdict comes
from ``evaluate_authoritative_snapback_gate()``.

Verdict mapping:
    PASSED        every authoritative check passed
    INCONCLUSIVE  the sample is too small to decide (<60 sessions or <300 trades)
    FAILED        a sufficient sample failed an authoritative check
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from study.snapback_authoritative_gate import evaluate_authoritative_snapback_gate

MIN_SESSIONS = 60
MIN_TRADES = 300
MIN_QUOTE_COVERAGE_PCT = 95.0


def _f(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _entry_session(row: Dict[str, Any]) -> str:
    raw = str(row.get("entry_ts") or row.get("entry_timestamp") or "")
    return raw[:10] if raw else ""


def build_gate_inputs(*, records: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Shape observed warehouse records into authoritative gate inputs."""
    outcomes = records.get("outcomes") or []
    positions = records.get("paper_positions") or []
    quotes = records.get("option_quotes") or []
    mtm_rows = records.get("daily_mtm") or []

    trade_pnls = [_f(o.get("actual_total_pnl")) for o in outcomes]
    statutory_costs = [_f(o.get("actual_costs")) for o in outcomes]
    entry_dates = [_entry_session(o) or f"unknown_{i}" for i, o in enumerate(outcomes)]

    # Unresolved exposure: anything still carrying risk or not reconcilable.
    unresolved = sum(
        1
        for p in positions
        if str(p.get("status") or "").upper() in {"OPEN", "EXIT_PENDING", "UNKNOWN", ""}
    )

    # Quote coverage: executable observations over required observations.
    total_quotes = len(quotes)
    usable = 0
    for q in quotes:
        if int(q.get("is_stale") or 0) == 1:
            continue
        bid = _f(q.get("bid"))
        ask = _f(q.get("ask"))
        if bid > 0 and ask > 0 and ask >= bid:
            usable += 1
    quote_coverage_pct = (usable / total_quotes * 100.0) if total_quotes else 0.0

    # Observed liquidation-equity path, aggregated per session in date order.
    per_session: Dict[str, float] = {}
    for row in mtm_rows:
        session = str(row.get("session_date") or "")[:10]
        if not session:
            continue
        per_session[session] = per_session.get(session, 0.0) + _f(row.get("total_mtm"))
    equity_series = [per_session[k] for k in sorted(per_session)]

    return {
        "trade_pnls": trade_pnls,
        "statutory_costs": statutory_costs,
        "entry_dates": entry_dates,
        "daily_mtm_equity_series": equity_series,
        "unresolved_exposures_count": unresolved,
        "quote_coverage_pct": quote_coverage_pct,
    }


def _missing_requirements(
    *, sessions: int, trades: int, unresolved: int, quote_coverage_pct: float, has_mtm: bool
) -> List[str]:
    missing: List[str] = []
    if sessions < MIN_SESSIONS:
        missing.append(
            f"independent sessions {sessions} of {MIN_SESSIONS} required"
        )
    if trades < MIN_TRADES:
        missing.append(f"completed trades {trades} of {MIN_TRADES} required")
    if unresolved:
        missing.append(f"unresolved exposures {unresolved} (must be 0)")
    if trades and quote_coverage_pct < MIN_QUOTE_COVERAGE_PCT:
        missing.append(
            f"quote coverage {quote_coverage_pct:.1f}% of {MIN_QUOTE_COVERAGE_PCT}% required"
        )
    if trades and not has_mtm:
        missing.append("daily MTM equity evidence absent")
    return missing


def evaluate_forward_gate(
    *,
    records: Dict[str, List[Dict[str, Any]]],
    allocation_capital_budget: float = 1_000_000.0,
) -> Dict[str, Any]:
    """Run the authoritative gate over prospective evidence and classify the verdict."""
    inputs = build_gate_inputs(records=records)

    sessions = len({d for d in inputs["entry_dates"] if not d.startswith("unknown_")})
    trades = len(inputs["trade_pnls"])

    result = evaluate_authoritative_snapback_gate(
        trade_pnls=inputs["trade_pnls"],
        entry_dates=inputs["entry_dates"],
        statutory_costs=inputs["statutory_costs"],
        daily_mtm_equity_series=inputs["daily_mtm_equity_series"] or None,
        allocation_capital_budget=allocation_capital_budget,
        unresolved_exposures_count=inputs["unresolved_exposures_count"],
        quote_coverage_pct=inputs["quote_coverage_pct"],
    )

    payload = result.as_dict()
    sample_sufficient = (
        payload.get("total_sessions", sessions) >= MIN_SESSIONS
        and payload.get("completed_trades", trades) >= MIN_TRADES
    )

    if payload.get("promoted"):
        verdict = "PASSED"
    elif not sample_sufficient:
        verdict = "INCONCLUSIVE"
    else:
        verdict = "FAILED"

    payload["verdict"] = verdict
    payload["missing_requirements"] = _missing_requirements(
        sessions=payload.get("total_sessions", sessions),
        trades=payload.get("completed_trades", trades),
        unresolved=inputs["unresolved_exposures_count"],
        quote_coverage_pct=inputs["quote_coverage_pct"],
        has_mtm=bool(inputs["daily_mtm_equity_series"]),
    )
    payload["evaluated_at"] = datetime.now(timezone.utc).isoformat()
    payload["authority"] = "study/snapback_authoritative_gate.evaluate_authoritative_snapback_gate"
    return payload


def evaluate_forward_gate_from_warehouse(warehouse=None) -> Dict[str, Any]:
    """Read the clean frozen prospective database and evaluate the gate."""
    from study.snapback_forward_report import load_forward_records

    if warehouse is None:
        from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

        warehouse = SnapbackObservationWarehouse()

    return evaluate_forward_gate(records=load_forward_records(warehouse))


if __name__ == "__main__":
    print(json.dumps(evaluate_forward_gate_from_warehouse(), indent=2))
