"""Sterling Kite Adaptive Edge Engine.

This package owns the strategy-specific decision model. It must remain
independent from SuperTrend and Flow Navigator strategy implementations while
using Sterling Kite's shared execution, account, and safety infrastructure.
"""

ENGINE_NAME = "Adaptive Edge"
ENGINE_ID = "adaptive_edge"
ENGINE_VERSION = "0.1.0"

#: Shared strategy vocabulary, identical in shape to every other engine here so
#: the settings hub, the board and the config API address this engine the same
#: way they address the others.
STRATEGY_ID = ENGINE_ID
STRATEGY_NAME = ENGINE_NAME
#: The authoritative source artifact this engine implements.
CONTRACT_VERSION = "MS-1.0"

from .config import (
    AdaptiveEdgeConfig,
    CALIBRATED_FIELDS,
    DATA_SOURCES,
    DECISION_TIMEFRAMES,
    EXIT_POLICIES,
    PARAMETER_PROVENANCE,
    SIZING_MODES,
    STOP_MODES,
    STRATEGY_VERSIONS,
)

from .execution_ordering import (
    DeterministicExecutionSequencer,
    ExecutionConflictError,
    ExecutionOrderingError,
    OrderExecutionTracker,
    OrderLifecycleState,
)
from .lifecycle_engine import (
    A126LifecycleEngine,
    HorizonState,
    LifecycleAction,
    LifecycleEvidence,
    OverlayState,
    ProtectionState,
    ThesisState,
    TransitionRecord,
)
from .position_projector import (
    DeterministicPositionProjector,
    FillRecord,
    PositionInvariantError,
)
from .replay import ReplayResult, replay_trace, validate_audit_chain
from .risk_sizing import (
    ExecutionCostParameters,
    ParameterEstimationMethod,
    ParameterGovernanceError,
    ParameterMetadata,
    ParameterValidationStatus,
    PositionSizingAssessment,
    RiskPerUnitAssessment,
    SizingParameters,
    calculate_position_sizing,
    calculate_risk_per_unit,
)

__all__ = [
    "ENGINE_NAME",
    "ENGINE_ID",
    "ENGINE_VERSION",
    "DeterministicPositionProjector",
    "PositionInvariantError",
    "FillRecord",
    "DeterministicExecutionSequencer",
    "OrderExecutionTracker",
    "OrderLifecycleState",
    "ExecutionConflictError",
    "ExecutionOrderingError",
    "A126LifecycleEngine",
    "HorizonState",
    "ThesisState",
    "ProtectionState",
    "OverlayState",
    "LifecycleAction",
    "LifecycleEvidence",
    "TransitionRecord",
    "ReplayResult",
    "replay_trace",
    "validate_audit_chain",
    "ParameterValidationStatus",
    "ParameterEstimationMethod",
    "ParameterGovernanceError",
    "ParameterMetadata",
    "ExecutionCostParameters",
    "SizingParameters",
    "RiskPerUnitAssessment",
    "PositionSizingAssessment",
    "calculate_risk_per_unit",
    "calculate_position_sizing",
]

