"""One message a day, including on the days when nothing happened.

A monitoring system that only speaks when something is wrong is
indistinguishable from a monitoring system that has died. The digest is sent on
a quiet day precisely so that silence becomes information: if it does not
arrive, the scheduler is the thing to look at.

Every line is read from the same sources the operator's own commands read, so
the digest cannot say something the doctor would contradict. Anything unknown is
printed as UNKNOWN rather than left out — a missing line reads as "fine" to a
tired person at 6am.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

__all__ = ["Digest", "build_digest", "render_digest"]

UNKNOWN = "UNKNOWN"


@dataclass
class Digest:
    generated_at: str
    release_tag: str = UNKNOWN
    runtime_sha: str = UNKNOWN
    health: str = UNKNOWN
    broker: str = UNKNOWN
    account_binding: str = UNKNOWN
    safety: str = UNKNOWN
    recovery: str = UNKNOWN
    open_exposure: str = UNKNOWN
    unresolved_exposure: str = UNKNOWN
    external_exposure: str = UNKNOWN
    market_feed: str = UNKNOWN
    backup_age: str = UNKNOWN
    lanes: list[str] = field(default_factory=list)
    next_action: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "release_tag": self.release_tag,
            "runtime_sha": self.runtime_sha,
            "health": self.health,
            "broker": self.broker,
            "account_binding": self.account_binding,
            "safety": self.safety,
            "recovery": self.recovery,
            "open_exposure": self.open_exposure,
            "unresolved_exposure": self.unresolved_exposure,
            "external_exposure": self.external_exposure,
            "market_feed": self.market_feed,
            "backup_age": self.backup_age,
            "lanes": list(self.lanes),
            "next_action": self.next_action,
        }


def _safe(fn, default: str = UNKNOWN) -> str:
    try:
        value = fn()
    except Exception as exc:  # noqa: BLE001 - a digest must always be produced
        log.info("digest: %s", exc)
        return default
    return default if value is None else str(value)


def build_digest() -> Digest:
    """Assemble the digest from live state. Never raises."""
    digest = Digest(generated_at=datetime.now(timezone.utc).isoformat())

    from app.core.release_manifest import release_tag, runtime_sha

    digest.release_tag = _safe(release_tag)
    digest.runtime_sha = _safe(lambda: runtime_sha()[:12])

    checks: dict[str, Any] = {}
    try:
        from app.core.operator_report import doctor_from_preflight, lane_doctor_checks
        from app.services.snapback_preflight import run_preflight

        report = doctor_from_preflight(run_preflight().checks, extra=lane_doctor_checks())
        checks = {c.name: c for c in report.checks}
        if report.unknowns:
            digest.health = "BLOCKED (checks could not be run)"
        elif report.failures:
            digest.health = "BLOCKED: " + ", ".join(c.name for c in report.failures[:4])
        else:
            digest.health = "PASS"
    except Exception as exc:  # noqa: BLE001
        digest.health = f"{UNKNOWN} (doctor could not run: {exc})"

    def _detail(name: str) -> str:
        check = checks.get(name)
        if check is None:
            return UNKNOWN
        mark = {True: "ok", False: "FAIL", None: UNKNOWN}[check.passed]
        return f"{mark}: {check.detail}" if check.detail else mark

    digest.broker = _detail("broker_session")
    digest.account_binding = _detail("account_binding")
    digest.safety = _detail("safe_mode_state")
    digest.recovery = _detail("execution_control_state")
    digest.market_feed = _detail("market_freshness")
    digest.backup_age = _detail("backup_age")

    try:
        from app.services.exposure_snapshot import exposure_snapshot

        snapshot = exposure_snapshot()
        digest.open_exposure = (UNKNOWN if snapshot.open_positions is None
                                else str(snapshot.open_positions))
        digest.unresolved_exposure = (UNKNOWN if snapshot.unresolved_intents is None
                                      else str(snapshot.unresolved_intents))
        if snapshot.external_positions is None:
            digest.external_exposure = f"{UNKNOWN} ({snapshot.external_detail})"
        elif snapshot.external_positions:
            digest.external_exposure = (
                f"{snapshot.external_positions} NOT MANAGED BY STERLING: "
                + ", ".join(snapshot.external_instruments[:5]))
        else:
            digest.external_exposure = "0"
    except Exception:  # noqa: BLE001
        pass

    try:
        from app.core.lane_registry import LANES
        from app.services.lane_gate_inputs import lane_verdicts

        for lane_key in sorted(LANES):
            verdicts = lane_verdicts(lane_key)
            digest.lanes.append(
                f"{lane_key}: {LANES[lane_key].state.value}"
                f" | economic {verdicts.economic.value}"
                f" | shadow {verdicts.shadow_execution.value}"
                f" | operational {verdicts.operational.value}"
            )
    except Exception as exc:  # noqa: BLE001
        digest.lanes.append(f"{UNKNOWN}: lane verdicts could not be read ({exc})")

    digest.next_action = _next_action(digest)
    return digest


def _next_action(digest: Digest) -> str:
    """The single most useful thing to do next, in the operator's own words."""
    # A live position nobody is watching outranks every procedural state: the
    # others cost time, this one can cost money while the operator reads.
    if (digest.external_exposure not in ("0", UNKNOWN)
            and not digest.external_exposure.startswith(UNKNOWN)):
        return ("The broker holds a position Sterling did not open. Decide whether to "
                "hold or exit it at the broker; Sterling will not manage it.")
    if digest.recovery.startswith("FAIL"):
        return "Run `sterlingctl reconcile`, then `sterlingctl doctor`."
    if digest.safety.startswith("FAIL"):
        return "SAFE_MODE is engaged. Read the reason, fix it, then `sterlingctl safe off --ack`."
    if digest.open_exposure not in ("0", UNKNOWN):
        return "There is open exposure. Check it is protected before anything else."
    if digest.broker.startswith(UNKNOWN) or digest.broker.startswith("FAIL"):
        return "Log in to Kite again — see docs/operations/BROKER_REAUTH.md."
    if digest.backup_age.startswith("FAIL"):
        return "Run `sterlingctl backup` and then `sterlingctl restore-check`."
    if digest.health != "PASS":
        return "Run `sterlingctl doctor` and clear what it names."
    return "Nothing. The system is collecting evidence."


def render_digest(digest: Digest) -> str:
    lines = [
        "Sterling daily status",
        f"Release: {digest.release_tag} / {digest.runtime_sha}",
        f"Health: {digest.health}",
        f"Broker: {digest.broker}",
        f"Account binding: {digest.account_binding}",
        f"Safety: {digest.safety} / {digest.recovery}",
        f"Exposure: open={digest.open_exposure} unresolved={digest.unresolved_exposure}",
        f"External broker exposure: {digest.external_exposure}",
        f"Market feed: {digest.market_feed}",
        f"Backup: {digest.backup_age}",
        "Lanes:",
    ]
    lines.extend(f"  {lane}" for lane in digest.lanes)
    lines.append(f"Next safe action: {digest.next_action}")
    return "\n".join(lines)
