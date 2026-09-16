"""Legacy schema repair: a pre-existing table missing a newer column must not
take the whole SQLite store offline, because `db.is_available()` gates the
Snapback config store, and an unavailable config store disables the engine.
"""

from __future__ import annotations

import sqlite3

import pytest


def _legacy_calibration_trades(path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE calibration_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pnl_pct REAL NOT NULL,
            regime TEXT,
            closed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("INSERT INTO calibration_trades (pnl_pct, regime) VALUES (1.5, 'default')")
    conn.commit()
    conn.close()


def test_legacy_schema_is_migrated_not_fatal(tmp_path, monkeypatch):
    db_path = tmp_path / "sterling_paper.db"
    _legacy_calibration_trades(db_path)

    from app.services import db as db_mod

    monkeypatch.setattr(db_mod, "_DB_PATH", str(db_path))

    assert db_mod.init() is True
    assert db_mod.is_available() is True

    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(calibration_trades)")}
        rows = conn.execute("SELECT pnl_pct FROM calibration_trades").fetchall()
    finally:
        conn.close()

    # Column added, existing evidence preserved.
    assert "source_trade_id" in cols
    assert rows == [(1.5,)]


def test_init_is_idempotent_across_restarts(tmp_path, monkeypatch):
    db_path = tmp_path / "sterling_paper.db"
    _legacy_calibration_trades(db_path)

    from app.services import db as db_mod

    monkeypatch.setattr(db_mod, "_DB_PATH", str(db_path))

    assert db_mod.init() is True
    assert db_mod.init() is True
    assert db_mod.is_available() is True


def test_stored_snapback_config_survives_a_legacy_schema_store(tmp_path, monkeypatch):
    """The real defect: a failed init() made every config read return 'defaults OFF',
    so the frozen runner stayed alive while the strategy was disabled."""
    db_path = tmp_path / "sterling_paper.db"
    _legacy_calibration_trades(db_path)

    from app.services import db as db_mod

    monkeypatch.setattr(db_mod, "_DB_PATH", str(db_path))
    assert db_mod.init() is True

    db_mod.set_config("snapback_config", '{"enabled": true}')

    from app.services.snapback import get_config

    cfg = get_config("default")

    assert cfg.enabled is True


def test_unavailable_store_still_fails_closed(tmp_path, monkeypatch):
    """When the store really is unreachable, the engine must still be OFF."""
    from app.services import db as db_mod
    from app.services.snapback import get_config

    monkeypatch.setattr(db_mod, "_available", False)

    assert get_config("default").enabled is False
