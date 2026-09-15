"""Model vs. Observed Reconciliation & Research Artifact Bundle Generator.

Produces the model-vs-observed reconciliation report, error distribution,
decile breakdown, tail winner impact, cost stress, and full research artifact
bundle inside research/snapback_reality_v1/<run_id>/.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np

from study.snapback_authoritative_gate import evaluate_authoritative_snapback_gate
from study.snapback_observed_replay import ObservedTradeRecord


@dataclass
class ReconciliationSummary:
    run_id: str
    total_trades: int
    mean_modeled_pnl: float
    mean_observed_pnl: float
    mean_error: float
    median_error: float
    std_error: float
    worst_decile_mean_error: float
    top_1pct_pnl_impact: float
    authoritative_gate_promoted: bool

    def as_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "total_trades": self.total_trades,
            "mean_modeled_pnl": round(self.mean_modeled_pnl, 2),
            "mean_observed_pnl": round(self.mean_observed_pnl, 2),
            "mean_error": round(self.mean_error, 2),
            "median_error": round(self.median_error, 2),
            "std_error": round(self.std_error, 2),
            "worst_decile_mean_error": round(self.worst_decile_mean_error, 2),
            "top_1pct_pnl_impact": round(self.top_1pct_pnl_impact, 2),
            "authoritative_gate_promoted": self.authoritative_gate_promoted,
        }


def generate_snapback_reconciliation_bundle(
    run_id: str,
    observed_records: List[ObservedTradeRecord],
    output_base_dir: str = "research/snapback_reality_v1",
) -> Dict[str, Any]:
    """Generate structured research artifact directory and reconciliation report."""
    run_dir = os.path.join(output_base_dir, run_id)
    os.makedirs(run_dir, exist_ok=True)

    if len(observed_records) == 0:
        summary = ReconciliationSummary(
            run_id=run_id,
            total_trades=0,
            mean_modeled_pnl=0.0,
            mean_observed_pnl=0.0,
            mean_error=0.0,
            median_error=0.0,
            std_error=0.0,
            worst_decile_mean_error=0.0,
            top_1pct_pnl_impact=0.0,
            authoritative_gate_promoted=False,
        )
        with open(os.path.join(run_dir, "validation_report.json"), "w") as f:
            json.dump(summary.as_dict(), f, indent=2)
        return summary.as_dict()

    modeled_pnls = []
    observed_pnls = []
    errors = []
    costs = []
    trades_dict_list = []

    for rec in observed_records:
        modeled_pnl = rec.modeled_exit_price - rec.modeled_entry_price
        observed_pnl = rec.total_trade_pnl
        err = observed_pnl - modeled_pnl
        
        modeled_pnls.append(modeled_pnl)
        observed_pnls.append(observed_pnl)
        errors.append(err)
        costs.append(rec.statutory_charges)
        
        d = rec.as_dict()
        d["modeled_pnl"] = round(modeled_pnl, 2)
        d["observed_error"] = round(err, 2)
        trades_dict_list.append(d)

    errors_arr = np.array(errors)
    mean_err = float(np.mean(errors_arr))
    med_err = float(np.median(errors_arr))
    std_err = float(np.std(errors_arr))

    # Worst decile error
    sorted_errors = np.sort(errors_arr)
    decile_n = max(1, int(len(errors_arr) * 0.1))
    worst_decile_mean = float(np.mean(sorted_errors[:decile_n]))

    # Top 1% winner impact
    sorted_obs = np.sort(observed_pnls)[::-1]
    top_n = max(1, int(math.ceil(0.01 * len(observed_pnls))))
    top_1pct_impact = float(np.sum(sorted_obs[:top_n]))

    # Run authoritative gate
    unique_dates = len(set(r.entry_date for r in observed_records))
    verdict = evaluate_authoritative_snapback_gate(
        trade_pnls=observed_pnls,
        entry_sessions_count=unique_dates,
        statutory_costs=costs,
    )

    summary = ReconciliationSummary(
        run_id=run_id,
        total_trades=len(observed_records),
        mean_modeled_pnl=float(np.mean(modeled_pnls)),
        mean_observed_pnl=float(np.mean(observed_pnls)),
        mean_error=mean_err,
        median_error=med_err,
        std_error=std_err,
        worst_decile_mean_error=worst_decile_mean,
        top_1pct_pnl_impact=top_1pct_impact,
        authoritative_gate_promoted=verdict.promoted,
    )

    # Write artifact files
    with open(os.path.join(run_dir, "trades.json"), "w") as f:
        json.dump(trades_dict_list, f, indent=2)

    with open(os.path.join(run_dir, "validation_report.json"), "w") as f:
        json.dump(verdict.as_dict(), f, indent=2)

    with open(os.path.join(run_dir, "model_vs_observed.json"), "w") as f:
        json.dump(summary.as_dict(), f, indent=2)

    return summary.as_dict()
