"""Unattended background runner for Snapback prospective paper observation.

Executes autonomous opening entry resolution (09:15-09:45 IST) and daily position
MTM / rebalance / exit evaluation (15:00-15:45 IST) on NSE trading days.

CRITICAL OPERATIONAL GUARANTEE:
This runner operates exclusively in paper observation mode. It MUST NEVER invoke
broker order execution methods (place_order, modify_order, cancel_order).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time, timezone, timedelta
from typing import Any, Dict, Optional

from app.core.logging import get_logger
from app.services.snapback_family_ops import new_trades_halted
from app.engines.snapback.policy import EXECUTION_POLICY
from app.services.snapback_health import record_cycle
from app.services.snapback_prospective_scanner import SCAN_AFTER_CLOSE, finalize_session_signals

EOD_OBSERVE_START = time.fromisoformat(EXECUTION_POLICY.eod_observation_start)
EOD_OBSERVE_END = time.fromisoformat(EXECUTION_POLICY.eod_observation_end)
from app.services.snapback import (
    _IST,
    get_config,
    process_prospective_pending_entries,
    process_prospective_intraday_risk,
    process_prospective_daily_mtm_and_exits,
)

log = get_logger(__name__)

_INTERVAL_SECONDS = 30
_task: Optional[asyncio.Task] = None
_lock = asyncio.Lock()

OPENING_WINDOW_START = time(9, 15)
OPENING_WINDOW_END = time(9, 45)
MARKET_WINDOW_START = time(9, 15)
MARKET_WINDOW_END = time(15, 30)
EOD_WINDOW_START = time(15, 29)
EOD_WINDOW_END = time(15, 30)


def _is_nse_trading_day(d: Any) -> bool:
    """Check if given date is an active NSE trading day using official calendar.
    
    Fails closed (returns False) if calendar lookup fails or is unavailable.
    """
    try:
        from app.services.navigator.calendar import is_trading_day
        return bool(is_trading_day(d))
    except Exception as exc:
        log.error("NSE trading calendar check failed for %s: %s", d, exc)
        return False  # Fail closed: DO NOT fall back to weekday < 5


async def tick(uid: str = "default") -> Dict[str, Any]:
    """Single tick of the Snapback prospective observation runner."""
    if _lock.locked():
        return {"status": "overlap_suppressed"}

    async with _lock:
        # Observability only: the tick was entered, regardless of market or broker state.
        record_cycle("runner_tick")

        now_ist = datetime.now(_IST)
        today = now_ist.date()
        curr_time = now_ist.time()

        # 1. Weekend / Holiday Check (Fail closed)
        if not _is_nse_trading_day(today):
            return {"status": "market_closed_weekend_or_holiday", "date": str(today)}

        cfg = get_config(uid)

        # 2. Acquire Connected Kite Client
        client = None
        try:
            from app.services.exchanges.kite import accounts
            from app.services.snapback_family_account import (
                FamilyAccountBindingError, binding_configured, resolve_family_account,
            )

            if binding_configured():
                # Exactly one bound account. A different connected account is not a
                # substitute for the family account being logged out.
                try:
                    acct = resolve_family_account()
                except FamilyAccountBindingError as bind_exc:
                    log.error("Snapback runner: family account unavailable: %s", bind_exc)
                    acct = None
            else:
                acct = accounts.get_active(uid)

            if acct and acct.connected:
                client = await accounts.acquire_client(acct)
        except Exception as client_exc:
            log.warning("Kite client acquisition failed in snapback_runner tick: %s", client_exc)

        if not client:
            log.debug("Snapback runner tick skipped: Kite client disconnected or unavailable")
            return {"status": "kite_disconnected_or_unavailable"}

        entries_processed = 0
        risk_processed = 0
        mtm_processed = 0

        # 3. Phase A: Opening Entry Phase (09:15 - 09:45 IST, or catch-up if pending)
        # The family STOP switch blocks new exposure only; risk monitoring, exits and
        # end-of-day reconciliation below continue untouched.
        # Reconcile the broker's book against Sterling's. Sterling does not own the
        # account exclusively, so "flat" is a claim that must be checked.
        try:
            from app.services.snapback_family_account import binding_configured
            from app.services.snapback_reconciliation import reconcile_family_account

            if binding_configured():
                snapshot = await reconcile_family_account()
                if not snapshot.clean:
                    log.warning(
                        "Snapback reconciliation mismatches: %s",
                        [m.code for m in snapshot.mismatches],
                    )
        except Exception as recon_exc:
            log.warning("Snapback reconciliation failed: %s", recon_exc)

        # An exposed position that is not being observed means a stop breach could
        # pass unseen. New exposure waits until observation is healthy again.
        stop_gap = False
        try:
            from app.services.snapback_stop_monitor import get_stop_monitor

            gaps = get_stop_monitor().data_gaps()
            if gaps:
                stop_gap = True
                log.warning("Snapback MARKET_DATA_GAP for %s; entries halted", gaps)
        except Exception as gap_exc:
            log.debug("Snapback stop-gap probe failed: %s", gap_exc)

        # Entry observation runs from the opening bell, whether or not anything is
        # pending: continuity is a property of the window, not of a trade.
        try:
            from app.services.snapback_entry_observation import (
                heartbeat_entry_monitor, start_entry_monitor,
            )
            from app.services.snapback_prospective_collector import SnapbackObservationWarehouse

            if OPENING_WINDOW_START <= curr_time <= MARKET_WINDOW_END:
                obs_warehouse = SnapbackObservationWarehouse()
                start_entry_monitor(obs_warehouse, session_date=str(today), at=now_ist)
                heartbeat_entry_monitor(obs_warehouse, session_date=str(today), at=now_ist)
        except Exception as obs_exc:
            log.warning("Snapback entry observation heartbeat failed: %s", obs_exc)

        entries_halted = new_trades_halted() or stop_gap
        if entries_halted:
            log.warning("Snapback entries halted by family stop switch; exits continue")
        elif OPENING_WINDOW_START <= curr_time <= OPENING_WINDOW_END or curr_time > OPENING_WINDOW_END:
            entries_processed = await process_prospective_pending_entries(client, cfg)
            record_cycle("entry_cycle")

        # 4. Phase B: Intraday Risk Monitor (Market hours 09:15 - 15:30 IST)
        if MARKET_WINDOW_START <= curr_time <= MARKET_WINDOW_END:
            risk_processed = await process_prospective_intraday_risk(client, cfg)
            record_cycle("intraday_risk_cycle")

        # 5. Phase C1: OBSERVE the close (15:29-15:30 IST). Fetch and persist only;
        # no state transition happens inside the window.
        if EOD_OBSERVE_START <= curr_time <= EOD_OBSERVE_END:
            try:
                from app.services.snapback_eod import collect_eod_observations
                from app.services.snapback_prospective_collector import SnapbackObservationWarehouse

                obs = await collect_eod_observations(
                    client=client, cfg=cfg, warehouse=SnapbackObservationWarehouse(),
                    session_date=today, now_ist=now_ist,
                )
                log.info(
                    "Snapback EOD observation %s: %s accepted, %s rejected",
                    today, obs.observed, obs.rejected,
                )
            except Exception as obs_exc:
                log.exception("Snapback EOD observation failed: %s", obs_exc)

        # 5b. Phase C2: FINALIZE after the close, from persisted evidence only. A
        # restart at 15:31 finalises from what was observed, or records a gap — it
        # never fetches a post-close quote and calls it the close.
        if curr_time >= EOD_OBSERVE_END:
            try:
                from app.services.snapback_eod import finalize_eod_session
                from app.services.snapback_prospective_collector import SnapbackObservationWarehouse
                from app.services.snapback_session_ledger import session_record

                warehouse = SnapbackObservationWarehouse()
                row = session_record(warehouse, str(today)) or {}
                if str(row.get("eod_phase_status") or "PENDING") != "COMPLETE":
                    final = finalize_eod_session(
                        cfg=cfg, warehouse=warehouse, session_date=today,
                        finalized_at=now_ist,
                    )
                    mtm_processed = final.positions_finalized
                    record_cycle("eod_cycle")
                    log.info(
                        "Snapback EOD finalisation %s: %s (%s positions, gaps=%s)",
                        today, final.status, final.positions_finalized, final.gap_codes,
                    )
            except Exception as fin_exc:
                log.exception("Snapback EOD finalisation failed: %s", fin_exc)

        # 5c. Entry phase reaches a durable verdict once its window has closed.
        if curr_time > OPENING_WINDOW_END:
            try:
                from app.services.snapback_entry_observation import finalize_entry_phase
                from app.services.snapback_prospective_collector import SnapbackObservationWarehouse

                finalize_entry_phase(
                    SnapbackObservationWarehouse(), session_date=today, now_ist=now_ist,
                )
            except Exception as entry_exc:
                log.warning("Snapback entry phase finalisation failed: %s", entry_exc)

        # 6. Phase D: unattended Day-T signal finalisation, after the official close.
        # A signal must exist because the session closed, not because a browser was
        # open. Idempotent: a session already recorded complete is not rescanned.
        session_scanned = False
        if curr_time >= SCAN_AFTER_CLOSE:
            try:
                from app.services.snapback_prospective_collector import SnapbackObservationWarehouse
                from app.services.snapback_session_ledger import session_scan_complete, session_record

                warehouse = SnapbackObservationWarehouse()
                if not session_scan_complete(session_record(warehouse, str(today)) or {}):
                    scan_result = await finalize_session_signals(
                        session_date=today, client=client, warehouse=warehouse, uid=uid,
                    )
                    session_scanned = True
                    record_cycle("signal_scan")
                    log.info(
                        "Snapback session scan %s: %s (%s/%s scanned, %s signals)",
                        today, scan_result.status, scan_result.universe_scanned,
                        scan_result.universe_expected, scan_result.signals_authoritative,
                    )
            except Exception as scan_exc:
                log.exception("Snapback session scan failed for %s: %s", today, scan_exc)

        return {
            "status": "ok",
            "date": str(today),
            "entries_processed": entries_processed,
            "entries_halted": entries_halted,
            "risk_processed": risk_processed,
            "mtm_processed": mtm_processed,
            "session_scanned": session_scanned,
        }


async def run_forever() -> None:
    """Continuous background loop running tick every 30 seconds."""
    log.info("Snapback prospective unattended runner loop started")
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            log.info("Snapback prospective runner task cancelled")
            raise
        except Exception as exc:
            log.exception("Snapback prospective runner tick unhandled exception: %s", exc)
        await asyncio.sleep(_INTERVAL_SECONDS)


def start() -> asyncio.Task:
    """Start the background Snapback prospective runner task if not running."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(run_forever(), name="snapback-prospective-runner")
        log.info("Started snapback-prospective-runner background task")
    return _task


def stop() -> None:
    """Stop the background Snapback prospective runner task if running."""
    global _task
    if _task is not None and not _task.done():
        _task.cancel()
        log.info("Stopped snapback-prospective-runner background task")
    _task = None
