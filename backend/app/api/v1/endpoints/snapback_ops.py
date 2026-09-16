"""Operational (non-strategy) endpoints for the frozen Snapback prospective runtime."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.services.snapback_family_ops import (
    get_family_evidence_verdict,
    new_trades_halted,
    set_new_trades_halted,
)
from app.services.snapback_health import get_prospective_health

log = logging.getLogger(__name__)

router = APIRouter(tags=["snapback-ops"])


@router.get("/snapback/prospective/health")
async def prospective_health() -> dict:
    """Return server-truth health of the prospective paper runtime.

    A failing probe is reported as HALTED with HTTP 503; it is never converted
    into a green response.
    """
    try:
        return get_prospective_health()
    except Exception as exc:
        log.exception("Snapback prospective health probe failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={
                "status": "HALTED",
                "healthy": False,
                "unresolved_errors": ["health_probe_failed"],
                "error": str(exc),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
        )


@router.get("/snapback/family/operations")
async def family_operations() -> dict:
    """The Family Operations surface: health, mode, evidence, exposure and risk.

    Deliberately carries no strategy controls, and reports unknown economics as null
    rather than as zero.
    """
    try:
        health = get_prospective_health()
    except Exception as exc:
        log.exception("Family operations: health probe failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={
                "system_status": "HALTED",
                "mode": "HALTED",
                "strategy": "Snapback 1.0.5",
                "evidence": "INCONCLUSIVE",
                "live_blocked": True,
                "error": str(exc),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    try:
        evidence = get_family_evidence_verdict()
    except Exception as exc:
        log.warning("Family operations: evidence verdict unavailable: %s", exc)
        evidence = {"verdict": "INCONCLUSIVE", "net_pnl": None}

    verdict = str(evidence.get("verdict") or "INCONCLUSIVE")
    halted = new_trades_halted()
    status = str(health.get("status") or "HALTED")

    return {
        "system_status": "HALTED" if halted else status,
        "mode": "HALTED" if halted else str(health.get("mode") or "PAPER"),
        "strategy": "Snapback 1.0.5",
        "runtime_sha": health.get("runtime_sha"),
        "strategy_manifest": health.get("strategy_manifest"),
        "evidence": verdict,
        "evidence_missing_requirements": evidence.get("missing_requirements", []),
        "broker_connected": bool(health.get("broker_connected")),
        "market_data_fresh": bool(health.get("market_data_fresh")),
        "runner_alive": bool(health.get("runner_alive")),
        "backup_ok": bool(health.get("backup_ok", True)),
        "alert_transport_configured": bool(health.get("alert_transport_configured", False)),
        "last_report": health.get("last_eod_cycle"),
        # Unknown economics stay unknown; never a fabricated zero.
        "allocated_capital": None,
        "net_pnl": evidence.get("net_pnl"),
        "current_exposure": int(health.get("open_positions") or 0),
        "open_positions": int(health.get("open_positions") or 0),
        "exit_pending": int(health.get("exit_pending") or 0),
        "drawdown_pct": None,
        "new_trades_halted": halted,
        # LIVE stays blocked until the authoritative gate says PASSED.
        "live_blocked": verdict != "PASSED" or halted,
        "unresolved_errors": health.get("unresolved_errors", []),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/snapback/family/stop-new-trades")
async def family_stop_new_trades(reason: str = "family stop switch") -> dict:
    """STOP ALL NEW TRADES. Halts new entries; exits and reconciliation continue."""
    set_new_trades_halted(True, reason=reason)
    return {
        "new_trades_halted": True,
        "exits_still_processed": True,
        "reconciliation_still_processed": True,
        "state_preserved": True,
        "reason": reason,
        "changed_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/snapback/family/resume-new-trades")
async def family_resume_new_trades(reason: str = "family resume") -> dict:
    """Release the stop switch. Live entries still require the authoritative gate."""
    set_new_trades_halted(False, reason=reason)
    return {
        "new_trades_halted": False,
        "reason": reason,
        "changed_at": datetime.now(timezone.utc).isoformat(),
    }
