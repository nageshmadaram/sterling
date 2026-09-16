"""Operational alerts for the frozen Snapback prospective runtime.

Alerts cover actionable operational failures only: the system cannot observe, cannot
persist, cannot reconcile, or has halted. Ordinary signals and ordinary losing trades
are never alerted — economic P&L is deliberately not an input here, because alert
fatigue is what makes people ignore the alerts that matter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Protocol

log = logging.getLogger(__name__)

CRITICAL = "CRITICAL"
WARNING = "WARNING"

DEFAULT_REPEAT_AFTER = timedelta(hours=4)


@dataclass(frozen=True)
class OperationalAlert:
    code: str
    severity: str
    title: str
    message: str


@dataclass
class DispatchResult:
    sent: int = 0
    suppressed: int = 0
    failed: int = 0
    codes_sent: List[str] = field(default_factory=list)


class AlertSink(Protocol):
    def send(self, alert: OperationalAlert) -> Any:  # pragma: no cover - protocol
        ...


def derive_operational_alerts(
    *,
    health: Dict[str, Any],
    backup_ok: bool,
    exit_pending_stale: bool = False,
    processing_entry_stale: bool = False,
    daily_loss_breached: bool = False,
    drawdown_breached: bool = False,
    reconciliation_mismatch: bool = False,
) -> List[OperationalAlert]:
    """Map observed operational state to actionable alerts."""
    alerts: List[OperationalAlert] = []
    health = health or {}
    errors = set(health.get("unresolved_errors") or [])

    def add(code: str, severity: str, title: str, message: str) -> None:
        alerts.append(
            OperationalAlert(code=code, severity=severity, title=title, message=message)
        )

    if not health.get("database_ok", True) or "database_unavailable" in errors:
        add(
            "database_unavailable",
            CRITICAL,
            "Sterling database unavailable",
            "The Snapback evidence database cannot be read or written. Evidence collection has stopped.",
        )

    if not health.get("manifest_ok", True) or "manifest_mismatch" in errors:
        add(
            "manifest_mismatch",
            CRITICAL,
            "Frozen strategy manifest mismatch",
            "The running configuration does not match the frozen Snapback manifest. Stop and investigate before trusting any new evidence.",
        )

    if not health.get("runner_alive", True) or "runner_stale" in errors:
        add(
            "runner_stopped",
            CRITICAL,
            "Prospective runner stopped",
            "The background runner has missed multiple cycles. Entries, risk checks and end-of-day processing are not running.",
        )

    if not health.get("broker_connected", True) or "broker_disconnected" in errors:
        add(
            "broker_disconnected",
            WARNING,
            "Broker login required",
            "Sterling is not connected to the broker. Log in to the broker to restore market data and paper execution.",
        )

    if not health.get("market_data_fresh", True) or "market_data_stale" in errors:
        add(
            "market_data_stale",
            WARNING,
            "Market data stale",
            "No fresh executable quote has been observed. Sterling fails closed and will not act on stale data.",
        )

    if not health.get("calendar_ok", True) or "calendar_unavailable" in errors:
        add(
            "calendar_failure",
            CRITICAL,
            "Trading calendar unavailable",
            "The NSE trading calendar could not be verified. Sterling fails closed until it is available.",
        )

    if reconciliation_mismatch or "position_reconciliation_mismatch" in errors:
        add(
            "position_reconciliation_mismatch",
            CRITICAL,
            "Position reconciliation mismatch",
            "Recorded positions do not match the ledger. Do not increase exposure until this is resolved.",
        )

    if exit_pending_stale:
        add(
            "exit_pending_unresolved",
            CRITICAL,
            "Exit pending unresolved",
            f"{health.get('exit_pending')} position(s) latched for exit have not been liquidated. Liquidation evidence is missing.",
        )

    if processing_entry_stale:
        add(
            "pending_entry_stuck",
            CRITICAL,
            "Pending entry stuck",
            f"{health.get('processing_entries')} entry lease(s) have not completed. An entry may be half-processed.",
        )

    if not backup_ok:
        add(
            "backup_failed",
            CRITICAL,
            "Evidence backup failed",
            "The daily backup of the prospective evidence database did not complete. The evidence is currently unprotected.",
        )

    if daily_loss_breached:
        add(
            "daily_loss_breaker",
            CRITICAL,
            "Daily loss breaker hit",
            "The session loss limit was breached. New exposure is blocked.",
        )

    if drawdown_breached:
        add(
            "drawdown_breaker",
            CRITICAL,
            "Drawdown breaker hit",
            "The drawdown limit was breached. The system is halted for new exposure.",
        )

    if str(health.get("status")) == "HALTED":
        add(
            "system_halted",
            CRITICAL,
            "Sterling halted",
            "Sterling is HALTED. No new trades will be taken until the underlying fault is cleared.",
        )

    # Preserve order, keep the first occurrence of each code.
    deduped: List[OperationalAlert] = []
    seen = set()
    for alert in alerts:
        if alert.code in seen:
            continue
        seen.add(alert.code)
        deduped.append(alert)
    return deduped


class AlertDispatcher:
    """Send alerts once per fault episode, re-sending only after repeat_after.

    An alert that disappears from a later poll is treated as recovered, so the same
    fault occurring again is a new episode and alerts again.
    """

    def __init__(
        self,
        *,
        sink: AlertSink,
        repeat_after: timedelta = DEFAULT_REPEAT_AFTER,
    ) -> None:
        self.sink = sink
        self.repeat_after = repeat_after
        self._last_sent: Dict[str, datetime] = {}

    def dispatch(
        self,
        alerts: Iterable[OperationalAlert],
        *,
        now: Optional[datetime] = None,
    ) -> DispatchResult:
        now = now or datetime.now(timezone.utc)
        alerts = list(alerts or [])
        active_codes = {a.code for a in alerts}
        result = DispatchResult()

        # Codes no longer present have recovered; forget them.
        for code in list(self._last_sent):
            if code not in active_codes:
                self._last_sent.pop(code, None)

        for alert in alerts:
            last = self._last_sent.get(alert.code)
            if last is not None and (now - last) < self.repeat_after:
                result.suppressed += 1
                continue
            try:
                self.sink.send(alert)
            except Exception as exc:
                # A broken transport must never stop monitoring.
                log.warning("Snapback alert transport failed for %s: %s", alert.code, exc)
                result.failed += 1
                continue
            self._last_sent[alert.code] = now
            result.sent += 1
            result.codes_sent.append(alert.code)

        return result


class LoggingAlertSink:
    """Default sink: write alerts to the application log."""

    def send(self, alert: OperationalAlert) -> None:
        level = logging.ERROR if alert.severity == CRITICAL else logging.WARNING
        log.log(level, "[SNAPBACK ALERT][%s] %s — %s", alert.severity, alert.title, alert.message)
