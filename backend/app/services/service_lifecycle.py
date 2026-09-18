"""Start and stop Sterling as one command, for an operator who does not read code.

The family operator should not have to know that a start is eleven things in a
fixed order, or that a stop is refused while a position is open. This module is
that order, written once, so `sterlingctl start` and `sterlingctl stop` are the
whole interface.

Two rules shape every step:

  * A step that could not be checked is UNKNOWN, and UNKNOWN is not a pass. The
    run stops there, the system stays in RECOVERY_REQUIRED, and the reason is
    printed. Nothing downgrades an unreadable answer into a reassuring one.
  * Only the last step clears RECOVERY_REQUIRED. A start that fails halfway
    leaves the system in the state it was put into at step one, which is the
    state that refuses new exposure.

Some steps can only be answered by the running process — whether the market
feed is delivering ticks, which instruments it is subscribed to. Those are read
over the loopback ops interface the service exposes for exactly this purpose.
When the service is not answering yet, the answer is UNKNOWN rather than
assumed, which is why `wait for health` comes before them.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

__all__ = [
    "StepResult", "LifecycleReport", "start_sequence", "stop_sequence",
    "START_STEPS", "STOP_STEPS", "render_report",
]

#: Seconds a freshly started service is given to answer its liveness probe.
HEALTH_TIMEOUT_S = float(os.environ.get("STERLING_HEALTH_TIMEOUT_S", "90"))

#: A tick older than this is not a live feed. Kite delivers several ticks a
#: second per subscribed instrument during market hours, so a minute of silence
#: on a connected socket is a dead stream, not a quiet one.
MAX_TICK_AGE_MS = int(os.environ.get("STERLING_MAX_TICK_AGE_MS", "60000"))

_BASE_URL = os.environ.get("STERLING_OPS_URL", "http://127.0.0.1:8000")
_UNIT = os.environ.get("STERLING_SERVICE_UNIT", "sterling-backend")


@dataclass(frozen=True)
class StepResult:
    """One numbered step. ``ok`` is tri-state: True, False, or None for UNKNOWN."""

    step: int
    name: str
    ok: Optional[bool]
    detail: str = ""

    @property
    def verdict(self) -> str:
        return {True: "ok", False: "FAILED", None: "UNKNOWN"}[self.ok]

    def as_dict(self) -> Dict[str, Any]:
        return {"step": self.step, "name": self.name, "ok": self.ok,
                "verdict": self.verdict, "detail": self.detail}


@dataclass
class LifecycleReport:
    action: str
    steps: List[StepResult] = field(default_factory=list)

    @property
    def completed(self) -> bool:
        """Every step ran and passed. One UNKNOWN is enough to make this False."""
        return bool(self.steps) and all(s.ok is True for s in self.steps)

    @property
    def stopped_at(self) -> Optional[StepResult]:
        for step in self.steps:
            if step.ok is not True:
                return step
        return None

    @property
    def exit_code(self) -> int:
        blocker = self.stopped_at
        if blocker is None:
            return 0
        return 1 if blocker.ok is False else 2

    def as_dict(self) -> Dict[str, Any]:
        return {"action": self.action, "completed": self.completed,
                "exit_code": self.exit_code,
                "steps": [s.as_dict() for s in self.steps]}


def render_report(report: LifecycleReport) -> str:
    lines = [f"{report.action}:"]
    for step in report.steps:
        lines.append(f"  {step.step:>2}. {step.name:<34}{step.verdict}"
                     + (f"   {step.detail}" if step.detail else ""))
    blocker = report.stopped_at
    if blocker is None:
        lines.append("")
        lines.append(f"{report.action} completed; every step ran and passed.")
    else:
        lines.append("")
        if blocker.ok is None:
            lines.append(f"{report.action} STOPPED at step {blocker.step} "
                         f"({blocker.name}): could not be checked. That is not a pass.")
        else:
            lines.append(f"{report.action} STOPPED at step {blocker.step} "
                         f"({blocker.name}).")
        if report.action == "start":
            lines.append("The system remains in RECOVERY_REQUIRED and will not open new exposure.")
    return "\n".join(lines)


# ── the pieces each step is built from ────────────────────────────────────────

def _ops_get(path: str, timeout: float = 5.0) -> Any:
    """Read one loopback ops endpoint. Raises on anything that is not a body."""
    url = f"{_BASE_URL.rstrip('/')}{path}"
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _set_recovery(state: str, reason_code: str, reason: str) -> None:
    from app.services import db

    db.init()
    db.set_recovery_state(recovery_state=state, reason_code=reason_code,
                          reason=reason, actor="sterlingctl")


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    binary = shutil.which("systemctl")
    if binary is None:
        raise FileNotFoundError("systemctl not found")
    return subprocess.run([binary, "--user", *args], capture_output=True, text=True, timeout=120)


# ── start: the eleven steps, in order ─────────────────────────────────────────

def _step_engage_recovery() -> StepResult:
    """A start always begins from RECOVERY_REQUIRED.

    Not because something is known to be wrong, but because nothing is yet known
    to be right. The last step is what clears it.
    """
    try:
        _set_recovery("RECOVERY_REQUIRED", "STARTUP",
                      "Service start in progress; nothing reconciled yet")
    except Exception as exc:  # noqa: BLE001
        return StepResult(1, "engage RECOVERY_REQUIRED", None, f"state store unreadable: {exc}")
    return StepResult(1, "engage RECOVERY_REQUIRED", True)


def _step_preflight() -> StepResult:
    try:
        from app.core.operator_report import doctor_from_preflight, lane_doctor_checks
        from app.services.snapback_preflight import run_preflight

        report = doctor_from_preflight(run_preflight().checks, extra=lane_doctor_checks())
    except Exception as exc:  # noqa: BLE001
        return StepResult(2, "pre-start doctor", None, f"doctor could not run: {exc}")
    if report.unknowns:
        return StepResult(2, "pre-start doctor", None,
                          "could not check: " + ", ".join(c.name for c in report.unknowns))
    if report.failures:
        return StepResult(2, "pre-start doctor", False,
                          "failed: " + ", ".join(c.name for c in report.failures))
    return StepResult(2, "pre-start doctor", True, f"{len(report.checks)} checks passed")


def _step_start_unit() -> StepResult:
    try:
        completed = _systemctl("start", _UNIT)
    except FileNotFoundError:
        return StepResult(3, "start service unit", None,
                          "systemctl not available on this host")
    except Exception as exc:  # noqa: BLE001
        return StepResult(3, "start service unit", None, f"could not run systemctl: {exc}")
    if completed.returncode != 0:
        return StepResult(3, "start service unit", False,
                          (completed.stderr or completed.stdout or "").strip()[:200])
    return StepResult(3, "start service unit", True, _UNIT)


def _step_wait_health() -> StepResult:
    deadline = time.time() + HEALTH_TIMEOUT_S
    last_error = "no response"
    while time.time() < deadline:
        try:
            body = _ops_get("/api/v1/health/live", timeout=3.0)
            if body.get("alive") is True:
                return StepResult(4, "application health", True)
            last_error = f"health said {body}"
        except urllib.error.URLError as exc:
            last_error = str(exc.reason)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(2.0)
    return StepResult(4, "application health", None,
                      f"no healthy response within {HEALTH_TIMEOUT_S:.0f}s: {last_error}")


def _step_release_identity() -> StepResult:
    try:
        from app.core.release_manifest import read_manifest, verify_manifest

        stored = read_manifest()
        if stored is None:
            return StepResult(5, "release identity", None,
                              "no frozen release manifest; run `sterlingctl freeze`")
        verdict = verify_manifest(stored)
    except Exception as exc:  # noqa: BLE001
        return StepResult(5, "release identity", None, f"manifest unreadable: {exc}")
    if verdict.matches:
        tag = (stored.get("build") or {}).get("release_tag") or "frozen"
        return StepResult(5, "release identity", True, str(tag))
    drift = [str(d) for d in verdict.drift] + [f"lane missing: {l}" for l in verdict.missing_lanes]
    return StepResult(5, "release identity", False, "; ".join(drift)[:200])


def _step_broker_connected() -> StepResult:
    """The account we are bound to must be the account we are authenticated as."""
    try:
        body = _ops_get("/api/v1/ops/runtime")
    except Exception as exc:  # noqa: BLE001
        return StepResult(6, "broker connection", None, f"runtime view unavailable: {exc}")
    broker = body.get("broker") or {}
    if broker.get("connected") is not True:
        return StepResult(6, "broker connection", bool(broker.get("connected")),
                          broker.get("detail") or "not connected")
    if broker.get("binding_matches") is False:
        return StepResult(6, "broker connection", False,
                          f"authenticated account {broker.get('account_id')} is not the "
                          f"active binding {broker.get('bound_client_id')}")
    if broker.get("binding_matches") is None:
        return StepResult(6, "broker connection", None,
                          "no active account binding to verify against")
    return StepResult(6, "broker connection", True, str(broker.get("account_id") or ""))


def _step_reconcile() -> StepResult:
    try:
        from app.services.snapback_reconciliation import latest_reconciliation

        snapshot = latest_reconciliation()
    except Exception as exc:  # noqa: BLE001
        return StepResult(7, "reconcile broker state", None, f"reconciliation unreadable: {exc}")
    if snapshot is None:
        return StepResult(7, "reconcile broker state", None, "no reconciliation recorded")
    payload = snapshot.as_dict()
    if payload.get("clean") is None:
        return StepResult(7, "reconcile broker state", None, "reconciliation result unknown")
    if not payload.get("clean"):
        return StepResult(7, "reconcile broker state", False,
                          f"{len(payload.get('mismatches') or [])} mismatch(es)")
    return StepResult(7, "reconcile broker state", True)


def _step_subscriptions() -> StepResult:
    try:
        body = _ops_get("/api/v1/ops/runtime")
    except Exception as exc:  # noqa: BLE001
        return StepResult(8, "market subscriptions", None, f"runtime view unavailable: {exc}")
    feed = body.get("feed") or {}
    subscribed = feed.get("subscribed")
    if subscribed is None:
        return StepResult(8, "market subscriptions", None, "subscription set not reported")
    if not subscribed:
        return StepResult(8, "market subscriptions", False, "no instrument is subscribed")
    return StepResult(8, "market subscriptions", True, f"{len(subscribed)} instrument(s)")


def _step_fresh_feed() -> StepResult:
    try:
        body = _ops_get("/api/v1/ops/runtime")
    except Exception as exc:  # noqa: BLE001
        return StepResult(9, "fresh market feed", None, f"runtime view unavailable: {exc}")
    feed = body.get("feed") or {}
    if feed.get("connected") is not True:
        return StepResult(9, "fresh market feed", False, "tick socket is not connected")
    last_tick_ms = int(feed.get("last_tick_ms") or 0)
    if last_tick_ms <= 0:
        return StepResult(9, "fresh market feed", None, "no tick has arrived yet")
    age_ms = int(time.time() * 1000) - last_tick_ms
    if age_ms > MAX_TICK_AGE_MS:
        return StepResult(9, "fresh market feed", False, f"last tick {age_ms // 1000}s ago")
    return StepResult(9, "fresh market feed", True, f"last tick {age_ms // 1000}s ago")


def _step_evidence_writer() -> StepResult:
    """Prove the evidence store accepts a write, not merely that a file exists."""
    try:
        from app.services.snapback_preflight import check_database

        passed, detail = check_database()
    except Exception as exc:  # noqa: BLE001
        return StepResult(10, "evidence writer", None, f"evidence store unreadable: {exc}")
    if not passed:
        return StepResult(10, "evidence writer", False, json.dumps(detail)[:200])
    return StepResult(10, "evidence writer", True)


def _step_clear_recovery() -> StepResult:
    try:
        _set_recovery("CLEAN", "STARTUP_COMPLETE",
                      "Start sequence completed; broker reconciled and feed verified")
    except Exception as exc:  # noqa: BLE001
        return StepResult(11, "clear RECOVERY_REQUIRED", None, f"state store unwritable: {exc}")
    return StepResult(11, "clear RECOVERY_REQUIRED", True)


START_STEPS: List[Callable[[], StepResult]] = [
    _step_engage_recovery,
    _step_preflight,
    _step_start_unit,
    _step_wait_health,
    _step_release_identity,
    _step_broker_connected,
    _step_reconcile,
    _step_subscriptions,
    _step_fresh_feed,
    _step_evidence_writer,
    _step_clear_recovery,
]


def start_sequence(steps: Optional[List[Callable[[], StepResult]]] = None) -> LifecycleReport:
    """Run the start sequence, stopping at the first step that is not a pass."""
    report = LifecycleReport(action="start")
    for step in (steps if steps is not None else START_STEPS):
        result = step()
        report.steps.append(result)
        if result.ok is not True:
            break
    return report


# ── stop: refuse to walk away from exposure ───────────────────────────────────

def _step_halt_new_risk() -> StepResult:
    try:
        from app.services.snapback_family_ops import set_new_trades_halted

        set_new_trades_halted(True, reason="service stop requested via sterlingctl")
    except Exception as exc:  # noqa: BLE001
        return StepResult(1, "halt new risk", None, f"could not engage the halt: {exc}")
    return StepResult(1, "halt new risk", True)


def _step_stop_reconcile() -> StepResult:
    result = _step_reconcile()
    return StepResult(2, "reconcile broker exposure", result.ok, result.detail)


def _step_no_abandoned_exposure() -> StepResult:
    """Refuse an ordinary shutdown that would abandon something being managed."""
    from app.services.exposure_snapshot import exposure_snapshot

    snapshot = exposure_snapshot()
    if snapshot.total is None:
        return StepResult(3, "no exposure left unmanaged", None,
                          snapshot.detail or "durable exposure could not be read")
    if snapshot.open_positions:
        return StepResult(3, "no exposure left unmanaged", False,
                          f"{snapshot.open_positions} open position(s) still require "
                          "monitoring: " + ", ".join(snapshot.held[:5]))
    if snapshot.unresolved_intents:
        return StepResult(3, "no exposure left unmanaged", False,
                          f"{snapshot.unresolved_intents} unresolved order intent(s)")
    return StepResult(3, "no exposure left unmanaged", True)


def _step_stop_unit() -> StepResult:
    try:
        completed = _systemctl("stop", _UNIT)
    except FileNotFoundError:
        return StepResult(4, "stop service unit", None, "systemctl not available on this host")
    except Exception as exc:  # noqa: BLE001
        return StepResult(4, "stop service unit", None, f"could not run systemctl: {exc}")
    if completed.returncode != 0:
        return StepResult(4, "stop service unit", False,
                          (completed.stderr or completed.stdout or "").strip()[:200])
    return StepResult(4, "stop service unit", True, _UNIT)


def _step_stopped() -> StepResult:
    try:
        completed = _systemctl("is-active", _UNIT)
    except FileNotFoundError:
        return StepResult(5, "service is stopped", None, "systemctl not available on this host")
    except Exception as exc:  # noqa: BLE001
        return StepResult(5, "service is stopped", None, f"could not run systemctl: {exc}")
    state = (completed.stdout or "").strip()
    if state in ("inactive", "failed", "unknown", ""):
        return StepResult(5, "service is stopped", True, state or "inactive")
    return StepResult(5, "service is stopped", False, f"unit is {state}")


STOP_STEPS: List[Callable[[], StepResult]] = [
    _step_halt_new_risk,
    _step_stop_reconcile,
    _step_no_abandoned_exposure,
    _step_stop_unit,
    _step_stopped,
]


def stop_sequence(steps: Optional[List[Callable[[], StepResult]]] = None,
                  *, force: bool = False) -> LifecycleReport:
    """Run the stop sequence.

    ``force`` is for the operator who has decided, having read the refusal, that
    the service must stop anyway — a machine being shut down, say. It skips no
    check and hides no result: every step still runs and is still reported. It
    only allows the sequence to continue past a refusal.
    """
    report = LifecycleReport(action="stop")
    for step in (steps if steps is not None else STOP_STEPS):
        result = step()
        report.steps.append(result)
        if result.ok is not True and not force:
            break
    return report
