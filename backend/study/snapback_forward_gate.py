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


def build_gate_inputs(
    *,
    records: Dict[str, List[Dict[str, Any]]],
    expected_build_sha: Optional[str] = None,
) -> Dict[str, Any]:
    """Shape observed warehouse records into authoritative gate inputs.

    Only rows that are flagged authoritative, carry an authoritative source and match
    the expected build are admitted. Catch-up replays and rows written by a different
    build are evidence about the pipeline, not about the strategy.
    """
    from app.services.snapback_authority import filter_authoritative

    outcomes = filter_authoritative(records.get("outcomes"), expected_build_sha=expected_build_sha)
    positions = records.get("paper_positions") or []
    quotes = filter_authoritative(records.get("option_quotes"), expected_build_sha=expected_build_sha)
    mtm_rows = filter_authoritative(records.get("daily_mtm"), expected_build_sha=expected_build_sha)

    # Strict admission: a row missing an economic field is evidence that collection
    # broke, not evidence of a zero-P&L trade. Malformed rows are excluded from the
    # sample and recorded as data-quality errors.
    trade_pnls: List[float] = []
    statutory_costs: List[float] = []
    entry_dates: List[str] = []
    data_quality_errors: List[str] = []

    REQUIRED_NUMERIC = (
        "actual_total_pnl",
        "actual_option_pnl",
        "actual_futures_pnl",
        "actual_costs",
    )

    for index, outcome in enumerate(outcomes):
        opp = str(outcome.get("opportunity_id") or f"row_{index}")
        problems: List[str] = []

        values: Dict[str, float] = {}
        for field in REQUIRED_NUMERIC:
            raw = outcome.get(field)
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                problems.append(f"{opp}: missing {field}")
                continue
            try:
                parsed = float(raw)
            except (TypeError, ValueError):
                problems.append(f"{opp}: non-numeric {field}={raw!r}")
                continue
            if not math.isfinite(parsed):
                problems.append(f"{opp}: non-finite {field}")
                continue
            values[field] = parsed

        session = _entry_session(outcome)
        if not session:
            problems.append(f"{opp}: missing entry date")

        if problems:
            data_quality_errors.extend(problems)
            continue

        trade_pnls.append(values["actual_total_pnl"])
        statutory_costs.append(values["actual_costs"])
        entry_dates.append(session)

    # Unresolved exposure: anything still carrying risk or not reconcilable.
    unresolved = sum(
        1
        for p in positions
        if str(p.get("status") or "").upper() in {"OPEN", "EXIT_PENDING", "UNKNOWN", ""}
    )

    # Quote coverage over REQUIRED attempts, including refusals. Falling back to
    # persisted quotes alone would divide good rows by good rows and always look
    # perfect, which is exactly how missing evidence hides.
    from app.services.snapback_quote_evidence import coverage_from_events

    events = filter_authoritative(
        records.get("quote_quality_events"), expected_build_sha=expected_build_sha
    )
    coverage = coverage_from_events(events)
    # No fallback to the legacy option_quotes table: reconstructing coverage from
    # stored rows divides good rows by good rows, so absent instrumentation would
    # read as measurable coverage. Absent attempts is UNKNOWN.
    quote_coverage_pct = coverage["coverage_pct"]

    # Real portfolio equity: realized P&L carries forward instead of vanishing when a
    # position closes.
    from app.services.snapback_portfolio import build_equity_curve, equity_series as _series

    curve = build_equity_curve(
        daily_mtm=mtm_rows,
        outcomes=outcomes,
        costs=filter_authoritative(records.get("costs"), expected_build_sha=expected_build_sha),
    )
    equity_series = _series(curve)

    return {
        "trade_pnls": trade_pnls,
        "statutory_costs": statutory_costs,
        "entry_dates": entry_dates,
        "daily_mtm_equity_series": equity_series,
        "unresolved_exposures_count": unresolved,
        "quote_coverage_pct": quote_coverage_pct,
        "quote_attempts": coverage,
        "data_quality_errors": data_quality_errors,
        "equity_curve": curve,
    }


def evaluation_capital() -> tuple:
    """Evaluation capital from the frozen config, never a convenient default.

    A drawdown measured against the wrong denominator is the difference between a 10%
    breach and a 1% blip, so an unreadable capital base is a data-quality failure.
    """
    errors: List[str] = []
    try:
        from app.services.snapback import get_config

        cfg = get_config("default")
        capital = float(getattr(cfg, "capital_inr", 0) or 0)
        if capital <= 0:
            errors.append("evaluation capital missing from the frozen config")
            return 0.0, errors
        return capital, errors
    except Exception as exc:
        errors.append(f"evaluation capital unavailable: {exc}")
        return 0.0, errors


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
    if trades and (quote_coverage_pct or 0.0) < MIN_QUOTE_COVERAGE_PCT:
        missing.append(
            f"quote coverage {quote_coverage_pct:.1f}% of {MIN_QUOTE_COVERAGE_PCT}% required"
        )
    if trades and not has_mtm:
        missing.append("daily MTM equity evidence absent")
    return missing


def evaluate_forward_gate(
    *,
    records: Dict[str, List[Dict[str, Any]]],
    allocation_capital_budget: Optional[float] = None,
    load_errors: Optional[List[str]] = None,
    expected_build_sha: Optional[str] = None,
    observed_sessions: Optional[int] = None,
) -> Dict[str, Any]:
    """Run the authoritative gate over prospective evidence and classify the verdict."""
    inputs = build_gate_inputs(records=records, expected_build_sha=expected_build_sha)

    data_quality_errors = list(load_errors or []) + list(inputs.get("data_quality_errors") or [])

    attempts = inputs.get("quote_attempts") or {}
    completed_trades = len(inputs["trade_pnls"])
    if completed_trades > 0 and int(attempts.get("required") or 0) == 0:
        data_quality_errors.append(
            "required quote-quality attempt evidence absent for completed trades"
        )
    coverage_pct = inputs.get("quote_coverage_pct")
    if completed_trades > 0 and coverage_pct is not None and coverage_pct < MIN_QUOTE_COVERAGE_PCT:
        data_quality_errors.append(
            f"quote coverage {coverage_pct:.1f}% below the required "
            f"{MIN_QUOTE_COVERAGE_PCT}%"
        )

    if allocation_capital_budget is None:
        allocation_capital_budget, capital_errors = evaluation_capital()
        data_quality_errors.extend(capital_errors)

    # The held-out session count comes from the session ledger, not from unique trade
    # entry dates: a fully observed session that produced no signal is still a
    # held-out session, and counting entry days silently shrinks the denominator.
    entry_sessions = len({d for d in inputs["entry_dates"] if not d.startswith("unknown_")})
    sessions = observed_sessions if observed_sessions is not None else entry_sessions
    trades = len(inputs["trade_pnls"])

    result = evaluate_authoritative_snapback_gate(
        trade_pnls=inputs["trade_pnls"],
        entry_dates=inputs["entry_dates"],
        statutory_costs=inputs["statutory_costs"],
        daily_mtm_equity_series=inputs["daily_mtm_equity_series"] or None,
        allocation_capital_budget=allocation_capital_budget,
        unresolved_exposures_count=inputs["unresolved_exposures_count"],
        quote_coverage_pct=(
            inputs["quote_coverage_pct"] if inputs["quote_coverage_pct"] is not None else 0.0
        ),
        entry_sessions_count=sessions,
    )

    payload = result.as_dict()
    payload["total_sessions"] = sessions
    payload["entry_date_count"] = entry_sessions
    sample_sufficient = (
        sessions >= MIN_SESSIONS
        and payload.get("completed_trades", trades) >= MIN_TRADES
    )

    if payload.get("promoted"):
        verdict = "PASSED"
    elif not sample_sufficient:
        verdict = "INCONCLUSIVE"
    else:
        verdict = "FAILED"

    if data_quality_errors:
        # Broken or missing evidence can never be a decision.
        verdict = "INCONCLUSIVE"

    payload["data_quality_ok"] = not data_quality_errors
    payload["data_quality_errors"] = data_quality_errors
    payload["allocation_capital_budget"] = allocation_capital_budget
    payload["verdict"] = verdict
    payload["missing_requirements"] = _missing_requirements(
        sessions=sessions,
        trades=payload.get("completed_trades", trades),
        unresolved=inputs["unresolved_exposures_count"],
        quote_coverage_pct=inputs["quote_coverage_pct"] or 0.0,
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

    from app.services.snapback_session_ledger import observed_session_count

    records, load_errors = load_forward_records(warehouse, strict=True)
    return evaluate_forward_gate(
        records=records,
        load_errors=load_errors,
        observed_sessions=observed_session_count(warehouse),
    )


if __name__ == "__main__":
    print(json.dumps(evaluate_forward_gate_from_warehouse(), indent=2))
