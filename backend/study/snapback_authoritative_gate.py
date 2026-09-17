"""Single Authoritative Economic Promotion Gate for Snapback.

Implements the single authoritative promotion decision framework matching
docs/strategy/snapback/specs/04_VALIDATION_AND_DELIVERY.md.

Replaces all secondary/legacy promotion reports.

Runtime 1.6 post-merge hardening predeclares two additional robustness checks
before any authoritative prospective sample is accepted:

* expectancy remains positive under 3x observed execution/statutory costs; and
* expectancy remains positive after removing the most profitable 1% of trades
  (minimum three trades once the 300-trade sample exists).

These are evidence/promotion rules, not strategy rules. They do not change which
signals fire or which contracts are selected.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np


PASSED = "PASSED"
FAILED = "FAILED"
INCONCLUSIVE = "INCONCLUSIVE"


def verdict_for(
    *, promoted: bool, sample_sufficient: bool, data_quality_ok: bool = True,
) -> str:
    """Map a gate result onto the operator-facing verdict.

    Broken or missing evidence outranks everything: it can never be a decision,
    in either direction.
    """
    if not data_quality_ok:
        return INCONCLUSIVE
    if promoted:
        return PASSED
    if not sample_sufficient:
        return INCONCLUSIVE
    return FAILED


@dataclass
class AuthoritativeGateVerdict:
    promoted: bool
    total_sessions: int
    completed_trades: int
    net_expectancy: float
    lower_95_ci: float
    expectancy_2x_cost: float
    expectancy_3x_cost: float
    expectancy_without_top_1pct_profits: float
    max_mtm_drawdown_pct: float
    top_1pct_pnl_share: float
    unresolved_exposures_count: int
    quote_coverage_pct: float = 100.0
    checks: Dict[str, bool] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "promoted": self.promoted,
            "total_sessions": self.total_sessions,
            "completed_trades": self.completed_trades,
            "net_expectancy": round(self.net_expectancy, 2),
            "lower_95_ci": round(self.lower_95_ci, 2),
            "expectancy_2x_cost": round(self.expectancy_2x_cost, 2),
            "expectancy_3x_cost": round(self.expectancy_3x_cost, 2),
            "expectancy_without_top_1pct_profits": round(
                self.expectancy_without_top_1pct_profits, 2
            ),
            "max_mtm_drawdown_pct": round(self.max_mtm_drawdown_pct, 2),
            "top_1pct_pnl_share": round(self.top_1pct_pnl_share, 4),
            "unresolved_exposures_count": self.unresolved_exposures_count,
            "quote_coverage_pct": round(self.quote_coverage_pct, 2),
            "checks": dict(self.checks),
            "reasons": list(self.reasons),
        }


def _invalid_verdict(
    *,
    n_trades: int,
    total_sessions: int,
    unresolved_exposures_count: int,
    quote_coverage_pct: float,
    checks: Dict[str, bool],
    reasons: List[str],
) -> AuthoritativeGateVerdict:
    """Fail closed when the numeric evidence itself is unusable."""
    checks.setdefault("independent_sessions_ge_60", total_sessions >= 60)
    checks.setdefault("completed_trades_ge_300", n_trades >= 300)
    for name in (
        "positive_lower_95_ci",
        "positive_baseline_expectancy",
        "positive_under_2x_costs",
        "positive_under_3x_costs",
        "positive_after_top_1pct_profit_removal",
        "drawdown_within_budget",
    ):
        checks.setdefault(name, False)
    checks.setdefault("zero_unresolved_exposures", unresolved_exposures_count == 0)
    checks.setdefault("quote_coverage_ge_95", False)
    return AuthoritativeGateVerdict(
        promoted=False,
        total_sessions=total_sessions,
        completed_trades=n_trades,
        net_expectancy=0.0,
        lower_95_ci=0.0,
        expectancy_2x_cost=0.0,
        expectancy_3x_cost=0.0,
        expectancy_without_top_1pct_profits=0.0,
        max_mtm_drawdown_pct=999.0,
        top_1pct_pnl_share=0.0,
        unresolved_exposures_count=unresolved_exposures_count,
        quote_coverage_pct=quote_coverage_pct,
        checks=checks,
        reasons=reasons,
    )


def evaluate_authoritative_snapback_gate(
    trade_pnls: List[float],
    entry_dates: Optional[List[str]] = None,
    statutory_costs: Optional[List[float]] = None,
    daily_mtm_equity_series: Optional[List[float]] = None,
    allocation_capital_budget: float = 1000000.0,
    unresolved_exposures_count: int = 0,
    max_allowed_drawdown_pct: float = 10.0,
    quote_coverage_pct: float = 100.0,
    seed: int = 42,
    entry_sessions_count: Optional[int] = None,
    require_mtm_evidence: bool = True,
) -> AuthoritativeGateVerdict:
    """Evaluate authoritative prospective results against the promotion contract."""
    n_trades = len(trade_pnls)
    reasons: List[str] = []
    checks: Dict[str, bool] = {}

    entry_dates_valid = entry_dates is not None and len(entry_dates) == n_trades
    checks["entry_dates_provided"] = entry_dates_valid
    if not entry_dates_valid:
        reasons.append(
            "Missing or mismatched actual entry dates evidence: "
            f"entry_dates={len(entry_dates) if entry_dates else 0} vs trades={n_trades}"
        )

    dates = list(entry_dates) if entry_dates_valid else [f"day_{i}" for i in range(n_trades)]
    entry_day_count = len(set(dates)) if n_trades > 0 else 0
    total_sessions = (
        int(entry_sessions_count)
        if entry_sessions_count is not None
        else entry_day_count
    )

    cost_evidence_valid = statutory_costs is not None and len(statutory_costs) == n_trades
    if cost_evidence_valid:
        try:
            cost_evidence_valid = all(
                math.isfinite(float(v)) and float(v) >= 0.0
                for v in statutory_costs or []
            )
        except (TypeError, ValueError):
            cost_evidence_valid = False
    checks["cost_evidence_provided"] = cost_evidence_valid
    if not cost_evidence_valid:
        reasons.append(
            "Missing, mismatched, negative or non-finite statutory cost evidence: "
            f"costs={len(statutory_costs) if statutory_costs else 0} vs trades={n_trades}"
        )

    try:
        pnls_finite = all(math.isfinite(float(v)) for v in trade_pnls)
    except (TypeError, ValueError):
        pnls_finite = False
    checks["trade_pnls_finite"] = pnls_finite
    if not pnls_finite:
        reasons.append("Trade P&L evidence contains a non-numeric or non-finite value")

    dates_nonempty = entry_dates_valid and all(str(v).strip() for v in (entry_dates or []))
    checks["entry_dates_nonempty"] = dates_nonempty
    if entry_dates_valid and not dates_nonempty:
        reasons.append("Entry-date evidence contains a blank value")

    try:
        capital_valid = (
            math.isfinite(float(allocation_capital_budget))
            and float(allocation_capital_budget) > 0.0
        )
    except (TypeError, ValueError):
        capital_valid = False
    checks["allocation_capital_valid"] = capital_valid
    if not capital_valid:
        reasons.append("Allocation capital budget must be finite and strictly positive")

    try:
        coverage_valid = (
            math.isfinite(float(quote_coverage_pct))
            and 0.0 <= float(quote_coverage_pct) <= 100.0
        )
    except (TypeError, ValueError):
        coverage_valid = False
    checks["quote_coverage_valid"] = coverage_valid
    if not coverage_valid:
        reasons.append("Quote coverage must be a finite percentage in [0, 100]")

    unresolved_valid = isinstance(unresolved_exposures_count, (int, np.integer)) and int(
        unresolved_exposures_count
    ) >= 0
    checks["unresolved_exposure_count_valid"] = unresolved_valid
    if not unresolved_valid:
        reasons.append("Unresolved exposure count must be a non-negative integer")

    has_mtm_evidence = daily_mtm_equity_series is not None and len(daily_mtm_equity_series) > 0
    mtm_valid = True
    if has_mtm_evidence:
        try:
            mtm_valid = all(math.isfinite(float(v)) for v in daily_mtm_equity_series or [])
        except (TypeError, ValueError):
            mtm_valid = False
    checks["daily_mtm_evidence_provided"] = (
        has_mtm_evidence and mtm_valid
    ) or not require_mtm_evidence
    if has_mtm_evidence and not mtm_valid:
        reasons.append("Daily MTM evidence contains a non-numeric or non-finite value")

    base_evidence_valid = all((
        cost_evidence_valid,
        entry_dates_valid,
        dates_nonempty if n_trades else True,
        pnls_finite,
        capital_valid,
        coverage_valid,
        unresolved_valid,
        mtm_valid,
    ))
    if not base_evidence_valid:
        return _invalid_verdict(
            n_trades=n_trades,
            total_sessions=total_sessions,
            unresolved_exposures_count=max(0, int(unresolved_exposures_count))
            if isinstance(unresolved_exposures_count, (int, np.integer)) else 0,
            quote_coverage_pct=float(quote_coverage_pct) if coverage_valid else 0.0,
            checks=checks,
            reasons=reasons,
        )

    if n_trades == 0:
        checks["completed_trades_ge_300"] = False
        reasons.append("No completed trades in evaluation run")
        return AuthoritativeGateVerdict(
            promoted=False,
            total_sessions=total_sessions,
            completed_trades=0,
            net_expectancy=0.0,
            lower_95_ci=0.0,
            expectancy_2x_cost=0.0,
            expectancy_3x_cost=0.0,
            expectancy_without_top_1pct_profits=0.0,
            max_mtm_drawdown_pct=0.0,
            top_1pct_pnl_share=0.0,
            unresolved_exposures_count=int(unresolved_exposures_count),
            quote_coverage_pct=float(quote_coverage_pct),
            checks=checks,
            reasons=reasons,
        )

    pnls = np.array(trade_pnls, dtype=float)
    costs = np.array(statutory_costs, dtype=float)

    net_expectancy = float(np.mean(pnls))
    pnls_2x = pnls - costs
    pnls_3x = pnls - (2.0 * costs)
    expectancy_2x = float(np.mean(pnls_2x))
    expectancy_3x = float(np.mean(pnls_3x))

    # Predeclared tail-concentration stress. Remove the best 1% of all trades,
    # with a minimum of three once the promotion-sized sample exists. Removing
    # only winners is intentional: the question is whether the apparent edge
    # survives without its most favourable outcomes.
    remove_n = max(3, int(math.ceil(0.01 * n_trades))) if n_trades >= 300 else max(
        1, int(math.ceil(0.01 * n_trades))
    )
    positive_indices = [i for i in np.argsort(pnls)[::-1] if pnls[i] > 0]
    remove_indices = set(int(i) for i in positive_indices[:remove_n])
    remaining = np.array(
        [p for i, p in enumerate(pnls) if i not in remove_indices], dtype=float
    )
    expectancy_without_top = float(np.mean(remaining)) if len(remaining) else float("-inf")

    # ENTRY-DAY BLOCK BOOTSTRAP.
    rng = np.random.default_rng(seed)
    day_to_pnls: Dict[str, List[float]] = {}
    for d, p in zip(dates, pnls):
        day_to_pnls.setdefault(d, []).append(float(p))

    unique_days_list = list(day_to_pnls.keys())
    if len(unique_days_list) >= 5:
        boot_means = []
        for _ in range(1000):
            resampled_days = rng.choice(unique_days_list, size=len(unique_days_list), replace=True)
            resampled_pnls = [p for d in resampled_days for p in day_to_pnls[d]]
            boot_means.append(np.mean(resampled_pnls))
        lower_95_ci = float(np.percentile(boot_means, 2.5))
    else:
        lower_95_ci = net_expectancy - 1.96 * (float(np.std(pnls)) / math.sqrt(n_trades))

    if has_mtm_evidence:
        mtm_series = np.array(daily_mtm_equity_series, dtype=float)
        peak = np.maximum.accumulate(mtm_series)
        drawdowns = peak - mtm_series
        max_dd_val = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0
        max_dd_pct = (max_dd_val / float(allocation_capital_budget)) * 100.0
    else:
        cum = np.cumsum(pnls)
        peak = np.maximum.accumulate(cum)
        drawdowns = peak - cum
        max_dd_val = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0
        max_dd_pct = (max_dd_val / float(allocation_capital_budget)) * 100.0

    sorted_pnls = np.sort(pnls)[::-1]
    top_1pct_n = max(1, int(math.ceil(0.01 * n_trades)))
    top_1pct_sum = float(np.sum(sorted_pnls[:top_1pct_n]))
    total_positive_pnl = float(np.sum(pnls[pnls > 0])) if np.any(pnls > 0) else 1.0
    top_1pct_share = top_1pct_sum / total_positive_pnl

    checks["independent_sessions_ge_60"] = total_sessions >= 60
    if not checks["independent_sessions_ge_60"]:
        reasons.append(f"Independent sessions {total_sessions} < required 60")

    checks["completed_trades_ge_300"] = n_trades >= 300
    if not checks["completed_trades_ge_300"]:
        reasons.append(f"Completed trades {n_trades} < required 300")

    checks["positive_lower_95_ci"] = lower_95_ci > 0.0
    if not checks["positive_lower_95_ci"]:
        reasons.append(
            f"Day-clustered lower 95% CI ({lower_95_ci:.2f}) is not strictly positive (>0)"
        )

    checks["positive_baseline_expectancy"] = net_expectancy > 0.0
    if not checks["positive_baseline_expectancy"]:
        reasons.append(f"Baseline expectancy ({net_expectancy:.2f}) <= 0")

    checks["positive_under_2x_costs"] = expectancy_2x > 0.0
    if not checks["positive_under_2x_costs"]:
        reasons.append(f"Expectancy under 2x costs ({expectancy_2x:.2f}) <= 0")

    checks["positive_under_3x_costs"] = expectancy_3x > 0.0
    if not checks["positive_under_3x_costs"]:
        reasons.append(f"Expectancy under 3x costs ({expectancy_3x:.2f}) <= 0")

    checks["positive_after_top_1pct_profit_removal"] = expectancy_without_top > 0.0
    if not checks["positive_after_top_1pct_profit_removal"]:
        reasons.append(
            "Expectancy after removing the strongest 1% of profitable trades "
            f"({expectancy_without_top:.2f}) <= 0"
        )

    checks["drawdown_within_budget"] = (
        max_dd_pct <= max_allowed_drawdown_pct
        and (has_mtm_evidence or not require_mtm_evidence)
    )
    if not checks["drawdown_within_budget"]:
        if require_mtm_evidence and not has_mtm_evidence:
            reasons.append("Missing daily MTM equity path evidence")
        else:
            reasons.append(
                f"Max MTM drawdown {max_dd_pct:.2f}% exceeds budget ceiling "
                f"{max_allowed_drawdown_pct:.2f}%"
            )

    checks["zero_unresolved_exposures"] = int(unresolved_exposures_count) == 0
    if not checks["zero_unresolved_exposures"]:
        reasons.append(f"Unresolved exposures present: {unresolved_exposures_count}")

    checks["quote_coverage_ge_95"] = float(quote_coverage_pct) >= 95.0
    if not checks["quote_coverage_ge_95"]:
        reasons.append(f"Quote coverage {quote_coverage_pct:.2f}% < required 95.0%")

    promoted = all(checks.values())

    return AuthoritativeGateVerdict(
        promoted=promoted,
        total_sessions=total_sessions,
        completed_trades=n_trades,
        net_expectancy=net_expectancy,
        lower_95_ci=lower_95_ci,
        expectancy_2x_cost=expectancy_2x,
        expectancy_3x_cost=expectancy_3x,
        expectancy_without_top_1pct_profits=expectancy_without_top,
        max_mtm_drawdown_pct=max_dd_pct,
        top_1pct_pnl_share=top_1pct_share,
        unresolved_exposures_count=int(unresolved_exposures_count),
        quote_coverage_pct=float(quote_coverage_pct),
        checks=checks,
        reasons=reasons,
    )


def evaluate_with_verdict(**kwargs) -> Dict[str, Any]:
    """The gate plus its verdict, as one call."""
    data_quality_ok = bool(kwargs.pop("data_quality_ok", True))
    min_sessions = int(kwargs.pop("min_sessions", 60))
    min_trades = int(kwargs.pop("min_trades", 300))

    payload = evaluate_authoritative_snapback_gate(**kwargs).as_dict()
    sample_sufficient = (
        int(payload.get("total_sessions") or 0) >= min_sessions
        and int(payload.get("completed_trades") or 0) >= min_trades
    )
    payload["verdict"] = verdict_for(
        promoted=bool(payload.get("promoted")),
        sample_sufficient=sample_sufficient,
        data_quality_ok=data_quality_ok,
    )
    return payload
