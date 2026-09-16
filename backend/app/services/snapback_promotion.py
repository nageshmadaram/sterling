"""The only production route from evidence to a promotion verdict.

    verified snapshot -> PromotionInputBuilder -> authoritative gate ->
    PromotionResult -> immutable promotion record -> UI / report / readiness

Nothing else may call the gate or write a promotion record. Two authorities
eventually disagree, and the disagreement reaches a family as "one screen says we may
trade and another says we may not".
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

GATE_VERSION = "authoritative_gate_v2"

PASSED = "PASSED"
FAILED = "FAILED"
INCONCLUSIVE = "INCONCLUSIVE"


@dataclass
class PromotionResult:
    promotion_id: str
    experiment_id: str
    gate_version: str
    gate_input_hash: str
    source_snapshot_sha256: str
    runtime_build_sha: str
    config_hash: str
    rule_hash: str
    execution_policy_hash: str
    cost_schedule_hash: str
    observed_sessions: int
    completed_trades: int
    verdict: str
    promoted: bool
    data_quality_ok: bool
    reasons: List[str] = field(default_factory=list)
    checks: Dict[str, Any] = field(default_factory=dict)
    missing_requirements: List[str] = field(default_factory=list)
    evaluated_at: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "promotion_id": self.promotion_id,
            "experiment_id": self.experiment_id,
            "gate_version": self.gate_version,
            "gate_input_hash": self.gate_input_hash,
            "source_snapshot_sha256": self.source_snapshot_sha256,
            "runtime_build_sha": self.runtime_build_sha,
            "config_hash": self.config_hash,
            "rule_hash": self.rule_hash,
            "execution_policy_hash": self.execution_policy_hash,
            "cost_schedule_hash": self.cost_schedule_hash,
            "observed_sessions": self.observed_sessions,
            "completed_trades": self.completed_trades,
            "verdict": self.verdict,
            "promoted": self.promoted,
            "data_quality_ok": self.data_quality_ok,
            "reasons": list(self.reasons),
            "checks": dict(self.checks),
            "missing_requirements": list(self.missing_requirements),
            "total_sessions": self.observed_sessions,
            "evaluated_at": self.evaluated_at,
        }


def _identity() -> Dict[str, str]:
    from app.engines.snapback.manifest import compute_config_hash, compute_rule_hash
    from app.engines.snapback.policy import EXECUTION_POLICY
    from app.services.snapback_costs import COST_SCHEDULE_VERSION
    from app.services.snapback_identity import build_sha
    import os

    return {
        "experiment_id": os.environ.get("STERLING_EXPERIMENT_ID", "prospective_runtime_1_1"),
        "runtime_build_sha": build_sha(),
        "strategy_config_hash": compute_config_hash(),
        "strategy_rule_hash": compute_rule_hash(),
        "execution_policy_hash": EXECUTION_POLICY.policy_hash(),
        "execution_cost_schedule_hash": COST_SCHEDULE_VERSION,
    }


class PromotionService:
    """Evaluates the authoritative gate and records the verdict, once."""

    MIN_SESSIONS = 60
    MIN_TRADES = 300

    def evaluate(self, *, warehouse, source_snapshot_sha256: str) -> PromotionResult:
        from study.snapback_authoritative_gate import evaluate_authoritative_snapback_gate
        from study.snapback_forward_report import load_forward_records
        from study.snapback_promotion_inputs import (
            PromotionInputError, build_promotion_input,
        )
        from app.services.snapback_session_ledger import observed_session_count

        identity = _identity()
        evaluated_at = datetime.now(timezone.utc).isoformat()

        records, load_errors = load_forward_records(warehouse, strict=True)
        observed_sessions = observed_session_count(warehouse)

        try:
            inputs = build_promotion_input(
                records=records,
                expected_identity=identity,
                source_snapshot_sha256=source_snapshot_sha256,
                observed_sessions=observed_sessions,
            )
        except PromotionInputError as exc:
            # Broken or missing evidence is never an economic verdict.
            return self._record(
                warehouse,
                self._inconclusive(
                    identity, source_snapshot_sha256, observed_sessions,
                    reasons=list(load_errors) + list(exc.errors), evaluated_at=evaluated_at,
                ),
            )

        if load_errors:
            return self._record(
                warehouse,
                self._inconclusive(
                    identity, source_snapshot_sha256, observed_sessions,
                    reasons=list(load_errors), evaluated_at=evaluated_at,
                    gate_input_hash=inputs.gate_input_hash,
                    completed_trades=inputs.completed_trades,
                ),
            )

        coverage_pct = (
            inputs.accepted_quote_events / inputs.required_quote_events * 100.0
            if inputs.required_quote_events else 0.0
        )

        verdict_payload = evaluate_authoritative_snapback_gate(
            trade_pnls=list(inputs.trade_pnls),
            entry_dates=list(inputs.entry_sessions),
            statutory_costs=list(inputs.trade_costs),
            daily_mtm_equity_series=list(inputs.portfolio_equity) or None,
            allocation_capital_budget=inputs.allocation_capital or 1.0,
            unresolved_exposures_count=inputs.unresolved_exposures,
            quote_coverage_pct=coverage_pct,
            entry_sessions_count=inputs.observed_sessions,
        ).as_dict()

        from study.snapback_authoritative_gate import verdict_for

        sample_sufficient = (
            inputs.observed_sessions >= self.MIN_SESSIONS
            and inputs.completed_trades >= self.MIN_TRADES
        )
        # One mapping, shared with study/snapback_forward_gate.py.
        verdict = verdict_for(
            promoted=bool(verdict_payload.get("promoted")),
            sample_sufficient=sample_sufficient,
        )

        missing: List[str] = []
        if inputs.observed_sessions < self.MIN_SESSIONS:
            missing.append(
                f"independent sessions {inputs.observed_sessions} of {self.MIN_SESSIONS} required"
            )
        if inputs.completed_trades < self.MIN_TRADES:
            missing.append(
                f"completed trades {inputs.completed_trades} of {self.MIN_TRADES} required"
            )

        result = PromotionResult(
            promotion_id=f"PROMO-{inputs.gate_input_hash[:16]}",
            experiment_id=inputs.experiment_id,
            gate_version=GATE_VERSION,
            gate_input_hash=inputs.gate_input_hash,
            source_snapshot_sha256=source_snapshot_sha256,
            runtime_build_sha=inputs.runtime_build_sha,
            config_hash=inputs.strategy_config_hash,
            rule_hash=inputs.strategy_rule_hash,
            execution_policy_hash=inputs.execution_policy_hash,
            cost_schedule_hash=inputs.execution_cost_schedule_hash,
            observed_sessions=inputs.observed_sessions,
            completed_trades=inputs.completed_trades,
            verdict=verdict,
            promoted=bool(verdict_payload.get("promoted")),
            data_quality_ok=True,
            reasons=list(verdict_payload.get("reasons") or []),
            checks=dict(verdict_payload.get("checks") or {}),
            missing_requirements=missing,
            evaluated_at=evaluated_at,
        )
        return self._record(warehouse, result)

    def latest(self, *, warehouse) -> Optional[PromotionResult]:
        row = warehouse.latest_promotion_record()
        if not row:
            return None
        return PromotionResult(
            promotion_id=row["promotion_id"],
            experiment_id=row["experiment_id"],
            gate_version=row["gate_version"],
            gate_input_hash=row["gate_input_hash"],
            source_snapshot_sha256=row["source_snapshot_sha256"],
            runtime_build_sha=row["runtime_build_sha"],
            config_hash=row["config_hash"],
            rule_hash=row["rule_hash"],
            execution_policy_hash=row["execution_policy_hash"],
            cost_schedule_hash=row["cost_schedule_hash"],
            observed_sessions=int(row["observed_sessions"] or 0),
            completed_trades=int(row["completed_trades"] or 0),
            verdict=row["verdict"],
            promoted=bool(row["promoted"]),
            data_quality_ok=bool(row["data_quality_ok"]),
            reasons=json.loads(row["reasons_json"] or "[]"),
            checks=json.loads(row["checks_json"] or "{}"),
            evaluated_at=row["evaluated_at"],
        )

    # ------------------------------------------------------------------ internals

    def _inconclusive(
        self, identity, snapshot, observed_sessions, *, reasons, evaluated_at,
        gate_input_hash: str = "", completed_trades: int = 0,
    ) -> PromotionResult:
        import hashlib

        digest = gate_input_hash or hashlib.sha256(
            json.dumps(
                {"snapshot": snapshot, "identity": identity, "reasons": sorted(reasons)},
                sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        return PromotionResult(
            promotion_id=f"PROMO-{digest[:16]}",
            experiment_id=identity.get("experiment_id", ""),
            gate_version=GATE_VERSION,
            gate_input_hash=digest,
            source_snapshot_sha256=snapshot,
            runtime_build_sha=identity.get("runtime_build_sha", ""),
            config_hash=identity.get("strategy_config_hash", ""),
            rule_hash=identity.get("strategy_rule_hash", ""),
            execution_policy_hash=identity.get("execution_policy_hash", ""),
            cost_schedule_hash=identity.get("execution_cost_schedule_hash", ""),
            observed_sessions=observed_sessions,
            completed_trades=completed_trades,
            verdict=INCONCLUSIVE,
            promoted=False,
            data_quality_ok=False,
            reasons=list(reasons),
            missing_requirements=list(reasons),
            evaluated_at=evaluated_at,
        )

    def _record(self, warehouse, result: PromotionResult) -> PromotionResult:
        try:
            warehouse.record_promotion_record(
                promotion_id=result.promotion_id,
                experiment_id=result.experiment_id,
                gate_version=result.gate_version,
                gate_input_hash=result.gate_input_hash,
                source_snapshot_sha256=result.source_snapshot_sha256,
                runtime_build_sha=result.runtime_build_sha,
                config_hash=result.config_hash,
                rule_hash=result.rule_hash,
                execution_policy_hash=result.execution_policy_hash,
                cost_schedule_hash=result.cost_schedule_hash,
                observed_sessions=result.observed_sessions,
                completed_trades=result.completed_trades,
                verdict=result.verdict,
                promoted=1 if result.promoted else 0,
                data_quality_ok=1 if result.data_quality_ok else 0,
                reasons_json=json.dumps(result.reasons),
                checks_json=json.dumps(result.checks),
                evaluated_at=result.evaluated_at,
            )
        except Exception as exc:
            log.warning("Promotion record could not be persisted: %s", exc)
        return result
