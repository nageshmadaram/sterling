"""What a backup has to contain before "we have a backup" means anything.

A backup of the evidence database alone restores the numbers and loses the
system: the durable intent journal that restart recovery reads, the safety
state that decides whether trading is allowed at all, the lane manifests that
say what the evidence is evidence *of*. Restoring those from memory, six months
later, after the disk that held them died, is not a plan.

So the eleven artifacts the specification lists are declared here, each with
where it lives and what kind of thing it is, and a backup reports coverage
rather than success. An artifact that does not exist on this host is recorded
as ABSENT — which may be correct, on a machine that has never traded — and one
that exists but was not captured is a GAP, which is never correct.

One artifact is deliberately never copied. The deployment configuration holds
broker credentials, and a backup is a file that gets copied to other disks and
other people. Its existence and its checksum are recorded so a restore can tell
whether the configuration changed; its contents stay where they are, and the
runbook tells the operator to recreate it by hand.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Final, Iterable

__all__ = [
    "ArtifactKind", "Coverage", "RequiredArtifact", "REQUIRED_ARTIFACTS",
    "resolve_artifacts", "coverage_report", "render_coverage",
]


class ArtifactKind(StrEnum):
    #: A SQLite database, snapshotted with the online backup API.
    DATABASE = "database"
    #: A file copied verbatim.
    FILE = "file"
    #: A directory copied recursively.
    DIRECTORY = "directory"
    #: Recorded by checksum only, never copied. See the module docstring.
    REFERENCED = "referenced"


class Coverage(StrEnum):
    COPIED = "COPIED"
    REFERENCED = "REFERENCED"
    #: Does not exist on this host. May be correct; the report says which.
    ABSENT = "ABSENT"
    #: Exists and was not captured. Never correct.
    GAP = "GAP"


@dataclass(frozen=True)
class RequiredArtifact:
    name: str
    kind: ArtifactKind
    locate: Callable[[Path], Path | None]
    why: str
    #: When True, its absence is a gap rather than a legitimate empty state.
    required_before_live: bool = True


def _env_path(variable: str, fallback: Callable[[Path], Path]) -> Callable[[Path], Path | None]:
    def _locate(root: Path) -> Path | None:
        configured = (os.environ.get(variable) or "").strip()
        return Path(configured).expanduser() if configured else fallback(root)

    return _locate


REQUIRED_ARTIFACTS: Final[tuple[RequiredArtifact, ...]] = (
    RequiredArtifact(
        "authoritative_evidence_db", ArtifactKind.DATABASE,
        _env_path("STERLING_EVIDENCE_DB", lambda r: r / "backend/snapback_observations.db"),
        "the forward sample every promotion decision is read from",
    ),
    RequiredArtifact(
        "canonical_intent_journal", ArtifactKind.DATABASE,
        _env_path("STERLING_DB_PATH", lambda r: r / "backend/sterling_paper.db"),
        "the durable order journal restart recovery reads to learn whether an "
        "order was ever sent; it also holds the execution-control state and the "
        "signed fill ledger",
    ),
    RequiredArtifact(
        "kite_engine_db", ArtifactKind.DATABASE,
        lambda r: r / "backend/kite_engine.db",
        "the SuperTrend engine's own durable state",
        required_before_live=False,
    ),
    RequiredArtifact(
        "safety_state", ArtifactKind.FILE,
        _env_path("STERLING_SAFE_MODE_FILE", lambda r: r / "data/safe_mode.json"),
        "whether trading is allowed at all; an absent file reads as SAFE_MODE, "
        "so losing it fails closed — but it also loses why it was engaged",
    ),
    RequiredArtifact(
        "release_manifest", ArtifactKind.FILE,
        lambda r: r / "data/manifests/release.json",
        "which build the evidence was recorded under",
    ),
    RequiredArtifact(
        "lane_manifests", ArtifactKind.DIRECTORY,
        lambda r: r / "data/manifests/strategies",
        "the frozen identity of each lane; without it the evidence cannot say "
        "what it is evidence of",
    ),
    RequiredArtifact(
        "vehicle_manifests", ArtifactKind.DIRECTORY,
        lambda r: r / "data/manifests/vehicles",
        "which execution vehicle each lane ran, which is part of its identity",
        required_before_live=False,
    ),
    RequiredArtifact(
        "account_bindings", ArtifactKind.DIRECTORY,
        lambda r: r / "data/manifests/account_bindings",
        "which broker account each evidence segment belongs to",
        required_before_live=False,
    ),
    RequiredArtifact(
        "certification", ArtifactKind.DIRECTORY,
        lambda r: r / "data/manifests/certification",
        "the release gate table and who attested each gate",
        required_before_live=False,
    ),
    RequiredArtifact(
        "continuity", ArtifactKind.FILE,
        lambda r: r / "data/manifests/continuity.json",
        "the access-continuity answers a successor operator needs",
        required_before_live=False,
    ),
    RequiredArtifact(
        "shadow_evidence", ArtifactKind.DIRECTORY,
        _env_path("STERLING_SHADOW_DIR", lambda r: r / "data/shadow"),
        "the shadow execution records, which are a lane's execution evidence "
        "while it may not send",
        required_before_live=False,
    ),
    RequiredArtifact(
        "deployment_configuration", ArtifactKind.REFERENCED,
        lambda r: Path("~/.config/sterling/sterling.env").expanduser(),
        "recorded by checksum only: it holds broker credentials, and a backup "
        "is a file that gets copied elsewhere. Recreate it by hand on restore",
        required_before_live=False,
    ),
)


@dataclass(frozen=True)
class ArtifactStatus:
    artifact: RequiredArtifact
    path: Path | None
    coverage: Coverage
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.artifact.name,
            "kind": self.artifact.kind.value,
            "path": str(self.path) if self.path else None,
            "coverage": self.coverage.value,
            "why": self.artifact.why,
            "detail": self.detail,
        }


def resolve_artifacts(root: Path | str) -> tuple[tuple[RequiredArtifact, Path | None], ...]:
    """Where each required artifact lives on this host, or None if nowhere."""
    base = Path(root)
    resolved: list[tuple[RequiredArtifact, Path | None]] = []
    for artifact in REQUIRED_ARTIFACTS:
        try:
            path = artifact.locate(base)
        except Exception:  # noqa: BLE001 - a resolver must never fail a backup
            path = None
        resolved.append((artifact, path))
    return tuple(resolved)


def coverage_report(
    root: Path | str,
    *,
    captured: Iterable[str] = (),
    referenced: Iterable[str] = (),
) -> tuple[ArtifactStatus, ...]:
    """Judge one backup's contents against the required set.

    ``captured`` and ``referenced`` are artifact names, not paths: the backup
    says what it took, and this says whether that was enough.
    """
    took = set(captured)
    noted = set(referenced)
    statuses: list[ArtifactStatus] = []

    for artifact, path in resolve_artifacts(root):
        exists = bool(path and path.exists())
        if artifact.name in took:
            statuses.append(ArtifactStatus(artifact, path, Coverage.COPIED))
        elif artifact.name in noted:
            statuses.append(ArtifactStatus(artifact, path, Coverage.REFERENCED))
        elif not exists:
            statuses.append(ArtifactStatus(
                artifact, path, Coverage.ABSENT,
                "does not exist on this host"))
        else:
            statuses.append(ArtifactStatus(
                artifact, path, Coverage.GAP,
                "exists but was not captured by this backup"))
    return tuple(statuses)


def render_coverage(statuses: Iterable[ArtifactStatus]) -> str:
    materialised = list(statuses)
    lines = ["BACKUP COVERAGE"]
    for status in materialised:
        lines.append(f"  {status.artifact.name:<28}{status.coverage.value:<12}"
                     f"{status.path or ''}")
    gaps = [s for s in materialised if s.coverage is Coverage.GAP]
    absent_required = [
        s for s in materialised
        if s.coverage is Coverage.ABSENT and s.artifact.required_before_live
    ]
    lines.append("")
    if gaps:
        lines.append("GAPS — these exist and were not backed up:")
        for status in gaps:
            lines.append(f"  {status.artifact.name}: {status.artifact.why}")
    if absent_required:
        lines.append("NOT PRESENT on this host, and required before live capital:")
        for status in absent_required:
            lines.append(f"  {status.artifact.name}: {status.artifact.why}")
    if not gaps and not absent_required:
        lines.append("Every required artifact is either backed up or legitimately absent.")
    return "\n".join(lines)
