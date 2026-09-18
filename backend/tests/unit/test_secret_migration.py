"""Re-keying credentials: all of them, atomically, and never in the output.

The failure this guards against is a half-migrated database: some rows on the
old key, some on the new, and nothing on the machine able to say which.
"""
from __future__ import annotations

import base64
import hashlib
import sqlite3
import stat

import pytest

from app.services.secret_migration import (
    ENCRYPTED_COLUMNS,
    generate_secret,
    migrate_secrets,
    plan_migration,
)

OLD_KEY = "sterling-dev-insecure-key"
SECRETS = {
    "api_secret_enc": "kite-api-secret-value",
    "access_token_enc": "kite-access-token-value",
}


def _fernet(secret):
    from cryptography.fernet import Fernet

    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def _encrypt(secret, plaintext):
    return "fernet:" + _fernet(secret).encrypt(plaintext.encode()).decode()


def _decrypt(secret, value):
    return _fernet(secret).decrypt(value[len("fernet:"):].encode()).decode()


@pytest.fixture()
def database(tmp_path):
    path = tmp_path / "sterling.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE kite_accounts (id TEXT PRIMARY KEY, "
                     "api_secret_enc TEXT, access_token_enc TEXT, refresh_token_enc TEXT)")
        conn.execute("CREATE TABLE truedata_credentials (id INTEGER PRIMARY KEY, "
                     "password_enc TEXT, session_token_enc TEXT)")
        for i in range(3):
            conn.execute(
                "INSERT INTO kite_accounts VALUES (?,?,?,'')",
                (f"KITE-{i}", _encrypt(OLD_KEY, f"secret-{i}"),
                 _encrypt(OLD_KEY, f"token-{i}")))
        conn.execute("INSERT INTO truedata_credentials VALUES (1,?,?)",
                     (_encrypt(OLD_KEY, "td-password"), _encrypt(OLD_KEY, "td-session")))
    return path


class TestThePlan:
    def test_it_counts_every_declared_column(self, database):
        plan = plan_migration(database)
        assert plan.counts["kite_accounts.api_secret_enc"] == 3
        assert plan.counts["kite_accounts.refresh_token_enc"] == 0
        assert plan.total == 8

    def test_an_undeclared_encrypted_column_is_reported(self, database):
        with sqlite3.connect(database) as conn:
            conn.execute("ALTER TABLE kite_accounts ADD COLUMN pin_enc TEXT")
        plan = plan_migration(database)
        assert "kite_accounts.pin_enc" in plan.unknown_columns

    def test_migration_refuses_while_a_column_is_undeclared(self, database, tmp_path):
        with sqlite3.connect(database) as conn:
            conn.execute("ALTER TABLE kite_accounts ADD COLUMN pin_enc TEXT")
        result = migrate_secrets(db_path=database, env_file=tmp_path / "env",
                                 old_secret=OLD_KEY)
        # Otherwise that column stays readable under the old key while the
        # report says the migration succeeded.
        assert result.ok is False
        assert "pin_enc" in result.error


class TestTheMigration:
    def test_every_value_moves_to_the_new_key(self, database, tmp_path):
        new_key = generate_secret()
        result = migrate_secrets(db_path=database, env_file=tmp_path / "prod.env",
                                 old_secret=OLD_KEY, new_secret=new_key)
        assert result.ok is True
        assert result.migrated == 8

        with sqlite3.connect(database) as conn:
            for row in conn.execute("SELECT id, api_secret_enc, access_token_enc "
                                    "FROM kite_accounts ORDER BY id"):
                index = row[0].split("-")[1]
                assert _decrypt(new_key, row[1]) == f"secret-{index}"
                assert _decrypt(new_key, row[2]) == f"token-{index}"
                with pytest.raises(Exception):
                    _decrypt(OLD_KEY, row[1])

    def test_the_env_file_is_owner_only(self, database, tmp_path):
        env_file = tmp_path / "prod.env"
        migrate_secrets(db_path=database, env_file=env_file, old_secret=OLD_KEY)
        mode = stat.S_IMODE(env_file.stat().st_mode)
        assert mode == 0o600, oct(mode)

    def test_the_two_secrets_are_different(self, database, tmp_path):
        env_file = tmp_path / "prod.env"
        migrate_secrets(db_path=database, env_file=env_file, old_secret=OLD_KEY)
        values = dict(
            line.split("=", 1) for line in env_file.read_text().splitlines()
            if "=" in line and not line.startswith("#")
        )
        assert values["STERLING_SECRET_KEY"] != values["STERLING_JWT_SECRET"]
        assert len(values["STERLING_SECRET_KEY"]) >= 32

    def test_reusing_one_secret_as_both_is_refused(self, database, tmp_path):
        same = generate_secret()
        result = migrate_secrets(db_path=database, env_file=tmp_path / "env",
                                 old_secret=OLD_KEY, new_secret=same,
                                 new_jwt_secret=same)
        assert result.ok is False
        assert "must differ" in result.error

    def test_a_backup_is_taken_before_anything_is_written(self, database, tmp_path):
        result = migrate_secrets(db_path=database, env_file=tmp_path / "env",
                                 old_secret=OLD_KEY, backup_dir=tmp_path / "bk")
        assert result.ok and result.backup_path
        from pathlib import Path

        assert Path(result.backup_path).exists()


class TestItIsAllOrNothing:
    def test_one_undecryptable_row_aborts_without_writing(self, database, tmp_path):
        with sqlite3.connect(database) as conn:
            conn.execute("UPDATE kite_accounts SET api_secret_enc = 'fernet:not-a-token' "
                         "WHERE id = 'KITE-1'")
            before = list(conn.execute("SELECT id, api_secret_enc FROM kite_accounts "
                                       "ORDER BY id"))

        result = migrate_secrets(db_path=database, env_file=tmp_path / "env",
                                 old_secret=OLD_KEY, backup_dir=tmp_path / "bk")
        assert result.ok is False
        assert "aborted before writing" in result.error

        with sqlite3.connect(database) as conn:
            after = list(conn.execute("SELECT id, api_secret_enc FROM kite_accounts "
                                      "ORDER BY id"))
        # Not one row moved: a partly-migrated database is the state nothing
        # can recover from.
        assert before == after

    def test_a_failed_env_write_restores_the_database(self, database, tmp_path):
        with sqlite3.connect(database) as conn:
            before = list(conn.execute("SELECT id, api_secret_enc FROM kite_accounts "
                                       "ORDER BY id"))

        # A directory where the file should be: the write cannot succeed.
        env_file = tmp_path / "blocked"
        env_file.mkdir()

        result = migrate_secrets(db_path=database, env_file=env_file,
                                 old_secret=OLD_KEY, backup_dir=tmp_path / "bk")
        assert result.ok is False

        with sqlite3.connect(database) as conn:
            after = list(conn.execute("SELECT id, api_secret_enc FROM kite_accounts "
                                      "ORDER BY id"))
        # Keys the operator never received must not be the keys the database is on.
        assert before == after


class TestNoSecretIsEverPrinted:
    def test_the_result_carries_no_plaintext_and_no_key(self, database, tmp_path):
        new_key = generate_secret()
        result = migrate_secrets(db_path=database, env_file=tmp_path / "env",
                                 old_secret=OLD_KEY, new_secret=new_key)
        rendered = repr(result.as_dict())
        for forbidden in (new_key, OLD_KEY, "secret-0", "token-0", "td-password"):
            assert forbidden not in rendered

    def test_an_abort_message_carries_no_plaintext(self, database, tmp_path):
        with sqlite3.connect(database) as conn:
            conn.execute("UPDATE kite_accounts SET api_secret_enc = 'fernet:broken'")
        result = migrate_secrets(db_path=database, env_file=tmp_path / "env",
                                 old_secret=OLD_KEY, backup_dir=tmp_path / "bk")
        assert "secret-0" not in result.error and OLD_KEY not in result.error


def test_the_declared_columns_match_the_live_schema_shape():
    """A new *_enc column anywhere must be declared before it can be migrated."""
    assert "kite_accounts" in ENCRYPTED_COLUMNS
    assert "access_token_enc" in ENCRYPTED_COLUMNS["kite_accounts"]
