"""Typed domain contracts for Snapback intraday and scalping strategy artifacts.

All artifacts follow the common envelope defined in Specification 01:
- Explicit versioning, timestamps, data provenance, and quality status.
- Immutable once created; updates emit new versioned records.
- Precise tick arithmetic support (prices and fees represented cleanly).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ArtifactEnvelope:
    """Common envelope header for durable strategy artifacts."""

    schema_version: str = "1.0"
    engine_version: str = "1.0"
    artifact_id: str = ""
    parent_artifact_ids: List[str] = field(default_factory=list)
    created_at_ms: int = 0
    available_at_ms: int = 0
    strategy_id: str = "snapback"
    trading_mode: str = "scalp"
    config_hash: str = ""
    config_generation: int = 0
    account_id: Optional[str] = None
    dataset_id: Optional[str] = None
    source_kind: str = "simulated"  # "broker_stream", "broker_rest", "historical_vendor", "imported", "simulated"
    quality_status: str = "VALID"  # "VALID", "DEGRADED", "REJECTED"
    quality_reason_codes: List[str] = field(default_factory=list)
    input_hashes: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ContractRegistry:
    """Identity and trading rules for a listed derivative contract."""

    exchange: str
    segment: str
    underlying_id: str
    exchange_token: int
    tradingsymbol: str
    option_type: str  # "CE" or "PE"
    strike: float
    expiry_date: date
    lot_size: int
    tick_size: float
    quantity_freeze: Optional[int] = None
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    source_version: str = "1.0"
    captured_at_ms: int = 0
    envelope: ArtifactEnvelope = field(default_factory=ArtifactEnvelope)


@dataclass(frozen=True)
class DepthLevel:
    """One visible price level of the order book."""

    price: float
    quantity: int


@dataclass(frozen=True)
class RawQuoteEvent:
    """Raw tick or quote snapshot from WebSocket or REST feed."""

    contract_id: str
    exchange_timestamp_ms: int
    received_at_ms: int
    best_bid: float
    best_ask: float
    bid_quantity: int
    ask_quantity: int
    last_price: float = 0.0
    open_interest: int = 0
    volume: int = 0
    payload_hash: str = ""
    envelope: ArtifactEnvelope = field(default_factory=ArtifactEnvelope)
    # Provider-observed ladders. Appended last on purpose: existing call sites build
    # this event positionally.
    bid_depth: tuple = ()
    ask_depth: tuple = ()


@dataclass(frozen=True)
class QualityDecision:
    """Result of data validation and quality check on a raw quote or bar."""

    raw_event_id: str
    decision_at_ms: int
    accepted_for_context: bool
    accepted_for_execution: bool
    quote_age_ms: float
    receive_delay_ms: float
    two_sided: bool
    non_crossed: bool
    finite_values: bool
    contract_valid: bool
    reason_codes: List[str] = field(default_factory=list)
    envelope: ArtifactEnvelope = field(default_factory=ArtifactEnvelope)
    # Book-quality facts, so no consumer has to recompute them from bid/ask.
    spread_pct: Optional[float] = None
    depth_valid: bool = False
    visible_bid_quantity: int = 0
    visible_ask_quantity: int = 0
    provider_timestamp_valid: bool = False


@dataclass(frozen=True)
class SessionTape:
    """Validated session tape containing completed bars."""

    session_date: str
    calendar_version: str
    interval_minutes: int
    underlying_symbol: str
    completed_bars: List[Dict[str, Any]] = field(default_factory=list)
    missing_intervals: List[int] = field(default_factory=list)
    envelope: ArtifactEnvelope = field(default_factory=ArtifactEnvelope)


@dataclass(frozen=True)
class FeatureSnapshot:
    """Causal indicator features evaluated at a completed bar boundary."""

    completed_bar_id: str
    timestamp_ms: int
    close: float
    mean: float
    upper_band: float
    lower_band: float
    std_dev: float
    ema9: Optional[float] = None
    adx14: Optional[float] = None
    atr14: Optional[float] = None
    pcr: Optional[float] = None
    vwap: Optional[float] = None
    oi_change: Optional[float] = None
    rvol: Optional[float] = None
    warmup_bars: int = 0
    available_features: List[str] = field(default_factory=list)
    envelope: ArtifactEnvelope = field(default_factory=ArtifactEnvelope)

    def is_finite(self) -> bool:
        return (
            math.isfinite(self.close)
            and math.isfinite(self.mean)
            and math.isfinite(self.upper_band)
            and math.isfinite(self.lower_band)
        )


@dataclass(frozen=True)
class SetupDecision:
    """Signal generation decision based on causal features."""

    setup_id: str
    timestamp_ms: int
    symbol: str
    side: str  # "CE" or "PE"
    trigger_price: float
    mean_target: float
    invalidation_reference: float
    band_reentry_confirmed: bool
    is_eligible: bool
    rejection_reasons: List[str] = field(default_factory=list)
    rule_results: Dict[str, bool] = field(default_factory=dict)
    envelope: ArtifactEnvelope = field(default_factory=ArtifactEnvelope)


@dataclass(frozen=True)
class ContractCandidateSet:
    """Set of candidate contracts evaluated for setup execution."""

    setup_id: str
    candidate_symbols: List[str] = field(default_factory=list)
    rejected_candidates: Dict[str, str] = field(default_factory=dict)
    selected_symbol: Optional[str] = None
    deterministic_rank: List[str] = field(default_factory=list)
    envelope: ArtifactEnvelope = field(default_factory=ArtifactEnvelope)


@dataclass(frozen=True)
class CostEstimate:
    """Breakdown of transaction costs, fees, and spread for a candidate trade."""

    quantity: int
    ask_entry: float
    bid_exit: float
    expected_entry: float
    expected_exit: float
    spread_points: float
    brokerage_fee: float
    stt_and_taxes: float
    total_round_trip_cost: float
    cost_per_unit_points: float
    envelope: ArtifactEnvelope = field(default_factory=ArtifactEnvelope)


@dataclass(frozen=True)
class TradePlan:
    """Cost-aware, feasible trade plan ready for risk reservation."""

    plan_id: str
    setup_id: str
    selected_contract_symbol: str
    requested_target_points: float
    effective_target_points: float
    feasible_target_ceiling: float
    initial_stop_points: float
    quantity: int
    lots: int
    cash_required: float
    risk_amount: float
    validity_time_ms: int
    policy_hash: str
    feasibility_status: str  # "FEASIBLE", "UNATTAINABLE", "UNMEASURED"
    envelope: ArtifactEnvelope = field(default_factory=ArtifactEnvelope)


@dataclass(frozen=True)
class RiskReservation:
    """Account-level risk reservation blocking capital for an intent."""

    reservation_id: str
    plan_id: str
    account_id: str
    reserved_cash: float
    reserved_risk: float
    session_id: str
    created_at_ms: int
    expires_at_ms: int
    status: str = "ACTIVE"  # "ACTIVE", "COMMITTED", "RELEASED", "EXPIRED"
    envelope: ArtifactEnvelope = field(default_factory=ArtifactEnvelope)


@dataclass(frozen=True)
class OrderIntent:
    """Durable order intent submitted to broker lifecycle boundary."""

    intent_id: str
    plan_id: str
    account_id: str
    contract_symbol: str
    side: str  # "BUY" or "SELL"
    quantity: int
    price: float
    order_type: str  # "LIMIT" or "MARKET"
    status: str = "RESERVED"  # "RESERVED", "SUBMITTING", "ACKNOWLEDGED", "PARTIAL", "FILLED", "REJECTED", "CANCELLED", "UNKNOWN"
    broker_order_id: Optional[str] = None
    filled_quantity: int = 0
    average_price: float = 0.0
    envelope: ArtifactEnvelope = field(default_factory=ArtifactEnvelope)


@dataclass(frozen=True)
class ProtectionIntent:
    """Strategy protection intent attached to an open position."""

    protection_id: str
    position_id: str
    account_id: str
    desired_stop: float
    active_stop: float
    status: str = "NONE"  # "NONE", "REQUIRED", "SUBMITTING", "ACTIVE", "MODIFY_PENDING", "RECONCILIATION_REQUIRED"
    broker_trigger_id: Optional[str] = None
    envelope: ArtifactEnvelope = field(default_factory=ArtifactEnvelope)


@dataclass(frozen=True)
class PositionState:
    """State of an active or closed Snapback position."""

    position_id: str
    account_id: str
    contract_symbol: str
    side: str  # "CE" or "PE"
    confirmed_quantity: int
    remaining_quantity: int
    entry_vwap: float
    active_phase: str  # "OPEN_SCALP", "LOCKED", "RUNNER", "EXIT_REQUIRED", "CLOSED"
    desired_stop: float
    active_stop: float
    target_price: float
    high_water_mark: float
    bars_held: int = 0
    created_at_ms: int = 0
    updated_at_ms: int = 0
    envelope: ArtifactEnvelope = field(default_factory=ArtifactEnvelope)


@dataclass(frozen=True)
class TradeLedger:
    """Finalized or provisional trade accounting entry."""

    trade_id: str
    position_id: str
    confirmed_fills: List[Dict[str, Any]] = field(default_factory=list)
    total_quantity: int = 0
    gross_pnl: float = 0.0
    confirmed_fees: float = 0.0
    net_pnl: float = 0.0
    fee_completeness: str = "COMPLETE"  # "COMPLETE" or "PROVISIONAL"
    envelope: ArtifactEnvelope = field(default_factory=ArtifactEnvelope)


@dataclass(frozen=True)
class ValidationReport:
    """Summary of backtest/replay validation on held-out data."""

    run_id: str
    config_hash: str
    rule_hash: str
    dataset_manifest_hash: str
    total_sessions: int
    total_opportunities: int
    completed_trades: int
    net_expectancy_per_trade: float
    lower_95_ci: float
    win_rate: float
    profit_factor: float
    max_drawdown_pct: float
    is_promotable: bool
    limitations: List[str] = field(default_factory=list)
    envelope: ArtifactEnvelope = field(default_factory=ArtifactEnvelope)
