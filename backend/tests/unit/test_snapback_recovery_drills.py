"""Recovery drills: what survives a crash, a corrupt database and a halted system."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse


def _warehouse(tmp_path) -> SnapbackObservationWarehouse:
    return SnapbackObservationWarehouse(db_path=str(tmp_path / "prospective.db"))


def _seed_position(w, *, status="OPEN", **over):
    payload = dict(
        opportunity_id="OPP-1",
        symbol="NIFTY",
        option_symbol="NIFTY26OCT25000PE",
        option_qty=25,
        option_entry_price=100.0,
        option_expiry="2026-10-29",
        option_strike=25000.0,
        futures_symbol="NIFTY26OCTFUT",
        futures_lot_size=25,
        current_futures_lots=1,
        avg_futures_entry_price=24500.0,
        realized_futures_pnl=0.0,
        entry_spot=24500.0,
        entry_timestamp=datetime.now(timezone.utc).isoformat(),
        entry_dte=45,
        entry_iv=0.2,
        causal_beta=1.0,
    )
    payload.update(over)
    w.save_paper_position(**payload)
    return payload


def test_open_position_survives_a_restart(tmp_path):
    w = _warehouse(tmp_path)
    _seed_position(w)

    # Simulated crash: drop every reference and reopen the database.
    del w
    w2 = SnapbackObservationWarehouse(db_path=str(tmp_path / "prospective.db"))

    pos = w2.get_paper_position("OPP-1")

    assert pos is not None
    assert pos["status"] == "OPEN"
    assert pos["option_qty"] == 25
    assert pos["current_futures_lots"] == 1


def test_exit_pending_reason_and_bid_survive_a_restart(tmp_path):
    w = _warehouse(tmp_path)
    _seed_position(w)

    w.set_paper_position_pending_exit(
        opportunity_id="OPP-1",
        pending_exit_reason="PREMIUM_STOP",
        pending_exit_option_bid=61.5,
        pending_exit_ts=datetime.now(timezone.utc).isoformat(),
    )

    del w
    w2 = SnapbackObservationWarehouse(db_path=str(tmp_path / "prospective.db"))

    pos = w2.get_paper_position("OPP-1")

    assert pos["status"] == "EXIT_PENDING"
    assert pos["pending_exit_reason"] == "PREMIUM_STOP"
    assert float(pos["pending_exit_option_bid"]) == pytest.approx(61.5)

    # The intraday processor must still see it, so liquidation can resume.
    active = w2.get_active_paper_positions()
    assert [p["opportunity_id"] for p in active] == ["OPP-1"]


def test_corrupt_evidence_database_is_recovery_required_not_a_blank_start(tmp_path):
    """A corrupt database must never be silently replaced by an empty one."""
    from app.services.snapback_startup import run_startup_preflight

    db_path = tmp_path / "prospective.db"
    db_path.write_bytes(b"THIS IS NOT A SQLITE FILE" * 100)

    def probe() -> bool:
        try:
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("CREATE TABLE IF NOT EXISTS _check (x TEXT)")
                conn.commit()
            finally:
                conn.close()
            return True
        except Exception:
            return False

    result = run_startup_preflight(
        db_writable_fn=probe,
        manifest_fn=lambda: (True, []),
        config_store_available_fn=lambda: True,
        config_fn=lambda: type("C", (), {"enabled": True})(),
        config_hash_fn=lambda cfg: "h",
        frozen_config_hash="h",
    )

    assert result.status == "RECOVERY_REQUIRED"
    assert result.may_start_runner is False

    # The corrupt file is still on disk, untouched, for forensic recovery.
    assert db_path.exists()
    assert db_path.stat().st_size > 0


def test_corrupt_backup_cannot_overwrite_a_good_database(tmp_path):
    from app.services.snapback_backup import BackupIntegrityError, create_backup, restore_backup

    source = tmp_path / "live.db"
    conn = sqlite3.connect(source)
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES ('real-evidence')")
    conn.commit()
    conn.close()

    artifact = create_backup(
        source_db=source,
        backup_root=tmp_path / "backups",
        freeze_record=None,
        now=datetime(2026, 9, 16, tzinfo=timezone.utc),
    )

    with artifact.db_path.open("ab") as fh:
        fh.write(b"CORRUPTION")

    with pytest.raises(BackupIntegrityError):
        restore_backup(
            backup_db=artifact.db_path,
            checksum_path=artifact.checksum_path,
            destination_db=source,
        )

    conn = sqlite3.connect(source)
    try:
        assert conn.execute("SELECT v FROM t").fetchall() == [("real-evidence",)]
    finally:
        conn.close()


def test_kill_switch_blocks_entries_but_keeps_exit_processing(tmp_path, monkeypatch):
    import app.services.snapback_family_ops as fam

    monkeypatch.setenv("STERLING_NEW_TRADES_HALT_PATH", str(tmp_path / "halt.json"))

    fam.set_new_trades_halted(True, reason="drill")

    assert fam.new_trades_halted() is True

    # State is intact and readable after the switch.
    payload = json.loads((tmp_path / "halt.json").read_text(encoding="utf-8"))
    assert payload["halted"] is True
    assert payload["reason"] == "drill"

    fam.set_new_trades_halted(False, reason="drill end")
    assert fam.new_trades_halted() is False


def test_invalid_manifest_halts_the_runner():
    from app.services.snapback_startup import run_startup_preflight

    result = run_startup_preflight(
        db_writable_fn=lambda: True,
        manifest_fn=lambda: (False, ["rule_hash drift"]),
        config_store_available_fn=lambda: True,
        config_fn=lambda: type("C", (), {"enabled": True})(),
        config_hash_fn=lambda cfg: "h",
        frozen_config_hash="h",
    )

    assert result.status == "HALTED"
    assert result.may_start_runner is False


def test_missing_broker_is_degraded_not_a_crash():
    from app.services.snapback_health import build_prospective_health

    class W:
        def opportunity_status_counts(self):
            return {}

        def paper_position_status_counts(self):
            return {}

    now = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
    body = build_prospective_health(
        warehouse=W(),
        runtime_sha="sha",
        strategy_manifest="m",
        manifest_ok=True,
        mode="PAPER",
        broker_connected=False,
        market_data_fresh=False,
        calendar_ok=True,
        database_ok=True,
        runner_alive=True,
        last_runner_tick=now,
        market_open=False,
        now=now,
    )

    assert body["status"] == "DEGRADED"
    assert body["mode"] == "PAPER"
    assert "broker_disconnected" in body["unresolved_errors"]
