"""Re-key every stored secret, atomically, without ever printing one.

The credentials in this deployment can be decrypted with Sterling's
deterministic development fallback key. That is fine on a research laptop and
unacceptable on a machine that trades unattended: anyone with a copy of the
database and a copy of the source has the broker session.

This migration decrypts every persisted secret under the old key and re-encrypts
it under a new random one. Three properties matter more than convenience:

  * It is all-or-nothing. A half-migrated database is worse than either state,
    because some rows decrypt and some do not, and nothing on the machine can
    tell you which. Every value is decrypted and re-encrypted in memory first;
    the database is written once, in a transaction, only if every single value
    round-tripped.
  * It never prints, logs or returns a secret. Not truncated, not "just the
    first four characters". The report says how many values moved and which
    columns they were in.
  * It writes the new keys to a file the operator names, with mode 600, and
    tells them to put it somewhere the repository is not.

`STERLING_SECRET_KEY` and `STERLING_JWT_SECRET` are generated separately and
must stay separate: reusing one as the other means a leaked JWT signing key is
also the key to every broker credential at rest.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

log = logging.getLogger(__name__)

__all__ = [
    "ENCRYPTED_COLUMNS", "MigrationPlan", "MigrationResult",
    "generate_secret", "plan_migration", "migrate_secrets",
]

#: Every table and column holding an encrypted value. A column missing from
#: this list is a credential that silently stays on the old key, so the
#: migration verifies the list against the live schema and refuses on a surprise.
ENCRYPTED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "kite_accounts": ("api_secret_enc", "access_token_enc", "refresh_token_enc"),
    "truedata_credentials": ("password_enc", "session_token_enc"),
}

#: Minimum length the production validator enforces. Generated keys are longer.
MIN_SECRET_LENGTH = 32


def generate_secret(nbytes: int = 48) -> str:
    """A random, URL-safe secret. Never derived from anything guessable."""
    return secrets.token_urlsafe(nbytes)


def _fernet_for(secret: str):
    from cryptography.fernet import Fernet

    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def _decrypt_with(fernet, value: str) -> str:
    """Decrypt one stored value under an explicit key, not the ambient one."""
    if value.startswith("fernet:"):
        return fernet.decrypt(value[len("fernet:"):].encode("utf-8")).decode("utf-8")
    if value.startswith("b64:"):
        return base64.urlsafe_b64decode(value[len("b64:"):].encode("utf-8")).decode("utf-8")
    # Written before encryption existed. It still has to move.
    return value


def _encrypt_with(fernet, plaintext: str) -> str:
    return "fernet:" + fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


@dataclass(frozen=True)
class MigrationPlan:
    """What would move, counted by column. Never the values themselves."""

    counts: Mapping[str, int]
    unknown_columns: tuple[str, ...] = ()
    missing_tables: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def as_dict(self) -> dict[str, Any]:
        return {"counts": dict(self.counts), "total": self.total,
                "unknown_columns": list(self.unknown_columns),
                "missing_tables": list(self.missing_tables)}


@dataclass(frozen=True)
class MigrationResult:
    migrated: int = 0
    verified: int = 0
    backup_path: str = ""
    env_file: str = ""
    ok: bool = False
    error: str = ""
    plan: MigrationPlan | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "migrated": self.migrated, "verified": self.verified,
                "backup_path": self.backup_path, "env_file": self.env_file,
                "error": self.error,
                "plan": self.plan.as_dict() if self.plan else None}


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def plan_migration(db_path: Path | str) -> MigrationPlan:
    """Count what would move, and notice any encrypted column nobody declared."""
    counts: dict[str, int] = {}
    unknown: list[str] = []
    missing: list[str] = []

    with sqlite3.connect(str(db_path)) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for table, columns in ENCRYPTED_COLUMNS.items():
            if table not in tables:
                missing.append(table)
                continue
            present = _table_columns(conn, table)
            for column in columns:
                if column not in present:
                    missing.append(f"{table}.{column}")
                    continue
                total = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {column} IS NOT NULL "
                    f"AND {column} <> ''").fetchone()[0]
                counts[f"{table}.{column}"] = int(total)

        # A credential column this module does not know about would stay on the
        # old key while the report said the migration was complete.
        for table in sorted(tables):
            for column in sorted(_table_columns(conn, table)):
                if column.endswith("_enc") and column not in ENCRYPTED_COLUMNS.get(table, ()):
                    unknown.append(f"{table}.{column}")

    return MigrationPlan(counts=counts, unknown_columns=tuple(unknown),
                         missing_tables=tuple(missing))


def _backup(db_path: Path, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    destination = directory / f"{db_path.name}.pre-migration-{stamp}"
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(str(destination))
        try:
            source.backup(target)
            integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            target.close()
    finally:
        source.close()
    if integrity != "ok":
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"pre-migration backup failed integrity_check: {integrity}")
    return destination


def _write_env_file(path: Path, secret_key: str, jwt_secret: str) -> None:
    """Write the new keys where only the owner can read them."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create with 0600 from the start: a world-readable moment is a leak.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(
            "# Sterling production secrets. Keep this file out of the repository,\n"
            "# out of backups that leave this machine, and out of screenshots.\n"
            f"STERLING_SECRET_KEY={secret_key}\n"
            f"STERLING_JWT_SECRET={jwt_secret}\n"
            "ENVIRONMENT=production\n"
        )
    os.chmod(path, 0o600)


def migrate_secrets(
    *,
    db_path: Path | str,
    env_file: Path | str,
    old_secret: str | None = None,
    new_secret: str | None = None,
    new_jwt_secret: str | None = None,
    backup_dir: Path | str | None = None,
) -> MigrationResult:
    """Re-encrypt every stored secret under a new key, or change nothing.

    ``old_secret`` defaults to the development fallback, which is the state
    this migration exists to leave.
    """
    database = Path(db_path)
    if not database.exists():
        return MigrationResult(error=f"no database at {database}")

    plan = plan_migration(database)
    if plan.unknown_columns:
        return MigrationResult(
            plan=plan,
            error="undeclared encrypted column(s): " + ", ".join(plan.unknown_columns)
            + " — add them to ENCRYPTED_COLUMNS before migrating, or they stay "
              "readable under the old key",
        )

    source_secret = old_secret if old_secret is not None else "sterling-dev-insecure-key"
    target_secret = new_secret or generate_secret()
    target_jwt = new_jwt_secret or generate_secret()

    if len(target_secret) < MIN_SECRET_LENGTH or len(target_jwt) < MIN_SECRET_LENGTH:
        return MigrationResult(plan=plan, error="a new secret must be at least "
                                                f"{MIN_SECRET_LENGTH} characters")
    if target_secret == target_jwt:
        return MigrationResult(
            plan=plan,
            error="STERLING_SECRET_KEY and STERLING_JWT_SECRET must differ: one key "
                  "for both means a leaked token signer is also the credential key",
        )

    try:
        old_fernet = _fernet_for(source_secret)
        new_fernet = _fernet_for(target_secret)
    except Exception as exc:  # noqa: BLE001
        return MigrationResult(plan=plan, error=f"cryptography unavailable: {exc}")

    try:
        backup_path = _backup(database, Path(backup_dir) if backup_dir
                              else database.parent / "secret-migration-backups")
    except Exception as exc:  # noqa: BLE001
        return MigrationResult(plan=plan, error=f"could not back up before writing: {exc}")

    # Phase one: read and re-encrypt everything in memory. Nothing is written
    # until every value has decrypted under the old key AND round-tripped under
    # the new one, so a single bad row aborts with the database untouched.
    updates: list[tuple[str, str, Any, str]] = []
    verified = 0
    try:
        with sqlite3.connect(str(database)) as conn:
            conn.row_factory = sqlite3.Row
            for table, columns in ENCRYPTED_COLUMNS.items():
                present = _table_columns(conn, table)
                if not present:
                    continue
                key_column = "id" if "id" in present else "rowid"
                for column in columns:
                    if column not in present:
                        continue
                    rows = conn.execute(
                        f"SELECT {key_column} AS k, {column} AS v FROM {table} "
                        f"WHERE {column} IS NOT NULL AND {column} <> ''"
                    ).fetchall()
                    for row in rows:
                        plaintext = _decrypt_with(old_fernet, str(row["v"]))
                        reencrypted = _encrypt_with(new_fernet, plaintext)
                        if _decrypt_with(new_fernet, reencrypted) != plaintext:
                            raise RuntimeError(
                                f"{table}.{column} did not round-trip under the new key")
                        verified += 1
                        updates.append((table, column, row["k"], reencrypted))
    except Exception as exc:  # noqa: BLE001
        # Nothing was written. The message must not carry a value.
        return MigrationResult(
            plan=plan, backup_path=str(backup_path),
            error=f"aborted before writing: {type(exc).__name__}: {exc}")

    # Phase two: one transaction.
    try:
        conn = sqlite3.connect(str(database))
        try:
            with conn:
                for table, column, key, value in updates:
                    key_column = "id" if "id" in _table_columns(conn, table) else "rowid"
                    conn.execute(
                        f"UPDATE {table} SET {column} = ? WHERE {key_column} = ?",
                        (value, key))
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        shutil.copyfile(backup_path, database)
        return MigrationResult(
            plan=plan, backup_path=str(backup_path),
            error=f"write failed and the database was restored from backup: {exc}")

    try:
        _write_env_file(Path(env_file), target_secret, target_jwt)
    except Exception as exc:  # noqa: BLE001
        shutil.copyfile(backup_path, database)
        return MigrationResult(
            plan=plan, backup_path=str(backup_path),
            error=f"could not write {env_file} — database restored, nothing changed: {exc}")

    log.info("secret migration: %d value(s) re-encrypted", len(updates))
    return MigrationResult(
        migrated=len(updates), verified=verified, backup_path=str(backup_path),
        env_file=str(env_file), ok=True, plan=plan,
    )
