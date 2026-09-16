"""Automatic backup, verification and restore of the Snapback prospective evidence database.

The prospective SQLite database is the authoritative record of frozen-runtime paper
evidence. It is backed up with SQLite's own online backup API rather than a byte copy,
so a snapshot taken while the runner holds an open WAL connection is still consistent.

Every backup directory carries:
    snapback_observations.db          consistent snapshot
    snapback_observations.db.sha256   checksum of those exact bytes
    freeze_record.txt                 frozen runtime identity at backup time
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)

DB_FILENAME = "snapback_observations.db"
CHECKSUM_FILENAME = DB_FILENAME + ".sha256"
FREEZE_RECORD_FILENAME = "freeze_record.txt"

DEFAULT_KEEP_DAILY = 14
DEFAULT_KEEP_WEEKLY = 8


class BackupIntegrityError(Exception):
    """Raised when backup bytes do not match their recorded checksum."""


@dataclass(frozen=True)
class BackupArtifact:
    directory: Path
    db_path: Path
    checksum_path: Path
    freeze_record_path: Optional[Path]
    checksum: str
    created_at: datetime


@dataclass
class PruneResult:
    removed_count: int = 0
    removed: List[str] = field(default_factory=list)
    kept: List[str] = field(default_factory=list)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _open_source_readonly(source_db: Path) -> sqlite3.Connection:
    """Open the live database for reading without writing to it where possible."""
    uri = f"file:{source_db}?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError:
        # A WAL database may refuse a read-only open when shared-memory cannot be
        # attached. Fall back to a normal connection; the backup path never writes.
        return sqlite3.connect(str(source_db))


def create_backup(
    *,
    source_db: Path,
    backup_root: Path,
    freeze_record: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> BackupArtifact:
    """Take a consistent snapshot of the prospective database into backup_root/<date>/."""
    source_db = Path(source_db)
    backup_root = Path(backup_root)
    now = now or datetime.now(timezone.utc)

    if not source_db.exists():
        raise FileNotFoundError(f"Snapback prospective database not found: {source_db}")

    directory = backup_root / now.date().isoformat()
    directory.mkdir(parents=True, exist_ok=True)

    db_path = directory / DB_FILENAME
    tmp_path = directory / (DB_FILENAME + ".partial")
    if tmp_path.exists():
        tmp_path.unlink()

    src = _open_source_readonly(source_db)
    try:
        dst = sqlite3.connect(str(tmp_path))
        try:
            src.backup(dst)
            integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            dst.close()
    finally:
        src.close()

    if integrity != "ok":
        tmp_path.unlink(missing_ok=True)
        raise BackupIntegrityError(
            f"Snapshot of {source_db} failed integrity_check: {integrity}"
        )

    os.replace(tmp_path, db_path)

    checksum = sha256_file(db_path)
    checksum_path = directory / CHECKSUM_FILENAME
    checksum_path.write_text(checksum + "\n", encoding="utf-8")

    freeze_record_path: Optional[Path] = None
    if freeze_record is not None and Path(freeze_record).exists():
        freeze_record_path = directory / FREEZE_RECORD_FILENAME
        shutil.copyfile(Path(freeze_record), freeze_record_path)

    log.info("Snapback evidence backup written to %s (sha256=%s)", db_path, checksum[:16])

    return BackupArtifact(
        directory=directory,
        db_path=db_path,
        checksum_path=checksum_path,
        freeze_record_path=freeze_record_path,
        checksum=checksum,
        created_at=now,
    )


def verify_backup(backup_db: Path, checksum_path: Path) -> bool:
    """Return True when the backup bytes still match the recorded checksum."""
    backup_db = Path(backup_db)
    checksum_path = Path(checksum_path)
    if not backup_db.exists() or not checksum_path.exists():
        return False
    expected = checksum_path.read_text(encoding="utf-8").strip().split()[0]
    return sha256_file(backup_db) == expected


def restore_backup(
    *,
    backup_db: Path,
    checksum_path: Path,
    destination_db: Path,
) -> Path:
    """Restore a verified backup over destination_db.

    The checksum is verified before the destination is touched, so a corrupted
    backup can never destroy a usable database.
    """
    backup_db = Path(backup_db)
    destination_db = Path(destination_db)

    if not verify_backup(backup_db, Path(checksum_path)):
        raise BackupIntegrityError(
            f"Backup {backup_db} failed checksum verification; destination left untouched"
        )

    destination_db.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=destination_db.name + ".restore.", dir=str(destination_db.parent)
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        shutil.copyfile(backup_db, tmp_path)
        conn = sqlite3.connect(str(tmp_path))
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
        if integrity != "ok":
            raise BackupIntegrityError(
                f"Restored copy of {backup_db} failed integrity_check: {integrity}"
            )
        os.replace(tmp_path, destination_db)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    log.info("Restored Snapback evidence database from %s to %s", backup_db, destination_db)
    return destination_db


def _parse_backup_date(name: str) -> Optional[date]:
    try:
        return date.fromisoformat(name)
    except ValueError:
        return None


def prune_backups(
    *,
    backup_root: Path,
    keep_daily: int = DEFAULT_KEEP_DAILY,
    keep_weekly: int = DEFAULT_KEEP_WEEKLY,
    now: Optional[datetime] = None,
) -> PruneResult:
    """Keep the latest `keep_daily` daily backups plus `keep_weekly` weekly backups."""
    backup_root = Path(backup_root)
    result = PruneResult()
    if not backup_root.exists():
        return result

    dated = []
    for entry in backup_root.iterdir():
        if not entry.is_dir():
            continue
        d = _parse_backup_date(entry.name)
        if d is not None:
            dated.append((d, entry))

    dated.sort(key=lambda item: item[0], reverse=True)

    keep: set = {entry for _, entry in dated[: max(0, keep_daily)]}

    # Weekly retention over the older history: newest backup of each ISO week.
    seen_weeks: set = set()
    for d, entry in dated[max(0, keep_daily) :]:
        if len(seen_weeks) >= max(0, keep_weekly):
            break
        iso_week = d.isocalendar()[:2]
        if iso_week in seen_weeks:
            continue
        seen_weeks.add(iso_week)
        keep.add(entry)

    for _, entry in dated:
        if entry in keep:
            result.kept.append(entry.name)
            continue
        shutil.rmtree(entry)
        result.removed.append(entry.name)

    result.removed_count = len(result.removed)
    return result
