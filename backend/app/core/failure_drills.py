"""The drills that have to pass before anything runs unattended.

"Failure drills passed" was a single attested line in the certification table.
That is a promise, not a record: it cannot say which drill was run, when, by
whom, or what the system actually did. Twelve separate failures have twelve
separate correct behaviours, and the one that was never rehearsed is the one
that will happen.

Each drill below names its required outcome in the same words the
specification uses, because the point of writing it down is that the person
running the drill and the person reading the result agree on what passing
means. A drill with no record is UNKNOWN, and UNKNOWN is not a pass.

Records are kept per release SHA for the same reason certification is: a drill
rehearsed against one build says nothing about a build with different code in
the recovery path.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Iterable, Mapping

__all__ = [
    "DRILLS", "DRILLS_BY_KEY", "Drill", "DrillResult", "DrillRegister",
    "PASS", "FAIL", "UNKNOWN", "drill_report", "render_drills",
]

PASS: Final[str] = "PASS"
FAIL: Final[str] = "FAIL"
UNKNOWN: Final[str] = "UNKNOWN"

DRILLS_DIR: Final[str] = "data/manifests/drills"


@dataclass(frozen=True)
class Drill:
    key: str
    scenario: str
    required_outcome: str
    #: A drill that can be rehearsed without a broker session or market hours.
    offline: bool = False


DRILLS: Final[tuple[Drill, ...]] = (
    Drill("power_loss", "Power loss",
          "restart to RECOVERY_REQUIRED; reconcile before entries", offline=True),
    Drill("broker_disconnect", "Broker disconnect",
          "no new exposure; monitor or reduce existing"),
    Drill("stale_feed", "Stale feed", "block new exposure"),
    Drill("submit_ack_lost", "Submit ACK lost",
          "SUBMITTED_UNKNOWN; broker query before retry"),
    Drill("partial_fill", "Partial fill", "exact partial inventory and protection"),
    Drill("missing_protection", "Missing protection", "SAFE_MODE; repair or close"),
    Drill("disk_full", "Disk full", "block new exposure", offline=True),
    Drill("evidence_db_failure", "Evidence DB failure",
          "EVIDENCE_ERROR; session incomplete", offline=True),
    Drill("restart_with_open_position", "Restart with open position",
          "recover broker truth and protection"),
    Drill("safe_mode_during_pending_entry", "SAFE_MODE engaged during pending entry",
          "final broker-boundary recheck blocks send"),
    Drill("backup_corruption", "Backup corruption", "restore-check fails", offline=True),
    Drill("clock_anomaly", "Clock anomaly",
          "timestamp-sensitive exposure blocked", offline=True),
)

DRILLS_BY_KEY: Final[dict[str, Drill]] = {d.key: d for d in DRILLS}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root() -> Path:
    import os

    configured = os.environ.get("STERLING_ROOT")
    return Path(configured) if configured else Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class DrillResult:
    key: str
    status: str = UNKNOWN
    observed: str = ""
    observed_by: str = ""
    evidence_refs: tuple[str, ...] = ()
    started_at: str = ""
    recorded_at: str = ""

    @property
    def passed(self) -> bool:
        return self.status == PASS

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "status": self.status,
            "observed": self.observed,
            "observed_by": self.observed_by,
            "evidence_refs": list(self.evidence_refs),
            "started_at": self.started_at,
            "recorded_at": self.recorded_at,
        }


class DrillRegister:
    """One JSON file per SHA. A drill rehearsed on another build does not count."""

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory else _root() / DRILLS_DIR

    def _path(self, sha: str) -> Path:
        return self.directory / f"{sha}.json"

    def read(self, sha: str) -> dict[str, DrillResult]:
        path = self._path(sha)
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        out: dict[str, DrillResult] = {}
        for row in payload.get("drills", []):
            key = str(row.get("key") or "")
            if key:
                refs = row.get("evidence_refs")
                if refs is None:
                    # Records written before the schema carried several refs.
                    single = str(row.get("evidence_ref") or "")
                    refs = [single] if single else []
                out[key] = DrillResult(
                    key=key,
                    status=str(row.get("status") or UNKNOWN),
                    observed=str(row.get("observed") or ""),
                    observed_by=str(row.get("observed_by") or ""),
                    evidence_refs=tuple(str(r) for r in refs if str(r).strip()),
                    started_at=str(row.get("started_at") or ""),
                    recorded_at=str(row.get("recorded_at") or ""),
                )
        return out

    def record(
        self,
        sha: str,
        key: str,
        status: str,
        *,
        observed_by: str,
        observed: str = "",
        evidence_refs: Iterable[str] = (),
        started_at: str = "",
    ) -> DrillResult:
        """Record what one drill actually did on one exact build."""
        if key not in DRILLS_BY_KEY:
            raise KeyError(f"unknown drill {key!r}")
        status = status.strip().upper()
        if status not in (PASS, FAIL, UNKNOWN):
            raise ValueError(f"status must be {PASS}, {FAIL} or {UNKNOWN}; got {status!r}")
        if status == PASS and not observed_by.strip():
            raise ValueError("a PASS must name who ran the drill and watched it")
        if status == PASS and not observed.strip():
            raise ValueError(
                "a PASS must say what the system actually did; "
                f"the required outcome is: {DRILLS_BY_KEY[key].required_outcome}"
            )

        existing = self.read(sha)
        existing[key] = DrillResult(
            key=key, status=status, observed=observed.strip(),
            observed_by=observed_by.strip(),
            evidence_refs=tuple(str(r).strip() for r in evidence_refs if str(r).strip()),
            started_at=started_at.strip() or _now(),
            recorded_at=_now(),
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path(sha).write_text(
            json.dumps({"sha": sha, "updated_at": _now(),
                        "drills": [r.as_dict() for r in existing.values()]},
                       indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return existing[key]


@dataclass(frozen=True)
class DrillReport:
    sha: str
    results: tuple[DrillResult, ...]

    @property
    def by_key(self) -> dict[str, DrillResult]:
        return {r.key: r for r in self.results}

    @property
    def failures(self) -> tuple[DrillResult, ...]:
        return tuple(r for r in self.results if r.status == FAIL)

    @property
    def unknowns(self) -> tuple[DrillResult, ...]:
        return tuple(r for r in self.results if r.status == UNKNOWN)

    @property
    def all_passed(self) -> bool:
        """Every drill, on this exact SHA. One UNKNOWN is enough to say no."""
        return bool(self.results) and all(r.passed for r in self.results)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sha": self.sha,
            "all_passed": self.all_passed,
            "drills": [
                {**r.as_dict(),
                 "scenario": DRILLS_BY_KEY[r.key].scenario,
                 "required_outcome": DRILLS_BY_KEY[r.key].required_outcome}
                for r in self.results
            ],
        }


def drill_report(sha: str, *, register: DrillRegister | None = None) -> DrillReport:
    """Every declared drill and its record on this SHA, in declared order."""
    recorded = (register or DrillRegister()).read(sha)
    return DrillReport(
        sha=sha,
        results=tuple(recorded.get(d.key, DrillResult(d.key)) for d in DRILLS),
    )


def render_drills(report: DrillReport) -> str:
    lines = [f"FAILURE DRILLS  {report.sha[:12] or 'no SHA'}"]
    for result in report.results:
        drill = DRILLS_BY_KEY[result.key]
        lines.append(f"  {drill.scenario:<38}{result.status:<9}"
                     + (result.observed_by or ""))
        lines.append(f"      required: {drill.required_outcome}")
        if result.observed:
            lines.append(f"      observed: {result.observed}")
    lines.append("")
    if report.all_passed:
        lines.append("Every drill was run on this build and behaved as required.")
    else:
        if report.failures:
            lines.append("FAILED: " + ", ".join(r.key for r in report.failures))
        if report.unknowns:
            lines.append("NOT RUN on this build: "
                         + ", ".join(r.key for r in report.unknowns))
            lines.append("A drill with no record is not a drill that passed.")
    return "\n".join(lines)
