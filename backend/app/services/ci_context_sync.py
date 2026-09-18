"""Read the required CI contexts for one commit from GitHub, and record them.

Transcribing nine check names and nine run ids by hand is how a release ends up
certified against the wrong commit. This asks GitHub for the check runs attached
to one exact SHA and records what it finds.

Two deliberate limits. It records only the contexts the specification requires,
so an unrelated check cannot pad the list. And it records what it was told —
a failure is stored as a failure — because a sync that quietly skipped bad news
would be worse than typing it in by hand.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.core.ci_certification import (
    REQUIRED_CONTEXTS,
    CIRecordStore,
    ci_report,
)

log = logging.getLogger(__name__)

__all__ = ["SyncResult", "sync_ci_contexts", "check_runs_for"]


@dataclass(frozen=True)
class SyncResult:
    runtime_sha: str
    recorded: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"runtime_sha": self.runtime_sha, "recorded": list(self.recorded),
                "missing": list(self.missing), "error": self.error}


def check_runs_for(sha: str, *, repo: str | None = None) -> list[Mapping[str, Any]]:
    """Every check run GitHub has attached to this commit.

    Uses the `gh` CLI so the operator's existing credentials are the only ones
    involved: this tool never handles a token itself.
    """
    binary = shutil.which("gh")
    if binary is None:
        raise FileNotFoundError("the gh CLI is not installed")

    endpoint = (f"repos/{repo}/commits/{sha}/check-runs" if repo
                else f"repos/{{owner}}/{{repo}}/commits/{sha}/check-runs")
    completed = subprocess.run(
        [binary, "api", endpoint, "--paginate", "--jq", ".check_runs[]"],
        capture_output=True, text=True, timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "gh failed").strip()[:300])

    runs: list[Mapping[str, Any]] = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            runs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return runs


def sync_ci_contexts(
    sha: str,
    *,
    repo: str | None = None,
    store: CIRecordStore | None = None,
    runs: Sequence[Mapping[str, Any]] | None = None,
) -> SyncResult:
    """Record each required context's conclusion for ``sha``.

    A context GitHub does not report is left with no record rather than being
    written as anything: "we did not find it" and "it failed" are different
    facts, and only one of them means someone should re-run a workflow.
    """
    record_store = store or CIRecordStore()
    try:
        found = list(runs) if runs is not None else check_runs_for(sha, repo=repo)
    except Exception as exc:  # noqa: BLE001
        return SyncResult(sha, error=str(exc))

    latest: dict[str, Mapping[str, Any]] = {}
    for run in found:
        name = str(run.get("name") or "")
        if name not in REQUIRED_CONTEXTS:
            continue
        # Several runs may carry one name (a re-run). The most recently
        # completed one is the current answer.
        previous = latest.get(name)
        if previous is None or str(run.get("completed_at") or "") >= str(
            previous.get("completed_at") or ""
        ):
            latest[name] = run

    recorded: list[str] = []
    for name, run in latest.items():
        conclusion = str(run.get("conclusion") or "").strip().lower()
        if not conclusion:
            continue  # still running: no record beats a guess
        try:
            record_store.record(
                runtime_sha=sha,
                context=name,
                conclusion=conclusion,
                run_id=str(run.get("id") or ""),
                observed_at=str(run.get("completed_at") or "") or None,
                url=str(run.get("html_url") or ""),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("ci sync: could not record %s: %s", name, exc)
            continue
        recorded.append(name)

    report = ci_report(sha, store=record_store)
    return SyncResult(
        runtime_sha=sha,
        recorded=tuple(sorted(recorded)),
        missing=tuple(r.context for r in report.unknowns),
    )
