"""A backup is proved by restoring it, not by existing.

The test that matters is the truncated table: a file whose checksum was
recorded before the loss restores and opens perfectly, and only the row counts
catch it.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from app.core.backup_manifest import (
    BackupManifest,
    RestoreStatus,
    build_backup_manifest,
    latest_backup_dir,
    prove_restore,
    read_backup_manifest,
    record_restore_proof,
    render_restore_proof,
    table_row_counts,
    write_backup_manifest,
)


def _make_db(path, rows: int = 5, table: str = "trades"):
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, pnl REAL)")
        conn.executemany(
            f"INSERT INTO {table} (pnl) VALUES (?)", [(float(i),) for i in range(rows)]
        )
        conn.commit()
    finally:
        conn.close()
    return path


@pytest.fixture()
def backup_dir(tmp_path):
    directory = tmp_path / "2026-09-18"
    directory.mkdir(parents=True)
    _make_db(directory / "snapback_observations.db", rows=7)
    (directory / "freeze_record.json").write_text('{"frozen": true}')
    # Assembled by hand, so it declares its own (empty) coverage: a restore-check
    # refuses a backup that cannot say what it was supposed to contain, and these
    # tests are about the restore mechanics rather than that rule.
    (directory / "coverage.json").write_text("[]", encoding="utf-8")
    return directory


def test_manifest_records_row_counts_and_checksums(backup_dir):
    manifest = build_backup_manifest(backup_dir)

    assert manifest.backup_id == "2026-09-18"
    assert manifest.row_counts["snapback_observations.db"]["trades"] == 7
    assert len(manifest.file_checksums["snapback_observations.db"]) == 64
    # A non-database file that travelled with the backup is covered too.
    assert "freeze_record.json" in manifest.file_checksums
    assert manifest.restore_test_status is RestoreStatus.UNTESTED


def test_untested_is_not_a_pass(backup_dir):
    manifest = build_backup_manifest(backup_dir)
    assert manifest.restore_test_status != RestoreStatus.PASSED
    assert str(manifest.restore_test_status) == "UNTESTED"


def test_restore_proof_passes_on_an_intact_backup(backup_dir):
    proof = prove_restore(backup_dir)

    assert proof.passed, proof.failures
    assert proof.restored_row_counts["snapback_observations.db"]["trades"] == 7
    assert "PASSED" in render_restore_proof(proof)


def test_restore_proof_leaves_nothing_behind(backup_dir, tmp_path):
    before = sorted(p.name for p in tmp_path.iterdir())
    prove_restore(backup_dir)
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_a_truncated_table_fails_even_though_the_file_opens(backup_dir):
    manifest = build_backup_manifest(backup_dir)

    # Lose rows AFTER the manifest was taken, then re-checksum so the bytes
    # agree with themselves. Only the row counts can catch this.
    db = backup_dir / "snapback_observations.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DELETE FROM trades WHERE id > 3")
        conn.commit()
    finally:
        conn.close()
    repaired = build_backup_manifest(backup_dir)
    silent = BackupManifest(
        backup_id=manifest.backup_id,
        created_at=manifest.created_at,
        release_sha=manifest.release_sha,
        release_tag=manifest.release_tag,
        schema_versions=manifest.schema_versions,
        file_checksums=repaired.file_checksums,  # checksums agree
        row_counts=manifest.row_counts,  # counts do not
        schema_signatures=manifest.schema_signatures,
    )

    proof = prove_restore(backup_dir, silent)

    assert not proof.passed
    assert any("3 rows restored" in f and "recorded 7" in f for f in proof.failures)


def test_a_corrupted_file_fails_on_checksum(backup_dir):
    manifest = build_backup_manifest(backup_dir)
    (backup_dir / "freeze_record.json").write_text('{"frozen": false}')

    proof = prove_restore(backup_dir, manifest)

    assert not proof.passed
    assert any("checksum mismatch" in f for f in proof.failures)


def test_a_missing_file_fails(backup_dir):
    manifest = build_backup_manifest(backup_dir)
    (backup_dir / "freeze_record.json").unlink()

    proof = prove_restore(backup_dir, manifest)

    assert not proof.passed
    assert any("missing from backup directory" in f for f in proof.failures)


def test_a_dropped_table_fails(backup_dir):
    manifest = build_backup_manifest(backup_dir)
    db = backup_dir / "snapback_observations.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DROP TABLE trades")
        conn.commit()
    finally:
        conn.close()
    repaired = build_backup_manifest(backup_dir)
    silent = BackupManifest(
        backup_id=manifest.backup_id,
        created_at=manifest.created_at,
        release_sha=manifest.release_sha,
        release_tag=manifest.release_tag,
        schema_versions=manifest.schema_versions,
        file_checksums=repaired.file_checksums,
        row_counts=manifest.row_counts,
        schema_signatures=manifest.schema_signatures,
    )

    proof = prove_restore(backup_dir, silent)

    assert not proof.passed
    assert any("table missing after restore" in f for f in proof.failures)


def test_proof_is_stamped_onto_the_manifest(backup_dir):
    manifest = build_backup_manifest(backup_dir)
    proof = prove_restore(backup_dir, manifest)

    stamped = record_restore_proof(manifest, proof)

    assert stamped.restore_test_status is RestoreStatus.PASSED
    assert stamped.restore_tested_at == proof.checked_at
    assert stamped.restore_failure is None
    # Everything else is carried forward untouched.
    assert stamped.row_counts == manifest.row_counts


def test_a_failed_proof_is_stamped_with_its_reason(backup_dir):
    manifest = build_backup_manifest(backup_dir)
    (backup_dir / "snapback_observations.db").unlink()

    stamped = record_restore_proof(manifest, prove_restore(backup_dir, manifest))

    assert stamped.restore_test_status is RestoreStatus.FAILED
    assert "missing from backup directory" in stamped.restore_failure


def test_manifest_round_trips_through_disk(backup_dir, tmp_path):
    manifest = build_backup_manifest(backup_dir)
    path = write_backup_manifest(manifest, root=tmp_path, backup_dir=backup_dir)

    assert path == tmp_path / "data/backups/manifests/2026-09-18.json"
    assert read_backup_manifest(path).as_dict() == manifest.as_dict()
    # A copy travels with the backup, so a restored directory explains itself.
    assert json.loads((backup_dir / "manifest.json").read_text())["backup_id"] == "2026-09-18"


def test_prove_restore_uses_the_manifest_beside_the_backup(backup_dir, tmp_path):
    manifest = build_backup_manifest(backup_dir)
    write_backup_manifest(manifest, root=tmp_path, backup_dir=backup_dir)

    proof = prove_restore(backup_dir)

    assert proof.backup_id == "2026-09-18"
    assert proof.passed, proof.failures


def test_a_backup_that_cannot_say_what_it_holds_is_refused(backup_dir, tmp_path):
    manifest = build_backup_manifest(backup_dir)
    write_backup_manifest(manifest, root=tmp_path, backup_dir=backup_dir)
    (backup_dir / "coverage.json").unlink()

    proof = prove_restore(backup_dir)

    assert proof.passed is False
    assert any("coverage.json" in failure for failure in proof.failures)


def test_latest_backup_dir_picks_the_newest(tmp_path):
    for day in ("2026-09-16", "2026-09-18", "2026-09-17"):
        directory = tmp_path / day
        directory.mkdir()
        _make_db(directory / "x.db", rows=1)
    # A directory with no database is not a backup.
    (tmp_path / "2026-09-19").mkdir()

    assert latest_backup_dir(tmp_path).name == "2026-09-18"


def test_latest_backup_dir_is_none_when_there_are_none(tmp_path):
    assert latest_backup_dir(tmp_path / "nothing") is None


def test_table_row_counts_ignores_sqlite_internals(backup_dir):
    counts = table_row_counts(backup_dir / "snapback_observations.db")
    assert set(counts) == {"trades"}
