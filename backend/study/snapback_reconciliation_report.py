"""Model vs. Observed Reconciliation & Research Artifact Bundle Generator.

Produces the model-vs-observed reconciliation report, error distribution,
decile breakdown, tail winner impact, cost stress, and full research artifact
bundle inside research/snapback_reality_v1/<run_id>/.
"""
from __future__ import annotations

import json
import math
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
    manifest_dict: Optional[Dict[str, Any]] = None,
    dataset_manifest_dict: Optional[Dict[str, Any]] = None,
    contract_registry_dict: Optional[Dict[str, Any]] = None,
    daily_equity_series: Optional[List[Dict[str, Any]]] = None,
    margin_usage_series: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Generate structured research artifact directory and reconciliation report."""
    run_dir = os.path.join(output_base_dir, run_id)
    os.makedirs(run_dir, exist_ok=True)

    filled_records = [r for r in observed_records if r.fill_status == "FILLED"]
    total_opportunities = len(observed_records)
    quote_coverage_pct = (len(filled_records) / total_opportunities * 100.0) if total_opportunities > 0 else 0.0

    if len(filled_records) == 0:
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
        empty_bundle = {
            "run_manifest.json": manifest_dict or {"run_id": run_id, "status": "EMPTY"},
            "dataset_manifest.json": dataset_manifest_dict or {"total_quotes": 0, "coverage_pct": quote_coverage_pct},
            "contract_registry.json": contract_registry_dict or {"contracts": 0},
            "opportunities.json": [],
            "quotes.json": [],
            "decisions.json": [],
            "fills.json": [],
            "hedge_events.json": [],
            "trades.json": [],
            "daily_mtm_equity.json": daily_equity_series or [],
            "margin_usage.json": margin_usage_series or [],
            "model_vs_observed.json": summary.as_dict(),
            "cost_stress.json": {"stress_multiplier": 2.0, "promoted": False},
            "concentration.json": {"max_day_share": 0.0},
            "validation_report.json": summary.as_dict(),
            "promotion_record.json": {"promoted": False, "reason": "No filled trades in replay"},
        }
        for fname, content in empty_bundle.items():
            with open(os.path.join(run_dir, fname), "w") as f:
                json.dump(content, f, indent=2)
        return summary.as_dict()

    modeled_pnls = []
    observed_pnls = []
    errors = []
    costs = []
    filled_entry_dates = []
    trades_dict_list = []
    opportunities_list = []
    fills_list = []
    hedge_events_list = []
    aggregated_mtm_series = []

    for rec in observed_records:
        opportunities_list.append({
            "trade_id": rec.trade_id,
            "entry_date": rec.entry_date,
            "symbol": rec.symbol,
            "side": rec.side,
            "strike": rec.strike,
        })
        if rec.fill_status == "FILLED":
            # Like-with-like economic PnL calculation in Rupee terms
            modeled_option_rupees = (rec.modeled_exit_price - rec.modeled_entry_price) * rec.quantity
            modeled_trade_net_rupees = modeled_option_rupees + rec.hedge_pnl - rec.statutory_charges
            observed_trade_net_rupees = rec.total_trade_pnl
            err = observed_trade_net_rupees - modeled_trade_net_rupees

            modeled_pnls.append(modeled_trade_net_rupees)
            observed_pnls.append(observed_trade_net_rupees)
            errors.append(err)
            costs.append(rec.statutory_charges)
            filled_entry_dates.append(rec.entry_date)

            d = rec.as_dict()
            d["modeled_pnl_rupees"] = round(modeled_trade_net_rupees, 2)
            d["observed_error_rupees"] = round(err, 2)
            trades_dict_list.append(d)

            fills_list.append({
                "trade_id": rec.trade_id,
                "symbol": rec.symbol,
                "entry_ask": rec.entry_ask_price,
                "exit_bid": rec.exit_bid_price,
                "qty": rec.quantity,
            })
            if rec.hedge_symbol:
                hedge_events_list.append({
                    "trade_id": rec.trade_id,
                    "hedge_symbol": rec.hedge_symbol,
                    "entry_price": rec.hedge_entry_price,
                    "exit_price": rec.hedge_exit_price,
                    "hedge_pnl": rec.hedge_pnl,
                })
            if rec.daily_mtm_equity:
                if not aggregated_mtm_series:
                    aggregated_mtm_series = list(rec.daily_mtm_equity)
                else:
                    for idx, val in enumerate(rec.daily_mtm_equity):
                        if idx < len(aggregated_mtm_series):
                            aggregated_mtm_series[idx] += (val - 1000000.0)

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

    # Run authoritative gate with exact filled entry dates and MTM equity path
    verdict = evaluate_authoritative_snapback_gate(
        trade_pnls=observed_pnls,
        entry_dates=filled_entry_dates,
        statutory_costs=costs,
        daily_mtm_equity_series=daily_equity_series or aggregated_mtm_series,
        quote_coverage_pct=quote_coverage_pct,
    )

    summary = ReconciliationSummary(
        run_id=run_id,
        total_trades=len(filled_records),
        mean_modeled_pnl=float(np.mean(modeled_pnls)),
        mean_observed_pnl=float(np.mean(observed_pnls)),
        mean_error=mean_err,
        median_error=med_err,
        std_error=std_err,
        worst_decile_mean_error=worst_decile_mean,
        top_1pct_pnl_impact=top_1pct_impact,
        authoritative_gate_promoted=verdict.promoted,
    )

    # Calculate concentration
    day_pnls: Dict[str, float] = {}
    for rec in observed_records:
        day_pnls[rec.entry_date] = day_pnls.get(rec.entry_date, 0.0) + rec.total_trade_pnl
    total_pnl = sum(observed_pnls)
    max_day_share = max(day_pnls.values()) / total_pnl if total_pnl > 0 else 0.0

    # Write all 16 required research artifact JSON files
    bundle_artifacts = {
        "run_manifest.json": manifest_dict or {"run_id": run_id, "trade_count": len(observed_records)},
        "dataset_manifest.json": dataset_manifest_dict or {"unique_dates": unique_dates},
        "contract_registry.json": contract_registry_dict or {"symbols": list(set(r.symbol for r in observed_records))},
        "opportunities.json": opportunities_list,
        "quotes.json": [{"trade_id": r.trade_id, "ask": r.entry_ask_price, "bid": r.exit_bid_price} for r in observed_records],
        "decisions.json": [{"trade_id": r.trade_id, "status": r.fill_status, "notes": r.notes} for r in observed_records],
        "fills.json": fills_list,
        "hedge_events.json": hedge_events_list,
        "trades.json": trades_dict_list,
        "daily_mtm_equity.json": daily_equity_series or [],
        "margin_usage.json": margin_usage_series or [{"trade_id": r.trade_id, "margin": r.peak_margin_required} for r in observed_records],
        "model_vs_observed.json": summary.as_dict(),
        "cost_stress.json": {"total_charges": sum(costs), "double_charge_pnl": sum(observed_pnls) - sum(costs)},
        "concentration.json": {"max_day_share": round(max_day_share, 4), "day_count": len(day_pnls)},
        "validation_report.json": verdict.as_dict(),
        "promotion_record.json": {
            "promoted": verdict.promoted,
            "gate_failures": verdict.reasons,
            "timestamp": rec.exit_date if observed_records else "",
        },
    }

    for fname, content in bundle_artifacts.items():
        with open(os.path.join(run_dir, fname), "w") as f:
            json.dump(content, f, indent=2)

    return summary.as_dict()
