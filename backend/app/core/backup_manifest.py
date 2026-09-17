"""Backup manifests, and the restore proof that makes a backup real.

A backup nobody has restored is a hypothesis. This module turns it into a
checked fact: every backup gets a manifest naming the release it came from and
the exact row counts it contained, and ``prove_restore`` copies it into an
isolated directory, opens it, and compares what came back against that manifest.

The row counts are the part that matters. A file that restores and opens is not
evidence that the evidence survived — a truncated table restores perfectly well.
Comparing counts per table is what catches silent loss, so a restore whose
counts differ from the manifest fails even when every checksum matches.

``restore_test_status`` is deliberately tri-state. ``UNTESTED`` is not a pass,
and it is not the same as ``FAILED``; the handoff gate requires ``PASSED``, so
a backup that was never exercised cannot be mistaken for one that was.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Iterable, Mapping, Sequence

from app.services.snapback_backup import BackupIntegrityError, sha256_file

#: Written relative to the repository root.
BACKUP_MANIFEST_DIR: Final[str] = "data/backups/manifests"

MANIFEST_SCHEMA_VERSION: Final[str] = "1"


class RestoreStatus(StrEnum):
    """Whether this backup has ever been proved to restore."""

    #: Never exercised. Not a pass.
    UNTESTED = "UNTESTED"
    PASSED = "PASSED"
    FAILED = "FAILED"


def table_row_counts(db_path: Path | str) -> dict[str, int]:
    """Row count per user table. Ordered, so two manifests compare cleanly."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        counts: dict[str, int] = {}
        for table in tables:
            # Table names come from sqlite_master, not from a caller, and are
            # quoted anyway; parameters are not permitted in this position.
            counts[table] = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        return counts
    finally:
        conn.close()


def _is_sqlite_sidecar(name: str) -> bool:
    """``foo.db-wal`` and friends describe a connection, not the data."""
    return name.endswith(("-wal", "-shm", "-journal"))


def schema_signature(db_path: Path | str) -> dict[str, str]:
    """The CREATE statement per table, so a schema change is visible as text."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return {
            name: (sql or "")
            for name, sql in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        }
    finally:
        conn.close()


@dataclass(frozen=True)
class BackupManifest:
    """Everything needed to judge one backup without opening it."""

    backup_id: str
    created_at: str

    release_sha: str
    release_tag: str

    #: ``{"evidence": "3", "universe": "2", ...}``
    schema_versions: Mapping[str, str]

    #: ``{"snapback_observations.db": "<sha256>"}``, relative to the backup dir.
    file_checksums: Mapping[str, str]

    #: ``{"<file>": {"<table>": count}}``
    row_counts: Mapping[str, Mapping[str, int]]

    #: ``{"<file>": {"<table>": "CREATE TABLE ..."}}``
    schema_signatures: Mapping[str, Mapping[str, str]]

    restore_test_status: RestoreStatus = RestoreStatus.UNTESTED
    restore_tested_at: str | None = None
    restore_failure: str | None = None

    manifest_schema_version: str = MANIFEST_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "backup_id": self.backup_id,
            "created_at": self.created_at,
            "release_sha": self.release_sha,
            "release_tag": self.release_tag,
            "schema_versions": dict(self.schema_versions),
            "file_checksums": dict(self.file_checksums),
            "row_counts": {k: dict(v) for k, v in self.row_counts.items()},
            "schema_signatures": {k: dict(v) for k, v in self.schema_signatures.items()},
            "restore_test_status": str(self.restore_test_status),
            "restore_tested_at": self.restore_tested_at,
            "restore_failure": self.restore_failure,
            "manifest_schema_version": self.manifest_schema_version,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BackupManifest":
        return cls(
            backup_id=payload["backup_id"],
            created_at=payload["created_at"],
            release_sha=payload.get("release_sha", "UNKNOWN"),
            release_tag=payload.get("release_tag", "UNKNOWN"),
            schema_versions=payload.get("schema_versions", {}),
            file_checksums=payload.get("file_checksums", {}),
            row_counts=payload.get("row_counts", {}),
            schema_signatures=payload.get("schema_signatures", {}),
            restore_test_status=RestoreStatus(
                payload.get("restore_test_status", RestoreStatus.UNTESTED)
            ),
            restore_tested_at=payload.get("restore_tested_at"),
            restore_failure=payload.get("restore_failure"),
            manifest_schema_version=payload.get(
                "manifest_schema_version", MANIFEST_SCHEMA_VERSION
            ),
        )


def _release_facts() -> tuple[str, str, dict[str, str]]:
    from app.core.release_manifest import release_tag, runtime_sha
    from app.core.strategy_identity import EVIDENCE_SCHEMA_VERSION

    try:
        from app.services.snapback_candidate_universe import UNIVERSE_SCHEMA_VERSION

        universe = str(UNIVERSE_SCHEMA_VERSION)
    except Exception:  # pragma: no cover - defensive
        universe = "UNKNOWN"

    return (
        runtime_sha(),
        release_tag(),
        {"evidence": EVIDENCE_SCHEMA_VERSION, "universe": universe},
    )


def build_backup_manifest(
    backup_dir: Path | str,
    *,
    backup_id: str | None = None,
    created_at: datetime | None = None,
    databases: Sequence[Path | str] | None = None,
) -> BackupManifest:
    """Describe a backup directory: checksums, schemas and row counts per file.

    ``databases`` defaults to every ``*.db`` in the directory, so a backup that
    grows a second database is covered without anybody remembering to add it.
    """
    directory = Path(backup_dir)
    if not directory.is_dir():
        raise FileNotFoundError(f"backup directory does not exist: {directory}")

    files = (
        [Path(p) for p in databases]
        if databases is not None
        else sorted(directory.glob("*.db"))
    )
    if not files:
        raise FileNotFoundError(f"no databases found in backup directory {directory}")

    checksums: dict[str, str] = {}
    counts: dict[str, dict[str, int]] = {}
    schemas: dict[str, dict[str, str]] = {}
    for db in files:
        name = db.name
        checksums[name] = sha256_file(db)
        counts[name] = table_row_counts(db)
        schemas[name] = schema_signature(db)

    # Any non-database file that travelled with the backup is checksummed too,
    # so a tampered freeze record is as visible as a tampered database.
    for extra in sorted(directory.iterdir()):
        if not extra.is_file() or extra.name == "manifest.json":
            continue
        if extra.suffix == ".db" or _is_sqlite_sidecar(extra.name):
            continue
        checksums[extra.name] = sha256_file(extra)

    sha, tag, schema_versions = _release_facts()
    stamp = created_at or datetime.now(timezone.utc)

    return BackupManifest(
        backup_id=backup_id or directory.name,
        created_at=stamp.isoformat(),
        release_sha=sha,
        release_tag=tag,
        schema_versions=schema_versions,
        file_checksums=checksums,
        row_counts=counts,
        schema_signatures=schemas,
    )


#: Databases a full backup must contain, resolved from the environment.
#: ``STERLING_BACKUP_DATABASES`` overrides it with a ``:``-separated list.
#:
#: Section 14.1 asks for evidence AND runtime databases. Backing up only the
#: evidence store leaves the durable intent record — the thing restart recovery
#: reads to decide whether an order was ever sent — outside the backup.
DEFAULT_DATABASE_ENV: Final[str] = "STERLING_BACKUP_DATABASES"


def declared_databases(root: Path | str | None = None) -> tuple[Path, ...]:
    """Every database a full backup should contain, in a stable order.

    Paths that do not exist are dropped here and reported by
    :func:`create_full_backup` as a gap, rather than failing the whole backup:
    a missing runtime database must not stop the evidence being protected.
    """
    import os

    from app.core.release_manifest import repo_root

    base = Path(root) if root is not None else repo_root()

    declared = (os.environ.get(DEFAULT_DATABASE_ENV) or "").strip()
    if declared:
        candidates = [Path(part) for part in declared.split(":") if part.strip()]
    else:
        evidence = os.environ.get("STERLING_EVIDENCE_DB") or str(
            base / "backend/snapback_observations.db"
        )
        candidates = [
            Path(evidence),
            base / "backend/sterling_paper.db",
            base / "data/intents.db",
        ]

    seen: dict[str, Path] = {}
    for path in candidates:
        resolved = path.expanduser()
        if resolved.exists() and resolved.name not in seen:
            seen[resolved.name] = resolved
    return tuple(seen.values())


def snapshot_database(source: Path | str, destination: Path | str) -> str:
    """Consistent online snapshot of one SQLite database. Returns its sha256.

    SQLite's backup API rather than a byte copy: a copy taken while the runner
    holds an open WAL connection is not a consistent database, and the copy
    that is wrong is the one you only discover during a restore.
    """
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    partial.unlink(missing_ok=True)

    try:
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        # WAL databases can refuse a read-only open when shared memory cannot
        # be attached. This path never writes to the source.
        src = sqlite3.connect(str(source))
    try:
        dst = sqlite3.connect(str(partial))
        try:
            src.backup(dst)
            integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            dst.close()
    finally:
        src.close()

    if integrity != "ok":
        partial.unlink(missing_ok=True)
        raise BackupIntegrityError(
            f"snapshot of {source} failed integrity_check: {integrity}"
        )

    partial.replace(destination)

    # The snapshot connection leaves -wal/-shm beside the copy. They describe a
    # connection that no longer exists, and checksumming them into the manifest
    # would fail every later restore-check for no real reason.
    for sidecar in ("-wal", "-shm", "-journal"):
        Path(str(destination) + sidecar).unlink(missing_ok=True)
        Path(str(partial) + sidecar).unlink(missing_ok=True)

    return sha256_file(destination)


@dataclass(frozen=True)
class FullBackup:
    """Where a full backup landed, and what it could not include."""

    directory: Path
    manifest: "BackupManifest"
    databases: tuple[str, ...]
    missing: tuple[str, ...] = field(default_factory=tuple)


def create_full_backup(
    *,
    backup_root: Path | str,
    root: Path | str | None = None,
    databases: Sequence[Path | str] | None = None,
    now: datetime | None = None,
) -> FullBackup:
    """A backup that contains what section 14.1 asks for.

    Databases, the release manifest and the schema versions travel together, so
    a restored directory can say which build wrote it without reference to the
    repository it came from.
    """
    from app.core.release_manifest import build_release_manifest, repo_root

    base = Path(root) if root is not None else repo_root()
    stamp = now or datetime.now(timezone.utc)
    directory = Path(backup_root) / stamp.date().isoformat()
    directory.mkdir(parents=True, exist_ok=True)

    requested = (
        [Path(p) for p in databases] if databases is not None else list(declared_databases(base))
    )
    missing = [str(p) for p in requested if not Path(p).exists()]
    present = [Path(p) for p in requested if Path(p).exists()]

    if not present:
        raise FileNotFoundError(
            f"no database to back up; requested: {', '.join(map(str, requested)) or 'none'}"
        )

    for source in present:
        snapshot_database(source, directory / source.name)

    # The release identity travels with the bytes. Restoring a backup and then
    # asking the repository what build wrote it gives the CURRENT build, which
    # is exactly the wrong answer.
    (directory / "release.json").write_text(
        json.dumps(build_release_manifest(), indent=2, sort_keys=True) + "\n"
    )

    manifest = build_backup_manifest(
        directory,
        created_at=stamp,
        databases=[directory / source.name for source in present],
    )
    write_backup_manifest(manifest, root=base, backup_dir=directory)


    return FullBackup(
        directory=directory,
        manifest=manifest,
        databases=tuple(source.name for source in present),
        missing=tuple(missing),
    )


def augment_backup_directory(
    directory: Path | str,
    *,
    root: Path | str | None = None,
    already_included: Sequence[Path | str] = (),
    now: datetime | None = None,
) -> FullBackup:
    """Bring an existing backup directory up to the section 14.1 contents.

    The scheduled backup snapshots the evidence database and verifies its
    checksum. That path is left exactly as it is — it is the one that has been
    proved — and this adds what section 14.1 also asks for: the remaining
    declared databases and the release identity that wrote them.

    Additive by design. A failure here must never invalidate an evidence backup
    that already succeeded, so the caller records it as an error and keeps the
    backup.
    """
    from app.core.release_manifest import build_release_manifest, repo_root

    base = Path(root) if root is not None else repo_root()
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)

    done = {Path(p).name for p in already_included} | {
        p.name for p in target.glob("*.db")
    }

    requested = list(declared_databases(base))
    missing = [str(p) for p in requested if not Path(p).exists()]
    added = [p for p in requested if p.exists() and p.name not in done]

    for source in added:
        snapshot_database(source, target / source.name)

    (target / "release.json").write_text(
        json.dumps(build_release_manifest(), indent=2, sort_keys=True) + "\n"
    )

    manifest = build_backup_manifest(target, created_at=now or datetime.now(timezone.utc))
    write_backup_manifest(manifest, root=base, backup_dir=target)

    return FullBackup(
        directory=target,
        manifest=manifest,
        databases=tuple(sorted(p.name for p in target.glob("*.db"))),
        missing=tuple(missing),
    )


def manifest_path(
    manifest: BackupManifest, root: Path | str | None = None
) -> Path:
    from app.core.release_manifest import repo_root

    base = Path(root) if root is not None else repo_root()
    return base / BACKUP_MANIFEST_DIR / f"{manifest.backup_id}.json"


def write_backup_manifest(
    manifest: BackupManifest,
    *,
    root: Path | str | None = None,
    backup_dir: Path | str | None = None,
) -> Path:
    """Write the manifest beside the backup and into the manifest directory."""
    path = manifest_path(manifest, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(manifest.as_dict(), indent=2, sort_keys=True) + "\n"
    path.write_text(body)
    if backup_dir is not None:
        (Path(backup_dir) / "manifest.json").write_text(body)
    return path


def read_backup_manifest(path: Path | str) -> BackupManifest:
    return BackupManifest.from_dict(json.loads(Path(path).read_text()))


@dataclass(frozen=True)
class RestoreProof:
    """The outcome of actually restoring a backup into an isolated location."""

    backup_id: str
    passed: bool
    checked_at: str
    failures: tuple[str, ...] = field(default_factory=tuple)
    restored_row_counts: Mapping[str, Mapping[str, int]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "backup_id": self.backup_id,
            "passed": self.passed,
            "checked_at": self.checked_at,
            "failures": list(self.failures),
            "restored_row_counts": {
                k: dict(v) for k, v in self.restored_row_counts.items()
            },
        }


def prove_restore(
    backup_dir: Path | str,
    manifest: BackupManifest | None = None,
    *,
    destination: Path | str | None = None,
) -> RestoreProof:
    """Restore into an isolated directory and check it against the manifest.

    Nothing in the live tree is touched: the copy lands in a temporary
    directory that is removed afterwards unless ``destination`` is given.
    """
    directory = Path(backup_dir)
    stored = manifest or _load_or_build(directory)
    failures: list[str] = []
    restored_counts: dict[str, dict[str, int]] = {}

    own_tmp = destination is None
    target = Path(destination) if destination is not None else Path(
        tempfile.mkdtemp(prefix="sterling-restore-check-")
    )

    try:
        for name, expected_sum in stored.file_checksums.items():
            source = directory / name
            if not source.exists():
                failures.append(f"{name}: missing from backup directory")
                continue
            actual_sum = sha256_file(source)
            if actual_sum != expected_sum:
                failures.append(
                    f"{name}: checksum mismatch "
                    f"(manifest {expected_sum[:16]}, file {actual_sum[:16]})"
                )
                continue

            copy = target / name
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, copy)

            if name not in stored.row_counts:
                continue

            try:
                conn = sqlite3.connect(str(copy))
                try:
                    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                failures.append(f"{name}: cannot be opened after restore: {exc}")
                continue

            if integrity != "ok":
                failures.append(f"{name}: integrity_check returned {integrity!r}")
                continue

            actual_counts = table_row_counts(copy)
            restored_counts[name] = actual_counts
            failures.extend(
                _count_failures(name, stored.row_counts[name], actual_counts)
            )

            expected_schema = stored.schema_signatures.get(name)
            if expected_schema is not None:
                actual_schema = schema_signature(copy)
                for table, sql in expected_schema.items():
                    if actual_schema.get(table) != sql:
                        failures.append(f"{name}.{table}: schema differs after restore")
    finally:
        if own_tmp:
            shutil.rmtree(target, ignore_errors=True)

    return RestoreProof(
        backup_id=stored.backup_id,
        passed=not failures,
        checked_at=datetime.now(timezone.utc).isoformat(),
        failures=tuple(failures),
        restored_row_counts=restored_counts,
    )


def _count_failures(
    name: str,
    expected: Mapping[str, int],
    actual: Mapping[str, int],
) -> Iterable[str]:
    for table in sorted(set(expected) | set(actual)):
        want = expected.get(table)
        got = actual.get(table)
        if want is None:
            yield f"{name}.{table}: table appeared after restore ({got} rows)"
        elif got is None:
            yield f"{name}.{table}: table missing after restore (expected {want} rows)"
        elif want != got:
            yield f"{name}.{table}: {got} rows restored, manifest recorded {want}"


def _load_or_build(directory: Path) -> BackupManifest:
    beside = directory / "manifest.json"
    if beside.exists():
        return read_backup_manifest(beside)
    return build_backup_manifest(directory)


def record_restore_proof(
    manifest: BackupManifest, proof: RestoreProof
) -> BackupManifest:
    """Return the manifest with the proof's verdict stamped on it."""
    return BackupManifest(
        backup_id=manifest.backup_id,
        created_at=manifest.created_at,
        release_sha=manifest.release_sha,
        release_tag=manifest.release_tag,
        schema_versions=manifest.schema_versions,
        file_checksums=manifest.file_checksums,
        row_counts=manifest.row_counts,
        schema_signatures=manifest.schema_signatures,
        restore_test_status=RestoreStatus.PASSED if proof.passed else RestoreStatus.FAILED,
        restore_tested_at=proof.checked_at,
        restore_failure="; ".join(proof.failures) or None,
        manifest_schema_version=manifest.manifest_schema_version,
    )


def latest_backup_dir(backup_root: Path | str) -> Path | None:
    """The most recent dated backup directory, or ``None`` if there is none."""
    root = Path(backup_root)
    if not root.is_dir():
        return None
    dated = [p for p in root.iterdir() if p.is_dir() and any(p.glob("*.db"))]
    if not dated:
        return None
    return sorted(dated, key=lambda p: p.name)[-1]


def render_restore_proof(proof: RestoreProof) -> str:
    """Operator-readable result. The failure list is the whole point."""
    head = f"restore-check {proof.backup_id}: {'PASSED' if proof.passed else 'FAILED'}"
    if proof.passed:
        total = sum(sum(c.values()) for c in proof.restored_row_counts.values())
        return f"{head}\n{total} rows restored and verified across " \
               f"{len(proof.restored_row_counts)} database(s)."
    return "\n".join([head, *(f"  - {f}" for f in proof.failures)])


__all__ = [
    "BACKUP_MANIFEST_DIR",
    "BackupManifest",
    "RestoreProof",
    "RestoreStatus",
    "augment_backup_directory",
    "build_backup_manifest",
    "create_full_backup",
    "declared_databases",
    "latest_backup_dir",
    "manifest_path",
    "prove_restore",
    "read_backup_manifest",
    "record_restore_proof",
    "render_restore_proof",
    "schema_signature",
    "snapshot_database",
    "table_row_counts",
    "write_backup_manifest",
]
