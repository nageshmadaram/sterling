"""Honesty helpers for the Gamma Move engine replay study."""
from __future__ import annotations

from datetime import date

from app.engines.gamma_move import GammaMoveConfig, days_to_expiry, expiry_in_window
from study.gamma_move.step9_engine_replay import window_label


def test_dte_34_is_out_of_the_shipped_window():
    cfg = GammaMoveConfig()
    today = date(2026, 8, 26)
    assert days_to_expiry("2026-09-29", today) == 34
    assert expiry_in_window("2026-09-29", today, cfg) is False
    assert window_label("2026-09-29", today, cfg) == "out"


def test_dte_9_is_in_window():
    cfg = GammaMoveConfig()
    today = date(2026, 9, 20)
    assert days_to_expiry("2026-09-29", today) == 9
    assert window_label("2026-09-29", today, cfg) == "in"


def test_labelled_replay_disables_the_wall_gate_without_asof_chain_oi():
    """candidates.json is a top-3 snapshot. Feeding it as chain_oi_max would
    filter every historical bar with future, partial-chain OI."""
    from study.gamma_move.step9_engine_replay import labelled_replay_config
    shipped = GammaMoveConfig()
    cfg, notes = labelled_replay_config(shipped, dtes=[34, 40, 103])
    assert cfg.require_chain_max_oi is False
    assert any("wall_gate=skipped" in n for n in notes)
    assert any("window=out" in n for n in notes)
