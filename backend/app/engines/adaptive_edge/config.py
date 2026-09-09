"""Configuration for the Adaptive Edge strategy.

Unlike the Gamma Move engine, **none of these numbers are calibrated**, and this
file does not pretend otherwise. That is not an oversight — it is what the
authoritative source demands. The Master Mathematical Specification
(``adaptive-edge/Adaptive Order-Flow Options Scalping and Intraday Strategy.md``,
Version 1.0) §19 states the rule directly:

    No fixed universal threshold ... is used unless that threshold survives
    walk-forward validation and is demonstrably robust.

and §51–§55 place every numeric parameter under walk-forward learning rather
than under specification. So the spec gives *structure* — which gates exist,
what must hold, the invariants and the state machine — and deliberately withholds
the numbers.

The consequence for this file: every value below is a **research default chosen
to be inert or conservative**, present so the engine can run and be calibrated,
never because it was measured. :data:`PARAMETER_PROVENANCE` records that for each
one, and the API publishes it, so the UI can say "uncalibrated" beside a number
instead of letting a reader assume it means something.

This is why the engine ships PAPER-only. ``promotion.py`` holds the strategy at
RESEARCH_ONLY, and A166 forbids live execution until
``research_validation_complete`` — the calibration these defaults are waiting
for. Flipping that gate without the calibration would be putting money on the
numbers in this file, and the numbers in this file are placeholders.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Optional

from app.engines.option_contracts import EXPIRY_SELECTIONS, EXPIRY_SERIES

#: The bar the strategy makes decisions on. The source is a scalping strategy
#: driven by order flow, so sub-minute is where it belongs; minute is Kite's
#: history floor, which is why it is the default until a tick feed is wired.
DECISION_TIMEFRAMES: frozenset[str] = frozenset({"minute", "3minute", "5minute", "15minute"})
EXIT_POLICIES: frozenset[str] = frozenset({"TIME_STOP", "TARGET_STOP", "PROTECTION_TRAIL"})
SIZING_MODES: frozenset[str] = frozenset({"LOTS", "RISK_PCT"})
STRATEGY_VERSIONS: frozenset[str] = frozenset({"v1_baseline", "v2_hardened"})
#: Same vocabulary and default as the SuperTrend and Gamma Move engines.
#: ``broker`` is a GTT that survives this process dying, ``monitor`` is our own
#: tick loop which exits intrabar but only while we are alive, ``both`` is the
#: production answer.
STOP_MODES: frozenset[str] = frozenset({"broker", "monitor", "both"})
#: The strategy's own §8–§11 need aggressor-classified trade prints. Kite gives
#: MODE_FULL ticks without an aggressor flag, so ``kite`` runs the engine in its
#: degraded, quote-derived mode and ``truedata`` is the path to the real thing.
DATA_SOURCES: frozenset[str] = frozenset({"kite", "truedata"})

#: Why each number is what it is. Everything here reads "research default" on
#: purpose: the spec defers all of them to calibration, so any other word would
#: be a claim nobody has earned. Compare Gamma Move's CALIBRATION map, where the
#: same structure carries measured values and sample sizes.
PARAMETER_PROVENANCE: dict[str, str] = {
    "status": "UNCALIBRATED — every value is a research default pending walk-forward validation (Master Spec §19, §51-55)",
    "edge_threshold": "research default; §21 defines directional edge, not its cut-off",
    "min_conservative_ev": "0.0 — the one value the source does fix: §35 requires ConservativeEV > 0",
    "min_expected_net_value": "0.0 — §34/§35, EV must be positive; the margin above zero is uncalibrated",
    "restricted_volatility_ratio": "research default; §27 defines regime states, not their boundaries",
    "disabled_volatility_ratio": "research default; §27",
    "horizon_bars": "research default; §28 makes the horizon a fitted distribution, not a constant",
    "max_spread_pct": "research default liquidity guard; §35 LiquidityOK/SlippageOK are gates without numbers",
    "stop_percent": "research default; §36 defines InitialRisk structurally",
    "profit_lock_fraction": "research default; §40 defines backward profit protection structurally",
}

#: Nothing is calibrated, so this is empty — and is the honest counterpart to
#: Gamma Move's CALIBRATED_FIELDS. The UI keys off it to mark every field
#: uncalibrated rather than silently showing bare numbers.
CALIBRATED_FIELDS: frozenset[str] = frozenset()


def _hhmm(value: str, label: str) -> str:
    parts = str(value).split(":")
    if len(parts) != 2:
        raise ValueError(f"{label} must be HH:MM")
    try:
        hh, mm = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError(f"{label} must be HH:MM") from exc
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"{label} must be a valid HH:MM time")
    return f"{hh:02d}:{mm:02d}"


@dataclass(frozen=True)
class AdaptiveEdgeConfig:
    """Immutable strategy configuration. Construct, then :meth:`validate`."""

    #: Whether this engine scans and may trade. Defaults ON, like every other
    #: engine here. It is not the safety device: the account's paper/live
    #: setting, the engine's manual/auto setting, the kill switch, the risk caps
    #: below, and — uniquely for this engine — the promotion gate all apply
    #: whatever this is set to. With the account on PAPER, an enabled engine
    #: paper-trades; it cannot reach real money while promotion is RESEARCH_ONLY.
    enabled: bool = True
    auto_execute: bool = False
    #: Strategy version: "v1_baseline" (Legacy baseline) vs "v2_hardened" (Production-Hardened)
    strategy_version: str = "v2_hardened"

    # --- universe -----------------------------------------------------------
    #: Same field names, semantics and curated-registry boundary as every other
    #: engine. The source is an index-and-liquid-stock options strategy; indices
    #: lead because their chains carry the depth the order-flow features need.
    #: NIFTY only, and that is a finding rather than a preference. BANKNIFTY
    #: weeklies were discontinued, so its nearest expiry sits ~33 days out while
    #: this strategy holds for `horizon_bars` minutes — it produced zero
    #: candidates on every scan and only a generic "no contract in the windows"
    #: to explain it. Widening `max_dte` to reach it would price a different
    #: instrument than the one the volatility forecast is calibrated on.
    scan_indices: tuple[str, ...] = ("NIFTY",)
    scan_stocks: tuple[str, ...] = ()
    scan_all_stocks: bool = False
    stock_contracts: bool = False
    min_option_oi: int = 50_000
    min_option_volume: int = 1_000
    #: Below this the tick is a large fraction of the price, so premium-change
    #: features measure tick granularity rather than market movement — the same
    #: trap that produced penny-option candidates in the Gamma Move calibration.
    min_option_premium: float = 10.0
    max_spread_pct: float = 3.0

    # --- contracts ----------------------------------------------------------
    #: Shared contract vocabulary — identical names, order and meaning to the
    #: other engines, so the Option-contracts picker is the same control here.
    expiry_selection: str = "nearest"
    expiry_dte_min: int = 0
    expiry_dte_max: int = 7
    #: §16 options state degenerates into settlement mechanics on expiry day,
    #: and an intraday scalp has no room for that.
    avoid_expiry_day: bool = True
    scan_expiries_indices: tuple[str, ...] = ("weekly", "monthly")
    scan_expiries_stocks: tuple[str, ...] = ("monthly",)
    scan_weekly_series_indices: tuple[int, ...] = (0, 1)
    scan_monthly_series_indices: tuple[int, ...] = (0,)
    scan_monthly_series_stocks: tuple[int, ...] = (0,)
    strike_window_pct: float = 2.0
    max_candidates: int = 25

    # --- data ---------------------------------------------------------------
    data_source: str = "kite"
    decision_timeframe: str = "minute"
    #: §5 event validation and §182 data quality: a decision built on stale
    #: state is a decision about the past. Fail closed rather than trade it.
    max_quote_age_seconds: int = 20
    min_chain_completeness: float = 0.8

    # --- features / probability (§18-§26) -----------------------------------
    feature_lookback_bars: int = 120
    #: §19 wants a conditional historical percentile, which needs a warm window
    #: before any normalized value means anything. Until then the engine is
    #: WARMING and emits nothing.
    normalization_warmup_bars: int = 60
    edge_threshold: float = 0.15
    horizon_bars: int = 15

    # --- regime (§27) -------------------------------------------------------
    restricted_volatility_ratio: float = 1.5
    disabled_volatility_ratio: float = 2.5
    restricted_drawdown_fraction: float = 0.03
    disabled_drawdown_fraction: float = 0.05

    # --- economics (§31-§35) ------------------------------------------------
    #: The two values the source *does* fix. §35 requires both EV and
    #: ConservativeEV strictly positive for any BUY_CE/BUY_PE, so these are
    #: structural, not tuned — which is why they appear in PARAMETER_PROVENANCE
    #: as the exceptions.
    min_expected_net_value: float = 0.0
    min_conservative_ev: float = 0.0
    fee_rate: float = 0.001
    slippage_bps: float = 5.0

    # --- risk / sizing (§36, §176) ------------------------------------------
    sizing_mode: str = "LOTS"
    lots: int = 1
    risk_pct: float = 0.5
    max_positions: int = 1
    max_daily_loss: float = 0.0
    stop_percent: float = 30.0
    stop_mode: str = "both"
    target_multiple: float = 2.0
    profit_lock_fraction: float = 0.5
    exit_policy: str = "PROTECTION_TRAIL"
    max_holding_bars: int = 60

    # --- session (§17, §49) -------------------------------------------------
    session_start: str = "09:20"
    #: §49 session termination. Flat before the close rather than carrying an
    #: intraday scalp into settlement.
    session_end: str = "15:10"
    square_off_time: str = "15:15"

    @classmethod
    def field_names(cls) -> frozenset[str]:
        return frozenset(f.name for f in fields(cls))

    def as_dict(self) -> dict[str, Any]:
        """JSON-round-trippable form. Tuples become lists; ``get_config`` puts
        them back, so a stored config compares equal to the one that wrote it."""
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            out[f.name] = list(value) if isinstance(value, tuple) else value
        return out

    def validate(self) -> "AdaptiveEdgeConfig":
        """Reject a configuration the engine cannot honestly run.

        Validation lives here rather than at the API boundary so a config
        persisted by an older build, or edited straight in the database, cannot
        become a trading config the engine silently disagrees with.
        """
        if self.decision_timeframe not in DECISION_TIMEFRAMES:
            raise ValueError(f"decision_timeframe must be one of {sorted(DECISION_TIMEFRAMES)}")
        if self.data_source not in DATA_SOURCES:
            raise ValueError(f"data_source must be one of {sorted(DATA_SOURCES)}")
        if self.exit_policy not in EXIT_POLICIES:
            raise ValueError(f"exit_policy must be one of {sorted(EXIT_POLICIES)}")
        if self.sizing_mode not in SIZING_MODES:
            raise ValueError(f"sizing_mode must be one of {sorted(SIZING_MODES)}")
        if self.stop_mode not in STOP_MODES:
            raise ValueError(f"stop_mode must be one of {sorted(STOP_MODES)}")
        if self.expiry_selection not in EXPIRY_SELECTIONS:
            raise ValueError(f"expiry_selection must be one of {sorted(EXPIRY_SELECTIONS)}")
        if self.strategy_version not in STRATEGY_VERSIONS:
            raise ValueError(f"strategy_version must be one of {sorted(STRATEGY_VERSIONS)}")

        if not self.scan_indices and not self.scan_stocks and not self.scan_all_stocks:
            raise ValueError("at least one of scan_indices, scan_stocks or scan_all_stocks is required")

        # An expiry window that cannot contain a contract disables the strategy
        # while looking like a filter — the recurring defect in this codebase.
        if self.expiry_dte_min < 0:
            raise ValueError("expiry_dte_min cannot be negative")
        if self.expiry_dte_max < self.expiry_dte_min:
            raise ValueError("expiry_dte_max must be >= expiry_dte_min")
        if self.avoid_expiry_day and self.expiry_dte_max < 1:
            raise ValueError(
                "avoid_expiry_day with expiry_dte_max = 0 excludes every contract; "
                "raise expiry_dte_max to at least 1 or turn avoid_expiry_day off"
            )

        if not 0 <= self.edge_threshold <= 1:
            raise ValueError("edge_threshold must be in [0, 1]")
        if self.horizon_bars <= 0:
            raise ValueError("horizon_bars must be positive")
        if self.feature_lookback_bars <= 0:
            raise ValueError("feature_lookback_bars must be positive")
        if self.normalization_warmup_bars <= 0:
            raise ValueError("normalization_warmup_bars must be positive")
        if self.normalization_warmup_bars > self.feature_lookback_bars:
            raise ValueError("normalization_warmup_bars cannot exceed feature_lookback_bars")

        if self.disabled_volatility_ratio < self.restricted_volatility_ratio:
            raise ValueError("disabled_volatility_ratio must be >= restricted_volatility_ratio")
        if self.disabled_drawdown_fraction < self.restricted_drawdown_fraction:
            raise ValueError("disabled_drawdown_fraction must be >= restricted_drawdown_fraction")

        # §35 makes both strictly positive a mandatory entry gate. A negative
        # floor would authorize trades the source forbids outright.
        if self.min_expected_net_value < 0:
            raise ValueError("min_expected_net_value cannot be negative (Master Spec §35 requires EV > 0)")
        if self.min_conservative_ev < 0:
            raise ValueError("min_conservative_ev cannot be negative (Master Spec §35 requires ConservativeEV > 0)")

        if self.fee_rate < 0 or self.slippage_bps < 0:
            raise ValueError("fee_rate and slippage_bps cannot be negative")
        if not 0 < self.stop_percent <= 100:
            raise ValueError("stop_percent must be in (0, 100]")
        if self.target_multiple <= 0:
            raise ValueError("target_multiple must be positive")
        if not 0 <= self.profit_lock_fraction <= 1:
            raise ValueError("profit_lock_fraction must be in [0, 1]")
        if self.lots < 1:
            raise ValueError("lots must be at least 1")
        if not 0 < self.risk_pct <= 100:
            raise ValueError("risk_pct must be in (0, 100]")
        if self.max_positions < 1:
            raise ValueError("max_positions must be at least 1")
        if self.max_daily_loss < 0:
            raise ValueError("max_daily_loss cannot be negative")
        if self.max_holding_bars < 1:
            raise ValueError("max_holding_bars must be at least 1")
        if self.max_quote_age_seconds < 1:
            raise ValueError("max_quote_age_seconds must be at least 1")
        if not 0 < self.min_chain_completeness <= 1:
            raise ValueError("min_chain_completeness must be in (0, 1]")
        if self.min_option_premium <= 0:
            raise ValueError("min_option_premium must be positive")
        if self.max_spread_pct <= 0:
            raise ValueError("max_spread_pct must be positive")
        if self.max_candidates < 1:
            raise ValueError("max_candidates must be at least 1")
        if self.strike_window_pct <= 0:
            raise ValueError("strike_window_pct must be positive")

        for label in ("session_start", "session_end", "square_off_time"):
            _hhmm(getattr(self, label), label)
        if self.session_end <= self.session_start:
            raise ValueError("session_end must be after session_start")
        if self.square_off_time < self.session_end:
            raise ValueError("square_off_time must be at or after session_end")

        return self

    def warnings(self) -> list[str]:
        """Configured risks worth saying out loud, in plain sentences.

        These are not validation errors — every one is a legitimate choice — but
        each changes what the engine will do with money, so it is stated rather
        than left for the operator to infer from the numbers.
        """
        out: list[str] = []
        out.append(
            "No parameter in this configuration has been walk-forward calibrated. "
            "The Master Specification (§19) forbids treating any threshold as valid "
            "until it survives validation, so these are research defaults."
        )
        if self.data_source == "kite":
            out.append(
                "Kite ticks carry no aggressor flag, so the order-flow features the "
                "strategy is built on (§8-§11) run in a degraded, quote-derived form."
            )
        if self.max_daily_loss == 0:
            out.append("No daily loss cap is set, so a losing day is bounded only by per-trade stops.")
        if self.stop_mode == "monitor":
            out.append(
                "The stop is held in this process only. If it dies the position is "
                "left unprotected at the broker; 'both' keeps a GTT as well."
            )
        if self.max_positions > 1:
            out.append(f"Up to {self.max_positions} positions may be open at once, so risk adds up across them.")
        if not self.avoid_expiry_day:
            out.append("Expiry-day contracts are eligible, where the options state is settlement mechanics.")
        return out


def config_fields() -> tuple[str, ...]:
    return tuple(f.name for f in fields(AdaptiveEdgeConfig))
