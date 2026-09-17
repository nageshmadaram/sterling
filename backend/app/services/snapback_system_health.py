"""Compose Sterling's one top-level health status from the live subsystems.

Each component is asked separately and every failure to ask is itself a
component failure, so "the broker check threw" and "the broker is fine" can
never produce the same status. app.core.health does the combining; this module
only supplies the readings.
"""
from __future__ import annotations

from typing import Any, Dict

from app.core.health import ComponentHealth, HealthReport, SystemHealth, compose_health
from app.core.logging import get_logger

log = get_logger(__name__)


def _safe_mode_component() -> ComponentHealth:
    from app.services.snapback_capacity import _configured_safe_mode_state

    state = _configured_safe_mode_state()
    if state.active:
        return ComponentHealth(
            "safe_mode",
            SystemHealth.SAFE_MODE,
            f"triggers: {', '.join(state.triggers) or 'unspecified'}",
        )
    return ComponentHealth("safe_mode", SystemHealth.NORMAL, "not engaged")


def _evidence_component() -> ComponentHealth:
    from app.services.snapback_preflight import check_database

    ok, detail = check_database()
    return ComponentHealth(
        "evidence",
        SystemHealth.NORMAL if ok else SystemHealth.EVIDENCE_ERROR,
        str(detail),
    )


def _reconciliation_component() -> ComponentHealth:
    from app.services.snapback_preflight import run_preflight

    result = run_preflight()
    recovery = [c.code for c in result.required_failures]
    if recovery:
        return ComponentHealth(
            "reconciliation",
            SystemHealth.RECOVERY_REQUIRED,
            "preflight failures: " + ", ".join(sorted(recovery)),
        )
    return ComponentHealth("reconciliation", SystemHealth.NORMAL, "preflight clean")


def _market_data_component() -> ComponentHealth:
    from app.services.kite_engine.market_hours import session_phase

    phase = session_phase()
    if phase == "calendar_unknown":
        return ComponentHealth(
            "market_data", SystemHealth.DATA_ERROR, "session calendar cannot answer"
        )
    # A closed market is not a data error; it is a closed market.
    return ComponentHealth("market_data", SystemHealth.NORMAL, f"session {phase}")


def _broker_component() -> ComponentHealth:
    from app.services.snapback_preflight import run_preflight

    result = run_preflight()
    codes = {c.code for c in result.required_failures}
    broker_codes = codes & {"kite_session", "broker", "family_account"}
    if broker_codes:
        return ComponentHealth(
            "broker", SystemHealth.BROKER_ERROR, ", ".join(sorted(broker_codes))
        )
    return ComponentHealth("broker", SystemHealth.NORMAL, "session usable")


_COMPONENTS = {
    "safe_mode": _safe_mode_component,
    "evidence": _evidence_component,
    "reconciliation": _reconciliation_component,
    "market_data": _market_data_component,
    "broker": _broker_component,
}


def system_health() -> HealthReport:
    """Read every component and compose the top-level status.

    A component that raises is recorded as failed rather than omitted: an
    exception means we do not know, and not knowing must not read as healthy.
    """
    readings: Dict[str, ComponentHealth] = {}
    for name, probe in _COMPONENTS.items():
        try:
            readings[name] = probe()
        except Exception as exc:  # noqa: BLE001 - the point is to catch all
            log.warning("health probe %s failed: %s", name, exc)
            readings[name] = ComponentHealth(
                name,
                _failure_status(name),
                f"probe raised {type(exc).__name__}: {exc}",
            )
    return compose_health(readings)


def _failure_status(name: str) -> SystemHealth:
    return {
        "broker": SystemHealth.BROKER_ERROR,
        "market_data": SystemHealth.DATA_ERROR,
        "evidence": SystemHealth.EVIDENCE_ERROR,
        "reconciliation": SystemHealth.RECOVERY_REQUIRED,
        "safe_mode": SystemHealth.SAFE_MODE,
    }[name]


def system_health_dict() -> Dict[str, Any]:
    return system_health().as_dict()
