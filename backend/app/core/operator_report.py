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
