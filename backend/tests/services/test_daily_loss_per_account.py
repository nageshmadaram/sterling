"""Per-account daily-loss thresholds.

The breaker used to hold one process-wide pair of numbers that nothing in the
app ever set, so every account halted at the shipped -1500 whether that suited
its size or not. These are the rules that make it per-account without letting a
bad stored row become a trading config.
"""
from __future__ import annotations

import json

import pytest

from app.services import db, live_safety


@pytest.fixture(autouse=True)
def _init_test_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_dl.db")
    monkeypatch.setenv("STERLING_DB_PATH", db_file)
    monkeypatch.setattr(db, "_DB_PATH", db_file)
    monkeypatch.setattr(db, "_available", True)
    db.init()
    yield


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch):
    """A config store of its own, so one test cannot set another's thresholds."""
    store: dict[str, str] = {}
    monkeypatch.setattr(db, "get_config", lambda key, default="": store.get(key, default))
    monkeypatch.setattr(db, "set_config", lambda key, value: store.__setitem__(key, value))
    live_safety.configure_daily_loss(live_safety.DailyLossConfig())
    yield store
    live_safety.configure_daily_loss(live_safety.DailyLossConfig())


def test_an_account_without_thresholds_gets_the_default():
    assert live_safety.daily_loss_config("u1") == live_safety.daily_loss_config()
    assert live_safety.has_daily_loss_override("u1") is False


def test_thresholds_are_stored_against_one_account_only():
    live_safety.configure_daily_loss(
        live_safety.DailyLossConfig(soft_warn_inr=-8_000.0, hard_halt_inr=-12_000.0), uid="u1")
    assert live_safety.daily_loss_config("u1").hard_halt_inr == -12_000.0
    # The neighbour is untouched, which is the whole point of the change.
    assert live_safety.daily_loss_config("u2").hard_halt_inr == -1_500.0
    assert live_safety.has_daily_loss_override("u1") is True
    assert live_safety.has_daily_loss_override("u2") is False


def test_clearing_an_override_returns_the_account_to_the_default():
    live_safety.configure_daily_loss(
        live_safety.DailyLossConfig(soft_warn_inr=-8_000.0, hard_halt_inr=-12_000.0), uid="u1")
    live_safety.clear_daily_loss("u1")
    assert live_safety.daily_loss_config("u1").hard_halt_inr == -1_500.0
    assert live_safety.has_daily_loss_override("u1") is False


def test_configuring_without_a_uid_still_moves_the_shared_default():
    live_safety.configure_daily_loss(
        live_safety.DailyLossConfig(soft_warn_inr=-4_000.0, hard_halt_inr=-5_000.0))
    assert live_safety.daily_loss_config("anyone").hard_halt_inr == -5_000.0


@pytest.mark.parametrize("bad", [
    {"enabled": True, "soft_warn_inr": 1_000.0, "hard_halt_inr": -1_500.0},   # positive warn
    {"enabled": True, "soft_warn_inr": -2_000.0, "hard_halt_inr": -1_500.0},  # warn below halt
    {"enabled": True, "soft_warn_inr": -1_000.0},                             # truncated row
])
def test_an_unusable_stored_row_falls_back_rather_than_being_repaired(_isolated_config, bad):
    """A row that will not validate must never become a trading config.

    Falling back to the default is the tighter direction in the shipped case,
    and tighter is the safe way to fail: it halts earlier than intended rather
    than later.
    """
    _isolated_config["daily_loss_cfg_u1"] = json.dumps(bad)
    assert live_safety.daily_loss_config("u1") == live_safety.DailyLossConfig()


def test_the_breaker_compares_against_the_account_s_own_threshold(monkeypatch):
    """A loss that halts one account must not halt another with more room."""
    from app.services.kite_engine import state
    monkeypatch.setattr(state, "daily_realized_pnl_strict", lambda uid, **kw: -3_000.0)

    live_safety.configure_daily_loss(
        live_safety.DailyLossConfig(soft_warn_inr=-8_000.0, hard_halt_inr=-12_000.0), uid="roomy")

    assert live_safety.daily_loss_state(uid="roomy")["level"] == "clear"
    assert live_safety.daily_loss_state(uid="default")["level"] == "halt"


def test_a_tightened_threshold_binds_without_a_restart(monkeypatch):
    """Read per call, not cached: the point of a threshold is the next order."""
    from app.services.kite_engine import state
    monkeypatch.setattr(state, "daily_realized_pnl_strict", lambda uid, **kw: -3_000.0)

    live_safety.configure_daily_loss(
        live_safety.DailyLossConfig(soft_warn_inr=-8_000.0, hard_halt_inr=-12_000.0), uid="u1")
    assert live_safety.assert_safe_to_trade([], None, uid="u1").allowed is True

    live_safety.configure_daily_loss(
        live_safety.DailyLossConfig(soft_warn_inr=-1_000.0, hard_halt_inr=-2_000.0), uid="u1")
    decision = live_safety.assert_safe_to_trade([], None, uid="u1")
    assert decision.allowed is False
    assert decision.code == "daily_loss_halt"


def test_disabling_the_breaker_for_one_account_leaves_the_others_armed(monkeypatch):
    from app.services.kite_engine import state
    monkeypatch.setattr(state, "daily_realized_pnl_strict", lambda uid, **kw: -50_000.0)

    live_safety.configure_daily_loss(
        live_safety.DailyLossConfig(enabled=False, soft_warn_inr=-1_000.0, hard_halt_inr=-1_500.0),
        uid="off")
    assert live_safety.daily_loss_state(uid="off")["level"] == "clear"
    assert live_safety.daily_loss_state(uid="on")["level"] == "halt"
