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
from app.services.snapback import (
    _IST,
    get_config,
    process_prospective_pending_entries,
    process_prospective_daily_mtm_and_exits,
)

log = get_logger(__name__)

_INTERVAL_SECONDS = 30
_task: Optional[asyncio.Task] = None
_lock = asyncio.Lock()

OPENING_WINDOW_START = time(9, 15)
OPENING_WINDOW_END = time(9, 45)
DAILY_MTM_WINDOW_START = time(15, 0)
DAILY_MTM_WINDOW_END = time(15, 45)


def _is_nse_trading_day(d: Any) -> bool:
    """Check if given date is an active NSE trading day using official calendar."""
    try:
        from app.services.navigator.calendar import is_trading_day
        return bool(is_trading_day(d))
    except Exception:
        # Fallback to plain weekday check if date is outside covered calendar bounds
        return d.weekday() < 5


async def tick(uid: str = "default") -> Dict[str, Any]:
    """Single tick of the Snapback prospective observation runner."""
    if _lock.locked():
        return {"status": "overlap_suppressed"}

    async with _lock:
        now_ist = datetime.now(_IST)
        today = now_ist.date()
        curr_time = now_ist.time()

        # 1. Weekend / Holiday Check
        if not _is_nse_trading_day(today):
            return {"status": "market_closed_weekend_or_holiday", "date": str(today)}

        cfg = get_config(uid)

        # 2. Acquire Connected Kite Client
        client = None
        try:
            from app.services.exchanges.kite import accounts
            acct = accounts.get_active(uid)
            if not acct:
                all_accts = accounts.all_accounts()
                acct = all_accts[0] if all_accts else None
            if acct and acct.connected:
                client = await accounts.acquire_client(acct)
        except Exception as client_exc:
            log.warning("Kite client acquisition failed in snapback_runner tick: %s", client_exc)

        if not client:
            log.debug("Snapback runner tick skipped: Kite client disconnected or unavailable")
            return {"status": "kite_disconnected_or_unavailable"}

        entries_processed = 0
        mtm_processed = 0

        # 3. Phase A: Opening Entry Phase (09:15 - 09:45 IST, or catch-up if pending)
        if OPENING_WINDOW_START <= curr_time <= OPENING_WINDOW_END or curr_time > OPENING_WINDOW_END:
            entries_processed = await process_prospective_pending_entries(client, cfg)

        # 4. Phase B: Daily Position Phase (MTM / Rebalance / Exits)
        if curr_time >= DAILY_MTM_WINDOW_START:
            mtm_processed = await process_prospective_daily_mtm_and_exits(client, cfg)

        return {
            "status": "ok",
            "date": str(today),
            "entries_processed": entries_processed,
            "mtm_processed": mtm_processed,
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
