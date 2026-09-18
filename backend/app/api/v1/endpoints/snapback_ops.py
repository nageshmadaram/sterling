"""Operational (non-strategy) endpoints for the frozen Snapback prospective runtime."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.core.auth import UserContext, get_current_user

from app.services.snapback_family_ops import (
    get_family_evidence_verdict,
    new_trades_halted,
    set_new_trades_halted,
)
from app.services.snapback_health import get_prospective_health

log = logging.getLogger(__name__)

router = APIRouter(tags=["snapback-ops"])


@router.get("/health/live")
async def health_live() -> dict:
    """Liveness only: this process is answering. Deliberately does no I/O.

    A liveness probe that touches the database restarts the process when the
    database is slow, which is the worst possible moment to lose in-memory state.
    """
    return {"alive": True, "checked_at": datetime.now(timezone.utc).isoformat()}


@router.get("/health/ready")
async def health_ready(response: Response) -> dict:
    """Readiness: may this process produce evidence right now.

    Not ready is a normal state, not an error, so it answers 503 rather than
    raising: the supervisor must back off, not restart.
    """
    from app.services.snapback_preflight import run_preflight

    result = run_preflight()
    if not result.passed:
        response.status_code = 503
    return {"ready": result.passed, **result.as_dict()}



def _reconciliation_view() -> dict:
    """The last reconciliation result. Never run live on a page load."""
    try:
        from app.services.snapback_reconciliation import latest_reconciliation

        snapshot = latest_reconciliation()
        if snapshot is None:
            # Unknown is not clean.
            return {"clean": None, "mismatches": []}
        payload = snapshot.as_dict()
        return {"clean": payload["clean"], "mismatches": payload["mismatches"]}
    except Exception:
        return {"clean": None, "mismatches": []}


def _allocated_capital():
    """Declared evaluation capital, or None when it is not configured."""
    try:
        from app.services.snapback import get_config

        capital = float(getattr(get_config("default"), "capital_inr", 0) or 0)
        return capital or None
    except Exception:
        return None


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
async def family_operations(user: UserContext = Depends(get_current_user)) -> dict:
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
        # Unknown backup state is UNKNOWN, never a reassuring true.
        "backup_ok": health.get("backup_ok"),
        "alert_transport_configured": bool(health.get("alert_transport_configured", False)),
        "last_report": health.get("last_eod_cycle"),
        # Unknown economics stay unknown; never a fabricated zero.
        "allocated_capital": _allocated_capital(),
        # Expectancy per trade and cumulative P&L are different numbers; do not
        # label one as the other. Exposure is rupees, not a position count.
        "cumulative_net_pnl": evidence.get("cumulative_net_pnl"),
        "mean_net_pnl_per_trade": evidence.get("net_pnl"),
        "current_exposure_inr": evidence.get("current_exposure_inr"),
        "open_positions_count": int(health.get("open_positions") or 0),
        "observed_sessions": evidence.get("total_sessions"),
        "completed_trades": evidence.get("completed_trades"),
        "build_sha": health.get("build_sha"),
        "exit_pending": int(health.get("exit_pending") or 0),
        "drawdown_pct": None,
        "new_trades_halted": halted,
        # LIVE stays blocked until the authoritative gate says PASSED.
        "live_blocked": verdict != "PASSED" or halted,
        "reconciliation_clean": _reconciliation_view().get("clean"),
        "reconciliation_mismatches": _reconciliation_view().get("mismatches", []),
        "unresolved_errors": health.get("unresolved_errors", []),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/snapback/family/stop-new-trades")
async def family_stop_new_trades(
    reason: str = "family stop switch",
    user: UserContext = Depends(get_current_user),
) -> dict:
    """STOP ALL NEW TRADES. Halts new entries; exits and reconciliation continue."""
    set_new_trades_halted(True, reason=f"{reason} (actor={getattr(user, 'uid', 'unknown')})")
    return {
        "actor": getattr(user, "uid", "unknown"),
        "new_trades_halted": True,
        "exits_still_processed": True,
        "reconciliation_still_processed": True,
        "state_preserved": True,
        "reason": reason,
        "changed_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/snapback/family/resume-new-trades")
async def family_resume_new_trades(
    reason: str = "family resume",
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Release the stop switch. Live entries still require the authoritative gate."""
    set_new_trades_halted(False, reason=f"{reason} (actor={getattr(user, 'uid', 'unknown')})")
    return {
        "actor": getattr(user, "uid", "unknown"),
        "new_trades_halted": False,
        "reason": reason,
        "changed_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/ops/runtime")
async def ops_runtime(request: Request) -> dict:
    """What only the running process knows, for the start/stop sequence.

    `sterlingctl start` has to answer three questions no other process can:
    whether the broker session is live and bound to the account we are supposed
    to be trading, what the tick socket is subscribed to, and when the last tick
    actually arrived. Everything else in the sequence is durable state a command
    line can read for itself.

    Loopback only. The reply names an account and a subscription set — not a
    secret, but not something to serve to a network either — and this endpoint
    carries no authentication precisely so that a start script does not need a
    credential to ask whether the service came up. The two must go together.

    Every field is tri-state. `null` means the question could not be answered,
    and the caller is required to treat that as a blocker rather than as a no.
    """
    client_host = (request.client.host if request.client else "") or ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=404, detail="Not Found")

    broker: dict = {"connected": None, "account_id": None,
                    "bound_client_id": None, "binding_matches": None, "detail": ""}
    feed: dict = {"connected": None, "subscribed": None, "last_tick_ms": None}

    try:
        from app.services.account_binding_service import active_binding

        binding = active_binding()
        broker["bound_client_id"] = getattr(binding, "client_id", None) if binding else None
    except Exception as exc:  # noqa: BLE001
        broker["detail"] = f"binding unreadable: {exc}"

    try:
        from app.services.exchanges.kite import accounts as kite_accounts

        live = [a for a in kite_accounts.all_accounts() if getattr(a, "connected", False)]
        broker["connected"] = bool(live)
        if live:
            account = live[0]
            broker["account_id"] = str(getattr(account, "kite_user_id", "") or getattr(account, "id", ""))
            bound = broker["bound_client_id"]
            if bound:
                broker["binding_matches"] = str(bound) == broker["account_id"]
        else:
            broker["detail"] = broker["detail"] or "no connected broker account"
    except Exception as exc:  # noqa: BLE001
        broker["connected"] = None
        broker["detail"] = f"broker state unreadable: {exc}"

    try:
        from app.services.exchanges.kite import ticker_manager

        statuses = [ticker_manager.status(uid) for uid in ticker_manager.known_users()]
        if statuses:
            feed["connected"] = any(s.get("connected") for s in statuses)
            subscribed: set = set()
            for status in statuses:
                subscribed.update(status.get("subscribed") or [])
            feed["subscribed"] = sorted(subscribed)
            feed["last_tick_ms"] = max((int(s.get("last_tick_ms") or 0) for s in statuses),
                                       default=0)
        else:
            feed["connected"] = False
            feed["subscribed"] = []
            feed["last_tick_ms"] = 0
    except Exception as exc:  # noqa: BLE001
        feed["detail"] = f"ticker state unreadable: {exc}"

    return {"broker": broker, "feed": feed,
            "checked_at": datetime.now(timezone.utc).isoformat()}
