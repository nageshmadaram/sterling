"""Adapter from OperationalAlert to Sterling's existing Kite Telegram transport.

This adds no new notification system and no new bot credentials: it reuses the
encrypted per-user target store and its async ``send_via``. Only operational faults
are sent — never signals, wins or losses. With no configured target, logging remains
the fallback and health reports ``alert_transport_configured=false``.
"""

from __future__ import annotations

import asyncio
import html as _html
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, List

log = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))


def _targets(user_id: str) -> List[Any]:
    from app.services.notifications import kite_telegram_store as kts

    return list(kts.enabled_targets(user_id) or [])


def transport_configured(user_id: str = "default") -> bool:
    """Whether at least one enabled Telegram target exists. Unknown counts as false."""
    try:
        return bool(_targets(user_id))
    except Exception as exc:
        log.warning("Snapback alerts: Telegram target probe failed: %s", exc)
        return False


def format_alert_html(alert: Any, *, system_status: str = "", now: datetime | None = None) -> str:
    now = now or datetime.now(_IST)
    severity = str(getattr(alert, "severity", "WARNING")).upper()
    state = system_status or ("HALTED" if severity == "CRITICAL" else "DEGRADED")
    return (
        f"<b>[STERLING {_html.escape(severity)}]</b>\n"
        "Frozen Snapback prospective runtime\n\n"
        f"Fault: {_html.escape(str(getattr(alert, 'title', '')))}\n"
        f"State: {_html.escape(state)}\n"
        f"Time: {now.isoformat(timespec='seconds')}\n"
        f"Action: Open Sterling &rarr; Family Operations\n\n"
        f"{_html.escape(str(getattr(alert, 'message', '')))}"
    )


class TelegramAlertSink:
    """Synchronous sink bridging to the async Telegram transport.

    ``AlertDispatcher`` is synchronous and is called from inside the ops scheduler's
    async task, so a running loop gets a background task and a bare thread gets its
    own short-lived loop. Delivery is best effort: a failure is logged, never raised,
    because monitoring must outlive its own transport.
    """

    def __init__(self, user_id: str = "default", *, system_status_fn=None) -> None:
        self.user_id = user_id
        self.system_status_fn = system_status_fn

    def _status(self) -> str:
        if self.system_status_fn is None:
            return ""
        try:
            return str(self.system_status_fn() or "")
        except Exception:
            return ""

    async def _deliver(self, html: str) -> None:
        from app.services.notifications import kite_telegram_store as kts

        for target in _targets(self.user_id):
            try:
                await kts.send_via(target.bot_token, target.chat_id, html)
            except Exception as exc:
                log.warning("Snapback alerts: Telegram delivery failed: %s", exc)

    def targets_configured(self) -> bool:
        return transport_configured(self.user_id)

    async def deliver(self, alert: Any) -> None:
        """Await actual delivery, so a failure is a failure the caller can see."""
        await self._deliver(format_alert_html(alert, system_status=self._status()))

    def send(self, alert: Any) -> None:
        try:
            targets = _targets(self.user_id)
        except Exception as exc:
            log.warning("Snapback alerts: Telegram target lookup failed: %s", exc)
            return

        if not targets:
            log.info(
                "Snapback alerts: no Telegram target configured; logging only [%s] %s",
                getattr(alert, "severity", ""),
                getattr(alert, "title", ""),
            )
            return

        html = format_alert_html(alert, system_status=self._status())

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        try:
            if loop is not None:
                # Inside the ops scheduler's loop: deliver without blocking it.
                loop.create_task(self._deliver(html))
            else:
                # No loop (scripts, tests): deliver synchronously so the caller can
                # observe the outcome rather than racing a daemon thread.
                asyncio.run(self._deliver(html))
        except Exception as exc:
            log.warning("Snapback alerts: Telegram dispatch failed: %s", exc)


class CompositeAlertSink:
    """Send through several sinks; one failing sink never blocks the others."""

    def __init__(self, *sinks: Any) -> None:
        self.sinks = list(sinks)

    def targets_configured(self) -> bool:
        return transport_configured(self.user_id)

    async def deliver(self, alert: Any) -> None:
        """Await actual delivery, so a failure is a failure the caller can see."""
        await self._deliver(format_alert_html(alert, system_status=self._status()))

    def send(self, alert: Any) -> None:
        for sink in self.sinks:
            try:
                sink.send(alert)
            except Exception as exc:
                log.warning("Snapback alerts: sink %s failed: %s", type(sink).__name__, exc)
