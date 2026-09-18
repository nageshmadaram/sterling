"""Release certification: the gate table of §5, recorded against one exact SHA.

Certification is evidence about a build, not about a repository. The moment a
line of code changes, the SHA changes and every attestation recorded against
the old one stops applying — not "mostly applies", stops. That rule is the
entire reason this module keys everything by SHA and refuses to inherit.

Some gates the software can check for itself (is the tree clean, does the
frozen manifest match what is running, did the last restore-check pass). The
rest are human observations — a moving-market Kite acceptance, a forced
reconnect, a CI run on a remote — and those are recorded as attestations
naming who observed them and where the artifact lives. An unrecorded gate is
UNKNOWN, and UNKNOWN is not a pass.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Iterable, Mapping

from app.core.release_manifest import (
    read_manifest,
    runtime_sha,
    release_tag,
    verify_manifest,
    working_tree_clean,
)

__all__ = [
    "Gate",
    "GateResult",
    "CertificationReport",
    "CertificationStore",
    "certification_report",
    "render_certification",
    "RELEASE_GATES",
]

PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"

CERTIFICATION_DIR: Final[str] = "data/manifests/certification"


@dataclass(frozen=True)
class Gate:
    """One row of the §5 table."""

    key: str
    requirement: str
    #: What this gate blocks when it is not PASS.
    blocks: str
    #: Can the running system determine this itself?
    automated: bool


RELEASE_GATES: Final[tuple[Gate, ...]] = (
    Gate("source_identity", "clean worktree; exact SHA; exact immutable tag", "all production", True),
    Gate("remote_ci", "all required protected checks green on the exact SHA", "release", False),
    Gate("test_suites", "backend, frontend and E2E suites PASS on a clean checkout", "release", False),
    Gate("kite_live_acceptance", "engineering PASS against a moving market", "release", False),
    Gate("reconnect", "forced disconnect -> observed close -> reconnect -> fresh ticks -> FULL depth", "release", False),
    Gate("persistence", "isolated run, reload and evidence checks PASS", "release", False),
    Gate("release_manifest", "the frozen manifest matches the running build and all lanes", "authoritative evidence", True),
    Gate("backup_restore", "fresh backup plus an isolated restore-check PASS", "handoff", True),
    Gate("failure_drills", "every capital-critical drill PASS", "unattended operation", False),
    Gate("open_exposure", "zero unresolved or unknown exposure before freeze", "release", False),
)

_GATES_BY_KEY: Final[Mapping[str, Gate]] = {g.key: g for g in RELEASE_GATES}

#: Gates composed from a per-item register rather than attested in one line.
#: `certify attest` refuses these: the register is the attestation, item by item.
_DERIVED_GATES: Final[frozenset[str]] = frozenset(
    {"failure_drills", "open_exposure", "remote_ci"}
)

#: The gates RELEASE_READY is the conjunction of. Every gate blocks something,
#: but these are the ones that block shipping at all.
_REQUIRED_FOR_READY: Final[tuple[str, ...]] = tuple(g.key for g in RELEASE_GATES)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root() -> Path:
    configured = os.environ.get("STERLING_ROOT")
    return Path(configured) if configured else Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class GateResult:
    """One gate's state for one SHA."""

    key: str
    status: str = UNKNOWN
    detail: str = ""
    #: Who observed it, for the human gates. Empty for automated ones.
    attested_by: str = ""
    #: Where the artifact lives: a CI run URL, a log path, a drill report.
    evidence_ref: str = ""
    recorded_at: str = ""

    @property
    def passed(self) -> bool:
        return self.status == PASS

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "status": self.status,
            "detail": self.detail,
            "attested_by": self.attested_by,
            "evidence_ref": self.evidence_ref,
            "recorded_at": self.recorded_at,
            "requirement": _GATES_BY_KEY[self.key].requirement if self.key in _GATES_BY_KEY else "",
            "blocks": _GATES_BY_KEY[self.key].blocks if self.key in _GATES_BY_KEY else "",
        }


@dataclass(frozen=True)
class CertificationReport:
    """The whole gate table for one SHA, plus the RELEASE_READY verdict."""

    sha: str
    tag: str
    results: tuple[GateResult, ...] = field(default_factory=tuple)
    unresolved_exposure: int | None = None

    @property
    def by_key(self) -> dict[str, GateResult]:
        return {r.key: r for r in self.results}

    @property
    def failures(self) -> tuple[GateResult, ...]:
        return tuple(r for r in self.results if r.status == FAIL)

    @property
    def unknowns(self) -> tuple[GateResult, ...]:
        return tuple(r for r in self.results if r.status == UNKNOWN)

    @property
    def release_ready(self) -> bool:
        """§5: every required gate PASS and zero unresolved exposure.

        ``unresolved_exposure is None`` refuses: an exposure count nobody could
        read is not a count of zero.
        """
        if self.unresolved_exposure is None or self.unresolved_exposure != 0:
            return False
        results = self.by_key
        return all(results.get(key, GateResult(key)).passed for key in _REQUIRED_FOR_READY)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sha": self.sha,
            "tag": self.tag,
            "release_ready": self.release_ready,
            "unresolved_exposure": self.unresolved_exposure,
            "gates": [r.as_dict() for r in self.results],
            "failures": [r.key for r in self.failures],
            "unknowns": [r.key for r in self.unknowns],
        }


class CertificationStore:
    """One JSON file per SHA. Attestations are never copied between SHAs."""

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory else _root() / CERTIFICATION_DIR

    def _path(self, sha: str) -> Path:
        return self.directory / f"{sha}.json"

    def read(self, sha: str) -> dict[str, GateResult]:
        path = self._path(sha)
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        out: dict[str, GateResult] = {}
        for row in payload.get("gates", []):
            key = str(row.get("key") or "")
            if key:
                out[key] = GateResult(
                    key=key,
                    status=str(row.get("status") or UNKNOWN),
                    detail=str(row.get("detail") or ""),
                    attested_by=str(row.get("attested_by") or ""),
                    evidence_ref=str(row.get("evidence_ref") or ""),
                    recorded_at=str(row.get("recorded_at") or ""),
                )
        return out

    def attest(
        self,
        sha: str,
        key: str,
        status: str,
        *,
        attested_by: str,
        evidence_ref: str = "",
        detail: str = "",
    ) -> GateResult:
        """Record one human gate against one exact SHA."""
        if key not in _GATES_BY_KEY:
            raise KeyError(f"unknown certification gate {key!r}")
        if key in _DERIVED_GATES:
            raise ValueError(
                f"{key} is derived from its own register and cannot be attested "
                "in one line; record each item with `sterlingctl drills record`"
            )
        status = status.strip().upper()
        if status not in (PASS, FAIL, UNKNOWN):
            raise ValueError(f"status must be {PASS}, {FAIL} or {UNKNOWN}; got {status!r}")
        if status == PASS and not attested_by.strip():
            raise ValueError("a PASS must name who observed it")

        existing = self.read(sha)
        existing[key] = GateResult(
            key=key,
            status=status,
            detail=detail,
            attested_by=attested_by.strip(),
            evidence_ref=evidence_ref.strip(),
            recorded_at=_now(),
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path(sha).write_text(
            json.dumps(
                {
                    "sha": sha,
                    "updated_at": _now(),
                    "gates": [r.as_dict() for r in existing.values()],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return existing[key]


# -- the automated gates ---------------------------------------------------
def _source_identity(sha: str, tag: str) -> GateResult:
    clean = working_tree_clean()
    problems: list[str] = []
    if clean is not True:
        problems.append("working tree is not clean" if clean is False else "cleanliness unknown")
    if not sha or sha.upper() == "UNKNOWN":
        problems.append("runtime SHA is unknown")
    if not tag or tag.upper() == "UNKNOWN":
        problems.append("no immutable release tag")
    if problems:
        status = UNKNOWN if clean is None else FAIL
        return GateResult("source_identity", status, "; ".join(problems))
    return GateResult("source_identity", PASS, f"{tag} @ {sha[:12]}", attested_by="sterlingctl")


def _release_manifest_gate() -> GateResult:
    stored = read_manifest()
    if stored is None:
        return GateResult("release_manifest", FAIL, "no frozen release manifest; run `sterlingctl freeze`")
    verdict = verify_manifest(stored)
    if verdict.matches:
        return GateResult("release_manifest", PASS, "manifest matches the running build", attested_by="sterlingctl")
    return GateResult(
        "release_manifest",
        FAIL,
        f"{len(verdict.drift)} field(s) drifted; "
        f"{len(verdict.identity_drift)} change a lane identity",
    )


def _backup_restore_gate() -> GateResult:
    try:
        from app.core.backup_manifest import (
            latest_backup_dir,
            read_backup_manifest,
        )
    except Exception as exc:  # pragma: no cover - import guard
        return GateResult("backup_restore", UNKNOWN, f"backup module unavailable: {exc}")

    backup_root = Path(
        os.environ.get("STERLING_BACKUP_ROOT") or _root() / "backups" / "snapback"
    )
    directory = latest_backup_dir(backup_root)
    if directory is None:
        return GateResult("backup_restore", FAIL, f"no backup under {backup_root}")
    manifest_file = directory / "manifest.json"
    if not manifest_file.exists():
        return GateResult("backup_restore", UNKNOWN, f"backup at {directory} has no manifest")
    try:
        manifest = read_backup_manifest(manifest_file)
    except Exception as exc:  # noqa: BLE001
        return GateResult("backup_restore", UNKNOWN, f"manifest unreadable: {exc}")
    status_text = str(getattr(manifest, "restore_test_status", "") or "UNTESTED")
    if status_text.upper() in ("PASSED", "PASS", "VERIFIED"):
        return GateResult("backup_restore", PASS, f"{directory.name}: {status_text}", attested_by="sterlingctl")
    return GateResult(
        "backup_restore",
        FAIL,
        f"{directory.name}: restore_test_status={status_text}; run `sterlingctl restore-check`",
    )


def _failure_drills_gate(sha: str) -> GateResult:
    """Every declared drill, on this exact SHA, with a name against each PASS."""
    try:
        from app.core.failure_drills import DRILLS, drill_report
    except Exception as exc:  # pragma: no cover - import guard
        return GateResult("failure_drills", UNKNOWN, f"drill register unavailable: {exc}")

    report = drill_report(sha)
    if report.all_passed:
        names = sorted({r.observed_by for r in report.results if r.observed_by})
        return GateResult("failure_drills", PASS,
                          f"all {len(DRILLS)} drills passed on this build",
                          attested_by=", ".join(names))
    if report.failures:
        return GateResult("failure_drills", FAIL,
                          "failed: " + ", ".join(r.key for r in report.failures))
    return GateResult("failure_drills", UNKNOWN,
                      f"{len(report.unknowns)} of {len(DRILLS)} drills have no record "
                      "on this build")


def _remote_ci_gate(sha: str) -> GateResult:
    """All nine required contexts, recorded against this exact commit.

    Derived rather than attested. "CI was green" cannot say which contexts ran,
    and a green run on another commit is the mistake this replaces.
    """
    try:
        from app.core.ci_certification import REQUIRED_CONTEXTS, ci_report
    except Exception as exc:  # pragma: no cover - import guard
        return GateResult("remote_ci", UNKNOWN, f"CI record store unavailable: {exc}")

    report = ci_report(sha)
    if report.all_passed:
        runs = ", ".join(sorted({r.run_id for r in report.records if r.run_id}))
        return GateResult("remote_ci", PASS,
                          f"all {len(REQUIRED_CONTEXTS)} required contexts succeeded"
                          + (f" (runs {runs})" if runs else ""),
                          attested_by="github-actions")
    if report.failures:
        return GateResult("remote_ci", FAIL,
                          "failed: " + ", ".join(r.context for r in report.failures))
    return GateResult("remote_ci", UNKNOWN,
                      f"{len(report.unknowns)} of {len(REQUIRED_CONTEXTS)} required "
                      "contexts have no record on this commit")


def _open_exposure_gate() -> GateResult:
    """Derived, not attested: nobody may declare exposure closed that is open."""
    from app.services.exposure_snapshot import exposure_snapshot

    snapshot = exposure_snapshot()
    if snapshot.total is None:
        return GateResult("open_exposure", UNKNOWN,
                          snapshot.detail or "durable exposure could not be read")
    if snapshot.total:
        held = ", ".join(snapshot.held[:5])
        return GateResult("open_exposure", FAIL,
                          f"{snapshot.open_positions} open position(s) and "
                          f"{snapshot.unresolved_intents} unresolved intent(s)"
                          + (f": {held}" if held else ""))
    return GateResult("open_exposure", PASS, "no open position, no unresolved intent",
                      attested_by="sterlingctl")


def certification_report(
    *,
    sha: str | None = None,
    tag: str | None = None,
    store: CertificationStore | None = None,
    unresolved_exposure: int | None = None,
) -> CertificationReport:
    """Compose the automated checks with whatever has been attested for this SHA."""
    resolved_sha = sha if sha is not None else runtime_sha()
    resolved_tag = tag if tag is not None else release_tag()
    if unresolved_exposure is None:
        # Nothing supplied this, so the gate was permanently UNKNOWN — not
        # because exposure was open but because nobody had read the stores.
        from app.services.exposure_snapshot import unresolved_exposure_count

        unresolved_exposure = unresolved_exposure_count()
    attested = (store or CertificationStore()).read(resolved_sha)

    automated = {
        "source_identity": _source_identity(resolved_sha, resolved_tag),
        "release_manifest": _release_manifest_gate(),
        "backup_restore": _backup_restore_gate(),
        # Derived from the drill register rather than attested as one line: a
        # single "drills passed" cannot say which of the twelve was run, or
        # what the system did when it was.
        "failure_drills": _failure_drills_gate(resolved_sha),
        "open_exposure": _open_exposure_gate(),
        "remote_ci": _remote_ci_gate(resolved_sha),
    }

    results: list[GateResult] = []
    for gate in RELEASE_GATES:
        # An operator attestation never overrides a machine check that FAILs:
        # a human cannot attest that the working tree is clean when it is not.
        auto = automated.get(gate.key)
        if gate.key in _DERIVED_GATES:
            # These are composed from their own per-item registers, so a blanket
            # attestation would be exactly the claim the register exists to
            # replace: "the drills passed", with no record of which ones ran.
            results.append(auto if auto is not None else GateResult(gate.key))
            continue
        if auto is not None and auto.status != UNKNOWN:
            results.append(auto)
            continue
        results.append(attested.get(gate.key, GateResult(gate.key)))

    return CertificationReport(
        sha=resolved_sha,
        tag=resolved_tag,
        results=tuple(results),
        unresolved_exposure=unresolved_exposure,
    )


def render_certification(report: CertificationReport) -> str:
    lines = [
        "RELEASE CERTIFICATION",
        "",
        f"{'sha':<20}{report.sha}",
        f"{'tag':<20}{report.tag}",
        "",
        f"{'gate':<24}{'status':<10}detail",
    ]
    for result in report.results:
        lines.append(f"{result.key:<24}{result.status:<10}{result.detail}")
    lines.append("")
    exposure = report.unresolved_exposure
    lines.append(
        f"{'unresolved exposure':<24}"
        + (str(exposure) if exposure is not None else "UNKNOWN (counts as blocking)")
    )
    lines.append("")
    if report.release_ready:
        lines.append("RELEASE_READY: this SHA may be tagged and deployed to SHADOW production.")
    else:
        lines.append("NOT release ready.")
        if report.failures:
            lines.append("  FAILED:  " + ", ".join(r.key for r in report.failures))
        if report.unknowns:
            lines.append("  UNKNOWN: " + ", ".join(r.key for r in report.unknowns))
        lines.append("  An unrecorded gate is not a passed gate.")
    return "\n".join(lines)
