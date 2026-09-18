"""Remote CI proof, one required context at a time, bound to one exact SHA.

`remote_ci` used to be a single line an operator attested: "CI was green". That
claim cannot say which contexts ran, when, or on what. It cannot distinguish a
build that passed nine checks from one that passed six and never ran the other
three, and — the failure this module exists to prevent — it cannot tell that the
green run everyone remembers was against a different commit.

So each required context is recorded separately, against a SHA, with the run id
that proves it. A context with no record is UNKNOWN, a success recorded against
another SHA satisfies nothing here, and the gate is the conjunction of all nine.

Nothing in this module talks to GitHub. It stores what it was told and judges
it; the fetching lives in the service layer, so the rule is testable without a
network and a wrong answer cannot be blamed on an API.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Iterable, Mapping

__all__ = [
    "REQUIRED_CONTEXTS", "SUCCESS", "CIContextRecord", "CIRecordStore",
    "CIReport", "ci_report", "render_ci", "CI_DIR",
]

#: The nine contexts §5.1 requires, spelled exactly as GitHub reports them.
#: A rename on either side must be a deliberate edit here, not a silent pass.
REQUIRED_CONTEXTS: Final[tuple[str, ...]] = (
    "Snapback release gate",
    "Sterling Release Gate",
    "Backend Tests (Python 3.12)",
    "Backend Tests (Python 3.13)",
    "Frontend Tests (Vitest)",
    "Frontend Type Check",
    "E2E Playwright (chromium)",
    "E2E Playwright (firefox)",
    "E2E Playwright (webkit)",
)

SUCCESS: Final[str] = "success"

CI_DIR: Final[str] = "data/manifests/ci"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root() -> Path:
    import os

    configured = os.environ.get("STERLING_ROOT")
    return Path(configured) if configured else Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class CIContextRecord:
    """One required context's outcome on one commit."""

    context: str
    conclusion: str = ""
    run_id: str = ""
    observed_at: str = ""
    url: str = ""

    @property
    def passed(self) -> bool:
        return self.conclusion == SUCCESS

    @property
    def status(self) -> str:
        if not self.conclusion:
            return "UNKNOWN"
        return "PASS" if self.passed else "FAIL"

    def as_dict(self) -> dict[str, Any]:
        return {"context": self.context, "conclusion": self.conclusion,
                "run_id": self.run_id, "observed_at": self.observed_at, "url": self.url}


class CIRecordStore:
    """One JSON file per SHA. A record is never copied between commits."""

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory else _root() / CI_DIR

    def _path(self, sha: str) -> Path:
        return self.directory / f"{sha}.json"

    def read(self, sha: str) -> dict[str, CIContextRecord]:
        path = self._path(sha)
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        out: dict[str, CIContextRecord] = {}
        for row in payload.get("contexts", []):
            context = str(row.get("context") or "")
            if context:
                out[context] = CIContextRecord(
                    context=context,
                    conclusion=str(row.get("conclusion") or ""),
                    run_id=str(row.get("run_id") or ""),
                    observed_at=str(row.get("observed_at") or ""),
                    url=str(row.get("url") or ""),
                )
        return out

    def record(
        self,
        *,
        runtime_sha: str,
        context: str,
        conclusion: str,
        run_id: str,
        observed_at: str | None = None,
        url: str = "",
    ) -> CIContextRecord:
        """Record one context's outcome against one commit.

        An unknown context name is refused rather than stored: a typo that
        silently becomes a tenth context would leave a required one permanently
        UNKNOWN
        while the file looked full.
        """
        if context not in REQUIRED_CONTEXTS:
            raise KeyError(
                f"{context!r} is not a required context; expected one of: "
                + ", ".join(REQUIRED_CONTEXTS)
            )
        if len(str(runtime_sha)) != 40:
            raise ValueError("a CI record must name the exact 40-character commit")
        if conclusion == SUCCESS and not str(run_id).strip():
            raise ValueError("a success must carry the run id that proves it")

        existing = self.read(runtime_sha)
        existing[context] = CIContextRecord(
            context=context, conclusion=str(conclusion).strip().lower(),
            run_id=str(run_id).strip(), observed_at=observed_at or _now(), url=url,
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path(runtime_sha).write_text(
            json.dumps({"runtime_sha": runtime_sha, "updated_at": _now(),
                        "contexts": [r.as_dict() for r in existing.values()]},
                       indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return existing[context]


@dataclass(frozen=True)
class CIReport:
    runtime_sha: str
    records: tuple[CIContextRecord, ...]

    @property
    def by_context(self) -> dict[str, CIContextRecord]:
        return {r.context: r for r in self.records}

    @property
    def failures(self) -> tuple[CIContextRecord, ...]:
        return tuple(r for r in self.records if r.conclusion and not r.passed)

    @property
    def unknowns(self) -> tuple[CIContextRecord, ...]:
        return tuple(r for r in self.records if not r.conclusion)

    @property
    def all_passed(self) -> bool:
        return bool(self.records) and all(r.passed for r in self.records)

    def as_dict(self) -> dict[str, Any]:
        return {"runtime_sha": self.runtime_sha, "all_passed": self.all_passed,
                "contexts": [r.as_dict() for r in self.records]}


def ci_report(sha: str, *, store: CIRecordStore | None = None) -> CIReport:
    """Every required context and its record on this SHA, in declared order."""
    recorded = (store or CIRecordStore()).read(sha)
    return CIReport(
        runtime_sha=sha,
        records=tuple(recorded.get(c, CIContextRecord(c)) for c in REQUIRED_CONTEXTS),
    )


def render_ci(report: CIReport) -> str:
    lines = [f"REQUIRED CI CONTEXTS  {report.runtime_sha[:12] or 'no SHA'}", ""]
    for record in report.records:
        lines.append(f"  {record.context:<32}{record.status:<9}"
                     + (f"run {record.run_id}" if record.run_id else ""))
    lines.append("")
    if report.all_passed:
        lines.append(f"All {len(REQUIRED_CONTEXTS)} required contexts succeeded on this commit.")
    else:
        if report.failures:
            lines.append("FAILED: " + ", ".join(r.context for r in report.failures))
        if report.unknowns:
            lines.append("NO RECORD on this commit: "
                         + ", ".join(r.context for r in report.unknowns))
            lines.append("A context with no record is not a context that passed.")
    return "\n".join(lines)
