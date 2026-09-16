"""Operations scheduler for the frozen Snapback prospective runtime.

Two duties, one background task:

    poll_health()  every minute: server-truth health -> operational alerts.
    run_due()      once per trading session after 15:40 IST:
                   SQLite online backup -> checksum verification ->
                   daily evidence report -> health -> alerts.

A session is marked complete only after both the backup verified and the report
generated. A failed or missing state file means rerun, never skip: the backup and
report are idempotent, a silently skipped session is not recoverable.

This module contains no strategy or execution logic and never places orders.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, List, Optional

log = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))

# Market closes at 15:30 IST; the EOD phase runs to 15:30. Operations start after.
POST_MARKET_START = time(15, 40)

_POLL_INTERVAL_SECONDS = 60

_task: Optional[asyncio.Task] = None


@dataclass
class OpsCycleResult:
    status: str
    session_date: Optional[str] = None
    backup_ok: Optional[bool] = None
    report_ok: Optional[bool] = None
    alert_sent: int = 0
    alert_suppressed: int = 0
    alert_failed: int = 0
    errors: List[str] = field(default_factory=list)


def _default_path(env_key: str, fallback: str) -> Path:
    return Path(os.environ.get(env_key, fallback))


class SnapbackOpsScheduler:
    def __init__(
        self,
        *,
        source_db: Optional[Path] = None,
        backup_root: Optional[Path] = None,
        freeze_record: Optional[Path] = None,
        report_root: Optional[Path] = None,
        state_path: Optional[Path] = None,
        backup_fn: Optional[Callable[..., Any]] = None,
        verify_backup_fn: Optional[Callable[..., bool]] = None,
        report_fn: Optional[Callable[..., Any]] = None,
        health_fn: Optional[Callable[[], dict]] = None,
        dispatcher: Any = None,
        is_trading_day_fn: Optional[Callable[[date], bool]] = None,
    ) -> None:
        from app.services import snapback_backup
        from app.services.snapback_alerts import AlertDispatcher, LoggingAlertSink
        from app.services.snapback_alert_telegram import CompositeAlertSink, TelegramAlertSink

        self.source_db = Path(
            source_db
            or _default_path("STERLING_OBSERVATIONS_DB_PATH", "snapback_observations.db")
        )
        self.backup_root = Path(
            backup_root
            or _default_path(
                "STERLING_SNAPBACK_BACKUP_ROOT",
                str(Path.home() / "Sterling" / "backups" / "snapback"),
            )
        )
        self.freeze_record = Path(
            freeze_record
            or _default_path(
                "STERLING_FREEZE_RECORD_PATH",
                "docs/strategy/snapback/PROSPECTIVE_FREEZE_RECORD.md",
            )
        )
        self.report_root = Path(
            report_root
            or _default_path("STERLING_FORWARD_REPORT_ROOT", "artifacts/snapback_forward")
        )
        self.state_path = Path(
            state_path
            or _default_path(
                "STERLING_POST_MARKET_STATE_PATH",
                "data/snapback/post_market_ops_state.json",
            )
        )

        self.backup_fn = backup_fn or snapback_backup.create_backup
        self.verify_backup_fn = verify_backup_fn or snapback_backup.verify_backup
        self.report_fn = report_fn or self._default_report_fn
        self.health_fn = health_fn or self._default_health_fn
        # Logging always; Telegram additionally when a target is configured.
        self.dispatcher = dispatcher or AlertDispatcher(
            sink=CompositeAlertSink(LoggingAlertSink(), TelegramAlertSink())
        )
        self.is_trading_day_fn = is_trading_day_fn or self._default_trading_day_fn

        self._last_backup_ok = True

    # ----------------------------------------------------------------- defaults

    @staticmethod
    def _default_report_fn(**kwargs):
        from study.snapback_forward_report import generate_forward_report

        return generate_forward_report(**kwargs)

    @staticmethod
    def _default_health_fn() -> dict:
        from app.services.snapback_health import get_prospective_health

        return get_prospective_health()

    @staticmethod
    def _default_trading_day_fn(d: date) -> bool:
        from app.services.navigator.calendar import is_trading_day

        return bool(is_trading_day(d))

    # -------------------------------------------------------------------- state

    def _read_state(self) -> dict:
        """Read the completion state. A missing or corrupt file means 'not complete'."""
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except Exception as exc:
            log.warning(
                "Snapback ops: state file %s unreadable (%s); rerunning the cycle",
                self.state_path,
                exc,
            )
            return {}

    def _write_state(self, session_date: str, now_ist: datetime) -> None:
        payload = {
            "last_completed_session": session_date,
            "completed_at": now_ist.isoformat(),
            "backup_verified": True,
            "report_generated": True,
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=self.state_path.name + ".", dir=str(self.state_path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.state_path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------------- duties

    def poll_health(self, *, now_ist: Optional[datetime] = None) -> Optional[OpsCycleResult]:
        """Probe health and dispatch operational alerts. Runs every minute."""
        now_ist = now_ist or datetime.now(_IST)
        result = OpsCycleResult(status="HEALTH_POLLED", session_date=now_ist.date().isoformat())

        try:
            health = self.health_fn()
        except Exception as exc:
            log.exception("Snapback ops: health probe failed: %s", exc)
            health = {
                "status": "HALTED",
                "healthy": False,
                "unresolved_errors": ["health_probe_failed"],
            }
            result.errors.append(f"health_probe_failed:{exc}")

        self._dispatch(health, result, now_ist, backup_ok=self._last_backup_ok, report_ok=True)
        return result

    def run_due(self, *, now_ist: Optional[datetime] = None) -> OpsCycleResult:
        """Run the once-per-session post-market chain when it is due."""
        now_ist = now_ist or datetime.now(_IST)
        session_date = now_ist.date()
        session_key = session_date.isoformat()

        try:
            trading_day = bool(self.is_trading_day_fn(session_date))
        except Exception as exc:
            log.warning("Snapback ops: calendar check failed: %s", exc)
            return OpsCycleResult(
                status="CALENDAR_UNAVAILABLE",
                session_date=session_key,
                errors=[f"calendar_unavailable:{exc}"],
            )

        if not trading_day:
            return OpsCycleResult(status="NON_TRADING_DAY", session_date=session_key)

        if now_ist.timetz().replace(tzinfo=None) < POST_MARKET_START:
            return OpsCycleResult(status="NOT_DUE", session_date=session_key)

        if self._read_state().get("last_completed_session") == session_key:
            return OpsCycleResult(status="ALREADY_COMPLETE", session_date=session_key)

        result = OpsCycleResult(status="RUNNING", session_date=session_key)

        # 1. Backup + 2. checksum verification
        backup_ok = False
        try:
            artifact = self.backup_fn(
                source_db=self.source_db,
                backup_root=self.backup_root,
                freeze_record=self.freeze_record if Path(self.freeze_record).exists() else None,
                now=now_ist.astimezone(timezone.utc),
            )
            backup_ok = bool(self.verify_backup_fn(artifact.db_path, artifact.checksum_path))
            if not backup_ok:
                result.errors.append("backup_verification_failed")
        except Exception as exc:
            log.exception("Snapback ops: evidence backup failed: %s", exc)
            result.errors.append(f"backup_failed:{exc}")
        result.backup_ok = backup_ok
        self._last_backup_ok = backup_ok

        # 3. Daily evidence report
        report_ok = False
        try:
            self.report_fn(
                output_root=self.report_root,
                report_date=session_date,
            )
            report_ok = True
        except Exception as exc:
            log.exception("Snapback ops: evidence report failed: %s", exc)
            result.errors.append(f"report_failed:{exc}")
        result.report_ok = report_ok

        # 4. Health + 5. alerts. Always runs, even when the steps above failed.
        try:
            health = self.health_fn()
        except Exception as exc:
            log.exception("Snapback ops: health probe failed: %s", exc)
            health = {
                "status": "HALTED",
                "healthy": False,
                "unresolved_errors": ["health_probe_failed"],
            }
            result.errors.append(f"health_probe_failed:{exc}")

        self._dispatch(health, result, now_ist, backup_ok=backup_ok, report_ok=report_ok)

        # 6. Only a fully successful core cycle marks the session complete.
        if backup_ok and report_ok:
            try:
                self._write_state(session_key, now_ist)
                result.status = "COMPLETE"
            except Exception as exc:
                log.exception("Snapback ops: could not persist session state: %s", exc)
                result.errors.append(f"state_write_failed:{exc}")
                result.status = "PARTIAL_FAILURE"
        else:
            result.status = "PARTIAL_FAILURE"

        return result

    # ------------------------------------------------------------------ helpers

    def _stale_state(self, health: dict) -> dict:
        """Stuck EXIT_PENDING / PROCESSING_ENTRY, preferring the health snapshot."""
        if health and "exit_pending_stale" in health:
            return {
                "exit_pending_stale": health.get("exit_pending_stale"),
                "processing_entry_stale": health.get("processing_entry_stale"),
            }
        try:
            from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse
            from app.services.snapback_stale import stale_lifecycle_state

            return stale_lifecycle_state(SnapbackObservationWarehouse())
        except Exception as exc:
            log.warning("Snapback ops: stale lifecycle probe failed: %s", exc)
            return {"exit_pending_stale": True, "processing_entry_stale": True}

    def _dispatch(
        self,
        health: dict,
        result: OpsCycleResult,
        now_ist: datetime,
        *,
        backup_ok: bool,
        report_ok: bool,
    ) -> None:
        from app.services.snapback_alerts import derive_operational_alerts

        try:
            # Derived from the ledger, not hardcoded: these alerts existed but could
            # never fire while their inputs were pinned to False.
            stale = self._stale_state(health)
            alerts = derive_operational_alerts(
                health=health,
                backup_ok=backup_ok,
                report_ok=report_ok,
                exit_pending_stale=bool(stale.get("exit_pending_stale")),
                processing_entry_stale=bool(stale.get("processing_entry_stale")),
            )
            dispatched = self.dispatcher.dispatch(alerts, now=now_ist)
            result.alert_sent = int(getattr(dispatched, "sent", 0) or 0)
            result.alert_suppressed = int(getattr(dispatched, "suppressed", 0) or 0)
            result.alert_failed = int(getattr(dispatched, "failed", 0) or 0)
        except Exception as exc:
            # Alerting must never fail the evidence cycle.
            log.exception("Snapback ops: alert dispatch failed: %s", exc)
            result.errors.append(f"alert_dispatch_failed:{exc}")


# --------------------------------------------------------------------------- #
# Background task
# --------------------------------------------------------------------------- #


async def run_forever() -> None:
    scheduler = SnapbackOpsScheduler()
    log.info("Snapback operations scheduler started")
    while True:
        try:
            scheduler.poll_health()
            scheduler.run_due()
        except asyncio.CancelledError:
            log.info("Snapback operations scheduler cancelled")
            raise
        except Exception as exc:
            log.exception("Snapback operations scheduler iteration failed: %s", exc)
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


def start() -> asyncio.Task:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(run_forever(), name="snapback-ops-scheduler")
        log.info("Started snapback-ops-scheduler background task")
    return _task


def stop() -> None:
    global _task
    if _task is not None and not _task.done():
        _task.cancel()
        log.info("Stopped snapback-ops-scheduler background task")
    _task = None
