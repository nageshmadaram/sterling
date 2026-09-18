"""Alerts an operator can act on, and one incident per root cause.

An alert that only says what broke is not actionable. Section 15 of the handoff
roadmap requires four things from every notification, so they are four required
fields here rather than four conventions in a message string:

1. what happened;
2. whether new trading is blocked;
3. which positions are affected;
4. the next safe operator action.

An alert constructed without a next action raises. "Do not alert only with
stack traces" is a rule that has to be enforced somewhere, and the constructor
is the only place it cannot be forgotten.

The second job is flood control. One root cause must not arrive as ten
independent critical incidents: a dead database also fails the backup, the
report and the session package, and an operator paging through four alerts at
06:00 will fix the wrong one. :func:`collapse_to_root_causes` keeps the cause
and files the rest underneath it, visible but not competing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final, Iterable, Mapping, Sequence


class Severity(StrEnum):
    """Three levels, three different expectations of the reader."""

    #: Goes into the daily digest. Nobody is woken up.
    INFO = "INFO"
    #: An operator should look today.
    WARNING = "WARNING"
    #: Immediate notification, and new exposure is blocked.
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class OperatorAlert:
    """One actionable notification."""

    code: str
    severity: Severity

    #: Plain language. What actually happened, not the exception class.
    what_happened: str

    #: Is new exposure blocked right now? Not "should be" — is.
    trading_blocked: bool

    #: The single next thing the operator should do.
    next_action: str

    #: Positions, lanes or instruments this touches. Empty means none known,
    #: which is different from none affected and is rendered as such.
    affected: tuple[str, ...] = field(default_factory=tuple)

    #: Set when this alert is a downstream effect of another one.
    caused_by: str | None = None

    def __post_init__(self) -> None:
        if not self.next_action.strip():
            raise ValueError(
                f"alert {self.code!r} has no next action; an alert the operator "
                "cannot act on is noise"
            )
        if not self.what_happened.strip():
            raise ValueError(f"alert {self.code!r} does not say what happened")
        if self.severity is Severity.CRITICAL and not self.trading_blocked:
            # Every CRITICAL in the policy blocks new exposure. One that does
            # not is either mis-severitied or a safety hole, and both are worth
            # failing loudly for.
            raise ValueError(
                f"alert {self.code!r} is CRITICAL but does not block new trading"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": str(self.severity),
            "what_happened": self.what_happened,
            "trading_blocked": self.trading_blocked,
            "next_action": self.next_action,
            "affected": list(self.affected),
            "caused_by": self.caused_by,
        }


def render_alert(alert: OperatorAlert) -> str:
    """The four required lines, in the order an operator reads them."""
    affected = ", ".join(alert.affected) if alert.affected else "none identified"
    lines = [
        f"[{alert.severity}] {alert.code}",
        alert.what_happened,
        f"New trading: {'BLOCKED' if alert.trading_blocked else 'still allowed'}",
        f"Affected: {affected}",
        f"Do this: {alert.next_action}",
    ]
    if alert.caused_by:
        lines.append(f"(downstream of {alert.caused_by})")
    return "\n".join(lines)


#: What to do about each known fault code, and whether it blocks new exposure.
#: Codes match :func:`app.services.snapback_alerts.derive_operational_alerts`.
ACTIONS: Final[Mapping[str, tuple[Severity, bool, str]]] = {
    "database_unavailable": (
        Severity.CRITICAL,
        True,
        "Run `sterlingctl safe on`, then `sterlingctl restore-check`. Do not "
        "trade while records are not durable.",
    ),
    "manifest_mismatch": (
        Severity.CRITICAL,
        True,
        "Run `sterlingctl verify` and read the drift list. Evidence recorded "
        "since the change belongs to a different identity.",
    ),
    "runner_stopped": (
        Severity.CRITICAL,
        True,
        "Check open positions in the broker app first, then restart with "
        "`sterlingctl start`.",
    ),
    "broker_disconnected": (
        Severity.WARNING,
        True,
        "Log in to the broker. Until then Sterling cannot see the real book.",
    ),
    "market_data_stale": (
        Severity.WARNING,
        True,
        "Confirm the market session and the data feed. Never trade on the last "
        "known price.",
    ),
    "calendar_failure": (
        Severity.CRITICAL,
        True,
        "Restore the trading calendar. Sterling fails closed until it is "
        "available.",
    ),
    "position_reconciliation_mismatch": (
        Severity.CRITICAL,
        True,
        "Open the broker app. The broker is the source of truth. Run "
        "`sterlingctl reconcile` and resolve every line before anything else.",
    ),
    "exit_pending_unresolved": (
        Severity.CRITICAL,
        True,
        "Confirm in the broker whether the position is actually flat. If it is "
        "not, exit it there.",
    ),
    "pending_entry_stuck": (
        Severity.CRITICAL,
        True,
        "Query the broker for the order state. Do NOT resubmit.",
    ),
    "stop_observation_gap": (
        Severity.CRITICAL,
        True,
        "Restore observation, or exit the unobserved position. A stop breach "
        "can pass unseen while this lasts.",
    ),
    "backup_failed": (
        Severity.CRITICAL,
        True,
        "Run `sterlingctl backup` then `sterlingctl restore-check`. The "
        "evidence is unprotected until both pass.",
    ),
    "evidence_report_failed": (
        Severity.CRITICAL,
        True,
        "Run `sterlingctl report`. Today's evidence package is not complete "
        "until it succeeds.",
    ),
    "session_evidence_incomplete": (
        Severity.CRITICAL,
        True,
        "Read the gap codes in `sterlingctl report`. Do not re-run the scanner "
        "to clear them; the gap is permanent by design.",
    ),
    "daily_loss_breaker": (
        Severity.CRITICAL,
        True,
        "Leave the block in place for the session. Do not raise the limit.",
    ),
    "drawdown_breaker": (
        Severity.CRITICAL,
        True,
        "Leave the block in place. A drawdown breach is reviewed, never "
        "overridden in the moment.",
    ),
    "system_halted": (
        Severity.CRITICAL,
        True,
        "Read the underlying fault below this alert. Clear the cause, not the "
        "halt.",
    ),
}


#: Downstream effect -> the cause that explains it. A dead database also fails
#: the backup, the report and the session package; paging all four sends the
#: operator to the wrong one first.
CAUSED_BY: Final[Mapping[str, tuple[str, ...]]] = {
    "backup_failed": ("database_unavailable",),
    "evidence_report_failed": ("database_unavailable",),
    "session_evidence_incomplete": (
        "database_unavailable",
        "runner_stopped",
        "market_data_stale",
        "broker_disconnected",
    ),
    "market_data_stale": ("broker_disconnected",),
    "exit_pending_unresolved": ("runner_stopped", "broker_disconnected"),
    "pending_entry_stuck": ("runner_stopped", "broker_disconnected"),
    "stop_observation_gap": ("runner_stopped", "market_data_stale"),
    # The halt is the effect of whichever breaker or fault tripped it.
    "system_halted": (
        "daily_loss_breaker",
        "drawdown_breaker",
        "database_unavailable",
        "position_reconciliation_mismatch",
        "runner_stopped",
    ),
}


def from_operational(
    alert: Any,
    *,
    affected: Sequence[str] = (),
) -> OperatorAlert:
    """Turn a :class:`OperationalAlert` into an actionable one.

    An unknown code is not dropped and is not given an invented action. It is
    reported at its original severity with an explicit "no action is written
    down for this", which is a true statement and a visible gap in this table.
    """
    code = str(getattr(alert, "code", "") or "unknown")
    known = ACTIONS.get(code)

    if known is not None:
        severity, blocked, action = known
    else:
        raw = str(getattr(alert, "severity", "") or "WARNING").upper()
        severity = Severity.CRITICAL if raw == "CRITICAL" else Severity.WARNING
        blocked = severity is Severity.CRITICAL
        action = (
            f"No action is written down for {code!r}. Block new trading with "
            "`sterlingctl safe on` and escalate to the technical contact."
        )

    message = str(getattr(alert, "message", "") or getattr(alert, "title", "") or code)

    return OperatorAlert(
        code=code,
        severity=severity,
        what_happened=message,
        trading_blocked=blocked,
        next_action=action,
        affected=tuple(affected),
    )


def daily_digest(
    *,
    session_status: str,
    signals_found: int,
    lanes_originating: int,
    backup_passed: bool | None,
    restore_checked: bool | None = None,
) -> list[OperatorAlert]:
    """The INFO-severity lines that belong in a digest, not in a notification.

    Section 15 asks for an INFO level: daily start healthy, backup completed, no
    signals. Without a producer the level existed and nothing ever used it, so
    a quiet day reached the operator as silence — and silence is what a dead
    scheduler also looks like.

    Nothing here blocks trading. Anything that should is a WARNING or CRITICAL
    and comes from the fault path instead.
    """
    rows: list[OperatorAlert] = [
        OperatorAlert(
            code="session_status",
            severity=Severity.INFO,
            what_happened=f"Session status is {session_status}.",
            trading_blocked=False,
            next_action="Nothing. Read it in the daily digest.",
        ),
        OperatorAlert(
            code="lane_origination",
            severity=Severity.INFO,
            what_happened=(
                f"{lanes_originating} of 10 lanes may open a trade today."
            ),
            trading_blocked=False,
            next_action=(
                "Nothing. A lane that may not originate has not earned a sample "
                "yet; `sterlingctl lanes` says why."
            ),
        ),
    ]

    if signals_found == 0:
        rows.append(
            OperatorAlert(
                code="no_signals",
                severity=Severity.INFO,
                what_happened="No signals today. The scan ran and found nothing.",
                trading_blocked=False,
                next_action=(
                    "Nothing. Check the session line above: a quiet market and a "
                    "scan that never ran look identical without it."
                ),
            )
        )
    else:
        rows.append(
            OperatorAlert(
                code="signals_found",
                severity=Severity.INFO,
                what_happened=f"{signals_found} signal(s) recorded.",
                trading_blocked=False,
                next_action="Nothing. Review them in the weekly evidence review.",
            )
        )

    # An unknown backup result is NOT reported as success here. It is left out,
    # and the fault path raises backup_failed, so the digest can never be the
    # thing that says a backup happened when nobody checked.
    if backup_passed is True:
        rows.append(
            OperatorAlert(
                code="backup_completed",
                severity=Severity.INFO,
                what_happened="Evidence backup completed.",
                trading_blocked=False,
                next_action=(
                    "Nothing"
                    if restore_checked
                    else "Run `sterlingctl restore-check`; an untested backup is not proved."
                ),
            )
        )

    return rows


def render_digest(rows: Iterable[OperatorAlert]) -> str:
    """One line per INFO item. Nothing here needs an operator to act."""
    lines = [f"- {row.what_happened}" for row in rows if row.severity is Severity.INFO]
    return "\n".join(lines) or "- Nothing to report."


def collapse_to_root_causes(
    alerts: Iterable[OperatorAlert],
) -> tuple[tuple[OperatorAlert, ...], tuple[OperatorAlert, ...]]:
    """Split alerts into root causes and their downstream effects.

    Nothing is discarded. The effects keep their severity and their action, and
    gain ``caused_by``, so the incident reads as one problem with consequences
    instead of ten competing emergencies.
    """
    rows = list(alerts)
    present = {row.code for row in rows}

    roots: list[OperatorAlert] = []
    effects: list[OperatorAlert] = []

    for row in rows:
        cause = next(
            (c for c in CAUSED_BY.get(row.code, ()) if c in present and c != row.code),
            None,
        )
        if cause is None:
            roots.append(row)
        else:
            effects.append(
                OperatorAlert(
                    code=row.code,
                    severity=row.severity,
                    what_happened=row.what_happened,
                    trading_blocked=row.trading_blocked,
                    next_action=row.next_action,
                    affected=row.affected,
                    caused_by=cause,
                )
            )

    return tuple(roots), tuple(effects)


def should_enter_safe_mode(alerts: Iterable[OperatorAlert]) -> bool:
    """Any CRITICAL blocks new exposure. Policy, not a judgement call."""
    return any(a.severity is Severity.CRITICAL for a in alerts)


def render_incident(alerts: Iterable[OperatorAlert]) -> str:
    """One incident: the root causes first, their effects filed underneath."""
    roots, effects = collapse_to_root_causes(alerts)
    if not roots and not effects:
        return "No alerts."

    blocks = [render_alert(row) for row in roots]
    if effects:
        blocks.append(
            "Downstream of the above (fix the cause, not these):\n"
            + "\n".join(f"  - {e.code} (from {e.caused_by})" for e in effects)
        )
    if should_enter_safe_mode([*roots, *effects]):
        blocks.append("SAFE_MODE: new exposure is blocked until this is resolved.")
    return "\n\n".join(blocks)


__all__ = [
    "ACTIONS",
    "CAUSED_BY",
    "OperatorAlert",
    "Severity",
    "collapse_to_root_causes",
    "daily_digest",
    "from_operational",
    "render_digest",
    "render_alert",
    "render_incident",
    "should_enter_safe_mode",
]
