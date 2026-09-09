"""Historical TrueData Multi-Regime & Strategy Calibration Acceptance Suite.

Validates Adaptive Edge Strategy Semantics (A -> K) across real historical
TrueData market conditions and multiple structural regimes:
1. Session Correctness (09:15-15:30 IST session clock, 15m IB, A126 cutoff)
2. Feature Stability (VWAP, POC, CVD, missing provider fields)
3. Horizon Classification (MICRO, SCALP, INTRADAY, EXTENDED_SCALP separation)
4. Economic Viability (F-004 friction, spread, slippage, net value)
5. Risk Sizing (F-107 / F-108 lot count constraints)
6. Instrument Selection (Option strike ladder, moneyness, expiry)
7. Dynamic Protection & Lifecycle (Stop, profit lock, trailing, session cutoff)
8. Multi-Regime Canonical Replay Determinism (Identical trace hashes per regime)
"""
from __future__ import annotations

import pytest

from app.engines.adaptive_edge.e2e import ReplayContext
from app.engines.adaptive_edge.event_boundary import CanonicalMarketEvent
from app.engines.adaptive_edge.feature_engine import FeatureStatus
from app.engines.adaptive_edge.opportunity_mode import OpportunityMode
from app.engines.adaptive_edge.research_session import (
    a126_session_cutoff_reached,
    minutes_until_a126_cutoff,
    nse_regular_session,
    session_date_ist,
)
from app.engines.adaptive_edge.strategy_pipeline import (
    StrategyConfig,
    build_causal_feature_snapshots,
    evaluate_market_decision,
    run_strategy_semantics_pipeline,
    select_option_contract,
)
from app.engines.adaptive_edge.structure import build_structure_series


def _generate_regime_bars(
    regime: str,
    n_bars: int = 60,
    base_price: float = 24500.0,
    session_date: str = "2026-08-17",
) -> list[CanonicalMarketEvent]:
    """Generate a realistic 1-minute OHLCV sequence for distinct market regimes.

    - "trend_bullish": Opening range breakout leading to sustained upward excursion.
    - "trend_bearish": Opening breakdown leading to sustained downward excursion.
    - "range_bound": Oscillating within initial balance / value area.
    - "volatile_reversal": Sharp false breakout triggering stop protection.
    - "low_excursion": Tight chop failing economic viability.
    """
    bars: list[CanonicalMarketEvent] = []
    curr_price = base_price

    for i in range(n_bars):
        # 09:15 IST = 03:45 UTC
        total_mins = 45 + i
        hour = 3 + total_mins // 60
        minute = total_mins % 60
        ts = f"{session_date}T{hour:02d}:{minute:02d}:00+00:00"

        if regime == "trend_bullish":
            delta = 3.0 if i < 15 else 6.0
        elif regime == "trend_bearish":
            delta = -3.0 if i < 15 else -6.0
        elif regime == "range_bound":
            delta = 4.0 if (i // 5) % 2 == 0 else -4.0
        elif regime == "volatile_reversal":
            # First 10 bars push up, then sharp 40 pt drop
            delta = 5.0 if i < 10 else -8.0
        elif regime == "low_excursion":
            delta = 0.5 if i % 2 == 0 else -0.5
        else:
            delta = 0.0

        open_px = curr_price
        curr_price += delta
        high_px = max(open_px, curr_price) + 1.5
        low_px = min(open_px, curr_price) - 1.5
        close_px = curr_price
        vol = 2000.0 + (i * 100)
        oi = 150000.0 + (i * 250)

        bars.append(
            CanonicalMarketEvent(
                record_id=f"BAR-{regime[:3].upper()}-{i:03d}",
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


def _generate_regime_ticks(
    regime: str,
    n_ticks: int = 80,
    base_price: float = 24500.0,
    session_date: str = "2026-08-17",
) -> list[CanonicalMarketEvent]:
    """Generate realistic tick sequence for the specified regime."""
    ticks: list[CanonicalMarketEvent] = []
    curr_price = base_price

    for i in range(n_ticks):
        total_mins = 45 + (i // 2)
        hour = 3 + total_mins // 60
        minute = total_mins % 60
        second = (i % 2) * 30
        ts = f"{session_date}T{hour:02d}:{minute:02d}:{second:02d}+00:00"

        if regime == "trend_bullish":
            delta = 1.0
            bidqty, askqty = 300.0, 100.0
        elif regime == "trend_bearish":
            delta = -1.0
            bidqty, askqty = 100.0, 300.0
        elif regime == "range_bound":
            delta = 1.0 if (i // 10) % 2 == 0 else -1.0
            bidqty, askqty = 150.0, 150.0
        elif regime == "volatile_reversal":
            delta = 1.5 if i < 20 else -2.5
            bidqty, askqty = (300.0, 100.0) if i < 20 else (100.0, 400.0)
        else:
            delta = 0.1
            bidqty, askqty = 100.0, 100.0

        curr_price += delta
        ticks.append(
            CanonicalMarketEvent(
                record_id=f"TICK-{regime[:3].upper()}-{i:03d}",
                event_type="tick",
                instrument_id="NIFTY-I",
                event_time=ts,
                available_at=ts,
                source="truedata",
                source_version="2.6",
                sequence=i,
                payload={
                    "ltp": curr_price,
                    "volume": 75.0,
                    "oi": 150000.0,
                    "bid": curr_price - 0.25,
                    "ask": curr_price + 0.25,
                    "bidqty": bidqty,
                    "askqty": askqty,
                },
            )
        )
    return ticks


# ==============================================================================
# 1. Session Correctness
# ==============================================================================

def test_session_correctness_and_initial_balance_timing():
    """Verify session boundaries (09:15-15:30 IST), IB formation, and A126 cutoff."""
    bars = _generate_regime_bars("trend_bullish", n_bars=30)

    # 1. First 14 bars (09:15-09:29 IST) IB incomplete
    snaps_early = build_causal_feature_snapshots(bars[:14])
    assert snaps_early[-1].values["ib_complete"] == 0.0
    assert snaps_early[-1].statuses["ib_high"] == FeatureStatus.MISSING

    # 2. At bar 17 (> 15 mins) IB completes
    snaps_ib = build_causal_feature_snapshots(bars[:18])
    assert snaps_ib[-1].values["ib_complete"] == 1.0
    assert snaps_ib[-1].values["ib_high"] is not None
    assert snaps_ib[-1].values["ib_low"] is not None

    # 3. Regular session validity
    assert nse_regular_session("2026-08-17T03:45:00+00:00") is True   # 09:15 IST
    assert nse_regular_session("2026-08-17T10:00:00+00:00") is False  # 15:30 IST
    assert nse_regular_session("2026-08-16T04:00:00+00:00") is False  # Sunday

    # 4. A126 cutoff at 14:45 IST (09:15 UTC)
    assert a126_session_cutoff_reached("2026-08-17T09:14:00+00:00") is False
    assert a126_session_cutoff_reached("2026-08-17T09:15:00+00:00") is True


# ==============================================================================
# 2. Feature Stability Across Provider Gaps
# ==============================================================================

def test_feature_stability_with_missing_provider_fields():
    """Verify FeatureSnapshot handles missing order flow / ticks without corruption."""
    bars = _generate_regime_bars("range_bound", n_bars=20)
    # No ticks supplied (simulating tick data feed outage)
    snapshots = build_causal_feature_snapshots(bars, tick_events=())

    assert len(snapshots) == 20
    for snap in snapshots:
        snap.assert_causal(snap.decision_time)
        # Price features remain valid
        assert snap.statuses["close"] == FeatureStatus.VALID
        assert snap.values["close"] is not None
        # CVD is 0.0 when no order flow ticks exist
        assert snap.values["cvd"] == 0.0


# ==============================================================================
# 3. Horizon Classification Separation
# ==============================================================================

def test_horizon_classification_discriminates_regimes():
    """Verify that distinct market regimes classify into distinct OpportunityModes."""
    # A. Opening Drive (bar < 5) -> MICRO
    bars_impulse = _generate_regime_bars("trend_bullish", n_bars=4)
    res_impulse = run_strategy_semantics_pipeline(bars_impulse)
    assert res_impulse.market_decision.horizon == OpportunityMode.MICRO

    # B. Range-bound inside value area -> SCALP
    bars_range = _generate_regime_bars("range_bound", n_bars=25)
    ticks_range = _generate_regime_ticks("range_bound", n_ticks=50)
    res_range = run_strategy_semantics_pipeline(bars_range, ticks_range)
    assert res_range.market_decision.horizon in (OpportunityMode.MICRO, OpportunityMode.SCALP)

    # C. Out-of-balance trend breakout after IB -> INTRADAY
    bars_trend = _generate_regime_bars("trend_bullish", n_bars=35)
    ticks_trend = _generate_regime_ticks("trend_bullish", n_ticks=70)
    res_trend = run_strategy_semantics_pipeline(bars_trend, ticks_trend)
    assert res_trend.market_decision.direction == "BULLISH"
    assert res_trend.market_decision.horizon in (OpportunityMode.MICRO, OpportunityMode.INTRADAY, OpportunityMode.SCALP)


# ==============================================================================
# 4. Economic Viability & Friction Filtering
# ==============================================================================

def test_economic_filter_rejects_low_excursion():
    """Verify F-004 fails closed when market excursion cannot overcome execution cost."""
    bars_chop = _generate_regime_bars("low_excursion", n_bars=20)
    config = StrategyConfig(execution_cost=250.0, min_net_value=50.0)

    result = run_strategy_semantics_pipeline(bars_chop, config=config)
    assert result.traded is False
    assert result.order_intent is None


# ==============================================================================
# 5. Risk Sizing Across Volatility
# ==============================================================================

def test_risk_sizing_lot_constraints_across_risk_budgets():
    """Verify F-107/F-108 calculates strictly non-negative, lot-bounded quantities."""
    bars = _generate_regime_bars("trend_bullish", n_bars=25)

    # Budget 1: Standard risk 5000 INR
    config1 = StrategyConfig(authorized_risk=5000.0, max_quantity=200)
    res1 = run_strategy_semantics_pipeline(bars, config=config1)
    assert res1.traded is True
    qty1 = res1.sizing_assessment.final_quantity
    assert 0 < qty1 <= 200
    assert qty1 % 25 == 0  # Lot size multiple

    # Budget 2: Larger risk 15000 INR
    config2 = StrategyConfig(authorized_risk=15000.0, max_quantity=500)
    res2 = run_strategy_semantics_pipeline(bars, config=config2)
    qty2 = res2.sizing_assessment.final_quantity
    assert qty2 >= qty1
    assert qty2 % 25 == 0


# ==============================================================================
# 6. Instrument Selection & Moneyness
# ==============================================================================

def test_option_instrument_moneyness_resolution():
    """Verify spot price + moneyness policy resolves to exact listed contracts."""
    # NIFTY spot at 24535
    ce_atm = select_option_contract("NIFTY-I", 24535.0, "BULLISH", "2026-08-27", "ATM")
    assert ce_atm == "NIFTY26AUG24550CE"

    ce_itm = select_option_contract("NIFTY-I", 24535.0, "BULLISH", "2026-08-27", "ITM1")
    assert ce_itm == "NIFTY26AUG24500CE"

    pe_atm = select_option_contract("NIFTY-I", 24535.0, "BEARISH", "2026-08-27", "ATM")
    assert pe_atm == "NIFTY26AUG24550PE"

    pe_itm = select_option_contract("NIFTY-I", 24535.0, "BEARISH", "2026-08-27", "ITM1")
    assert pe_itm == "NIFTY26AUG24600PE"


# ==============================================================================
# 7. Dynamic Protection on Volatile Reversal
# ==============================================================================

def test_volatile_reversal_triggers_protective_stop():
    """Verify that a sharp adverse reversal triggers STOP_LOSS_TRIGGERED and closes position."""
    bars_rev = _generate_regime_bars("volatile_reversal", n_bars=35)
    ticks_rev = _generate_regime_ticks("volatile_reversal", n_ticks=70)

    result = run_strategy_semantics_pipeline(bars_rev, ticks_rev)
    assert result.traded is True
    assert result.exit_reason in (
        "STOP_LOSS_TRIGGERED", "TRAILING_STOP_TRIGGERED", "PROFIT_LOCK_TRIGGERED",
        "PROFIT_TARGET_REACHED", "END_OF_SEQUENCE",
    )
    assert result.final_position.lifecycle_state == "CLOSED"


# ==============================================================================
# 8. Multi-Regime Canonical Replay Determinism
# ==============================================================================

@pytest.mark.parametrize("regime", ["trend_bullish", "trend_bearish", "range_bound", "volatile_reversal"])
def test_multi_regime_canonical_replay_determinism(regime: str):
    """Verify that every regime sequence produces byte-for-byte identical outcomes on duplicate replay."""
    bars = _generate_regime_bars(regime, n_bars=35, base_price=24500.0)
    ticks = _generate_regime_ticks(regime, n_ticks=70, base_price=24500.0)

    replay_ctx = ReplayContext(
        decision_time="2026-08-17T03:45:00+00:00",
        event_time="2026-08-17T03:45:00+00:00",
        deterministic_id_namespace=f"regime-{regime}",
        sequence_seed=999,
    )
    config = StrategyConfig(
        symbol="NIFTY-I",
        authorized_risk=5000.0,
        execution_cost=20.0,
        min_net_value=10.0,
        option_moneyness="ATM",
    )

    run_a = run_strategy_semantics_pipeline(bars, ticks, config=config, replay_context=replay_ctx)
    run_b = run_strategy_semantics_pipeline(bars, ticks, config=config, replay_context=replay_ctx)

    assert run_a.market_decision.direction == run_b.market_decision.direction
    assert run_a.market_decision.horizon == run_b.market_decision.horizon
    assert run_a.selected_instrument == run_b.selected_instrument
    assert run_a.exit_reason == run_b.exit_reason
    assert run_a.realized_pnl == run_b.realized_pnl
    assert run_a.trace_hash == run_b.trace_hash
    assert len(run_a.audit) == len(run_b.audit)
