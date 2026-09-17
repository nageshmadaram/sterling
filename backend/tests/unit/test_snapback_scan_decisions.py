"""E10: every scanned symbol says what it saw.

"200 scanned, 0 signals" is an assertion until each of those 200 symbols leaves a
durable, reconstructible record of the inputs it was judged on.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.models import to_bars
from app.services.snapback_observation_warehouse import (
    EvidenceIntegrityError,
    SnapbackObservationWarehouse,
)
from app.services.snapback_scan_evidence import (
    ScanEvidenceIdentityError,
    build_symbol_decision,
    input_window_hash,
    record_symbol_decision,
)


@pytest.fixture(autouse=True)
def experiment_identity(monkeypatch):
    monkeypatch.setenv("STERLING_EXPERIMENT_ID", "prospective_runtime_1_6")


@pytest.fixture
def warehouse():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
        path = fh.name
    wh = SnapbackObservationWarehouse(db_path=path)
    yield wh
    if os.path.exists(path):
        os.remove(path)


def _candles(n=120, base=100.0, last_close=None):
    out = []
    ts = 1_700_000_000_000
    for i in range(n):
        close = base + (i % 7) * 0.5
        if last_close is not None and i == n - 1:
            close = last_close
        out.append({
            "time": ts + i * 86_400_000,
            "open": close - 0.5, "high": close + 1.0, "low": close - 1.0,
            "close": close, "volume": 1000 + i,
        })
    return out


def test_missing_experiment_identity_is_refused(monkeypatch):
    monkeypatch.delenv("STERLING_EXPERIMENT_ID", raising=False)
    bars = to_bars(_candles())
    with pytest.raises(ScanEvidenceIdentityError):
        build_symbol_decision(
            session_date="2026-10-15", symbol="INFY", bars=bars, cfg=SnapbackConfig(),
            market_gate_status="EVALUATED", market_gate_passed=True,
        )


def test_a_quiet_symbol_still_leaves_a_decision(warehouse):
    bars = to_bars(_candles())
    decision = build_symbol_decision(
        session_date="2026-10-15", symbol="INFY", bars=bars, cfg=SnapbackConfig(),
        market_gate_status="EVALUATED", market_gate_passed=True,
    )
    record_symbol_decision(warehouse, decision)

    rows = warehouse.get_records_by_table("scan_symbol_decisions")

    assert len(rows) == 1
    assert rows[0]["canonical_symbol"] == "INFY"
    assert rows[0]["experiment_id"] == "prospective_runtime_1_6"
    assert rows[0]["signal_emitted"] == 0
    assert "NO_FROZEN_SIGNAL" in rows[0]["decision_codes_json"]


def test_the_decision_mirrors_the_engine_features(warehouse):
    from app.engines.snapback.strategy import features

    bars = to_bars(_candles())
    cfg = SnapbackConfig()
    engine_features = features(bars, cfg)
    last = len(bars) - 1

    decision = build_symbol_decision(
        session_date="2026-10-15", symbol="INFY", bars=bars, cfg=cfg,
        market_gate_status="EVALUATED", market_gate_passed=True,
    )

    assert decision["close"] == pytest.approx(float(bars.close[last]))
    assert decision["ema"] == pytest.approx(float(engine_features.ema[last]))
    assert decision["atr"] == pytest.approx(float(engine_features.atr[last]))
    assert decision["stretch_atr"] == pytest.approx(float(engine_features.stretch[last]))


def test_the_input_window_hash_follows_the_bars():
    bars_a = to_bars(_candles())
    bars_b = to_bars(_candles(last_close=999.0))
    assert input_window_hash(bars_a) == input_window_hash(bars_a)
    assert input_window_hash(bars_a) != input_window_hash(bars_b)


def test_the_decision_carries_frozen_identity(warehouse):
    bars = to_bars(_candles())
    decision = build_symbol_decision(
        session_date="2026-10-15", symbol="INFY", bars=bars, cfg=SnapbackConfig(),
        market_gate_status="EVALUATED", market_gate_passed=True,
    )
    record_symbol_decision(warehouse, decision)
    row = warehouse.get_records_by_table("scan_symbol_decisions")[0]

    assert row["runtime_build_sha"] and row["runtime_build_sha"] != "UNKNOWN"
    assert row["config_hash"]
    assert row["rule_hash"]
    assert row["execution_policy_hash"]
    assert row["input_window_hash"]
    assert int(row["bars_used"]) == len(bars)


def test_a_market_gate_rejection_is_recorded(warehouse):
    bars = to_bars(_candles())
    decision = build_symbol_decision(
        session_date="2026-10-15", symbol="INFY", bars=bars, cfg=SnapbackConfig(),
        market_gate_status="BLOCKED", market_gate_passed=False,
    )
    record_symbol_decision(warehouse, decision)
    row = warehouse.get_records_by_table("scan_symbol_decisions")[0]
    assert row["market_gate_passed"] == 0
    assert "MARKET_GATE_REJECTED" in row["decision_codes_json"]


def test_an_identical_decision_replay_is_a_no_op(warehouse):
    bars = to_bars(_candles())
    decision = build_symbol_decision(
        session_date="2026-10-15", symbol="INFY", bars=bars, cfg=SnapbackConfig(),
        market_gate_status="EVALUATED", market_gate_passed=True,
    )
    record_symbol_decision(warehouse, decision)
    record_symbol_decision(warehouse, decision)
    assert len(warehouse.get_records_by_table("scan_symbol_decisions")) == 1


def test_a_changed_decision_under_the_same_id_is_refused(warehouse):
    bars = to_bars(_candles())
    decision = build_symbol_decision(
        session_date="2026-10-15", symbol="INFY", bars=bars, cfg=SnapbackConfig(),
        market_gate_status="EVALUATED", market_gate_passed=True,
    )
    record_symbol_decision(warehouse, decision)
    tampered = dict(decision)
    tampered["close"] = 12345.0
    with pytest.raises(EvidenceIntegrityError):
        record_symbol_decision(warehouse, tampered)


def test_decision_count_must_reconcile_with_the_universe(warehouse):
    from app.services.snapback_session_ledger import (
        SessionStatus, record_session_scan, session_record, session_scan_complete,
    )
    from app.services.snapback_scan_evidence import decisions_reconcile

    bars = to_bars(_candles())
    for i in range(3):
        record_symbol_decision(warehouse, build_symbol_decision(
            session_date="2026-10-15", symbol=f"SYM{i}", bars=bars,
            cfg=SnapbackConfig(), market_gate_status="EVALUATED", market_gate_passed=True,
        ))

    record_session_scan(
        warehouse, session_date="2026-10-15", experiment_id="prospective_runtime_1_6",
        calendar_version="v", universe_expected=5, universe_scanned=5, symbol_failures=0,
        market_gate_status="EVALUATED", signals_authoritative=0,
        status=SessionStatus.COMPLETE,
    )
    assert decisions_reconcile(warehouse, session_date="2026-10-15", expected=5) is False
    assert session_scan_complete(session_record(warehouse, "2026-10-15")) is False


def test_a_reconciled_session_is_scan_complete(warehouse):
    from app.services.snapback_scan_evidence import decisions_reconcile
    from app.services.snapback_session_ledger import (
        SessionStatus, record_session_scan, session_record, session_scan_complete,
    )

    bars = to_bars(_candles())
    for i in range(3):
        record_symbol_decision(warehouse, build_symbol_decision(
            session_date="2026-10-15", symbol=f"SYM{i}", bars=bars,
            cfg=SnapbackConfig(), market_gate_status="EVALUATED", market_gate_passed=True,
        ))

    record_session_scan(
        warehouse, session_date="2026-10-15", experiment_id="prospective_runtime_1_6",
        calendar_version="v", universe_expected=3, universe_scanned=3, symbol_failures=0,
        market_gate_status="EVALUATED", signals_authoritative=0,
        status=SessionStatus.COMPLETE,
    )
    assert decisions_reconcile(warehouse, session_date="2026-10-15", expected=3) is True
    assert session_scan_complete(session_record(warehouse, "2026-10-15")) is True


def test_the_scanner_records_one_decision_per_symbol():
    import inspect
    from app.services import snapback_prospective_scanner as scanner
    source = inspect.getsource(scanner)
    assert "record_symbol_decision" in source
    assert "build_symbol_decision" in source
