"""Daily evidence report over the frozen Snapback prospective warehouse.

Diagnostic only. This module measures what was actually observed — fills, hedges,
costs, quotes and completed outcomes — and never promotes Snapback. Promotion is
decided exclusively by the separate authoritative economic gate
(`study/snapback_authoritative_gate.py`).

Missing evidence is reported as UNKNOWN (None), never as zero.
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Sterling's written validation contract. Reported for context only; this report
# never converts a threshold into a promotion decision.
MIN_HELD_OUT_SESSIONS = 60
MIN_COMPLETED_TRADES = 300

WAREHOUSE_TABLES = (
    "opportunities",
    "contract_candidates",
    "option_quotes",
    "futures_quotes",
    "decisions",
    "paper_fills",
    "hedge_rebalances",
    "daily_mtm",
    "margin_snapshots",
    "costs",
    "outcomes",
    "paper_positions",
)


@dataclass
class ForwardReportArtifacts:
    directory: Path
    run_manifest: Path
    evidence_summary: Path
    trades_csv: Path
    daily_mtm_csv: Path
    rejection_summary_csv: Path
    validation_report: Path
    authoritative_gate: Optional[Path] = None
    operational_health: Optional[Path] = None


def _f(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _counts(rows: List[Dict[str, Any]], field: str = "status") -> Dict[str, int]:
    out: Dict[str, int] = {}
    for row in rows or []:
        key = str(row.get(field) or "UNKNOWN")
        out[key] = out.get(key, 0) + 1
    return out


def load_forward_records(warehouse) -> Dict[str, List[Dict[str, Any]]]:
    """Read every prospective warehouse table into plain dict rows."""
    records: Dict[str, List[Dict[str, Any]]] = {}
    for table in WAREHOUSE_TABLES:
        try:
            records[table] = [dict(r) for r in (warehouse.get_records_by_table(table) or [])]
        except Exception:
            records[table] = []
    return records


def _quote_stats(quotes: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(quotes or [])
    stale = 0
    invalid = 0
    spreads: List[float] = []

    for q in quotes or []:
        if int(q.get("is_stale") or 0) == 1:
            stale += 1
            continue
        bid = _f(q.get("bid"), 0.0) or 0.0
        ask = _f(q.get("ask"), 0.0) or 0.0
        if bid <= 0 or ask <= 0 or ask < bid:
            invalid += 1
            continue
        mid = (bid + ask) / 2.0
        if mid > 0:
            spreads.append((ask - bid) / mid * 100.0)

    usable = total - stale - invalid
    return {
        "option_quote_observations": total,
        "stale_option_quotes": stale,
        "invalid_option_quotes": invalid,
        "usable_option_quotes": usable,
        "option_quote_coverage_pct": (usable / total * 100.0) if total else None,
        "average_option_spread_pct": (sum(spreads) / len(spreads)) if spreads else None,
    }


def _tail_share(values: List[float], pct: float) -> Optional[float]:
    positives = sorted([v for v in values if v > 0], reverse=True)
    if not positives:
        return None
    total = sum(positives)
    if total <= 0:
        return None
    # Tiny samples still use at least one trade.
    k = max(1, math.ceil(len(positives) * pct))
    return sum(positives[:k]) / total


def build_forward_summary(
    *,
    records: Dict[str, List[Dict[str, Any]]],
    runtime_sha: str,
    strategy_manifest: str,
) -> Dict[str, Any]:
    """Summarize observed prospective evidence. Absent economics stay None (UNKNOWN)."""
    outcomes = records.get("outcomes") or []
    positions = records.get("paper_positions") or []
    opportunities = records.get("opportunities") or []
    fills = records.get("paper_fills") or []
    hedges = records.get("hedge_rebalances") or []
    mtm_rows = records.get("daily_mtm") or []

    pnls = [_f(o.get("actual_total_pnl"), 0.0) or 0.0 for o in outcomes]
    modeled = [_f(o.get("modeled_total_pnl"), 0.0) or 0.0 for o in outcomes]
    option_pnls = [_f(o.get("actual_option_pnl"), 0.0) or 0.0 for o in outcomes]
    futures_pnls = [_f(o.get("actual_futures_pnl"), 0.0) or 0.0 for o in outcomes]
    outcome_costs = [_f(o.get("actual_costs"), 0.0) or 0.0 for o in outcomes]

    completed = len(outcomes)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    net_pnl = sum(pnls) if completed else None
    modeled_total = sum(modeled) if completed else None

    pos_counts = _counts(positions)
    open_positions = int(pos_counts.get("OPEN", 0))
    exit_pending = int(pos_counts.get("EXIT_PENDING", 0))

    session_dates = {
        str(row.get("entry_timestamp") or row.get("session_date") or "")[:10]
        for row in positions
    }
    session_dates.discard("")
    mtm_sessions = {str(r.get("session_date") or "")[:10] for r in mtm_rows}
    mtm_sessions.discard("")
    observed_sessions = len(session_dates | mtm_sessions)

    slippages = [_f(f.get("slippage")) for f in fills]
    slippages = [s for s in slippages if s is not None]

    turnover = 0
    for h in hedges:
        prior = int(_f(h.get("prior_hedge_lots"), 0.0) or 0.0)
        new = int(_f(h.get("new_hedge_lots"), 0.0) or 0.0)
        turnover += abs(new - prior)

    rejection_counts = _counts(opportunities, field="rejection_reason")
    rejection_counts.pop("", None)
    rejection_counts.pop("UNKNOWN", None)

    summary: Dict[str, Any] = {
        # Frozen identity
        "runtime_sha": runtime_sha,
        "strategy_manifest": strategy_manifest,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        # Sample
        "completed_trades": completed,
        "observed_sessions": observed_sessions,
        "signals_total": len(opportunities),
        "opportunity_status_counts": _counts(opportunities),
        "paper_position_status_counts": pos_counts,
        "open_positions": open_positions,
        "exit_pending": exit_pending,
        "unresolved_exposures": open_positions + exit_pending,
        "rejection_counts": rejection_counts,
        # Observed economics (None when no completed trades)
        "net_pnl": net_pnl,
        "mean_net_pnl_per_trade": (net_pnl / completed) if completed else None,
        "win_rate": (len(wins) / completed) if completed else None,
        "average_win": (sum(wins) / len(wins)) if wins else None,
        "average_loss": (sum(losses) / len(losses)) if losses else None,
        "profit_factor": (
            (sum(wins) / abs(sum(losses))) if wins and losses else None
        ),
        "gross_option_pnl": sum(option_pnls) if completed else None,
        "futures_hedge_pnl": sum(futures_pnls) if completed else None,
        "closed_trade_costs": sum(outcome_costs) if completed else None,
        # Model vs observed
        "modeled_total_pnl": modeled_total,
        "model_optimism": (
            modeled_total - net_pnl
            if modeled_total is not None and net_pnl is not None
            else None
        ),
        # Concentration
        "top_1pct_positive_pnl_share": _tail_share(pnls, 0.01),
        "top_5pct_positive_pnl_share": _tail_share(pnls, 0.05),
        # Execution reality
        "average_fill_slippage": (
            sum(slippages) / len(slippages) if slippages else None
        ),
        "hedge_turnover_lots": turnover,
        "hedge_rebalance_count": len(hedges),
        "daily_mtm_records": len(mtm_rows),
    }

    summary.update(_quote_stats(records.get("option_quotes") or []))

    # Evidence status only. Promotion belongs to the authoritative gate.
    if completed >= MIN_COMPLETED_TRADES and observed_sessions >= MIN_HELD_OUT_SESSIONS:
        summary["evidence_status"] = "SAMPLE_SUFFICIENT_SEE_AUTHORITATIVE_GATE"
    else:
        summary["evidence_status"] = "INCONCLUSIVE"

    summary["evidence_status_reason"] = (
        f"completed_trades={completed} (min {MIN_COMPLETED_TRADES}), "
        f"observed_sessions={observed_sessions} (min {MIN_HELD_OUT_SESSIONS})"
    )
    return summary


def _fmt(value: Any) -> str:
    if value is None:
        return "UNKNOWN"
    if isinstance(value, float):
        return f"{value:,.4f}"
    return str(value)


def _write_csv(path: Path, rows: List[Dict[str, Any]], fallback_header: List[str]) -> None:
    header = sorted({k for row in rows for k in row.keys()}) if rows else fallback_header
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _validation_markdown(summary: Dict[str, Any], report_date: date) -> str:
    lines = [
        f"# Snapback Prospective Evidence Report — {report_date.isoformat()}",
        "",
        "Diagnostic report over observed prospective evidence. This report never",
        "promotes Snapback; promotion is decided only by the authoritative economic gate.",
        "",
        "## Frozen identity",
        f"- runtime_sha: {summary.get('runtime_sha')}",
        f"- strategy_manifest: {summary.get('strategy_manifest')}",
        f"- generated_at: {summary.get('generated_at')}",
        "",
        "## Evidence status",
        f"- evidence_status: {summary.get('evidence_status')}",
        f"- reason: {summary.get('evidence_status_reason')}",
        "",
        "## Sample",
        f"- signals_total: {_fmt(summary.get('signals_total'))}",
        f"- completed_trades: {_fmt(summary.get('completed_trades'))}",
        f"- observed_sessions: {_fmt(summary.get('observed_sessions'))}",
        f"- open_positions: {_fmt(summary.get('open_positions'))}",
        f"- exit_pending: {_fmt(summary.get('exit_pending'))}",
        f"- unresolved_exposures: {_fmt(summary.get('unresolved_exposures'))}",
        "",
        "## Opportunity status counts",
    ]
    for key, val in sorted((summary.get("opportunity_status_counts") or {}).items()):
        lines.append(f"- {key}: {val}")
    if not summary.get("opportunity_status_counts"):
        lines.append("- (none)")

    lines += [
        "",
        "## Observed economics",
        f"- net_pnl: {_fmt(summary.get('net_pnl'))}",
        f"- mean_net_pnl_per_trade: {_fmt(summary.get('mean_net_pnl_per_trade'))}",
        f"- win_rate: {_fmt(summary.get('win_rate'))}",
        f"- average_win: {_fmt(summary.get('average_win'))}",
        f"- average_loss: {_fmt(summary.get('average_loss'))}",
        f"- profit_factor: {_fmt(summary.get('profit_factor'))}",
        f"- gross_option_pnl: {_fmt(summary.get('gross_option_pnl'))}",
        f"- futures_hedge_pnl: {_fmt(summary.get('futures_hedge_pnl'))}",
        f"- closed_trade_costs: {_fmt(summary.get('closed_trade_costs'))}",
        "",
        "## Model vs observed",
        f"- modeled_total_pnl: {_fmt(summary.get('modeled_total_pnl'))}",
        f"- model_optimism: {_fmt(summary.get('model_optimism'))}",
        "",
        "## Concentration",
        f"- top_1pct_positive_pnl_share: {_fmt(summary.get('top_1pct_positive_pnl_share'))}",
        f"- top_5pct_positive_pnl_share: {_fmt(summary.get('top_5pct_positive_pnl_share'))}",
        "",
        "## Execution reality",
        f"- option_quote_observations: {_fmt(summary.get('option_quote_observations'))}",
        f"- stale_option_quotes: {_fmt(summary.get('stale_option_quotes'))}",
        f"- invalid_option_quotes: {_fmt(summary.get('invalid_option_quotes'))}",
        f"- usable_option_quotes: {_fmt(summary.get('usable_option_quotes'))}",
        f"- option_quote_coverage_pct: {_fmt(summary.get('option_quote_coverage_pct'))}",
        f"- average_option_spread_pct: {_fmt(summary.get('average_option_spread_pct'))}",
        f"- average_fill_slippage: {_fmt(summary.get('average_fill_slippage'))}",
        f"- hedge_turnover_lots: {_fmt(summary.get('hedge_turnover_lots'))}",
        f"- hedge_rebalance_count: {_fmt(summary.get('hedge_rebalance_count'))}",
        f"- daily_mtm_records: {_fmt(summary.get('daily_mtm_records'))}",
        "",
        "## Rejections",
    ]
    for key, val in sorted((summary.get("rejection_counts") or {}).items()):
        lines.append(f"- {key}: {val}")
    if not summary.get("rejection_counts"):
        lines.append("- (none)")

    lines += ["", "UNKNOWN means the evidence does not exist. It never means zero.", ""]
    return "\n".join(lines)


def write_forward_report(
    *,
    summary: Dict[str, Any],
    records: Dict[str, List[Dict[str, Any]]],
    output_root: Path,
    report_date: date,
) -> ForwardReportArtifacts:
    """Write the day's evidence artifacts under output_root/<report_date>/."""
    directory = Path(output_root) / report_date.isoformat()
    directory.mkdir(parents=True, exist_ok=True)

    run_manifest = directory / "run_manifest.json"
    run_manifest.write_text(
        json.dumps(
            {
                "report_date": report_date.isoformat(),
                "runtime_sha": summary.get("runtime_sha"),
                "strategy_manifest": summary.get("strategy_manifest"),
                "generated_at": summary.get("generated_at"),
                "record_counts": {k: len(v or []) for k, v in records.items()},
                "report_kind": "DIAGNOSTIC_EVIDENCE_ONLY",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    evidence_summary = directory / "evidence_summary.json"
    evidence_summary.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    trades_csv = directory / "trades.csv"
    _write_csv(
        trades_csv,
        records.get("outcomes") or [],
        [
            "opportunity_id",
            "actual_total_pnl",
            "modeled_total_pnl",
            "actual_option_pnl",
            "actual_futures_pnl",
            "actual_costs",
            "observed_vs_model_error",
        ],
    )

    daily_mtm_csv = directory / "daily_mtm.csv"
    _write_csv(
        daily_mtm_csv,
        records.get("daily_mtm") or [],
        ["opportunity_id", "session_date", "option_bid", "mtm_pnl"],
    )

    rejection_rows = [
        {"rejection_reason": reason, "count": count}
        for reason, count in sorted((summary.get("rejection_counts") or {}).items())
    ]
    rejection_rows += [
        {"rejection_reason": "stale_option_quote", "count": summary.get("stale_option_quotes")},
        {"rejection_reason": "invalid_option_quote", "count": summary.get("invalid_option_quotes")},
    ]
    rejection_summary_csv = directory / "rejection_summary.csv"
    _write_csv(rejection_summary_csv, rejection_rows, ["rejection_reason", "count"])

    validation_report = directory / "validation_report.md"
    validation_report.write_text(_validation_markdown(summary, report_date), encoding="utf-8")

    # Authoritative verdict over the same records. Diagnostic summary and economic
    # verdict stay in separate files so neither can be mistaken for the other.
    authoritative_gate = directory / "authoritative_gate.json"
    try:
        from study.snapback_forward_gate import evaluate_forward_gate

        gate_payload = evaluate_forward_gate(records=records)
    except Exception as exc:
        gate_payload = {
            "verdict": "INCONCLUSIVE",
            "error": str(exc),
            "missing_requirements": ["authoritative gate could not be evaluated"],
        }
    authoritative_gate.write_text(json.dumps(gate_payload, indent=2), encoding="utf-8")

    operational_health = directory / "operational_health.json"
    try:
        from app.services.snapback_health import get_prospective_health

        health_payload = get_prospective_health()
    except Exception as exc:
        health_payload = {
            "status": "HALTED",
            "healthy": False,
            "unresolved_errors": ["health_probe_failed"],
            "error": str(exc),
        }
    operational_health.write_text(json.dumps(health_payload, indent=2), encoding="utf-8")

    return ForwardReportArtifacts(
        directory=directory,
        run_manifest=run_manifest,
        evidence_summary=evidence_summary,
        trades_csv=trades_csv,
        daily_mtm_csv=daily_mtm_csv,
        rejection_summary_csv=rejection_summary_csv,
        validation_report=validation_report,
        authoritative_gate=authoritative_gate,
        operational_health=operational_health,
    )


def generate_forward_report(
    *,
    warehouse=None,
    output_root: Optional[Path] = None,
    report_date: Optional[date] = None,
    runtime_sha: Optional[str] = None,
    strategy_manifest: Optional[str] = None,
) -> ForwardReportArtifacts:
    """Read the frozen prospective warehouse and write the day's evidence artifacts."""
    from app.services.snapback_observation_warehouse import (
        FROZEN_MANIFEST_HASH,
        SnapbackObservationWarehouse,
    )

    if warehouse is None:
        warehouse = SnapbackObservationWarehouse()
    if runtime_sha is None:
        from app.services.snapback_health import _runtime_sha

        runtime_sha = _runtime_sha() or "UNKNOWN"
    if strategy_manifest is None:
        strategy_manifest = FROZEN_MANIFEST_HASH
    if output_root is None:
        output_root = Path(
            os.environ.get("STERLING_FORWARD_REPORT_ROOT", "artifacts/snapback_forward")
        )
    if report_date is None:
        report_date = datetime.now(timezone.utc).date()

    records = load_forward_records(warehouse)
    summary = build_forward_summary(
        records=records,
        runtime_sha=runtime_sha,
        strategy_manifest=strategy_manifest,
    )
    return write_forward_report(
        summary=summary,
        records=records,
        output_root=Path(output_root),
        report_date=report_date,
    )


if __name__ == "__main__":
    artifacts = generate_forward_report()
    print(f"report_directory={artifacts.directory}")
    print(f"validation_report={artifacts.validation_report}")
