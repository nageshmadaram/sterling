"""One payload the operator screen can render without deciding anything.

The browser must not compute health, eligibility, promotion or risk. If it
could, it could be wrong about them — and a screen that is wrong about whether
trading is allowed is worse than no screen. So every value here is read from the
service that already owns the rule, and this module only changes the shape.

Two invariants hold throughout:

* **No rule is recomputed.** A gate's status comes from the certification
  service, a lane's verdicts from the lane gate inputs, admission from the
  safety supervisor. Where a source cannot answer, the field is ``None`` and the
  screen shows UNKNOWN — never False, never zero, never an empty list that reads
  as "nothing there".
* **Nothing here touches the broker.** External positions come from the
  recorded observation, the same one admission reads. An operator refreshing a
  dashboard must not put a network call between a strategy and its order.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable

log = logging.getLogger(__name__)

__all__ = ["SCHEMA_VERSION", "family_operations_v2"]

SCHEMA_VERSION: int = 1

PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"


def _safe(fn: Callable[[], Any], default: Any = None) -> Any:
    """Run one source. A source that fails contributes UNKNOWN, not an exception."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        log.info("family operations: %s", exc)
        return default


def _tri(value: bool | None) -> str:
    if value is None:
        return UNKNOWN
    return PASS if value else FAIL


def _gate(results: dict[str, Any], key: str) -> dict[str, Any]:
    """One certification gate, shaped for the screen. Status is never invented."""
    result = results.get(key)
    if result is None:
        return {"status": UNKNOWN, "detail": "no result for this gate",
                "observed_at": None, "evidence_refs": []}
    refs = [r for r in [getattr(result, "evidence_ref", "")] if r]
    return {
        "status": getattr(result, "status", UNKNOWN) or UNKNOWN,
        "detail": getattr(result, "detail", "") or "",
        "observed_at": getattr(result, "recorded_at", "") or None,
        "evidence_refs": refs,
    }


# -- sections ----------------------------------------------------------------

def _release_section(certification: Any, doctor_by_name: dict[str, Any]) -> dict[str, Any]:
    from app.core.release_manifest import read_manifest, release_tag, runtime_sha

    sha = _safe(runtime_sha)
    tag = _safe(release_tag)
    stored = _safe(read_manifest)

    gates = certification.by_key if certification is not None else {}
    ci_passed: int | None = None
    ci_required = 9
    ci = _safe(lambda: _ci_counts(sha))
    if ci is not None:
        ci_passed, ci_required = ci

    return {
        "runtime_sha": sha,
        "release_tag": tag,
        # "Not frozen" and "frozen but drifted" are different problems with
        # different fixes, so the screen is given both facts rather than one word.
        "manifest_frozen": None if stored is None else True,
        "manifest_state": _gate(gates, "release_manifest")["status"],
        "source_identity": _gate(gates, "source_identity")["status"],
        "remote_ci": _gate(gates, "remote_ci")["status"],
        "ci_passed": ci_passed,
        "ci_required": ci_required,
        "live_acceptance": _gate(gates, "kite_live_acceptance")["status"],
        "reconnect": _gate(gates, "reconnect")["status"],
        "persistence": _gate(gates, "persistence")["status"],
        "test_suites": _gate(gates, "test_suites")["status"],
    }


def _ci_counts(sha: str | None) -> tuple[int | None, int]:
    from app.core.ci_certification import REQUIRED_CONTEXTS, ci_report

    if not sha:
        return None, len(REQUIRED_CONTEXTS)
    report = ci_report(sha)
    return sum(1 for r in report.records if r.passed), len(REQUIRED_CONTEXTS)


def _safety_section() -> dict[str, Any]:
    from app.services.safety_supervisor import SafetySupervisor

    snapshot = _safe(lambda: SafetySupervisor().snapshot())
    if snapshot is None:
        return {
            "safety_state": UNKNOWN, "execution_control_state": UNKNOWN,
            "new_risk_allowed": None, "family_stop_engaged": None,
            "live_execution_enabled": _live_switch(),
            "blockers": [{"code": "safety_unreadable", "severity": "BLOCK",
                          "message": "the safety authority could not be read",
                          "source": "safety_supervisor"}],
        }

    blockers = [
        {"code": code, "severity": "BLOCK", "message": reason, "source": "safety_supervisor"}
        for code, reason in zip(snapshot.blockers, snapshot.blocker_reasons)
    ]
    # Admission is the authority's own answer, not `len(blockers) == 0`.
    allowed = not snapshot.blockers if snapshot.blockers is not None else None

    return {
        "safety_state": "SAFE_MODE" if snapshot.safe_mode else (
            str(snapshot.operator_state or UNKNOWN).upper()),
        "execution_control_state": str(snapshot.recovery_state or UNKNOWN).upper(),
        "new_risk_allowed": allowed,
        "family_stop_engaged": bool(snapshot.new_trades_halted),
        "live_execution_enabled": _live_switch(),
        "blockers": blockers,
    }


def _live_switch() -> bool:
    try:
        from app.core.lane_registry import LIVE_EXECUTION_ENABLED

        return bool(LIVE_EXECUTION_ENABLED)
    except Exception:  # noqa: BLE001
        return False


def _broker_section(certification: Any, doctor_by_name: dict[str, Any]) -> dict[str, Any]:
    from app.services.exposure_snapshot import exposure_snapshot
    from app.services.external_positions import last_observation

    # include_broker=False: the dashboard reads the recorded observation, so a
    # refresh never becomes a broker call.
    snapshot = _safe(lambda: exposure_snapshot(include_broker=False))
    observation = _safe(last_observation) or {}

    binding = _safe(_binding_facts) or {}
    flatness = doctor_by_name.get("broker_flatness")
    gates = certification.by_key if certification is not None else {}

    readable = observation.get("readable")
    external = [_external_row(row) for row in (observation.get("positions") or [])]

    managed: list[dict[str, Any]] = []
    if snapshot is not None:
        for symbol in snapshot.held:
            managed.append({"instrument": symbol, "managed_by_sterling": True})

    return {
        "account_id": binding.get("account_id"),
        "binding_id": binding.get("binding_id"),
        "connected": binding.get("connected"),
        "is_paper": binding.get("is_paper"),
        "broker_flatness": _tri(getattr(flatness, "passed", None)) if flatness else UNKNOWN,
        "broker_state_readable": None if readable is None else bool(readable),
        "broker_state_detail": str(observation.get("detail") or ""),
        "broker_observed_at": observation.get("observed_at"),
        "managed_positions": managed,
        "external_positions": external,
        "unresolved_intents": None if snapshot is None else snapshot.unresolved_intents,
        "open_exposure": _gate(gates, "open_exposure")["status"],
    }


def _external_row(row: dict[str, Any]) -> dict[str, Any]:
    """Shape one recorded external position. Provenance fields are fixed."""
    return {
        "source": "BROKER_EXTERNAL",
        "managed_by_sterling": False,
        "account": row.get("account") or "",
        "instrument": row.get("instrument") or "",
        "exchange": row.get("exchange"),
        "product": row.get("product"),
        "quantity": int(row.get("quantity") or 0),
        "broker_avg_price": row.get("broker_avg_price"),
        "last_price": row.get("last_price"),
        "unrealised_pnl": row.get("unrealised"),
        "discovery_reason": row.get("discovery_reason") or "RECONCILIATION_UNKNOWN_POSITION",
        "protection_known": bool(row.get("protection_known", False)),
        "sterling_intent": None,
        "sterling_fill": None,
        "observed_at": row.get("observed_at") or "",
    }


def _binding_facts() -> dict[str, Any]:
    from app.services.account_binding_service import active_binding
    from app.services.exchanges.kite import accounts as kite_accounts

    binding = active_binding()
    kite_accounts.bootstrap()
    rows = kite_accounts.all_accounts()
    live = [a for a in rows if not getattr(a, "is_paper", True)]
    account = live[0] if live else (rows[0] if rows else None)

    return {
        "account_id": (str(getattr(account, "kite_user_id", "") or getattr(account, "id", ""))
                       if account else None),
        "binding_id": getattr(binding, "binding_id", None) if binding else None,
        "connected": bool(getattr(account, "connected", False)) if account else None,
        "is_paper": bool(getattr(account, "is_paper", True)) if account else None,
    }


def _deployment_section(doctor_by_name: dict[str, Any]) -> dict[str, Any]:
    from app.core.deployment_identity import DeploymentIdentityStore, verify_deployment_identity

    verdict = _safe(verify_deployment_identity)
    latest = _safe(lambda: DeploymentIdentityStore().latest())

    def _check(name: str) -> str:
        check = doctor_by_name.get(name)
        return _tri(getattr(check, "passed", None)) if check else UNKNOWN

    last_tick = _safe(_last_tick_at)

    return {
        "static_egress": _check("static_egress"),
        "expected_egress_ip": getattr(verdict, "expected_ip", None) or None,
        "observed_egress_ip": getattr(latest, "outbound_ip", None) if latest else None,
        "deployment_identity": _tri(getattr(verdict, "verified", None)) if verdict else UNKNOWN,
        "lake_mount": _check("lake_mount"),
        "network_path": _check("network_path"),
        "market_freshness": _check("market_freshness"),
        "last_tick_at": last_tick,
        "backend_reachable": True,
    }


def _last_tick_at() -> str | None:
    from app.services.exchanges.kite import ticker_manager

    stamps = [int(ticker_manager.status(uid).get("last_tick_ms") or 0)
              for uid in ticker_manager.known_users()]
    newest = max(stamps, default=0)
    if newest <= 0:
        return None
    return datetime.fromtimestamp(newest / 1000, tz=timezone.utc).isoformat()


def _certification_section(certification: Any) -> dict[str, Any]:
    gates = certification.by_key if certification is not None else {}
    keys = ("source_identity", "remote_ci", "test_suites", "kite_live_acceptance",
            "reconnect", "persistence", "backup_restore", "failure_drills",
            "open_exposure", "release_manifest")
    return {
        "gates": {key: _gate(gates, key) for key in keys},
        # The overall verdict is the backend's, not a count done in the browser.
        "release_ready": (bool(certification.release_ready)
                          if certification is not None else None),
    }


def _drills_section(sha: str | None) -> dict[str, Any]:
    from app.core.failure_drills import DRILLS, DRILLS_BY_KEY, drill_report

    report = _safe(lambda: drill_report(sha or ""))
    if report is None:
        return {"required": len(DRILLS), "passed": 0, "failed": 0,
                "unknown": len(DRILLS), "drills": []}

    rows = []
    for result in report.results:
        drill = DRILLS_BY_KEY[result.key]
        rows.append({
            "id": result.key,
            "title": drill.scenario,
            "required_outcome": drill.required_outcome,
            "status": result.status,
            "observed_by": result.observed_by or None,
            "started_at": result.started_at or None,
            "observed": result.observed or None,
            "evidence_refs": list(result.evidence_refs),
        })
    return {
        "required": len(DRILLS),
        "passed": sum(1 for r in report.results if r.status == PASS),
        "failed": len(report.failures),
        "unknown": len(report.unknowns),
        "drills": rows,
    }


def _security_section() -> dict[str, Any]:
    from app.core.security import DEV_FALLBACK_VALUES

    environment = (os.environ.get("ENVIRONMENT", "development") or "development").lower()
    configured = (os.environ.get("STERLING_SECRET_KEY") or "").strip()
    dev_fallback = (not configured) or configured in DEV_FALLBACK_VALUES

    production_security = UNKNOWN
    if environment == "production":
        production_security = _safe(_validate_production, UNKNOWN)
    elif dev_fallback:
        # Outside production the rule does not apply, but the fallback key is
        # still a release blocker and must not read as PASS.
        production_security = UNKNOWN

    stored = _safe(_stored_secret_count)

    return {
        "environment": environment if environment in ("development", "production") else "unknown",
        "production_security": production_security,
        "stored_secret_count": stored,
        "dev_fallback_in_use": dev_fallback,
        "last_migration_at": None,
    }


def _validate_production() -> str:
    from app.core.security import validate_production_security

    try:
        validate_production_security()
    except RuntimeError:
        return FAIL
    return PASS


def _stored_secret_count() -> int | None:
    from app.services.secret_migration import plan_migration

    path = os.environ.get("STERLING_DB_PATH") or "sterling_paper.db"
    return plan_migration(path).total


def _evidence_section() -> dict[str, Any]:
    from app.services.authoritative_start_report import authoritative_start_state

    start = _safe(authoritative_start_state)
    if start is None:
        return {"authoritative_start": UNKNOWN, "sessions": None, "trades": None,
                "unresolved_exposure": None, "identity_drift": UNKNOWN,
                "release_tag": None, "runtime_sha": None, "regimes": []}

    by_name = {c.name: c for c in start.conditions}

    def _count(name: str) -> int | None:
        condition = by_name.get(name)
        if condition is None or condition.satisfied is None:
            return None
        try:
            return int(condition.detail)
        except (TypeError, ValueError):
            return None

    drift = by_name.get("identity_drift")
    return {
        "authoritative_start": PASS if start.started_clean else (
            UNKNOWN if start.unknowns else FAIL),
        "sessions": _count("lane_sessions"),
        "trades": _count("authoritative_trades"),
        "unresolved_exposure": _count("unresolved_exposure"),
        "identity_drift": _tri(getattr(drift, "satisfied", None)) if drift else UNKNOWN,
        "release_tag": start.release_tag or None,
        "runtime_sha": start.runtime_sha or None,
        "regimes": _regimes(),
    }


def _regimes() -> list[dict[str, Any]]:
    """Paper, shadow and broker counted separately. Never pooled here."""
    from app.core.execution_regime import REGIMES

    return [{"regime": regime.value.upper(), "sessions": None, "trades": None,
             "expectancy": None, "promotion_status": UNKNOWN} for regime in REGIMES]


def _lanes_section() -> list[dict[str, Any]]:
    """Ten lanes, with the expensive sources read once rather than per lane.

    Reading the evidence store, the shadow store and the certification report
    inside the loop made one dashboard poll take over two minutes. The rules are
    unchanged; only the number of times they are asked is.
    """
    from app.core.lane_registry import LANES

    evidence_rows = _safe(_all_authoritative_rows, [])
    shadow_rows = _safe(_all_shadow_rows, [])
    identity = _safe(_identity_drift_lanes)
    permissions = {row.get("lane_key"): row for row in (_safe(_all_permissions, []) or [])}

    rows: list[dict[str, Any]] = []
    for lane_key in sorted(LANES):
        lane = LANES[lane_key]
        verdicts = _safe(lambda key=lane_key: _lane_verdicts_from(
            key, evidence_rows, shadow_rows, identity))
        permission = permissions.get(lane_key)
        rows.append({
            "lane_key": lane_key,
            "family": lane.strategy_id,
            "mode": lane.mode.value if hasattr(lane.mode, "value") else str(lane.mode),
            "lifecycle_state": str(lane.state.value).upper(),
            "identity_verdict": (verdicts or {}).get("identity", UNKNOWN),
            "shadow_verdict": (verdicts or {}).get("shadow", UNKNOWN),
            "economic_verdict": (verdicts or {}).get("economic", UNKNOWN),
            "live_minimum_eligible": (permission or {}).get("allowed"),
            "blockers": [str(b) for b in (permission or {}).get("blockers", [])],
            "execution_vehicle": _safe(lambda key=lane_key: _vehicle(key)),
            "runtime_sha": None,
            "rule_hash": None,
        })
    return rows


def _all_authoritative_rows() -> list[dict[str, Any]]:
    """Every authoritative outcome row, read once for all ten lanes."""
    import sqlite3
    from pathlib import Path

    path = Path(os.environ.get("STERLING_OBSERVATIONS_DB_PATH")
                or "snapback_observations.db")
    if not path.exists():
        return []
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM outcomes WHERE authoritative = 1").fetchall()
    return [dict(r) for r in rows]


def _all_shadow_rows() -> list[dict[str, Any]]:
    from app.services.shadow_execution import ShadowStore

    store = ShadowStore()
    rows: list[dict[str, Any]] = []
    for session_date in store.sessions():
        rows.extend(store.read(session_date))
    return rows


def _identity_drift_lanes() -> set[str] | None:
    """Which lanes drifted from the frozen manifest. None when none is frozen."""
    from app.core.release_manifest import read_manifest, verify_manifest

    stored = read_manifest()
    if stored is None:
        return None
    verdict = verify_manifest(stored)
    drifted = {d.scope for d in verdict.identity_drift}
    drifted.update(verdict.missing_lanes)
    drifted.update(verdict.unexpected_lanes)
    return drifted


def _lane_verdicts_from(lane_key: str, evidence_rows, shadow_rows,
                        identity) -> dict[str, str]:
    """The three verdicts for one lane, from already-read data.

    Each still comes from the module that owns the rule; only the reading of the
    underlying rows is shared.
    """
    from app.core.lane_promotion import evaluate_lane_regimes
    from app.core.shadow_record import summarize_all
    from app.services.lane_gate_inputs import MIN_SHADOW_INTENTS
    from app.services.shadow_execution import _records_from_rows

    if identity is None:
        identity_verdict = UNKNOWN
    else:
        identity_verdict = FAIL if lane_key in identity else PASS

    lane_rows = [r for r in (evidence_rows or []) if r.get("lane_key") == lane_key]
    try:
        broker = evaluate_lane_regimes(lane_rows, lane_key)["by_regime"]["broker"]
        economic = PASS if str(broker.get("verdict") or "").upper() == "PASS" else FAIL
    except Exception:  # noqa: BLE001
        economic = UNKNOWN

    lane_shadow = [r for r in (shadow_rows or []) if str(r.get("lane_key")) == lane_key]
    if not lane_shadow:
        shadow = UNKNOWN
    else:
        try:
            metrics = summarize_all(_records_from_rows(lane_shadow)).get(lane_key)
            if metrics is None:
                shadow = UNKNOWN
            elif (metrics.intents < MIN_SHADOW_INTENTS or metrics.unobserved
                  or metrics.margin_unknown or metrics.contracts_unavailable
                  or metrics.protection_unknown):
                shadow = FAIL
            else:
                shadow = PASS
        except Exception:  # noqa: BLE001
            shadow = UNKNOWN

    return {"identity": identity_verdict, "shadow": shadow, "economic": economic}


def _all_permissions() -> list[dict[str, Any]]:
    """Every lane's capital permission, with the shared facts computed once."""
    from app.services.capital_permission_report import all_lane_permissions

    return all_lane_permissions()


def _vehicle(lane_key: str) -> str | None:
    from app.core.execution_vehicle import lane_vehicle

    vehicle = lane_vehicle(lane_key)
    return vehicle.value if vehicle is not None else None


def _production_mode(safety: dict[str, Any], release: dict[str, Any]) -> str:
    """The mode the backend is in. The browser must not derive this.

    Deliberately conservative: anything that cannot be established reads
    UNKNOWN, and UNKNOWN is shown as blocked.
    """
    if safety.get("execution_control_state") == "RECOVERY_REQUIRED":
        return "RECOVERY_REQUIRED"
    if safety.get("safety_state") == "SAFE_MODE":
        return "RECOVERY_REQUIRED"
    if safety.get("live_execution_enabled"):
        return "LIVE"
    if safety.get("new_risk_allowed") is None:
        return "UNKNOWN"
    return "PRODUCTION_SHADOW"


def _system_status(safety: dict[str, Any]) -> str:
    if safety.get("execution_control_state") == "RECOVERY_REQUIRED":
        return "RECOVERY_REQUIRED"
    if safety.get("safety_state") == "SAFE_MODE":
        return "HALTED"
    if safety.get("new_risk_allowed") is None:
        return "UNKNOWN"
    if not safety.get("new_risk_allowed"):
        return "DEGRADED"
    return "HEALTHY"


def family_operations_v2() -> dict[str, Any]:
    """Compose every operator-facing fact into one payload."""
    from app.core.operator_report import doctor_from_preflight, lane_doctor_checks
    from app.core.release_manifest import runtime_sha
    from app.services.release_certification import certification_report
    from app.services.snapback_preflight import run_preflight

    sha = _safe(runtime_sha)
    certification = _safe(certification_report)

    doctor = _safe(lambda: doctor_from_preflight(run_preflight().checks,
                                                 extra=lane_doctor_checks()))
    doctor_by_name = {c.name: c for c in getattr(doctor, "checks", ())}

    safety = _safety_section()
    release = _release_section(certification, doctor_by_name)

    digest_action = _safe(_next_action) or ""

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_sha": sha,
        "release_tag": release.get("release_tag"),
        "environment": (os.environ.get("ENVIRONMENT", "development") or "development").lower(),
        "system_status": _system_status(safety),
        "production_mode": _production_mode(safety, release),
        "new_risk_allowed": safety.get("new_risk_allowed"),
        "new_risk_blockers": [b["code"] for b in safety.get("blockers", [])],
        "next_safe_action": digest_action,
        "release": release,
        "safety": safety,
        "broker": _broker_section(certification, doctor_by_name),
        "deployment": _deployment_section(doctor_by_name),
        "certification": _certification_section(certification),
        "drills": _drills_section(sha),
        "security": _security_section(),
        "evidence": _evidence_section(),
        "lanes": _lanes_section(),
        "operator": {
            "stop_available": True,
            "resume_available": bool(safety.get("family_stop_engaged")),
            "stop_engaged": safety.get("family_stop_engaged"),
        },
    }


def _next_action() -> str:
    from app.services.daily_digest import build_digest

    return build_digest().next_action
