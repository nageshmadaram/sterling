"""Acceptance test suite for TrueData Historical Corpus Replay & Observational Baseline.

Verifies:
1. Versioned Historical Corpus Ingestion from data/adaptive_edge/replay/
2. Raw & Canonical Data Hashing Integrity
3. TrueData Raw Format to CanonicalMarketEvent Transformation
4. Multi-Session Observational Baseline Telemetry Extraction (no parameter fitting)
5. Replay Determinism across all real Historical Corpus Sessions
"""
from __future__ import annotations

from pathlib import Path
import pytest

from app.engines.adaptive_edge.e2e import ReplayContext
from app.engines.adaptive_edge.historical_corpus import (
    CorpusSession,
    ObservationalTelemetry,
    evaluate_corpus_session,
    list_corpus_sessions,
    load_corpus_session,
)
from app.engines.adaptive_edge.strategy_pipeline import StrategyConfig

CORPUS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "adaptive_edge" / "replay"


def test_corpus_sessions_discovered_and_loaded():
    """Verify all 5 fixed, versioned corpus sessions load cleanly."""
    assert CORPUS_DIR.exists(), f"Corpus directory {CORPUS_DIR} must exist"
    sessions = list_corpus_sessions(CORPUS_DIR)
    assert len(sessions) == 5, f"Expected 5 historical sessions, found {len(sessions)}"

    regimes = {s.metadata.regime_label for s in sessions}
    expected_regimes = {
        "bullish_trend",
        "bearish_trend",
        "range_bound",
        "volatile_reversal",
        "expiry_session",
    }
    assert regimes == expected_regimes


def test_corpus_raw_and_canonical_data_hashing():
    """Verify raw and canonical data hashes are deterministic and non-empty."""
    sessions = list_corpus_sessions(CORPUS_DIR)
    for s in sessions:
        assert len(s.metadata.raw_data_hash) == 64
        assert len(s.metadata.canonical_data_hash) == 64
        assert len(s.raw_bars) == s.metadata.bar_count
        assert len(s.canonical_events) == s.metadata.bar_count

        # Check canonical event fields
        for ev in s.canonical_events:
            assert ev.event_type == "bar"
            assert ev.source == "truedata"
            assert ev.source_version == "2.6"
            assert "open" in ev.payload
            assert "high" in ev.payload
            assert "low" in ev.payload
            assert "close" in ev.payload


def test_observational_baseline_telemetry_across_all_sessions():
    """Establish the observational telemetry baseline across all 5 corpus sessions."""
    sessions = list_corpus_sessions(CORPUS_DIR)
    telemetries: list[ObservationalTelemetry] = []

    config = StrategyConfig(
        symbol="NIFTY-I",
        authorized_risk=5000.0,
        execution_cost=20.0,
        min_net_value=10.0,
        option_moneyness="ATM",
    )

    for s in sessions:
        tel = evaluate_corpus_session(s, config=config)
        telemetries.append(tel)

        assert tel.session_date == s.metadata.session_date
        assert tel.regime_label == s.metadata.regime_label
        assert tel.bar_count == s.metadata.bar_count
        assert tel.signals_detected >= 1
        assert len(tel.trace_hash) == 64
        assert tel.mae_points >= 0.0
        assert tel.mfe_points >= 0.0

        if tel.traded:
            assert tel.selected_instrument is not None
            assert tel.authorized_quantity > 0
            assert tel.authorized_quantity % 25 == 0  # Lot sizing constraint
            assert tel.exit_reason in (
                "STOP_LOSS_TRIGGERED",
                "TRAILING_STOP_TRIGGERED",
                "PROFIT_LOCK_TRIGGERED",
                "PROFIT_TARGET_REACHED",
                "END_OF_SEQUENCE",
                "SESSION_CUTOFF",
            )

    # Validate observational regime distribution
    traded_count = sum(1 for t in telemetries if t.traded)
    assert traded_count > 0, "At least one historical session should produce a governed trade"


@pytest.mark.parametrize("session_index", [0, 1, 2, 3, 4])
def test_corpus_session_replay_determinism(session_index: int):
    """Verify that repeated historical replay of any corpus session yields identical telemetry and trace hash."""
    sessions = list_corpus_sessions(CORPUS_DIR)
    session = sessions[session_index]

    replay_ctx = ReplayContext(
        decision_time=session.metadata.session_start,
        event_time=session.metadata.session_start,
        deterministic_id_namespace=f"corpus-replay-{session.metadata.session_date}",
        sequence_seed=555,
    )
    config = StrategyConfig(
        symbol=session.metadata.instrument,
        authorized_risk=5000.0,
        execution_cost=20.0,
        min_net_value=10.0,
    )

    run_1 = evaluate_corpus_session(session, config=config, replay_context=replay_ctx)
    run_2 = evaluate_corpus_session(session, config=config, replay_context=replay_ctx)

    assert run_1.to_dict() == run_2.to_dict()
    assert run_1.trace_hash == run_2.trace_hash
    assert run_1.net_pnl == run_2.net_pnl
    assert run_1.mae_points == run_2.mae_points
    assert run_1.mfe_points == run_2.mfe_points
