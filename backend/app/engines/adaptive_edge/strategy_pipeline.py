"""Adaptive Edge Strategy Semantics Pipeline (Stages A through K).

Composes:
[A] Market sequence ingestion & causal boundary validation
[B] Causal FeatureSnapshot construction (Volume Profile, POC, CVD, 15m IB)
[C] Directional Hypothesis evaluation
[D] Adaptive Horizon selection (MICRO / SCALP / EXTENDED_SCALP / INTRADAY)
[E] Edge Assessment (expected gross value, win rate)
[F] F-004 Economic Viability evaluation (expected net value)
[G] Risk Authorization & Sizing (F-107 Risk-Per-Unit, F-108 Position Sizing)
[H] Option Instrument Selection (moneyness, strike, expiry)
[I] CanonicalOrderIntent construction & simulated execution
[J] Position Protection Envelope (Initial stop, profit lock, trailing, session cutoff)
[K] Lifecycle Termination, PnL reconciliation & deterministic audit trace
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Mapping, Sequence

from .accounting import mark_accounting
from .broker_event_mapper import BrokerEventMapper, BrokerExecutionEvent
from .contracts import DynamicMode, RiskAuthorization, RiskState
from .e2e import AuditLedger, AuditRecord, ExecutionMode, PositionState, ReplayContext
from .economic import EconomicAssessment, evaluate_economics
from .edge import EdgeAssessment, EdgeFormula, evaluate_edge
from .event_boundary import CanonicalMarketEvent
from .execution_adapter import (
    CanonicalExecutionEvent,
    CanonicalExecutionStatus,
    CanonicalOrderIntent,
    ExecutionAdapter,
)
from .execution_event_registry import ExecutionEventRegistry
from .execution_gate import evaluate_execution_gate
from .execution_gateway import ExecutionGateway
from .feature_engine import (
    FeatureInput,
    FeatureProvenance,
    FeatureSnapshot,
    FeatureStatus,
    InstrumentContext,
    build_feature_snapshot,
)
from .lifecycle_engine import (
    A126LifecycleEngine,
    HorizonState,
    LifecycleEvidence,
    ProtectionState,
    ThesisState,
)
from .opportunity_mode import OpportunityMode, OpportunityModeEngine, ModePolicy
from .option_ladder import STRIKE_STEP
from .position_projector import DeterministicPositionProjector
from .protection import ProtectionDecision, ProtectionEngine, ProtectionPolicy
from .research_session import (
    a126_session_cutoff_reached,
    minutes_until_a126_cutoff,
    nse_regular_session,
    session_date_ist,
)
from .risk_sizing import (
    ExecutionCostParameters,
    ParameterEstimationMethod,
    ParameterMetadata,
    ParameterValidationStatus,
    PositionSizingAssessment,
    RiskPerUnitAssessment,
    SizingParameters,
    calculate_position_sizing,
    calculate_risk_per_unit,
)
from .structure import StructureSnapshot, build_structure_series


@dataclass(frozen=True)
class StrategyConfig:
    """Strategy configuration parameters for Adaptive Edge."""

    strategy_version: str = "v2.0"
    feature_set_version: str = "fset-v1"
    symbol: str = "NIFTY-I"
    tick_size: float = 0.05
    value_area_coverage: float = 0.70
    execution_cost: float = 20.0
    min_net_value: float = 10.0
    authorized_risk: float = 5000.0
    max_quantity: int = 500
    stop_points: float = 30.0
    target_rr: float = 2.0
    option_moneyness: str = "ATM"
    option_expiry: str = "2026-08-27"


@dataclass(frozen=True)
class MarketStateDecision:
    """Evaluated directional hypothesis and adaptive horizon from market state."""

    direction: str  # BULLISH / BEARISH / NEUTRAL
    horizon: OpportunityMode  # MICRO / SCALP / EXTENDED_SCALP / INTRADAY
    uncertainty: float
    decision_reason: str
    target_points: float
    stop_points: float


@dataclass(frozen=True)
class StrategyExecutionResult:
    """Complete, deterministic result of strategy execution across a market sequence."""

    market_decision: MarketStateDecision
    feature_snapshot: FeatureSnapshot
    edge_assessment: EdgeAssessment
    economic_assessment: EconomicAssessment
    risk_assessment: RiskPerUnitAssessment | None
    sizing_assessment: PositionSizingAssessment | None
    selected_instrument: str | None
    order_intent: CanonicalOrderIntent | None
    initial_position: PositionState | None
    final_position: PositionState | None
    protection_history: tuple[ProtectionDecision, ...]
    lifecycle_history: tuple[LifecycleEvidence, ...]
    realized_pnl: float
    audit: tuple[AuditRecord, ...]
    trace_hash: str
    traded: bool
    exit_reason: str | None


class SimulatedExecutionTransport:
    def __init__(self) -> None:
        self.submitted_orders: list[CanonicalOrderIntent] = []

    def submit(self, intent: CanonicalOrderIntent) -> str:
        self.submitted_orders.append(intent)
        return f"SIM-BROKER-{intent.order_intent_id}"


def _make_param_metadata(name: str, value: float, units: str = "INR") -> ParameterMetadata:
    return ParameterMetadata(
        name=name,
        value=value,
        units=units,
        version="v1.0",
        provenance="strategy_config",
        estimation_method=ParameterEstimationMethod.CANONICAL_SPEC,
        validation_status=ParameterValidationStatus.VALIDATED,
    )


def build_causal_feature_snapshots(
    bar_events: Sequence[CanonicalMarketEvent],
    tick_events: Sequence[CanonicalMarketEvent] = (),
    *,
    symbol: str = "NIFTY-I",
    strategy_version: str = "v2.0",
    feature_set_version: str = "fset-v1",
    tick_size: float = 0.05,
    value_area_coverage: float = 0.70,
) -> list[FeatureSnapshot]:
    """Stage B: Build causal, versioned FeatureSnapshots from market event sequence."""
    structure_series = build_structure_series(
        bar_events,
        tick_events,
        tick_size=tick_size,
        value_area_coverage=value_area_coverage,
    )
    snapshots: list[FeatureSnapshot] = []
    bars = sorted(bar_events, key=lambda b: (b.available_at, b.record_id))

    for bar, struct in zip(bars, structure_series):
        inputs: list[FeatureInput] = []
        source_ids = (bar.record_id,)

        # Core price & structure features
        def add_feat(name: str, val: float | None) -> None:
            st = FeatureStatus.VALID if val is not None else FeatureStatus.MISSING
            inputs.append(
                FeatureInput(
                    name=name,
                    value=val,
                    available_at=bar.available_at,
                    status=st,
                    provenance=FeatureProvenance(source_event_ids=source_ids),
                )
            )

        bar_payload = bar.payload if isinstance(bar.payload, dict) else {}
        bar_open = float(bar_payload["open"]) if "open" in bar_payload and bar_payload["open"] is not None else struct.session_open
        bar_high = float(bar_payload["high"]) if "high" in bar_payload and bar_payload["high"] is not None else struct.vah
        bar_low = float(bar_payload["low"]) if "low" in bar_payload and bar_payload["low"] is not None else struct.val

        add_feat("open", bar_open)
        add_feat("high", bar_high)
        add_feat("low", bar_low)
        add_feat("close", struct.close)
        add_feat("vwap", struct.vwap)
        add_feat("poc", struct.poc)
        add_feat("vah", struct.vah)
        add_feat("val", struct.val)
        add_feat("vpoc", struct.vpoc)
        add_feat("vp_vah", struct.vp_vah)
        add_feat("vp_val", struct.vp_val)
        add_feat("cvd", struct.cvd)
        add_feat("bar_delta", struct.bar_delta)
        add_feat("buy_volume", struct.buy_volume)
        add_feat("sell_volume", struct.sell_volume)
        add_feat("gap", struct.gap)
        add_feat("ib_high", struct.ib_high if struct.ib_complete else None)
        add_feat("ib_low", struct.ib_low if struct.ib_complete else None)
        add_feat("ib_complete", 1.0 if struct.ib_complete else 0.0)

        snap = build_feature_snapshot(
            snapshot_id=f"SNAP-{bar.record_id}",
            strategy_version=strategy_version,
            feature_set_version=feature_set_version,
            observation_cutoff_time=bar.available_at,
            decision_time=bar.available_at,
            instrument_context=InstrumentContext(instrument_id=symbol),
            inputs=inputs,
        )
        snapshots.append(snap)

    return snapshots


def evaluate_market_decision(
    snapshot: FeatureSnapshot,
    struct: StructureSnapshot,
    bar_index: int,
    config: StrategyConfig,
) -> MarketStateDecision:
    """Stages C & D: Directional hypothesis and adaptive horizon from market state."""
    close = struct.close or 0.0
    session_open = struct.session_open if struct.session_open is not None else close
    effective_vwap = struct.vwap if struct.vwap is not None else session_open
    effective_poc = struct.poc if struct.poc is not None else session_open
    vah = struct.vah or close
    val = struct.val or close
    cvd = struct.cvd

    # Directional hypothesis
    is_bullish = (
        close >= effective_vwap
        and close >= effective_poc
        and (cvd >= 0 or (struct.ib_complete and struct.ib_high and close > struct.ib_high))
        and (close > session_open or (struct.vwap is not None and close > struct.vwap))
    )
    is_bearish = (
        close <= effective_vwap
        and close <= effective_poc
        and (cvd <= 0 or (struct.ib_complete and struct.ib_low and close < struct.ib_low))
        and (close < session_open or (struct.vwap is not None and close < struct.vwap))
    )
    # Directional hypothesis - differentiated between V1 baseline and V2 hardened
    strat_ver = getattr(config, "strategy_version", "v2_hardened") or "v2_hardened"
    is_v2 = str(strat_ver).lower() in ("v2_hardened", "v2", "v2.0")
    is_v2_hardened = str(strat_ver).lower() == "v2_hardened"
    is_v2 = is_v2_hardened or str(strat_ver).lower() in ("v2", "v2.0")

    if is_v2:
        # V2 Hardened: strict multi-condition confirmation (VWAP + POC + CVD + Session Open)
        # 1. V2 Hardened Opening Toxic Window Lockout (09:15 - 09:28 IST)
        if is_v2_hardened and snapshot.decision_time:
            try:
                dt_raw = datetime.fromisoformat(str(snapshot.decision_time).replace("Z", "+00:00"))
                dt_ist = dt_raw.astimezone(timezone(timedelta(hours=5, minutes=30)))
                if dt_ist.hour == 9 and dt_ist.minute < 28:
                    return MarketStateDecision(
                        direction="NEUTRAL",
                        horizon=OpportunityMode.MICRO,
                        uncertainty=0.5,
                        decision_reason="opening_toxic_window_lockout",
                        target_points=0.0,
                        stop_points=config.stop_points,
                    )
            except Exception:
                pass

        # 2. V2 Hardened: strict multi-condition confirmation (VWAP + POC + CVD + Session Open)
        is_bullish = (
            close >= effective_vwap
            and close >= effective_poc
            and (cvd >= 0 or (struct.ib_complete and struct.ib_high and close > struct.ib_high))
            and (close > session_open or (struct.vwap is not None and close > struct.vwap))
        )
        is_bearish = (
            close <= effective_vwap
            and close <= effective_poc
            and (cvd <= 0 or (struct.ib_complete and struct.ib_low and close < struct.ib_low))
            and (close < session_open or (struct.vwap is not None and close < struct.vwap))
        )

        # 3. V2 Candle Body & Rejection Filter (Close near extreme, avoid exhaustion wicks)
        b_high = snapshot.values.get("high")
        b_low = snapshot.values.get("low")
        if b_high is not None and b_low is not None and b_high > b_low:
            denom = max(b_high - b_low, 1e-9)
            if is_bullish and ((close - b_low) / denom) < 0.60:
                is_bullish = False
            if is_bearish and ((b_high - close) / denom) < 0.60:
                is_bearish = False
    else:
        # V1 Baseline: relaxed single-anchor confirmation
        # V1 Baseline: relaxed single-anchor confirmation without morning lockout or wick filter
        is_bullish = (
            (close >= effective_vwap or close >= effective_poc)
            and (close >= session_open or cvd >= 0)
        )
        is_bearish = (
            (close <= effective_vwap or close <= effective_poc)
            and (close <= session_open or cvd <= 0)
        )

    if is_bullish and not is_bearish:
        direction = "BULLISH"
        reason = "price_above_vwap_poc_and_positive_order_flow"
        stop_points = max(config.stop_points, abs(close - val) if struct.inside_value(close) else config.stop_points)
        target_points = stop_points * config.target_rr
    elif is_bearish and not is_bullish:
        direction = "BEARISH"
        reason = "price_below_vwap_poc_and_negative_order_flow"
        stop_points = max(config.stop_points, abs(vah - close) if struct.inside_value(close) else config.stop_points)
        target_points = stop_points * config.target_rr
    else:
        direction = "NEUTRAL"
        reason = "mixed_or_consolidating_market_state"
        stop_points = config.stop_points
        target_points = 0.0

    # Adaptive Horizon selection from structural regime
    if direction == "NEUTRAL":
        horizon = OpportunityMode.MICRO
        uncertainty = 0.5
    elif bar_index < 5:
        # Session opening impulse
        horizon = OpportunityMode.MICRO
        uncertainty = 0.15
    elif not struct.ib_complete:
        # Developing initial balance
        horizon = OpportunityMode.SCALP
        uncertainty = 0.10
    elif struct.inside_value(close):
        # Range-bound tactical scalp
        horizon = OpportunityMode.SCALP
        uncertainty = 0.12
    elif (struct.ib_high and close > struct.ib_high) or (struct.ib_low and close < struct.ib_low):
        # Out-of-balance trend continuation
        horizon = OpportunityMode.INTRADAY
        uncertainty = 0.08
    else:
        horizon = OpportunityMode.EXTENDED_SCALP
        uncertainty = 0.10

    return MarketStateDecision(
        direction=direction,
        horizon=horizon,
        uncertainty=uncertainty,
        decision_reason=reason,
        target_points=target_points,
        stop_points=stop_points,
    )


def select_option_contract(
    underlying: str,
    spot_price: float,
    direction: str,
    expiry_date: str,
    moneyness: str = "ATM",
) -> str:
    """Stage H: Select listed option contract based on underlying, direction and moneyness."""
    # NIFTY strike step = 50.0
    step = 50.0
    atm_strike = round(spot_price / step) * step

    if moneyness == "ATM":
        strike = atm_strike
    elif moneyness == "ITM1":
        strike = atm_strike - step if direction == "BULLISH" else atm_strike + step
    elif moneyness == "OTM1":
        strike = atm_strike + step if direction == "BULLISH" else atm_strike - step
    else:
        strike = atm_strike

    # Convert expiry e.g. "2026-08-27" to e.g. "26AUG"
    dt = datetime.fromisoformat(expiry_date)
    month_abbr = dt.strftime("%b").upper()
    day_str = dt.strftime("%d")
    yy_str = dt.strftime("%y")
    expiry_code = f"{yy_str}{month_abbr}"

    option_type = "CE" if direction == "BULLISH" else "PE"
    strike_str = f"{int(strike)}"
    return f"NIFTY{expiry_code}{strike_str}{option_type}"


def run_strategy_semantics_pipeline(
    bar_events: Sequence[CanonicalMarketEvent],
    tick_events: Sequence[CanonicalMarketEvent] = (),
    *,
    config: StrategyConfig = StrategyConfig(),
    replay_context: ReplayContext | None = None,
) -> StrategyExecutionResult:
    """Run the complete 11-stage Adaptive Edge strategy semantics pipeline."""
    if not bar_events:
        raise ValueError("market event sequence cannot be empty")

    audit = AuditLedger()
    bars = sorted(bar_events, key=lambda b: (b.available_at, b.record_id))

    # [A] Market Ingestion: Record all incoming market events into audit
    for bar in bars:
        audit.append("market_event", bar.record_id)

    # [B] Causal Feature Construction
    snapshots = build_causal_feature_snapshots(
        bars,
        tick_events,
        symbol=config.symbol,
        strategy_version=config.strategy_version,
        feature_set_version=config.feature_set_version,
        tick_size=config.tick_size,
        value_area_coverage=config.value_area_coverage,
    )
    for snap in snapshots:
        audit.append("feature_snapshot", snap.snapshot_id)

    structure_series = build_structure_series(
        bars,
        tick_events,
        tick_size=config.tick_size,
        value_area_coverage=config.value_area_coverage,
    )

    # Find the first actionable decision bar
    trigger_index = -1
    trigger_snap: FeatureSnapshot | None = None
    trigger_struct: StructureSnapshot | None = None
    market_decision: MarketStateDecision | None = None

    for idx, (snap, struct) in enumerate(zip(snapshots, structure_series)):
        dec = evaluate_market_decision(snap, struct, idx, config)
        if dec.direction != "NEUTRAL":
            trigger_index = idx
            trigger_snap = snap
            trigger_struct = struct
            market_decision = dec
            break

    if trigger_index == -1 or trigger_snap is None or trigger_struct is None or market_decision is None:
        # No trade detected across entire sequence
        last_snap = snapshots[-1]
        last_struct = structure_series[-1]
        market_decision = evaluate_market_decision(last_snap, last_struct, len(snapshots) - 1, config)
        edge = EdgeAssessment(
            opportunity_id=f"OPP-{last_snap.snapshot_id}",
            score=0.0,
            confidence=0.0,
            expected_gross_value=0.0,
            formula_id="F-004",
            formula_version="1.0",
            inputs={},
        )
        econ = evaluate_economics(edge, execution_cost=config.execution_cost, minimum_net_value=config.min_net_value)
        return StrategyExecutionResult(
            market_decision=market_decision,
            feature_snapshot=last_snap,
            edge_assessment=edge,
            economic_assessment=econ,
            risk_assessment=None,
            sizing_assessment=None,
            selected_instrument=None,
            order_intent=None,
            initial_position=None,
            final_position=None,
            protection_history=(),
            lifecycle_history=(),
            realized_pnl=0.0,
            audit=audit.records(),
            trace_hash=hashlib.sha256(json.dumps([r.stage for r in audit.records()]).encode()).hexdigest(),
            traded=False,
            exit_reason="NO_DIRECTIONAL_OPPORTUNITY",
        )

    # [C] Directional Hypothesis & [D] Horizon
    audit.append("decision", f"DEC-{trigger_snap.snapshot_id}", trigger_snap.snapshot_id)

    # [E] Edge Assessment
    opp_id = f"OPP-{trigger_snap.snapshot_id}"
    expected_gross = market_decision.target_points * 2.0  # e.g. Expected monetary payoff
    edge = EdgeAssessment(
        opportunity_id=opp_id,
        score=0.75,
        confidence=1.0 - market_decision.uncertainty,
        expected_gross_value=expected_gross,
        formula_id="F-004",
        formula_version="1.0",
        inputs={
            "target_points": market_decision.target_points,
            "stop_points": market_decision.stop_points,
        },
    )
    audit.append("edge", opp_id, trigger_snap.snapshot_id)

    # [F] Economic Viability
    econ = evaluate_economics(
        edge,
        execution_cost=config.execution_cost,
        minimum_net_value=config.min_net_value,
    )
    audit.append("economics", f"ECON-{opp_id}", opp_id)

    if not econ.eligible:
        return StrategyExecutionResult(
            market_decision=market_decision,
            feature_snapshot=trigger_snap,
            edge_assessment=edge,
            economic_assessment=econ,
            risk_assessment=None,
            sizing_assessment=None,
            selected_instrument=None,
            order_intent=None,
            initial_position=None,
            final_position=None,
            protection_history=(),
            lifecycle_history=(),
            realized_pnl=0.0,
            audit=audit.records(),
            trace_hash=hashlib.sha256(json.dumps([r.stage for r in audit.records()]).encode()).hexdigest(),
            traded=False,
            exit_reason="INSUFFICIENT_ECONOMIC_EDGE",
        )

    # [G] Risk Authorization & Sizing (F-107, F-108)
    issued_at = trigger_snap.decision_time
    spot_close = trigger_struct.close or 24500.0
    initial_stop_price = spot_close - market_decision.stop_points if market_decision.direction == "BULLISH" else spot_close + market_decision.stop_points

    cost_params = ExecutionCostParameters(
        spread_cost=_make_param_metadata("spread_cost", 0.5),
        expected_slippage=_make_param_metadata("expected_slippage", 0.5),
        brokerage_per_unit=_make_param_metadata("brokerage_per_unit", 0.2),
        exchange_charges_per_unit=_make_param_metadata("exchange_charges_per_unit", 0.1),
        taxes_per_unit=_make_param_metadata("taxes_per_unit", 0.1),
        latency_cost_per_unit=_make_param_metadata("latency_cost_per_unit", 0.1),
    )
    risk_unit = calculate_risk_per_unit(
        entry_price=spot_close,
        initial_stop=spot_close - market_decision.stop_points,
        cost_params=cost_params,
    )

    sizing_params = SizingParameters(
        max_position_qty=_make_param_metadata("max_position_qty", float(config.max_quantity), units="units"),
        max_capital_allocation=_make_param_metadata("max_capital_allocation", 50_000_000.0, units="INR"),
        lot_size=_make_param_metadata("lot_size", 25.0, units="units"),
    )
    risk_auth = RiskAuthorization(
        opportunity_id=opp_id,
        authorized_risk=config.authorized_risk,
        risk_state=RiskState.AUTHORIZED,
        policy_version="v1.0",
        issued_at=issued_at,
    )
    sizing = calculate_position_sizing(
        risk_auth,
        risk_unit,
        sizing_params,
    )
    trade_qty = sizing.final_quantity
    audit.append("risk_authorization", f"AUTH-{opp_id}", opp_id)

    if trade_qty <= 0:
        return StrategyExecutionResult(
            market_decision=market_decision,
            feature_snapshot=trigger_snap,
            edge_assessment=edge,
            economic_assessment=econ,
            risk_assessment=risk_unit,
            sizing_assessment=sizing,
            selected_instrument=None,
            order_intent=None,
            initial_position=None,
            final_position=None,
            protection_history=(),
            lifecycle_history=(),
            realized_pnl=0.0,
            audit=audit.records(),
            trace_hash=hashlib.sha256(json.dumps([r.stage for r in audit.records()]).encode()).hexdigest(),
            traded=False,
            exit_reason="ZERO_AUTHORIZED_QUANTITY",
        )

    # [H] Option Instrument Selection
    spot_close = trigger_struct.close or 24500.0
    selected_option = select_option_contract(
        config.symbol,
        spot_close,
        market_decision.direction,
        config.option_expiry,
        config.option_moneyness,
    )
    audit.append("instrument", f"SEL-{selected_option}", f"AUTH-{opp_id}")

    # [I] Canonical Order Intent & Simulated Execution
    order_id = replay_context.deterministic_id("ORDER", f"SEL-{selected_option}") if replay_context else f"ORDER-{trigger_snap.snapshot_id}"
    idem_key = replay_context.deterministic_id("IDEM", order_id) if replay_context else f"IDEM-{order_id}"

    order = CanonicalOrderIntent(
        order_intent_id=order_id,
        selection_id=f"SEL-{selected_option}",
        instrument_id=selected_option,
        side="BUY",
        quantity=trade_qty,
        intent_version="v2.0",
        idempotency_key=idem_key,
        created_at=trigger_snap.decision_time,
    )
    audit.append("order_intent", order.order_intent_id, f"SEL-{selected_option}")

    transport = SimulatedExecutionTransport()
    gateway = ExecutionGateway(
        ExecutionAdapter(transport),
        BrokerEventMapper({"SIM_FILL": CanonicalExecutionStatus.FILLED}),
        ExecutionEventRegistry(),
    )
    broker_ref = gateway.submit(order, formula_ids=("F-004",))

    # Entry fill price: model option premium ~ 150.0 (or spot based)
    entry_fill_price = 150.0
    exec_event = gateway.receive(
        BrokerExecutionEvent(
            broker_event_id=f"BE-ENTRY-{order_id}",
            order_intent_id=order_id,
            broker_status="SIM_FILL",
            event_time=trigger_snap.decision_time,
            broker_reference=broker_ref,
            filled_quantity=trade_qty,
            fill_price=entry_fill_price,
        )
    )
    audit.append("execution_event", exec_event.execution_event_id, order.order_intent_id)

    # [J] Position Protection Envelope
    projector = DeterministicPositionProjector(
        position_id=f"POS-{order_id}",
        instrument_id=selected_option,
        side="BUY",
    )
    initial_pos = projector.project(exec_event)
    audit.append("position", initial_pos.position_id, exec_event.execution_event_id)

    protection_policy = ProtectionPolicy(
        label="STRATEGY_PROTECTION_POLICY",
        protective_stop_points=market_decision.stop_points * 0.5,
        trail_points=market_decision.stop_points * 0.4,
        profit_lock_activation_points=market_decision.stop_points * 1.0,
        profit_lock_offset_points=market_decision.stop_points * 0.3,
    )
    protector = ProtectionEngine(
        protection_policy,
        side="BUY",
        entry_price=entry_fill_price,
    )

    protection_history: list[ProtectionDecision] = []
    lifecycle_history: list[LifecycleEvidence] = []
    current_pos = initial_pos
    exit_reason: str | None = None
    exit_bar_index = -1
    exit_fill_price = entry_fill_price

    # Process subsequent bars through protection and lifecycle
    for curr_idx in range(trigger_index + 1, len(bars)):
        curr_bar = bars[curr_idx]
        curr_close = float(curr_bar.payload.get("close") or spot_close)
        # Spot change mapped to option change (~ delta 0.5)
        spot_delta = (curr_close - spot_close) if market_decision.direction == "BULLISH" else (spot_close - curr_close)
        simulated_option_price = max(1.0, entry_fill_price + spot_delta * 0.5)

        prot_decision = protector.update(simulated_option_price)
        protection_history.append(prot_decision)

        # Check A126 session cutoff (15:15 IST)
        if a126_session_cutoff_reached(curr_bar.available_at):
            exit_reason = "SESSION_CUTOFF_A126"
            exit_bar_index = curr_idx
            exit_fill_price = simulated_option_price
            break

        # Check stop / trail / profit-lock hit (ProtectionEngine evaluates all three)
        if prot_decision.hit:
            exit_reason = {
                "PROTECTIVE_STOP": "STOP_LOSS_TRIGGERED",
                "TRAILING_PROTECTION": "TRAILING_STOP_TRIGGERED",
                "PROFIT_LOCK": "PROFIT_LOCK_TRIGGERED",
            }.get(prot_decision.authority or "", "STOP_LOSS_TRIGGERED")
            exit_bar_index = curr_idx
            # Use the authority's own price for the fill: trail_price for a trail
            # exit, lock_price for a lock exit, stop_price for a hard stop.
            if prot_decision.authority == "TRAILING_PROTECTION" and prot_decision.trail_price is not None:
                exit_fill_price = prot_decision.trail_price
            elif prot_decision.authority == "PROFIT_LOCK" and prot_decision.lock_price is not None:
                exit_fill_price = prot_decision.lock_price
            elif prot_decision.stop_price is not None:
                exit_fill_price = prot_decision.stop_price
            else:
                exit_fill_price = simulated_option_price
            break

        if simulated_option_price >= entry_fill_price + (market_decision.target_points * 0.5):
            exit_reason = "PROFIT_TARGET_REACHED"
            exit_bar_index = curr_idx
            exit_fill_price = simulated_option_price
            break

    # [K] Lifecycle Termination & PnL Reconciliation
    if exit_reason is None:
        exit_reason = "END_OF_SEQUENCE"
        exit_bar_index = len(bars) - 1
        last_spot = float(bars[-1].payload.get("close") or spot_close)
        spot_delta = (last_spot - spot_close) if market_decision.direction == "BULLISH" else (spot_close - last_spot)
        exit_fill_price = max(1.0, entry_fill_price + spot_delta * 0.5)

    exit_exec_event = CanonicalExecutionEvent(
        execution_event_id=f"BE-EXIT-{order_id}",
        order_intent_id=order_id,
        event_type=CanonicalExecutionStatus.FILLED,
        event_time=bars[exit_bar_index].available_at,
        filled_quantity=trade_qty,
        fill_price=exit_fill_price,
    )
    final_pos = PositionState(
        position_id=initial_pos.position_id,
        instrument_id=selected_option,
        quantity=0,
        average_price=exit_fill_price,
        lifecycle_state="CLOSED",
        source_execution_event_id=exit_exec_event.execution_event_id,
    )
    audit.append("lifecycle_exit", f"EXIT-{order_id}", initial_pos.position_id)

    gross_pnl = (exit_fill_price - entry_fill_price) * trade_qty
    realized_pnl = gross_pnl - config.execution_cost

    # Canonical Trace Hash
    audit_data = [
        {"seq": r.sequence, "stage": r.stage, "id": r.object_id, "parents": r.parent_ids}
        for r in audit.records()
    ]
    trace_payload = {
        "market_decision": {
            "direction": market_decision.direction,
            "horizon": market_decision.horizon.value,
            "uncertainty": market_decision.uncertainty,
        },
        "instrument": selected_option,
        "quantity": trade_qty,
        "entry_price": entry_fill_price,
        "exit_price": exit_fill_price,
        "exit_reason": exit_reason,
        "realized_pnl": round(realized_pnl, 4),
        "audit": audit_data,
    }
    trace_hash = hashlib.sha256(json.dumps(trace_payload, sort_keys=True).encode()).hexdigest()

    return StrategyExecutionResult(
        market_decision=market_decision,
        feature_snapshot=trigger_snap,
        edge_assessment=edge,
        economic_assessment=econ,
        risk_assessment=risk_unit,
        sizing_assessment=sizing,
        selected_instrument=selected_option,
        order_intent=order,
        initial_position=initial_pos,
        final_position=final_pos,
        protection_history=tuple(protection_history),
        lifecycle_history=tuple(lifecycle_history),
        realized_pnl=realized_pnl,
        audit=audit.records(),
        trace_hash=trace_hash,
        traded=True,
        exit_reason=exit_reason,
    )
