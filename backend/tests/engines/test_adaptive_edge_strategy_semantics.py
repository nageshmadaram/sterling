"""Strategy Semantics E2E Acceptance Test Suite (Stages A through K).

Verifies that the strategy pipeline deterministically transforms real market
sequences into governed trade lifecycles across all 11 stages:
[A] Market ingestion & validation
[B] Causal FeatureSnapshot (Volume Profile, POC, CVD, 15m IB)
[C] Directional Hypothesis
[D] Adaptive Horizon Selection (MICRO / SCALP / EXTENDED_SCALP / INTRADAY)
[E] Edge Assessment
[F] F-004 Economic Viability
[G] Risk Authorization & Sizing
[H] Option Instrument Selection
[I] CanonicalOrderIntent construction
[J] Dynamic Protection Envelope
[K] Lifecycle Termination, PnL & Canonical Replay Determinism
"""
from __future__ import annotations

import pytest

from app.engines.adaptive_edge.e2e import ReplayContext
from app.engines.adaptive_edge.event_boundary import CanonicalMarketEvent
from app.engines.adaptive_edge.feature_engine import FeatureStatus
from app.engines.adaptive_edge.opportunity_mode import OpportunityMode
from app.engines.adaptive_edge.strategy_pipeline import (
    StrategyConfig,
    build_causal_feature_snapshots,
    run_strategy_semantics_pipeline,
    select_option_contract,
)


def _make_sample_session_bars(n_bars: int = 30, base_price: float = 24500.0, trend: str = "bullish") -> list[CanonicalMarketEvent]:
    """Generate a realistic 1-minute OHLCV sequence for NIFTY starting at 09:15 IST."""
    bars: list[CanonicalMarketEvent] = []
    curr_price = base_price

    for i in range(n_bars):
        # 09:15 IST = 03:45 UTC
        # Minute increments
        total_mins = 45 + i
        hour = 3 + total_mins // 60
        minute = total_mins % 60
        ts = f"2026-08-17T{hour:02d}:{minute:02d}:00+00:00"

        if trend == "bullish":
            delta = 5.0 if i < 15 else 8.0
        elif trend == "bearish":
            delta = -5.0 if i < 15 else -8.0
        else:
            delta = 2.0 if i % 2 == 0 else -2.0

        open_px = curr_price
        curr_price += delta
        high_px = max(open_px, curr_price) + 2.0
        low_px = min(open_px, curr_price) - 2.0
        close_px = curr_price
        vol = 1500.0 + (i * 50)
        oi = 100000.0 + (i * 200)

        bars.append(
            CanonicalMarketEvent(
                record_id=f"BAR-{i:03d}",
                event_type="bar",
                instrument_id="NIFTY-I",
                event_time=ts,
                available_at=ts,
                source="truedata",
                source_version="2.6",
                sequence=i,
                payload={
                    "open": open_px,
                    "high": high_px,
                    "low": low_px,
                    "close": close_px,
                    "volume": vol,
                    "oi": oi,
                },
            )
        )
    return bars


def _make_sample_ticks(n_ticks: int = 60, base_price: float = 24500.0) -> list[CanonicalMarketEvent]:
    """Generate realistic tick sequence with bid/ask order flow."""
    ticks: list[CanonicalMarketEvent] = []
    for i in range(n_ticks):
        total_mins = 45 + (i // 2)
        hour = 3 + total_mins // 60
        minute = total_mins % 60
        second = (i % 2) * 30
        ts = f"2026-08-17T{hour:02d}:{minute:02d}:{second:02d}+00:00"
        ltp = base_price + (i * 1.5)
        ticks.append(
            CanonicalMarketEvent(
                record_id=f"TICK-{i:03d}",
                event_type="tick",
                instrument_id="NIFTY-I",
                event_time=ts,
                available_at=ts,
                source="truedata",
                source_version="2.6",
                sequence=i,
                payload={
                    "ltp": ltp,
                    "volume": 50.0,
                    "oi": 100000.0,
                    "bid": ltp - 0.25,
                    "ask": ltp + 0.25,
                    "bidqty": 200.0,
                    "askqty": 100.0,  # Positive delta pressure
                },
            )
        )
    return ticks


# ==============================================================================
# A–B: Causal Market Representation & Feature Construction Invariants
# ==============================================================================

def test_ab_causal_feature_snapshots_no_lookahead():
    """Verify that every FeatureSnapshot strictly respects available_at <= decision_time."""
    bars = _make_sample_session_bars(20)
    ticks = _make_sample_ticks(40)

    snapshots = build_causal_feature_snapshots(bars, ticks)
    assert len(snapshots) == 20

    for snap, bar in zip(snapshots, bars):
        # 1. No lookahead: all features available_at <= decision_time
        snap.assert_causal(snap.decision_time)
        assert snap.decision_time == bar.available_at
        # 2. Every feature has explicit provenance
        for feat_name, prov in snap.provenance.items():
            assert len(prov.source_event_ids) > 0
            assert bar.record_id in prov.source_event_ids


def test_ab_explicit_missingness_preserved():
    """Verify that incomplete structural features remain FeatureStatus.MISSING and not zero."""
    # First 10 bars: Initial Balance is incomplete (< 15 mins)
    bars = _make_sample_session_bars(10)
    snapshots = build_causal_feature_snapshots(bars)

    for snap in snapshots:
        assert snap.statuses["ib_high"] == FeatureStatus.MISSING
        assert snap.values["ib_high"] is None
        assert snap.statuses["ib_low"] == FeatureStatus.MISSING
        assert snap.values["ib_low"] is None
        assert snap.values["ib_complete"] == 0.0


def test_ab_incremental_consistency():
    """Verify that incremental feature snapshot matches full batch evaluation."""
    bars = _make_sample_session_bars(15)
    ticks = _make_sample_ticks(30)

    batch_snaps = build_causal_feature_snapshots(bars, ticks)
    incremental_snaps = [
        build_causal_feature_snapshots(bars[:i + 1], ticks)[-1]
        for i in range(len(bars))
    ]

    for batch, inc in zip(batch_snaps, incremental_snaps):
        assert batch.snapshot_id == inc.snapshot_id
        assert batch.values["close"] == inc.values["close"]
        assert batch.values["vwap"] == inc.values["vwap"]
        assert batch.values["poc"] == inc.values["poc"]


# ==============================================================================
# C–F: Market Decision, Adaptive Horizon, Edge & Economics
# ==============================================================================

def test_cf_bullish_market_determines_adaptive_horizon_and_valid_edge():
    """Verify bullish breakout produces BULLISH hypothesis, adaptive horizon, and positive edge."""
    bars = _make_sample_session_bars(25, trend="bullish")
    ticks = _make_sample_ticks(50)

    result = run_strategy_semantics_pipeline(bars, ticks)

    assert result.traded is True
    assert result.market_decision.direction == "BULLISH"
    assert result.market_decision.horizon in (
        OpportunityMode.MICRO,
        OpportunityMode.SCALP,
        OpportunityMode.INTRADAY,
        OpportunityMode.EXTENDED_SCALP,
    )
    assert result.edge_assessment.expected_gross_value > 0.0
    assert result.economic_assessment.eligible is True
    assert result.economic_assessment.expected_net_value >= 10.0


def test_cf_economic_filter_rejects_unviable_opportunity():
    """Verify that when expected gross value cannot cover execution friction, no trade occurs."""
    bars = _make_sample_session_bars(20, trend="neutral")
    # Set high execution cost relative to excursion
    config = StrategyConfig(execution_cost=500.0, min_net_value=100.0)

    result = run_strategy_semantics_pipeline(bars, config=config)

    assert result.traded is False
    assert result.economic_assessment.eligible is False
    assert result.order_intent is None
    assert result.initial_position is None


# ==============================================================================
# G–I: Risk Sizing, Option Selection, Order Intent
# ==============================================================================

def test_gi_risk_sizing_and_option_instrument_selection():
    """Verify F-107/F-108 risk sizing and listed option strike resolution."""
    bars = _make_sample_session_bars(25, base_price=24520.0, trend="bullish")
    config = StrategyConfig(authorized_risk=10000.0, max_quantity=200, option_moneyness="ATM")

    result = run_strategy_semantics_pipeline(bars, config=config)

    assert result.traded is True
    assert result.sizing_assessment is not None
    assert 0 < result.sizing_assessment.final_quantity <= 200
    assert result.selected_instrument is not None
    # 24520 spot + ATM -> 24500 CE
    assert "24500CE" in result.selected_instrument
    assert result.order_intent is not None
    assert result.order_intent.quantity == result.sizing_assessment.final_quantity
    assert result.order_intent.side == "BUY"


# ==============================================================================
# J–K: Dynamic Protection Envelope, Lifecycle & Replay Determinism
# ==============================================================================

def test_jk_protection_envelope_and_lifecycle_termination():
    """Verify position protection progression and lifecycle exit reconciliation."""
    bars = _make_sample_session_bars(30, trend="bullish")
    result = run_strategy_semantics_pipeline(bars)

    assert result.traded is True
    assert result.initial_position is not None
    assert result.final_position is not None
    assert result.final_position.lifecycle_state == "CLOSED"
    assert len(result.protection_history) > 0
    assert result.exit_reason in ("PROFIT_TARGET_REACHED", "STOP_LOSS_TRIGGERED", "SESSION_CUTOFF_A126", "END_OF_SEQUENCE")
    assert isinstance(result.realized_pnl, float)


def test_complete_strategy_semantics_canonical_replay_determinism():
    """Primary Acceptance Milestone:

    Running the exact same market sequence twice with identical StrategyConfig
    and ReplayContext MUST produce byte-for-byte identical decisions, risk,
    instruments, orders, lifecycle exits, realized PnL, and trace hashes.
    """
    bars = _make_sample_session_bars(30, base_price=24500.0, trend="bullish")
    ticks = _make_sample_ticks(60, base_price=24500.0)

    replay_ctx = ReplayContext(
        decision_time="2026-08-17T03:45:00+00:00",
        event_time="2026-08-17T03:45:00+00:00",
        deterministic_id_namespace="strategy-e2e",
        sequence_seed=123,
    )
    config = StrategyConfig(
        symbol="NIFTY-I",
        authorized_risk=5000.0,
        execution_cost=20.0,
        min_net_value=10.0,
        option_moneyness="ATM",
    )

    result_1 = run_strategy_semantics_pipeline(
        bars, ticks, config=config, replay_context=replay_ctx
    )
    result_2 = run_strategy_semantics_pipeline(
        bars, ticks, config=config, replay_context=replay_ctx
    )

    # 1. Strategy Decisions Match
    assert result_1.market_decision.direction == result_2.market_decision.direction
    assert result_1.market_decision.horizon == result_2.market_decision.horizon
    assert result_1.market_decision.decision_reason == result_2.market_decision.decision_reason

    # 2. Economic & Risk Assessments Match
    assert result_1.economic_assessment.expected_net_value == result_2.economic_assessment.expected_net_value
    assert result_1.sizing_assessment.final_quantity == result_2.sizing_assessment.final_quantity

    # 3. Instrument & Order Intent Match
    assert result_1.selected_instrument == result_2.selected_instrument
    assert result_1.order_intent.order_intent_id == result_2.order_intent.order_intent_id
    assert result_1.order_intent.idempotency_key == result_2.order_intent.idempotency_key
    assert result_1.order_intent.quantity == result_2.order_intent.quantity

    # 4. Protection & Lifecycle Outcome Match
    assert result_1.exit_reason == result_2.exit_reason
    assert result_1.final_position.lifecycle_state == result_2.final_position.lifecycle_state
    assert result_1.realized_pnl == result_2.realized_pnl

    # 5. Deterministic Trace Hash Match
    assert result_1.trace_hash == result_2.trace_hash
    assert len(result_1.audit) == len(result_2.audit)


def test_v1_baseline_vs_v2_hardened_decision_divergence():
    """Verify that V1 baseline and V2 hardened produce distinct strategy decisions."""
    # 1. 09:20 IST opening session bar (in toxic window)
    bars_open = _make_sample_session_bars(5, base_price=24500.0, trend="bullish")
    cfg_v1 = StrategyConfig(strategy_version="v1_baseline")
    cfg_v2 = StrategyConfig(strategy_version="v2_hardened")

    res_v1 = run_strategy_semantics_pipeline(bars_open, config=cfg_v1)
    res_v2 = run_strategy_semantics_pipeline(bars_open, config=cfg_v2)

    # V1 fires bullish at 09:15-09:20; V2 locks out with opening_toxic_window_lockout
    assert res_v1.market_decision.direction == "BULLISH"
    assert res_v2.market_decision.direction == "NEUTRAL"
    assert res_v2.market_decision.decision_reason == "opening_toxic_window_lockout"

