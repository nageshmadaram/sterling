"""
Tests for live execution hardening:
 1. Kill switch: state, toggle, persistence in process.
 2. Daily-loss circuit breaker on synthetic positions.
 3. Idempotency cache: hit / miss / TTL eviction.
 4. Retry queue: enqueue / mark_attempt / poison / remove.
 5. Composite gate `assert_safe_to_trade`.
 6. New /trading/* safety endpoints (kill-switch, daily-loss, retry-queue).
"""
from dataclasses import dataclass
from typing import Optional
import time

import pytest
from fastapi.testclient import TestClient

from app.services import live_safety
from app.services.live_safety import (
    DailyLossConfig,
    SafetyDecision,
    assert_safe_to_trade,
    check_idempotency,
    configure_daily_loss,
    daily_loss_state,
    daily_realized_pnl,
    enqueue_retry,
    kill_switch_state,
    list_retries,
    make_idempotency_key,
    mark_attempt,
    record_idempotency,
    remove_retry,
    reset_all_for_tests,
    set_kill_switch,
)


@pytest.fixture(autouse=True)
def _init_test_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_live_safety.db")
    monkeypatch.setenv("STERLING_DB_PATH", db_file)
    from app.services import db
    monkeypatch.setattr(db, "_DB_PATH", db_file)
    monkeypatch.setattr(db, "_available", True)
    db.init()
    yield


@dataclass
class _FakePos:
    """Minimal stand-in for PaperPosition — only the fields live_safety reads."""
    exit_timestamp_ms: Optional[int]
    realized_pnl_usd:  Optional[float]


def _now() -> int:
    return int(time.time() * 1000)


# ─── 1. Kill switch ────────────────────────────────────────────────────────

class TestKillSwitch:

    def setup_method(self) -> None:
        reset_all_for_tests()

    def test_default_state_is_disabled(self) -> None:
        assert kill_switch_state()["enabled"] is False

    def test_enable_then_state_reflects(self) -> None:
        set_kill_switch(True, reason="ops halt")
        s = kill_switch_state()
        assert s["enabled"] is True
        assert s["reason"] == "ops halt"
        assert s["set_ts_ms"] > 0

    def test_disable_clears_reason(self) -> None:
        set_kill_switch(True, reason="ops halt")
        set_kill_switch(False)
        assert kill_switch_state()["enabled"] is False
        assert kill_switch_state()["reason"] == ""


# ─── 2. Daily loss breaker ─────────────────────────────────────────────────

class TestDailyLoss:

    def setup_method(self) -> None:
        reset_all_for_tests()

    def test_empty_positions_yields_zero(self) -> None:
        assert daily_realized_pnl([]) == 0.0
        s = daily_loss_state([])
        assert s["pnl_usd"] == 0.0
        assert s["level"] == "clear"

    def test_only_today_counted(self) -> None:
        today = _now()
        yesterday = today - 25 * 60 * 60 * 1000
        pos = [
            _FakePos(exit_timestamp_ms=today, realized_pnl_usd=-100.0),
            _FakePos(exit_timestamp_ms=yesterday, realized_pnl_usd=-9999.0),
        ]
        assert daily_realized_pnl(pos) == -100.0

    def test_warning_level(self) -> None:
        configure_daily_loss(DailyLossConfig(soft_warn_usd=-200.0, hard_halt_usd=-500.0))
        pos = [_FakePos(exit_timestamp_ms=_now(), realized_pnl_usd=-250.0)]
        s = daily_loss_state(pos)
        assert s["level"] == "warning"

    def test_halt_level(self) -> None:
        configure_daily_loss(DailyLossConfig(soft_warn_usd=-200.0, hard_halt_usd=-500.0))
        pos = [_FakePos(exit_timestamp_ms=_now(), realized_pnl_usd=-600.0)]
        s = daily_loss_state(pos)
        assert s["level"] == "halt"

    def test_unrealised_positions_ignored(self) -> None:
        """exit_timestamp_ms=None must not contribute."""
        pos = [_FakePos(exit_timestamp_ms=None, realized_pnl_usd=-9999.0)]
        assert daily_realized_pnl(pos) == 0.0


# ─── 3. Idempotency ────────────────────────────────────────────────────────

class TestIdempotency:

    def setup_method(self) -> None:
        reset_all_for_tests()

    def test_make_key_is_deterministic(self) -> None:
        a = make_idempotency_key("NIFTY", "long", 1.0)
        b = make_idempotency_key("NIFTY", "long", 1.0)
        assert a == b

    def test_make_key_changes_with_inputs(self) -> None:
        a = make_idempotency_key("NIFTY", "long", 1.0)
        b = make_idempotency_key("NIFTY", "short", 1.0)
        assert a != b

    def test_check_returns_none_on_miss(self) -> None:
        assert check_idempotency("never-seen") is None

    def test_record_then_check_returns_order_id(self) -> None:
        record_idempotency("k1", "ORD-123")
        assert check_idempotency("k1") == "ORD-123"

    def test_empty_key_is_noop(self) -> None:
        record_idempotency("", "ORD-X")
        assert check_idempotency("") is None


# ─── 4. Retry queue ────────────────────────────────────────────────────────

class TestRetryQueue:

    def setup_method(self) -> None:
        reset_all_for_tests()

    def test_enqueue_adds_item(self) -> None:
        item = enqueue_retry({"underlying": "NIFTY", "size": 1.0}, error="timeout")
        assert item.id
        assert item.attempt == 0
        assert item.poison is False
        assert list_retries() == [item]

    def test_mark_attempt_increments(self) -> None:
        item = enqueue_retry({"u": "NIFTY"}, error="fail")
        m1 = mark_attempt(item.id, error="still fail")
        assert m1.attempt == 1
        assert m1.poison is False

    def test_max_attempts_marks_poison(self) -> None:
        item = enqueue_retry({"u": "NIFTY"}, error="fail", max_attempts=2)
        mark_attempt(item.id, error="still fail")
        m2 = mark_attempt(item.id, error="still fail")
        assert m2.attempt == 2
        assert m2.poison is True

    def test_remove(self) -> None:
        item = enqueue_retry({"u": "NIFTY"}, error="fail")
        assert remove_retry(item.id) is True
        assert remove_retry(item.id) is False

    def test_list_excludes_poison_when_filtered(self) -> None:
        item = enqueue_retry({"u": "NIFTY"}, error="fail", max_attempts=1)
        mark_attempt(item.id, error="still fail")
        assert list_retries(include_poison=True) == [item]
        assert list_retries(include_poison=False) == []


# ─── 5. Composite gate ─────────────────────────────────────────────────────

class TestAssertSafeToTrade:

    def setup_method(self) -> None:
        reset_all_for_tests()

    def test_clear_state_allows(self) -> None:
        d = assert_safe_to_trade(positions=[])
        assert d.allowed is True
        assert d.code == ""

    def test_kill_switch_blocks(self) -> None:
        set_kill_switch(True, reason="emergency")
        d = assert_safe_to_trade(positions=[])
        assert d.allowed is False
        assert d.code == "kill_switch"
        assert "emergency" in d.reason

    def test_daily_loss_halt_blocks(self) -> None:
        configure_daily_loss(DailyLossConfig(soft_warn_usd=-100.0, hard_halt_usd=-200.0))
        pos = [_FakePos(exit_timestamp_ms=_now(), realized_pnl_usd=-300.0)]
        d = assert_safe_to_trade(positions=pos)
        assert d.allowed is False
        assert d.code == "daily_loss_halt"

    def test_daily_loss_warning_does_not_block(self) -> None:
        configure_daily_loss(DailyLossConfig(soft_warn_usd=-100.0, hard_halt_usd=-200.0))
        pos = [_FakePos(exit_timestamp_ms=_now(), realized_pnl_usd=-150.0)]
        d = assert_safe_to_trade(positions=pos)
        assert d.allowed is True

    def test_idempotency_hit_blocks_with_prior_id(self) -> None:
        record_idempotency("k-dup", "ORD-PRIOR")
        d = assert_safe_to_trade(positions=[], idempotency_key="k-dup")
        assert d.allowed is False
        assert d.code == "duplicate_order"
        assert "ORD-PRIOR" in d.reason

    def test_kill_switch_takes_precedence_over_daily_loss(self) -> None:
        set_kill_switch(True, reason="halt")
        configure_daily_loss(DailyLossConfig(soft_warn_usd=-100.0, hard_halt_usd=-200.0))
        pos = [_FakePos(exit_timestamp_ms=_now(), realized_pnl_usd=-300.0)]
        d = assert_safe_to_trade(positions=pos)
        assert d.code == "kill_switch"

    def test_daily_loss_skipped_when_check_disabled(self) -> None:
        # Kite (INR) order paths pass check_daily_loss=False — the USD breaker is
        # legacy-only, so a position deep past the halt threshold must NOT block.
        configure_daily_loss(DailyLossConfig(soft_warn_usd=-100.0, hard_halt_usd=-200.0))
        pos = [_FakePos(exit_timestamp_ms=_now(), realized_pnl_usd=-300.0)]
        d = assert_safe_to_trade(positions=pos, check_daily_loss=False)
        assert d.allowed is True
        assert d.code == ""

    def test_kill_switch_still_blocks_when_daily_loss_disabled(self) -> None:
        # Disabling the daily-loss check must NOT weaken the kill switch.
        set_kill_switch(True, reason="halt")
        d = assert_safe_to_trade(positions=[], check_daily_loss=False)
        assert d.allowed is False
        assert d.code == "kill_switch"

    def test_idempotency_still_blocks_when_daily_loss_disabled(self) -> None:
        # Idempotency dedupe is currency-agnostic and must survive the flag.
        record_idempotency("k-dup-kite", "ORD-KITE")
        d = assert_safe_to_trade(positions=[], idempotency_key="k-dup-kite",
                                 check_daily_loss=False)
        assert d.allowed is False
        assert d.code == "duplicate_order"
        assert "ORD-KITE" in d.reason


# ─── 6. New /trading/* safety endpoints ────────────────────────────────────

class TestSafetyEndpoints:

    def setup_method(self) -> None:
        reset_all_for_tests()

    @pytest.fixture
    def client(self):
        from main import create_app
        app = create_app()
        return TestClient(app)
