"""What the operator sees: lane states, health, and the doctor verdict.

This is a reporting layer over machinery that already exists — the preflight
checks, the safe-mode file, the backup manifests and the lane registry — rather
than a second copy of any of it. A second backup checker or a second holiday
list would eventually disagree with the first, and the disagreement would
surface as a mystery rather than an error.

The one rule it adds is the doctor exit code: a check that could not be run is
a failure, not a skip. ``sterlingctl doctor`` exiting zero has to mean "I looked
and it is fine", never "I could not look".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from app.core.health import (
    ComponentHealth,
    HealthReport,
    SystemHealth,
    compose_health,
)
from app.core.lane_registry import dashboard_rows

#: Exit codes for the operator CLI. Distinct so a wrapper script can react.
EXIT_OK = 0
EXIT_UNSAFE = 1
EXIT_COULD_NOT_CHECK = 2


@dataclass(frozen=True)
class DoctorCheck:
    """One thing the doctor looked at."""

    name: str
    #: ``None`` means the check could not be run. That is NOT a pass.
    passed: bool | None
    detail: str = ""

    @property
    def blocking(self) -> bool:
        return self.passed is not True


@dataclass(frozen=True)
class DoctorReport:
    checks: tuple[DoctorCheck, ...] = field(default_factory=tuple)
    health: HealthReport | None = None

    @property
    def failures(self) -> tuple[DoctorCheck, ...]:
        return tuple(c for c in self.checks if c.passed is False)

    @property
    def unknowns(self) -> tuple[DoctorCheck, ...]:
        return tuple(c for c in self.checks if c.passed is None)

    @property
    def safe(self) -> bool:
        """Safe only when every check ran and every check passed."""
        return bool(self.checks) and not self.failures and not self.unknowns

    @property
    def exit_code(self) -> int:
        if not self.checks:
            # Nothing ran at all. Reporting success here is the single worst
            # thing this file could do.
            return EXIT_COULD_NOT_CHECK
        if self.failures:
            return EXIT_UNSAFE
        if self.unknowns:
            return EXIT_COULD_NOT_CHECK
        return EXIT_OK

    def as_dict(self) -> dict[str, Any]:
        return {
            "safe": self.safe,
            "exit_code": self.exit_code,
            "checks": [
                {
                    "name": c.name,
                    "result": "pass"
                    if c.passed is True
                    else ("fail" if c.passed is False else "could_not_check"),
                    "detail": c.detail,
                }
                for c in self.checks
            ],
            "health": self.health.as_dict() if self.health else None,
        }


def run_checks(
    checks: Mapping[str, Callable[[], tuple[bool, str]]],
) -> tuple[DoctorCheck, ...]:
    """Run every check, converting an exception into could-not-check.

    Every check runs even after one fails: a doctor that stops at the first
    problem invites a fix-one-rerun loop and hides how much is wrong.
    """
    results: list[DoctorCheck] = []
    for name in sorted(checks):
        try:
            passed, detail = checks[name]()
        except Exception as exc:  # noqa: BLE001 - the whole point is to catch all
            results.append(
                DoctorCheck(name, None, f"{type(exc).__name__}: {exc}")
            )
        else:
            results.append(DoctorCheck(name, bool(passed), detail))
    return tuple(results)


def doctor_from_preflight(
    preflight_checks: Sequence[Any],
    *,
    components: Mapping[str, ComponentHealth] | None = None,
    extra: Sequence[DoctorCheck] = (),
) -> DoctorReport:
    """Build a doctor report from existing ``PreflightCheck`` objects.

    Accepts anything with ``code`` and ``passed``, so the preflight module does
    not have to import this one.
    """
    checks = tuple(
        DoctorCheck(
            name=getattr(c, "code", "unknown"),
            passed=bool(getattr(c, "passed", False)),
            detail=str(getattr(c, "details", "") or ""),
        )
        for c in preflight_checks
    )
    health = compose_health(dict(components)) if components is not None else None
    return DoctorReport(checks=checks + tuple(extra), health=health)


def lane_doctor_checks() -> tuple[DoctorCheck, ...]:
    """Sterling-specific gaps the generic preflight does not cover.

    Each is something an operator can act on and would otherwise only discover
    by reading code: a focus setting that will not parse, a release that cannot
    be named, a risk hierarchy nobody configured, and whether any lane is
    actually allowed to trade.
    """
    import os

    checks: list[DoctorCheck] = []

    def add(name: str, fn):
        try:
            passed, detail = fn()
        except Exception as exc:  # noqa: BLE001 - a check that cannot run is not a pass
            checks.append(DoctorCheck(name, None, f"{type(exc).__name__}: {exc}"))
        else:
            checks.append(DoctorCheck(name, passed, detail))

    def _focus():
        from app.core.focus import focus_policy

        policy = focus_policy()
        return True, f"originators: {', '.join(sorted(policy.originators)) or 'none'}"

    def _release_tag():
        tag = (os.environ.get("STERLING_RELEASE_TAG") or "").strip()
        if not tag:
            # Not a failure of the runtime, but evidence written now cannot name
            # the release that produced it, so it will be unattributed.
            return False, (
                "STERLING_RELEASE_TAG unset: new evidence will be recorded "
                "unattributed and excluded from every lane gate"
            )
        return True, tag

    def _risk_limits():
        from app.core.risk_hierarchy import ENV_VAR, configured_hierarchy

        hierarchy = configured_hierarchy()
        if hierarchy is None:
            return False, (
                f"{ENV_VAR} unset: the four-level risk hierarchy is not enforced; "
                "only the capital arithmetic in capacity applies"
            )
        return True, f"config_hash {hierarchy.config_hash}"

    def _lanes():
        rows = dashboard_rows()
        open_lanes = [r["lane_key"] for r in rows if r["may_originate"]]
        return True, (
            f"{len(open_lanes)} of {len(rows)} lanes may originate"
            + (f": {', '.join(open_lanes)}" if open_lanes else "")
        )

    def _supertrend_frozen():
        from app.engines.sterling_kite_engine.lanes import audit_core_parity

        findings = audit_core_parity()
        if findings:
            return False, "; ".join(str(f) for f in findings)
        return True, "supertrend_core_v1 matches the frozen record"

    def _static_egress():
        from app.services.continuity_doctor import egress_status

        status = egress_status()
        # Not a failure of today's shadow operation — market data does not need
        # a registered address — but it is a hard blocker for order placement,
        # and an operator must learn that before the day they enable capital.
        return status.verified, status.reason

    def _account_binding():
        from app.services.account_binding_service import binding_health

        health = binding_health()
        if not health.get("account_binding_readable"):
            return None, str(health.get("error") or "binding store unreadable")
        if not health.get("account_binding_ready"):
            return False, str(health.get("detail") or "no usable broker account binding")
        return True, (
            f"{health.get('account_binding_id')} "
            f"({health.get('legal_account_holder')}, scope={health.get('account_scope')})"
        )

    def _release_certification():
        from app.services.release_certification import certification_report

        report = certification_report()
        if report.release_ready:
            return True, f"{report.tag} @ {report.sha[:12]}"
        outstanding = [r.key for r in report.failures] + [r.key for r in report.unknowns]
        return False, "gates not passed: " + ", ".join(outstanding)

    add("focus_policy", _focus)
    add("release_tag", _release_tag)
    add("risk_limits", _risk_limits)
    add("lane_origination", _lanes)
    add("supertrend_core_frozen", _supertrend_frozen)
    def _lake_mount():
        """The market-data lake. Data not recorded today cannot be bought back.

        The vendor does not sell the past for expired option contracts, so a
        day the lake was unplugged is a day that is gone. That makes this an
        operational check, not a nicety — and an unmounted drive that is
        physically attached needs a different fix from one that is missing, so
        the reason says which.
        """
        from kitelake.volume import lake_status

        status = lake_status()
        if status.available:
            return True, f"mounted at {status.root}"
        if status.volume_present_unmounted:
            return False, f"the lake volume is attached but not mounted: {status.reason}"
        return False, status.reason or "the lake is not reachable"

    def _network_path():
        """Whether the deployment last observed the broker as reachable.

        Read, never probed. An application that calls out to decide whether it
        is healthy has made its own safety check depend on a third party being
        up, and a doctor run on a deliberately offline host would then report a
        failure that is not one. The deployment records what it saw; an
        observation older than the egress window is not a current fact.
        """
        from datetime import datetime, timedelta, timezone

        observed = (os.environ.get("STERLING_BROKER_REACHABLE_AT") or "").strip()
        if not observed:
            return None, ("STERLING_BROKER_REACHABLE_AT is unset: nothing has recorded "
                          "whether this host can reach the broker")
        try:
            stamp = datetime.fromisoformat(observed)
        except ValueError:
            return None, f"STERLING_BROKER_REACHABLE_AT is not a timestamp: {observed!r}"
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - stamp
        if age > timedelta(days=7):
            return False, f"the broker path was last reachable {age.days} days ago"
        return True, f"broker reachable, observed {observed}"

    add("lake_mount", _lake_mount)
    add("network_path", _network_path)
    add("static_egress", _static_egress)
    add("account_binding", _account_binding)
    add("release_certification", _release_certification)
    return tuple(checks)


def operator_dashboard(health: HealthReport | None = None) -> dict[str, Any]:
    """The main screen: system status first, then every lane, then exposure."""
    rows = dashboard_rows()
    return {
        "system": (health or compose_health({})).as_dict(),
        "lanes": rows,
        "lanes_originating": [r["lane_key"] for r in rows if r["may_originate"]],
        "lanes_blocked": {
            r["lane_key"]: r["reason"] for r in rows if not r["may_originate"]
        },
    }


def render_dashboard(payload: Mapping[str, Any]) -> str:
    """Plain text for someone who does not read JSON."""
    system = payload["system"]
    lines = [
        "STERLING STATUS",
        "",
        f"{'System':<18}{str(system['overall']).upper()}",
    ]
    for component in system.get("components", []):
        lines.append(
            f"{'  ' + component['name']:<18}{str(component['status']).upper()}"
            + (f"   {component['detail']}" if component["detail"] else "")
        )
    if system.get("missing_components"):
        lines.append(
            f"{'  not reported':<18}{', '.join(system['missing_components'])}"
        )

    current = None
    for row in payload["lanes"]:
        if row["strategy"] != current:
            current = row["strategy"]
            lines.extend(["", str(current).upper()])
        mark = "open" if row["may_originate"] else f"blocked ({row['reason']})"
        lines.append(f"  {row['mode']:<16}{str(row['state']).upper():<10}{mark}")

    lines.extend(
        [
            "",
            "New exposure allowed only when System is NORMAL and the lane is open.",
        ]
    )
    return "\n".join(lines)
