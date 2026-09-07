"""Production background runner for the independent ORB option-buy strategy."""
from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.logging import get_logger

log = get_logger(__name__)
IST = ZoneInfo("Asia/Kolkata")
_INTERVAL_S = 5.0
_task: asyncio.Task | None = None
_user_locks: dict[str, asyncio.Lock] = {}
_recovered: set[str] = set()


def _lock_for(user_id: str):
    lock = _user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[user_id] = lock
    return lock


def _is_verified_market_open() -> bool:
    now = datetime.now(IST)
    try:
        from app.services.navigator.calendar import is_market_open_at

        return bool(is_market_open_at(int(now.timestamp() * 1000)))
    except Exception:
        return False


async def _ensure_recovered(user_id: str) -> None:
    if user_id in _recovered:
        return
    from app.services.nifty_orb_lifecycle import recover_after_restart

    try:
        report = await recover_after_restart(user_id)
        log.info("NIFTY ORB restart recovery user=%s report=%s", user_id, report)
        _recovered.add(user_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("NIFTY ORB restart recovery failed user=%s: %s", user_id, exc)


async def _run_user(user_id: str):
    from app.services.exchanges.kite import accounts
    from app.services.nifty_orb_execution import execute_scan
    from app.services.nifty_orb_lifecycle import square_off_expired
    from app.services.nifty_orb_options import get_config
    from app.services.nifty_orb_scanner import scan_user

    lock = _lock_for(user_id)
    if lock.locked():
        return {"status": "overlap_suppressed"}
    async with lock:
        try:
            await _ensure_recovered(user_id)
            cfg = get_config()
            if not cfg.enabled:
                return {"status": "disabled"}
            if not _is_verified_market_open():
                return {"status": "market_closed"}
            account = accounts.get_active(user_id)
            if account:
                try:
                    client = await accounts.acquire_client(account)
                    await square_off_expired(client, user_id)
                except Exception as exc:  # noqa: BLE001
                    log.warning("NIFTY ORB expiry square-off user=%s: %s", user_id, exc)
            scan = await scan_user(user_id, cfg)
            # Re-check after network/data acquisition: a 5-second tick can cross the
            # market boundary while the scanner is running.
            if not _is_verified_market_open():
                return {"status": "market_closed"}
            return await execute_scan(user_id, scan=scan, max_trades=cfg.max_trades_per_day)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("NIFTY ORB execution failed for user=%s: %s", user_id, exc)
            return {"status": "error", "message": str(exc)}


async def _tick():
    from app.services.exchanges.kite import accounts as kite_accounts

    if not _is_verified_market_open():
        return
    kite_accounts.bootstrap()
    users = {
        a.user_id
        for a in kite_accounts._accounts.values()
        if a.user_id and a.is_active and a.connected
    }
    if not users:
        return
    results = await asyncio.gather(*(_run_user(uid) for uid in sorted(users)), return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            log.warning("NIFTY ORB tenant tick exception: %s", result)
            continue
        if result.get("status") in {
            "executed",
            "blocked",
            "error",
            "daily_limit",
            "critical_unknown_position",
            "critical_unprotected",
            "executed_count_not_persisted",
            "manual",
        }:
            log.info("NIFTY ORB auto tick status=%s", result["status"])


async def run_forever():
    while True:
        try:
            await _tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("NIFTY ORB runner failure: %s", exc)
        await asyncio.sleep(_INTERVAL_S)


def start():
    global _task
    _recovered.clear()
    if _task is None or _task.done():
        _task = asyncio.create_task(run_forever(), name="nifty-orb-options-runner")
    return _task


def stop():
    global _task
    if _task is not None and not _task.done():
        _task.cancel()
    _task = None
    _recovered.clear()
