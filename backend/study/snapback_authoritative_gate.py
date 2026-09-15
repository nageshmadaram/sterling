"""Single Authoritative Economic Promotion Gate for Snapback.

Implements the single authoritative promotion decision framework matching
docs/strategy/snapback/specs/04_VALIDATION_AND_DELIVERY.md.

Replaces all secondary/legacy promotion reports.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np


@dataclass
class AuthoritativeGateVerdict:
    promoted: bool
    total_sessions: int
    completed_trades: int
    net_expectancy: float
    lower_95_ci: float
    expectancy_2x_cost: float
    expectancy_3x_cost: float
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
            "max_mtm_drawdown_pct": round(self.max_mtm_drawdown_pct, 2),
            "top_1pct_pnl_share": round(self.top_1pct_pnl_share, 4),
            "unresolved_exposures_count": self.unresolved_exposures_count,
            "quote_coverage_pct": round(self.quote_coverage_pct, 2),
            "checks": dict(self.checks),
            "reasons": list(self.reasons),
        }


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
) -> AuthoritativeGateVerdict:
    """Evaluate Snapback observed replay results against the 04_VALIDATION_AND_DELIVERY.md contract."""
    n_trades = len(trade_pnls)
    reasons: List[str] = []
    checks: Dict[str, bool] = {}

    if statutory_costs is None:
        statutory_costs = [0.0] * n_trades

    # Fail closed on cost length mismatch
    if len(statutory_costs) != n_trades:
        return AuthoritativeGateVerdict(
            promoted=False,
            total_sessions=len(set(entry_dates)) if entry_dates else (entry_sessions_count or 0),
            completed_trades=n_trades,
            net_expectancy=0.0,
            lower_95_ci=0.0,
            expectancy_2x_cost=0.0,
            expectancy_3x_cost=0.0,
            max_mtm_drawdown_pct=999.0,
            top_1pct_pnl_share=0.0,
            unresolved_exposures_count=unresolved_exposures_count,
            quote_coverage_pct=quote_coverage_pct,
            checks={"cost_data_length_matched": False},
            reasons=[f"Cost data length mismatch: costs {len(statutory_costs)} != trades {n_trades}"],
        )

    if entry_dates and len(entry_dates) == n_trades:
        dates = list(entry_dates)
    elif entry_sessions_count and entry_sessions_count > 0 and n_trades > 0:
        dates = [f"day_{i % entry_sessions_count}" for i in range(n_trades)]
    else:
        dates = [f"day_{i}" for i in range(n_trades)]

    if n_trades == 0:
        return AuthoritativeGateVerdict(
            promoted=False,
            total_sessions=len(set(dates)) if dates else 0,
            completed_trades=0,
            net_expectancy=0.0,
            lower_95_ci=0.0,
            expectancy_2x_cost=0.0,
            expectancy_3x_cost=0.0,
            max_mtm_drawdown_pct=0.0,
            top_1pct_pnl_share=0.0,
            unresolved_exposures_count=unresolved_exposures_count,
            quote_coverage_pct=quote_coverage_pct,
            checks={"completed_trades_ge_300": False},
            reasons=["No completed trades in evaluation run"],
        )

    pnls = np.array(trade_pnls, dtype=float)
    costs = np.array(statutory_costs, dtype=float)
    unique_sessions = len(set(dates))

    net_expectancy = float(np.mean(pnls))
    
    # 2x and 3x cost stress
    pnls_2x = pnls - costs
    pnls_3x = pnls - (2.0 * costs)
    
    expectancy_2x = float(np.mean(pnls_2x))
    expectancy_3x = float(np.mean(pnls_3x))

    # ENTRY-DAY BLOCK BOOTSTRAP (Resample by entry-day blocks with fixed seed)
    rng = np.random.default_rng(seed)
    day_to_pnls: Dict[str, List[float]] = {}
    for d, p in zip(dates, pnls):
        day_to_pnls.setdefault(d, []).append(p)
    
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

    # Daily MTM Drawdown against Predeclared Allocation Capital Budget
    if daily_mtm_equity_series and len(daily_mtm_equity_series) > 0:
        mtm_series = np.array(daily_mtm_equity_series, dtype=float)
        peak = np.maximum.accumulate(mtm_series)
        drawdowns = peak - mtm_series
        max_dd_val = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0
    else:
        cum = np.cumsum(pnls)
        peak = np.maximum.accumulate(cum)
        drawdowns = peak - cum
        max_dd_val = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

    max_dd_pct = (max_dd_val / allocation_capital_budget) * 100.0

    # Top 1% PnL share (tail concentration metric)
    sorted_pnls = np.sort(pnls)[::-1]
    top_1pct_n = max(1, int(math.ceil(0.01 * n_trades)))
    top_1pct_sum = float(np.sum(sorted_pnls[:top_1pct_n]))
    total_positive_pnl = float(np.sum(pnls[pnls > 0])) if np.any(pnls > 0) else 1.0
    top_1pct_share = top_1pct_sum / total_positive_pnl

    # Gate 1: Session count >= 60
    checks["independent_sessions_ge_60"] = unique_sessions >= 60
    if not checks["independent_sessions_ge_60"]:
        reasons.append(f"Independent sessions {unique_sessions} < required 60")

    # Gate 2: Completed trades >= 300
    checks["completed_trades_ge_300"] = n_trades >= 300
    if not checks["completed_trades_ge_300"]:
        reasons.append(f"Completed trades {n_trades} < required 300")

    # Gate 3: Lower 95% CI > 0
    checks["positive_lower_95_ci"] = lower_95_ci > 0.0
    if not checks["positive_lower_95_ci"]:
        reasons.append(f"Day-clustered lower 95% CI ({lower_95_ci:.2f}) is not strictly positive (>0)")

    # Gate 4: Baseline expectancy > 0
    checks["positive_baseline_expectancy"] = net_expectancy > 0.0
    if not checks["positive_baseline_expectancy"]:
        reasons.append(f"Baseline expectancy ({net_expectancy:.2f}) <= 0")

    # Gate 5: Positive under 2x execution cost stress
    checks["positive_under_2x_costs"] = expectancy_2x > 0.0
    if not checks["positive_under_2x_costs"]:
        reasons.append(f"Expectancy under 2x costs ({expectancy_2x:.2f}) <= 0")

    # Gate 6: Drawdown within initial evaluation ceiling (10.0%)
    checks["drawdown_within_budget"] = max_dd_pct <= max_allowed_drawdown_pct
    if not checks["drawdown_within_budget"]:
        reasons.append(f"Max MTM drawdown {max_dd_pct:.2f}% exceeds budget ceiling {max_allowed_drawdown_pct:.2f}%")

    # Gate 7: Zero unresolved exposures
    checks["zero_unresolved_exposures"] = unresolved_exposures_count == 0
    if not checks["zero_unresolved_exposures"]:
        reasons.append(f"Unresolved exposures present: {unresolved_exposures_count}")

    # Gate 8: Quote coverage >= 95%
    checks["quote_coverage_ge_95"] = quote_coverage_pct >= 95.0
    if not checks["quote_coverage_ge_95"]:
        reasons.append(f"Quote coverage {quote_coverage_pct:.2f}% < required 95.0%")

    promoted = all(checks.values())

    return AuthoritativeGateVerdict(
        promoted=promoted,
        total_sessions=unique_sessions,
        completed_trades=n_trades,
        net_expectancy=net_expectancy,
        lower_95_ci=lower_95_ci,
        expectancy_2x_cost=expectancy_2x,
        expectancy_3x_cost=expectancy_3x,
        max_mtm_drawdown_pct=max_dd_pct,
        top_1pct_pnl_share=top_1pct_share,
        unresolved_exposures_count=unresolved_exposures_count,
        quote_coverage_pct=quote_coverage_pct,
        checks=checks,
        reasons=reasons,
    )
