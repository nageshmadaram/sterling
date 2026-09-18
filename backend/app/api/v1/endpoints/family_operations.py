"""The one endpoint the operator dashboard reads.

A screen that had to call ten endpoints and compose release truth in the browser
would be a screen that can disagree with `sterlingctl`. There is exactly one
payload, assembled server-side by services that already own the rules, and the
browser renders it.

Nothing here mutates trading state, and nothing here calls the broker: external
positions come from the recorded observation, so a dashboard on a ten-second
timer can never put a network call between a strategy and its order.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.core.auth import UserContext, get_current_user

log = logging.getLogger(__name__)

router = APIRouter(tags=["family-operations"])

TriState = Literal["PASS", "FAIL", "UNKNOWN"]


class GateResult(BaseModel):
    status: TriState = "UNKNOWN"
    detail: str = ""
    observed_at: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class AdmissionBlocker(BaseModel):
    code: str
    severity: Literal["BLOCK", "WARN"] = "BLOCK"
    message: str = ""
    source: str = ""


class ReleaseStatus(BaseModel):
    runtime_sha: str | None = None
    release_tag: str | None = None
    manifest_frozen: bool | None = None
    manifest_state: TriState = "UNKNOWN"
    source_identity: TriState = "UNKNOWN"
    remote_ci: TriState = "UNKNOWN"
    #: None means the count could not be established — not zero contexts passing.
    ci_passed: int | None = None
    ci_required: int = 9
    live_acceptance: TriState = "UNKNOWN"
    reconnect: TriState = "UNKNOWN"
    persistence: TriState = "UNKNOWN"
    test_suites: TriState = "UNKNOWN"


class SafetyStatus(BaseModel):
    safety_state: str = "UNKNOWN"
    execution_control_state: str = "UNKNOWN"
    #: None is UNKNOWN and the screen must treat it as blocked.
    new_risk_allowed: bool | None = None
    family_stop_engaged: bool | None = None
    live_execution_enabled: bool = False
    blockers: list[AdmissionBlocker] = Field(default_factory=list)


class ManagedPosition(BaseModel):
    instrument: str
    managed_by_sterling: bool = True


class ExternalPosition(BaseModel):
    source: Literal["BROKER_EXTERNAL"] = "BROKER_EXTERNAL"
    managed_by_sterling: Literal[False] = False
    account: str = ""
    instrument: str = ""
    exchange: str | None = None
    product: str | None = None
    quantity: int = 0
    broker_avg_price: float | None = None
    last_price: float | None = None
    unrealised_pnl: float | None = None
    discovery_reason: str = "RECONCILIATION_UNKNOWN_POSITION"
    protection_known: bool = False
    sterling_intent: None = None
    sterling_fill: None = None
    observed_at: str = ""


class BrokerStatus(BaseModel):
    account_id: str | None = None
    binding_id: str | None = None
    connected: bool | None = None
    is_paper: bool | None = None
    broker_flatness: TriState = "UNKNOWN"
    #: False means the broker could not be read. An empty position list with
    #: this False must never render as "flat".
    broker_state_readable: bool | None = None
    broker_state_detail: str = ""
    broker_observed_at: str | None = None
    managed_positions: list[ManagedPosition] = Field(default_factory=list)
    external_positions: list[ExternalPosition] = Field(default_factory=list)
    unresolved_intents: int | None = None
    open_exposure: TriState = "UNKNOWN"


class DeploymentStatus(BaseModel):
    static_egress: TriState = "UNKNOWN"
    expected_egress_ip: str | None = None
    observed_egress_ip: str | None = None
    deployment_identity: TriState = "UNKNOWN"
    lake_mount: TriState = "UNKNOWN"
    network_path: TriState = "UNKNOWN"
    market_freshness: TriState = "UNKNOWN"
    last_tick_at: str | None = None
    backend_reachable: bool = True


class CertificationStatus(BaseModel):
    gates: dict[str, GateResult] = Field(default_factory=dict)
    release_ready: bool | None = None


class FailureDrillStatus(BaseModel):
    id: str
    title: str = ""
    required_outcome: str = ""
    status: TriState = "UNKNOWN"
    observed_by: str | None = None
    started_at: str | None = None
    observed: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class DrillsStatus(BaseModel):
    required: int = 12
    passed: int = 0
    failed: int = 0
    unknown: int = 12
    drills: list[FailureDrillStatus] = Field(default_factory=list)


class SecurityStatus(BaseModel):
    environment: Literal["development", "production", "unknown"] = "unknown"
    production_security: TriState = "UNKNOWN"
    stored_secret_count: int | None = None
    dev_fallback_in_use: bool | None = None
    last_migration_at: str | None = None


class RegimeStatus(BaseModel):
    regime: Literal["PAPER", "SHADOW", "BROKER"]
    sessions: int | None = None
    trades: int | None = None
    expectancy: float | None = None
    promotion_status: Literal["PASS", "FAIL", "UNKNOWN", "NOT_ELIGIBLE"] = "UNKNOWN"


class EvidenceStatus(BaseModel):
    authoritative_start: TriState = "UNKNOWN"
    sessions: int | None = None
    trades: int | None = None
    unresolved_exposure: int | None = None
    identity_drift: TriState = "UNKNOWN"
    release_tag: str | None = None
    runtime_sha: str | None = None
    regimes: list[RegimeStatus] = Field(default_factory=list)


class LaneStatus(BaseModel):
    lane_key: str
    family: str
    mode: str
    lifecycle_state: str
    identity_verdict: TriState = "UNKNOWN"
    shadow_verdict: TriState = "UNKNOWN"
    economic_verdict: TriState = "UNKNOWN"
    live_minimum_eligible: bool | None = None
    blockers: list[str] = Field(default_factory=list)
    execution_vehicle: str | None = None
    runtime_sha: str | None = None
    rule_hash: str | None = None


class OperatorActions(BaseModel):
    stop_available: bool = True
    resume_available: bool = False
    stop_engaged: bool | None = None


class FamilyOperationsV2(BaseModel):
    schema_version: int = 1
    generated_at: str
    runtime_sha: str | None = None
    release_tag: str | None = None
    environment: str = "unknown"
    system_status: str = "UNKNOWN"
    production_mode: str = "UNKNOWN"
    new_risk_allowed: bool | None = None
    new_risk_blockers: list[str] = Field(default_factory=list)
    next_safe_action: str = ""
    release: ReleaseStatus = Field(default_factory=ReleaseStatus)
    safety: SafetyStatus = Field(default_factory=SafetyStatus)
    broker: BrokerStatus = Field(default_factory=BrokerStatus)
    deployment: DeploymentStatus = Field(default_factory=DeploymentStatus)
    certification: CertificationStatus = Field(default_factory=CertificationStatus)
    drills: DrillsStatus = Field(default_factory=DrillsStatus)
    security: SecurityStatus = Field(default_factory=SecurityStatus)
    evidence: EvidenceStatus = Field(default_factory=EvidenceStatus)
    lanes: list[LaneStatus] = Field(default_factory=list)
    operator: OperatorActions = Field(default_factory=OperatorActions)


@router.get("/operations/family", response_model=FamilyOperationsV2)
async def family_operations(
    response: Response,
    _user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Every operator-facing fact, composed server-side.

    `no-store`: a cached safety screen is a screen that can show a green state
    the system left behind minutes ago.
    """
    from app.services.family_operations_v2 import family_operations_v2

    response.headers["Cache-Control"] = "no-store"
    return family_operations_v2()


class EgressStatusResponse(BaseModel):
    """What address this deployment answers from, for the broker's allowlist."""

    expected_ip: str | None = None
    observed_ip: str | None = None
    observed_at: str | None = None
    router_generation: str | None = None
    #: True, False, or None when it could not be determined.
    verified: bool | None = None
    reason: str = ""
    #: What changed, when the observed address moved.
    changed_from: str | None = None
    #: How to record an observation, shown verbatim to the operator.
    record_command: str = "sterlingctl egress record <ip>"


@router.get("/operations/egress", response_model=EgressStatusResponse)
async def operations_egress(
    _user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The deployment's outbound address, as recorded — never probed.

    Sterling does not ask the internet what its own address is: a safety check
    that depends on a third party is a safety check that fails when that third
    party does, and the answer would say nothing about what the broker sees
    anyway. The deployment records what it observed; this reads it.
    """
    from app.core.deployment_identity import (
        DeploymentIdentityStore,
        verify_deployment_identity,
    )

    verdict = verify_deployment_identity()
    latest = DeploymentIdentityStore().latest()

    return {
        "expected_ip": verdict.expected_ip or None,
        "observed_ip": latest.outbound_ip if latest else None,
        "observed_at": latest.observed_at if latest else None,
        "router_generation": (latest.router_generation or None) if latest else None,
        "verified": verdict.verified,
        "reason": verdict.reason,
        "changed_from": verdict.changed_from or None,
        "record_command": "sterlingctl egress record <ip>",
    }
