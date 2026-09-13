"""Research opportunity object + recovered entry-gate conjunction.

Canonical structure (POSITION INITIATION §6):

    DataValid AND ModelValid AND EconomicDecisionValid
    AND ExecutionValid AND RiskValid AND CapitalValid AND SessionValid

Numeric F-102 / F-103 thresholds are not recovered. Those IDs stay SPEC_GAP.
Gates without a recovered test are recorded as SPEC_GAP, not invented as pass.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .economic import EconomicAssessment, evaluate_economics
from .edge import EdgeAssessment
from .f101 import F101Result
from .feature_engine import FeatureSnapshot, FeatureStatus
from .features_f101 import F101_FEATURE_NAMES
from .research_references import ExecutableReference


@dataclass(frozen=True)
class GateResult:
    name: str
    status: str
    reason: str


@dataclass(frozen=True)
class ResearchOpportunity:
    opportunity_id: str
    decision_time: str
    snapshot: FeatureSnapshot
    f101: F101Result
    side: str
    reference: ExecutableReference
    gates: Mapping[str, GateResult]
    recovered_conjunction: bool
    formula_note: str = "not_F102_not_F103"


def _gate(name: str, ok: bool, reason: str) -> GateResult:
    return GateResult(name, "PASS" if ok else "FAIL", reason)


def _gap(name: str, reason: str) -> GateResult:
    return GateResult(name, "SPEC_GAP", reason)


def evaluate_research_opportunity(
    *,
    opportunity_id: str,
    snapshot: FeatureSnapshot,
    f101: F101Result,
    side: str,
    reference: ExecutableReference,
    expected_gross_value: float | None = None,
    execution_cost: float | None = None,
    risk_authorized: bool | None = None,
    capital_quantity: int | None = None,
    session_valid: bool | None = None,
) -> ResearchOpportunity:
    data_ok = all(snapshot.statuses.get(name) is FeatureStatus.VALID for name in F101_FEATURE_NAMES)
    model_ok = f101.status is FeatureStatus.VALID and f101.score is not None
    exec_ok = reference.status is FeatureStatus.VALID and reference.price is not None

    gates: dict[str, GateResult] = {
        "DataValid": _gate("DataValid", data_ok, "A206 features VALID" if data_ok else "A206 feature missing"),
        "ModelValid": _gate("ModelValid", model_ok, "F-101 VALID" if model_ok else "F-101 not VALID"),
        "ExecutionValid": _gate(
            "ExecutionValid",
            exec_ok,
            f"{reference.formula_id} VALID" if exec_ok else "executable reference missing",
        ),
        "EconomicDecisionValid": _gap("EconomicDecisionValid", "F-102/F-004 gross value not recovered"),
        "RiskValid": _gap("RiskValid", "F-103/F-106 risk schedule not recovered"),
        "CapitalValid": _gap("CapitalValid", "F-108 capital rule not required unless sizing supplied"),
        "SessionValid": _gap("SessionValid", "session eligibility formula not recovered"),
    }

    if expected_gross_value is not None and execution_cost is not None:
        edge = EdgeAssessment(
            opportunity_id=opportunity_id,
            score=f101.score or 0.0,
            confidence=None,
            expected_gross_value=expected_gross_value,
            formula_id="F-004",
            formula_version="1.0",
            inputs={},
        )
        economics: EconomicAssessment = evaluate_economics(edge, execution_cost=execution_cost)
        gates["EconomicDecisionValid"] = _gate(
            "EconomicDecisionValid",
            economics.eligible,
            economics.reason or "F-004 eligible",
        )

    if risk_authorized is not None:
        gates["RiskValid"] = _gate(
            "RiskValid",
            risk_authorized,
            "explicit research authorization" if risk_authorized else "not authorized",
        )
    if capital_quantity is not None:
        gates["CapitalValid"] = _gate(
            "CapitalValid",
            capital_quantity > 0,
            "quantity>0" if capital_quantity > 0 else "quantity<=0",
        )
    if session_valid is None and snapshot and getattr(snapshot, "decision_time", None):
        try:
            dt = snapshot.decision_time
            if isinstance(dt, str):
                from datetime import datetime
                dt = datetime.fromisoformat(dt)
            if hasattr(dt, "weekday"):
                is_weekday = dt.weekday() < 5
                time_mins = dt.hour * 60 + dt.minute
                in_hours = (9 * 60 + 15) <= time_mins <= (15 * 60 + 30)
                session_valid = is_weekday and in_hours
        except Exception:
            pass

    if session_valid is not None:
        gates["SessionValid"] = _gate(
            "SessionValid",
            session_valid,
            "operational NSE 09:15-15:30 IST weekday" if session_valid else "outside operational session",
        )

    recovered = all(gates[name].status == "PASS" for name in ("DataValid", "ModelValid", "ExecutionValid"))
    return ResearchOpportunity(
        opportunity_id=opportunity_id,
        decision_time=snapshot.decision_time,
        snapshot=snapshot,
        f101=f101,
        side=side,
        reference=reference,
        gates=gates,
        recovered_conjunction=recovered,
    )
