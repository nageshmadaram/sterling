"""Honesty helpers for the Gamma Move engine replay study."""
from __future__ import annotations

from datetime import date

from app.engines.gamma_move import GammaMoveConfig, days_to_expiry, expiry_in_window
from study.gamma_move.step9_engine_replay import chain_oi_max_from_quotes, window_label


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


def test_chain_oi_max_uses_same_day_quotes_for_the_leg():
    quotes = [
        {"underlying": "RELIANCE", "option_type": "CE", "expiry": "2026-09-29", "oi": 100_000},
        {"underlying": "RELIANCE", "option_type": "CE", "expiry": "2026-09-29", "oi": 6_000_000},
        {"underlying": "RELIANCE", "option_type": "PE", "expiry": "2026-09-29", "oi": 9_000_000},
    ]
    assert chain_oi_max_from_quotes(
        quotes, underlying="RELIANCE", option_type="CE", expiry="2026-09-29") == 6_000_000
    assert chain_oi_max_from_quotes(
        quotes, underlying="RELIANCE", option_type="PE", expiry="2026-09-29") == 9_000_000
    assert chain_oi_max_from_quotes(
        quotes, underlying="TCS", option_type="CE", expiry="2026-09-29") is None
