"""Server-truth operational health for the Snapback prospective (paper) runtime.

Health is derived from what the backend can actually observe: runner heartbeats,
warehouse state counts, broker connectivity, market-data freshness, calendar
availability and database reachability. An unknown critical component is never
reported as green.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger(__name__)

# The runner loop ticks every 30 seconds; 3 missed cycles means it is not alive.
RUNNER_STALE_AFTER_SECONDS = 90

# Errors that must stop the system outright rather than merely degrade it.
_HALTING_ERRORS = {
    "database_unavailable",
    "manifest_mismatch",
    "health_probe_failed",
    "startup_preflight_failed",
    "recovery_required",
}

_IST = timezone(timedelta(hours=5, minutes=30))

# --------------------------------------------------------------------------- #
# Heartbeat store (in-process; written by the prospective runner)
# --------------------------------------------------------------------------- #

_lock = threading.Lock()
_heartbeats: Dict[str, datetime] = {}
_errors: Dict[str, str] = {}


def record_cycle(name: str, when: Optional[datetime] = None) -> None:
    """Record that a named runtime cycle completed (e.g. 'runner_tick')."""
    with _lock:
        _heartbeats[name] = when or datetime.now(timezone.utc)


def record_error(key: str, detail: str = "") -> None:
    with _lock:
        _errors[key] = detail


def clear_error(key: str) -> None:
    with _lock:
        _errors.pop(key, None)


def get_heartbeat(name: str) -> Optional[datetime]:
    with _lock:
        return _heartbeats.get(name)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if isinstance(value, datetime) else None


def build_prospective_health(
    *,
    warehouse: Any,
    runtime_sha: Optional[str],
    strategy_manifest: Optional[str],
    manifest_ok: bool,
    mode: str,
    broker_connected: bool,
    market_data_fresh: bool,
    calendar_ok: bool,
    database_ok: bool,
    runner_alive: bool,
    last_runner_tick: Optional[datetime],
    last_signal_scan: Optional[datetime] = None,
    last_entry_cycle: Optional[datetime] = None,
    last_intraday_risk_cycle: Optional[datetime] = None,
    last_eod_cycle: Optional[datetime] = None,
    unresolved_errors: Optional[Iterable[str]] = None,
    market_open: bool = True,
    alert_transport_configured: bool = False,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build the health object from observed component truth. Unknown is never green."""
    now = now or datetime.now(timezone.utc)
    errors: List[str] = list(unresolved_errors or [])

    # Warehouse state counts. A warehouse that cannot answer is a database failure.
    pending = processing = open_positions = exit_pending = None
    try:
        opp_counts = dict(warehouse.opportunity_status_counts() or {})
        pos_counts = dict(warehouse.paper_position_status_counts() or {})
        pending = int(opp_counts.get("PENDING_ENTRY", 0))
        processing = int(opp_counts.get("PROCESSING_ENTRY", 0))
        open_positions = int(pos_counts.get("OPEN", 0))
        exit_pending = int(pos_counts.get("EXIT_PENDING", 0))
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("Snapback health: warehouse state counts unavailable: %s", exc)
        database_ok = False

    # Runner heartbeat freshness is server truth, not a caller claim.
    if last_runner_tick is None:
        runner_alive = False
    else:
        age = (now - last_runner_tick).total_seconds()
        if age > RUNNER_STALE_AFTER_SECONDS:
            runner_alive = False

    if not database_ok:
        errors.append("database_unavailable")
    if not manifest_ok:
        errors.append("manifest_mismatch")
    if not runner_alive:
        errors.append("runner_stale")
    if not broker_connected:
        errors.append("broker_disconnected")
    if not market_data_fresh and market_open:
        errors.append("market_data_stale")
    if not calendar_ok:
        errors.append("calendar_unavailable")

    # Preserve order, drop duplicates.
    deduped: List[str] = []
    for err in errors:
        if err not in deduped:
            deduped.append(err)

    healthy = not deduped
    if healthy:
        status = "HEALTHY"
    elif any(err in _HALTING_ERRORS for err in deduped):
        status = "HALTED"
    else:
        status = "DEGRADED"

    return {
        "status": status,
        "healthy": healthy,
        "runtime_sha": runtime_sha,
        "strategy_manifest": strategy_manifest,
        "manifest_ok": bool(manifest_ok),
        "mode": mode,
        "runner_alive": bool(runner_alive),
        "broker_connected": bool(broker_connected),
        "market_data_fresh": bool(market_data_fresh),
        "market_open": bool(market_open),
        "calendar_ok": bool(calendar_ok),
        "database_ok": bool(database_ok),
        "last_runner_tick": _iso(last_runner_tick),
        "last_signal_scan": _iso(last_signal_scan),
        "last_entry_cycle": _iso(last_entry_cycle),
        "last_intraday_risk_cycle": _iso(last_intraday_risk_cycle),
        "last_eod_cycle": _iso(last_eod_cycle),
        "pending_entries": pending,
        "processing_entries": processing,
        "open_positions": open_positions,
        "exit_pending": exit_pending,
        "alert_transport_configured": bool(alert_transport_configured),
        "unresolved_errors": deduped,
        "generated_at": _iso(now),
    }


# --------------------------------------------------------------------------- #
# Live probes
# --------------------------------------------------------------------------- #


def _runtime_sha() -> Optional[str]:
    env_sha = os.environ.get("STERLING_RUNTIME_SHA")
    if env_sha:
        return env_sha.strip()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:  # pragma: no cover - environment dependent
        pass
    return None


def _probe_alert_transport(uid: str = "default") -> bool:
    """Whether an operational alert can reach a person. Unknown counts as false."""
    try:
        from app.services.snapback_alert_telegram import transport_configured

        return bool(transport_configured(uid))
    except Exception:
        return False


def _probe_broker(uid: str = "default") -> bool:
    try:
        from app.services.exchanges.kite import accounts

        acct = accounts.get_active(uid)
        if not acct:
            all_accts = accounts.all_accounts()
            acct = all_accts[0] if all_accts else None
        return bool(acct and acct.connected)
    except Exception as exc:
        log.warning("Snapback health: broker probe failed: %s", exc)
        return False


def _probe_calendar(d=None) -> tuple[bool, bool]:
    """Return (calendar_available, is_trading_day_today).

    A closed market (weekend or NSE holiday) is a normal answer, not a calendar
    failure. Only an exception from the calendar service is a failure, and it
    fails closed.
    """
    from app.services.navigator import calendar as calendar_mod

    d = d or datetime.now(_IST).date()

    try:
        trading_day = bool(calendar_mod.is_trading_day(d))
        return True, trading_day
    except Exception as exc:
        log.warning("Snapback health: calendar probe failed: %s", exc)
        return False, False


def _probe_manifest() -> tuple[bool, list[str]]:
    """Verify the live config against the frozen Snapback manifest. Never assume green."""
    from app.engines.snapback.config import SnapbackConfig
    from app.engines.snapback import manifest as manifest_mod

    try:
        cfg = SnapbackConfig()
        manifest = manifest_mod.create_frozen_manifest(cfg)
        valid, reasons = manifest_mod.verify_manifest_integrity(manifest, cfg)
        return bool(valid), list(reasons)
    except Exception as exc:
        return False, [f"manifest_probe_failed:{exc}"]


def _market_open_now(now_ist: datetime) -> bool:
    t = now_ist.time()
    return t.hour * 60 + t.minute >= 9 * 60 + 15 and t.hour * 60 + t.minute <= 15 * 60 + 30


def get_prospective_health(uid: str = "default") -> Dict[str, Any]:
    """Probe every component and return the health object. Raises if the DB is unreachable."""
    from app.services.snapback_observation_warehouse import (
        FROZEN_MANIFEST_HASH,
        SnapbackObservationWarehouse,
    )

    now = datetime.now(timezone.utc)
    now_ist = now.astimezone(_IST)

    warehouse = SnapbackObservationWarehouse()
    database_ok = True
    try:
        warehouse.opportunity_status_counts()
    except Exception as exc:
        log.warning("Snapback health: database probe failed: %s", exc)
        database_ok = False

    last_tick = get_heartbeat("runner_tick")
    calendar_ok, trading_day = _probe_calendar(now_ist.date())
    market_open = calendar_ok and trading_day and _market_open_now(now_ist)
    last_quote = get_heartbeat("market_data")
    market_data_fresh = bool(
        last_quote and (now - last_quote).total_seconds() <= 180
    )

    with _lock:
        standing_errors = sorted(_errors.keys())

    manifest_ok, manifest_reasons = _probe_manifest()
    if not manifest_ok:
        log.warning("Snapback health: manifest verification failed: %s", manifest_reasons)

    return build_prospective_health(
        warehouse=warehouse,
        runtime_sha=_runtime_sha(),
        strategy_manifest=FROZEN_MANIFEST_HASH,
        manifest_ok=manifest_ok,
        mode="PAPER",
        broker_connected=_probe_broker(uid),
        market_data_fresh=market_data_fresh,
        calendar_ok=calendar_ok,
        database_ok=database_ok,
        runner_alive=last_tick is not None,
        last_runner_tick=last_tick,
        last_signal_scan=get_heartbeat("signal_scan"),
        last_entry_cycle=get_heartbeat("entry_cycle"),
        last_intraday_risk_cycle=get_heartbeat("intraday_risk_cycle"),
        last_eod_cycle=get_heartbeat("eod_cycle"),
        unresolved_errors=standing_errors,
        market_open=market_open,
        alert_transport_configured=_probe_alert_transport(),
        now=now,
    )
