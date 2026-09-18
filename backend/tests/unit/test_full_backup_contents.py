"""Section 14.1: a backup holds the evidence AND runtime databases, plus the
release identity that wrote them.

Backing up only the evidence store leaves the durable intent record — the thing
restart recovery reads to decide whether an order was ever sent — outside the
backup, and leaves a restored directory unable to say which build produced it.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from app.core.backup_manifest import (
    create_full_backup,
    declared_databases,
    prove_restore,
    snapshot_database,
)


def _make_db(path, rows: int = 3, table: str = "t"):
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
        conn.executemany(f"INSERT INTO {table} (id) VALUES (?)", [(i,) for i in range(rows)])
        conn.commit()
    finally:
        conn.close()
    return path


@pytest.fixture()
def sources(tmp_path):
    evidence = _make_db(tmp_path / "snapback_observations.db", rows=5)
    runtime = _make_db(tmp_path / "intents.db", rows=2, table="intents")
    return evidence, runtime


def test_every_declared_database_is_included(tmp_path, sources):
    evidence, runtime = sources

    result = create_full_backup(
        backup_root=tmp_path / "backups", root=tmp_path, databases=[evidence, runtime]
    )

    assert set(result.databases) == {"snapback_observations.db", "intents.db"}
    assert result.manifest.row_counts["snapback_observations.db"]["t"] == 5
    assert result.manifest.row_counts["intents.db"]["intents"] == 2


def test_the_release_identity_travels_with_the_bytes(tmp_path, sources):
    evidence, _ = sources

    result = create_full_backup(
        backup_root=tmp_path / "backups", root=tmp_path, databases=[evidence]
    )

    # Asking the repository later gives the CURRENT build, which is the wrong
    # answer about a backup taken months ago.
    stored = json.loads((result.directory / "release.json").read_text())
    assert stored["lane_count"] == 10
    assert "release.json" in result.manifest.file_checksums


def test_a_declared_database_that_does_not_exist_is_reported_not_silent(tmp_path, sources):
    evidence, _ = sources

    result = create_full_backup(
        backup_root=tmp_path / "backups",
        root=tmp_path,
        databases=[evidence, tmp_path / "never_created.db"],
    )

    assert result.databases == ("snapback_observations.db",)
    assert any("never_created.db" in name for name in result.missing)


def test_a_backup_with_nothing_to_back_up_fails_loudly(tmp_path):
    with pytest.raises(FileNotFoundError):
        create_full_backup(
            backup_root=tmp_path / "backups",
            root=tmp_path,
            databases=[tmp_path / "absent.db"],
        )


def test_wal_sidecars_never_reach_the_manifest(tmp_path, sources):
    # A -wal file describes a connection that no longer exists. Checksumming it
    # would fail every later restore-check for no real reason.
    evidence, _ = sources
    result = create_full_backup(
        backup_root=tmp_path / "backups", root=tmp_path, databases=[evidence]
    )

    assert not [
        name
        for name in result.manifest.file_checksums
        if name.endswith(("-wal", "-shm", "-journal"))
    ]
    assert not list(result.directory.glob("*-wal"))


def test_a_full_backup_proves_its_own_restore(tmp_path, sources, monkeypatch):
    evidence, runtime = sources
    # Coverage resolves the canonical journal from STERLING_DB_PATH, which the
    # suite points at a temporary database elsewhere. Left set, that database is
    # a real artifact this backup genuinely omits, and the restore correctly
    # refuses. This test is about the restore mechanics, so the host is the
    # temporary tree and nothing else.
    monkeypatch.delenv("STERLING_DB_PATH", raising=False)
    result = create_full_backup(
        backup_root=tmp_path / "backups", root=tmp_path, databases=[evidence, runtime]
    )

    proof = prove_restore(result.directory)

    assert proof.passed, proof.failures
    assert set(proof.restored_row_counts) == {"snapback_observations.db", "intents.db"}


def test_a_snapshot_is_consistent_while_the_source_is_open(tmp_path):
    source = _make_db(tmp_path / "live.db", rows=4)
    holder = sqlite3.connect(str(source))
    holder.execute("PRAGMA journal_mode=WAL")
    holder.execute("INSERT INTO t (id) VALUES (99)")
    holder.commit()

    try:
        checksum = snapshot_database(source, tmp_path / "copy.db")
    finally:
        holder.close()

    assert len(checksum) == 64
    conn = sqlite3.connect(str(tmp_path / "copy.db"))
    try:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 5
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


class TestDeclaredDatabases:
    def test_the_environment_can_name_the_set(self, tmp_path, monkeypatch):
        a = _make_db(tmp_path / "a.db")
        b = _make_db(tmp_path / "b.db")
        monkeypatch.setenv("STERLING_BACKUP_DATABASES", f"{a}:{b}")

        assert [p.name for p in declared_databases(tmp_path)] == ["a.db", "b.db"]

    def test_absent_paths_are_dropped_rather_than_failing_the_backup(
        self, tmp_path, monkeypatch
    ):
        a = _make_db(tmp_path / "a.db")
        monkeypatch.setenv("STERLING_BACKUP_DATABASES", f"{a}:{tmp_path / 'gone.db'}")

        assert [p.name for p in declared_databases(tmp_path)] == ["a.db"]

    def test_the_same_database_is_never_listed_twice(self, tmp_path, monkeypatch):
        a = _make_db(tmp_path / "a.db")
        monkeypatch.setenv("STERLING_BACKUP_DATABASES", f"{a}:{a}")

        assert len(declared_databases(tmp_path)) == 1
