"""The one place evidence becomes gate input.

Every other path that assembles "promotable" numbers is a second opinion nobody asked
for, and two opinions eventually disagree about whether a family may trade real money.
This builder proves the evidence hangs together BEFORE the gate sees it: identities
match, every completed trade's cost ledger reconciles with its outcome, coverage comes
from recorded attempts, and nothing is left unresolved.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger(__name__)

ABS_TOLERANCE = 0.01


class PromotionInputError(Exception):
    """The evidence does not hang together; no gate input can be built from it."""

    def __init__(self, errors: Iterable[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors[:8]))


@dataclass(frozen=True)
class PromotionInput:
    experiment_id: str
    runtime_build_sha: str
    strategy_config_hash: str
    strategy_rule_hash: str
    execution_policy_hash: str
    execution_cost_schedule_hash: str
    source_snapshot_sha256: str

    observed_sessions: int
    completed_trades: int

    trade_pnls: Tuple[float, ...]
    trade_costs: Tuple[float, ...]
    entry_sessions: Tuple[str, ...]
    portfolio_equity: Tuple[float, ...]

    required_quote_events: int
    accepted_quote_events: int

    unresolved_exposures: int
    evidence_gap_sessions: int

    allocation_capital: float
    gate_input_hash: str


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _hash(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _num(row: Dict[str, Any], field: str, errors: List[str], who: str) -> Optional[float]:
    raw = row.get(field)
    if raw is None:
        errors.append(f"{who}: missing {field}")
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        errors.append(f"{who}: non-numeric {field}")
        return None
    if not math.isfinite(value):
        errors.append(f"{who}: non-finite {field}")
        return None
    return value


def build_promotion_input(
    *,
    records: Dict[str, List[Dict[str, Any]]],
    expected_identity: Dict[str, str],
    source_snapshot_sha256: str,
    observed_sessions: int,
    allocation_capital: Optional[float] = None,
) -> PromotionInput:
    """Assemble gate input, refusing anything that cannot be reconciled."""
    from app.services.snapback_authority import filter_authoritative
    from app.services.snapback_portfolio import build_equity_curve, equity_series
    from app.services.snapback_quote_evidence import coverage_from_events

    errors: List[str] = []
    expected_build = expected_identity.get("runtime_build_sha") or ""

    outcomes = filter_authoritative(records.get("outcomes"))
    cost_rows = [dict(r) for r in filter_authoritative(records.get("costs"))]
    positions = [dict(r) for r in (records.get("paper_positions") or [])]

    # Costs by trade come from the LEDGER. A bug in outcome writing must not be able
    # to make costs disappear from the 2x/3x stress.
    ledger: Dict[str, float] = {}
    for row in cost_rows:
        opp = str(row.get("opportunity_id") or "")
        try:
            ledger[opp] = ledger.get(opp, 0.0) + float(row.get("total_cost") or 0.0)
        except (TypeError, ValueError):
            errors.append(f"{opp}: non-numeric cost event")

    trade_pnls: List[float] = []
    trade_costs: List[float] = []
    entry_sessions: List[str] = []
    seen_opportunities = set()

    for index, raw in enumerate(outcomes):
        outcome = dict(raw)
        opp = str(outcome.get("opportunity_id") or f"row_{index}")
        seen_opportunities.add(opp)

        build = str(outcome.get("runtime_build_sha") or "")
        if expected_build and build and build != expected_build:
            errors.append(f"{opp}: runtime build {build} does not match {expected_build}")

        option_pnl = _num(outcome, "actual_option_pnl", errors, opp)
        futures_pnl = _num(outcome, "actual_futures_pnl", errors, opp)
        outcome_costs = _num(outcome, "actual_costs", errors, opp)
        total = _num(outcome, "actual_total_pnl", errors, opp)

        session = str(outcome.get("entry_ts") or "")[:10]
        if not session:
            errors.append(f"{opp}: missing entry date")

        if opp not in ledger:
            errors.append(f"{opp}: no cost events for a completed trade")
            continue

        ledger_costs = ledger[opp]
        if outcome_costs is not None and abs(outcome_costs - ledger_costs) > ABS_TOLERANCE:
            errors.append(
                f"{opp}: outcome costs {outcome_costs} disagree with cost ledger {ledger_costs}"
            )

        if None not in (option_pnl, futures_pnl, total):
            expected_total = option_pnl + futures_pnl - ledger_costs
            if abs(total - expected_total) > ABS_TOLERANCE:
                errors.append(
                    f"{opp}: total {total} does not reconcile with "
                    f"option+futures-ledger {expected_total}"
                )

        if None in (option_pnl, futures_pnl, total) or not session:
            continue

        trade_pnls.append(total)
        trade_costs.append(ledger_costs)
        entry_sessions.append(session)

    orphans = sorted(set(ledger) - seen_opportunities)
    if orphans:
        errors.append(f"orphan cost events without an outcome: {orphans[:5]}")

    unresolved = sum(
        1 for p in positions
        if str(p.get("status") or "").upper() in {"OPEN", "EXIT_PENDING", "UNKNOWN", ""}
    )
    if unresolved:
        errors.append(f"unresolved exposures: {unresolved}")

    coverage = coverage_from_events(filter_authoritative(records.get("quote_quality_events")))
    if trade_pnls and int(coverage.get("required") or 0) == 0:
        errors.append("no required quote attempts recorded for completed trades")

    curve = build_equity_curve(
        daily_mtm=filter_authoritative(records.get("daily_mtm")),
        outcomes=outcomes,
        costs=cost_rows,
    )

    if allocation_capital is None:
        from study.snapback_forward_gate import evaluation_capital

        allocation_capital, capital_errors = evaluation_capital()
        errors.extend(capital_errors)

    gap_sessions = 0
    for row in (records.get("prospective_sessions") or []):
        from app.services.snapback_session_ledger import session_market_evidence_complete

        if not session_market_evidence_complete(dict(row)):
            gap_sessions += 1

    if errors:
        raise PromotionInputError(errors)

    identity_payload = {
        "experiment_id": expected_identity.get("experiment_id", ""),
        "runtime_build_sha": expected_build,
        "strategy_config_hash": expected_identity.get("strategy_config_hash", ""),
        "strategy_rule_hash": expected_identity.get("strategy_rule_hash", ""),
        "execution_policy_hash": expected_identity.get("execution_policy_hash", ""),
        "execution_cost_schedule_hash": expected_identity.get("execution_cost_schedule_hash", ""),
        "source_snapshot_sha256": source_snapshot_sha256,
        "observed_sessions": int(observed_sessions),
        "trade_pnls": trade_pnls,
        "trade_costs": trade_costs,
        "entry_sessions": entry_sessions,
        "portfolio_equity": equity_series(curve),
        "required_quote_events": int(coverage.get("required") or 0),
        "accepted_quote_events": int(coverage.get("accepted") or 0),
        "unresolved_exposures": unresolved,
        "allocation_capital": float(allocation_capital or 0.0),
    }

    return PromotionInput(
        experiment_id=identity_payload["experiment_id"],
        runtime_build_sha=expected_build,
        strategy_config_hash=identity_payload["strategy_config_hash"],
        strategy_rule_hash=identity_payload["strategy_rule_hash"],
        execution_policy_hash=identity_payload["execution_policy_hash"],
        execution_cost_schedule_hash=identity_payload["execution_cost_schedule_hash"],
        source_snapshot_sha256=source_snapshot_sha256,
        observed_sessions=int(observed_sessions),
        completed_trades=len(trade_pnls),
        trade_pnls=tuple(trade_pnls),
        trade_costs=tuple(trade_costs),
        entry_sessions=tuple(entry_sessions),
        portfolio_equity=tuple(equity_series(curve)),
        required_quote_events=int(coverage.get("required") or 0),
        accepted_quote_events=int(coverage.get("accepted") or 0),
        unresolved_exposures=unresolved,
        evidence_gap_sessions=gap_sessions,
        allocation_capital=float(allocation_capital or 0.0),
        # Deterministic over gate-relevant values only: no timestamps, no paths.
        gate_input_hash=_hash(identity_payload),
    )
