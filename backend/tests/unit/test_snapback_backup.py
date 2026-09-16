from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services.snapback_backup import (
    BackupIntegrityError,
    create_backup,
    prune_backups,
    restore_backup,
    verify_backup,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _create_live_wal_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE evidence (
            id INTEGER PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute("INSERT INTO evidence(value) VALUES ('first')")
    conn.commit()

    # Keep connection open deliberately. Backup must work safely
    # against an active WAL database.
    conn.execute("INSERT INTO evidence(value) VALUES ('second')")
    conn.commit()

    return conn


def test_backup_uses_sqlite_consistent_snapshot_while_wal_db_is_open(tmp_path):
    source = tmp_path / "prospective_freeze_1.db"
    live_conn = _create_live_wal_db(source)

    freeze_record = tmp_path / "PROSPECTIVE_FREEZE_RECORD.md"
    freeze_record.write_text(
        "runtime_sha=9e989dd910995deb5e77385b983e5992c58883c0\n",
        encoding="utf-8",
    )

    backup_root = tmp_path / "backups"
    now = datetime(2026, 9, 16, 16, 0, tzinfo=timezone.utc)

    try:
        artifact = create_backup(
            source_db=source,
            backup_root=backup_root,
            freeze_record=freeze_record,
            now=now,
        )
    finally:
        live_conn.close()

    assert artifact.db_path.exists()
    assert artifact.checksum_path.exists()
    assert artifact.freeze_record_path.exists()

    # Snapshot must contain all committed rows despite source WAL being active.
    conn = sqlite3.connect(artifact.db_path)
    try:
        rows = conn.execute(
            "SELECT value FROM evidence ORDER BY id"
        ).fetchall()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()

    assert rows == [("first",), ("second",)]
    assert integrity == "ok"


def test_backup_checksum_matches_database_bytes(tmp_path):
    source = tmp_path / "prospective.db"
    conn = _create_live_wal_db(source)
    conn.close()

    artifact = create_backup(
        source_db=source,
        backup_root=tmp_path / "backups",
        freeze_record=None,
        now=datetime(2026, 9, 16, 16, 0, tzinfo=timezone.utc),
    )

    checksum_from_file = artifact.checksum_path.read_text(
        encoding="utf-8"
    ).strip()

    assert checksum_from_file == _sha256(artifact.db_path)
    assert verify_backup(
        artifact.db_path,
        artifact.checksum_path,
    ) is True


def test_backup_contains_frozen_runtime_record(tmp_path):
    source = tmp_path / "prospective.db"
    conn = _create_live_wal_db(source)
    conn.close()

    freeze_record = tmp_path / "PROSPECTIVE_FREEZE_RECORD.md"
    freeze_record.write_text(
        (
            "runtime_tag=snapback-prospective-freeze-1.0\n"
            "runtime_sha=9e989dd910995deb5e77385b983e5992c58883c0\n"
            "mode=PAPER\n"
        ),
        encoding="utf-8",
    )

    artifact = create_backup(
        source_db=source,
        backup_root=tmp_path / "backups",
        freeze_record=freeze_record,
        now=datetime(2026, 9, 16, 16, 0, tzinfo=timezone.utc),
    )

    copied = artifact.freeze_record_path.read_text(encoding="utf-8")

    assert "snapback-prospective-freeze-1.0" in copied
    assert "9e989dd910995deb5e77385b983e5992c58883c0" in copied
    assert "mode=PAPER" in copied


def test_restore_round_trip_recovers_complete_database(tmp_path):
    source = tmp_path / "prospective.db"
    conn = _create_live_wal_db(source)
    conn.close()

    artifact = create_backup(
        source_db=source,
        backup_root=tmp_path / "backups",
        freeze_record=None,
        now=datetime(2026, 9, 16, 16, 0, tzinfo=timezone.utc),
    )

    restored = tmp_path / "restored.db"

    restore_backup(
        backup_db=artifact.db_path,
        checksum_path=artifact.checksum_path,
        destination_db=restored,
    )

    conn = sqlite3.connect(restored)
    try:
        rows = conn.execute(
            "SELECT value FROM evidence ORDER BY id"
        ).fetchall()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()

    assert rows == [("first",), ("second",)]
    assert integrity == "ok"


def test_restore_rejects_corrupted_backup_and_does_not_replace_destination(
    tmp_path,
):
    source = tmp_path / "prospective.db"
    conn = _create_live_wal_db(source)
    conn.close()

    artifact = create_backup(
        source_db=source,
        backup_root=tmp_path / "backups",
        freeze_record=None,
        now=datetime(2026, 9, 16, 16, 0, tzinfo=timezone.utc),
    )

    # Corrupt backed-up bytes after checksum generation.
    with artifact.db_path.open("ab") as fh:
        fh.write(b"CORRUPTION")

    destination = tmp_path / "existing_destination.db"
    destination.write_bytes(b"DO-NOT-DESTROY")

    with pytest.raises(BackupIntegrityError):
        restore_backup(
            backup_db=artifact.db_path,
            checksum_path=artifact.checksum_path,
            destination_db=destination,
        )

    # Failed restore must never destroy the previous usable destination.
    assert destination.read_bytes() == b"DO-NOT-DESTROY"


def test_missing_source_database_fails_closed(tmp_path):
    with pytest.raises(FileNotFoundError):
        create_backup(
            source_db=tmp_path / "does-not-exist.db",
            backup_root=tmp_path / "backups",
            freeze_record=None,
            now=datetime(2026, 9, 16, 16, 0, tzinfo=timezone.utc),
        )


def test_pruning_keeps_14_latest_daily_backups_and_weekly_history(tmp_path):
    root = tmp_path / "backups"
    root.mkdir()

    # One backup directory per day.
    from datetime import timedelta

    start = datetime(2026, 7, 1, tzinfo=timezone.utc)

    for offset in range(78):
        d = (start + timedelta(days=offset)).date()
        directory = root / d.isoformat()
        directory.mkdir()
        (directory / "snapback_observations.db").write_bytes(
            f"db-{d}".encode()
        )

    result = prune_backups(
        backup_root=root,
        keep_daily=14,
        keep_weekly=8,
        now=datetime(2026, 9, 17, tzinfo=timezone.utc),
    )

    remaining = sorted(
        p for p in root.iterdir()
        if p.is_dir()
    )

    # At minimum, the latest 14 calendar backup dates must survive.
    latest_expected = {
        (
            datetime(2026, 9, 17, tzinfo=timezone.utc)
            - timedelta(days=i)
        ).date().isoformat()
        for i in range(1, 15)
    }

    remaining_names = {p.name for p in remaining}

    assert latest_expected.issubset(remaining_names)

    # Older history must not be entirely erased: weekly retention remains.
    older_remaining = [
        p for p in remaining
        if p.name not in latest_expected
    ]
    assert len(older_remaining) >= 1

    # Function should explicitly report what it removed.
    assert result.removed_count > 0


def test_backup_never_modifies_source_database(tmp_path):
    source = tmp_path / "prospective.db"
    conn = _create_live_wal_db(source)
    conn.close()

    before_hash = _sha256(source)

    create_backup(
        source_db=source,
        backup_root=tmp_path / "backups",
        freeze_record=None,
        now=datetime(2026, 9, 16, 16, 0, tzinfo=timezone.utc),
    )

    after_hash = _sha256(source)

    assert before_hash == after_hash
