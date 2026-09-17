"""The frozen Triple-SuperTrend core, and its five strategy-mode lanes.

Before SuperTrend can be diversified across five modes, the core that exists
today has to be written down and checked. ``FROZEN_CORE`` below is that written
record. It is deliberately a hand-entered literal rather than a read of
:class:`SterlingKiteEngineConfig`'s defaults: comparing the config to itself
would pass no matter what anybody changed. ``audit_core_parity`` compares the
two and reports every disagreement, so an edit to the engine defaults surfaces
as a failed audit instead of as a quietly different strategy.

Only the timeframe and the holding horizon change between the five modes. Any
change to a SuperTrend period, multiplier, candle basis, exit mode or direction
rule is a challenger with its own identity, never an edit to this core.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Mapping

from app.core.horizon import HorizonMode, canonical_mode
from app.core.strategy_identity import StrategyModeIdentity, build_identity, stable_hash
from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig

STRATEGY_ID: Final[str] = "supertrend"
STRATEGY_VERSION: Final[str] = "supertrend_core_v1"

#: The core, as specified. Compared against the runtime config by
#: ``audit_core_parity``; never derived from it.
FROZEN_CORE: Final[Mapping[str, Any]] = {
    "candle_basis": "heikin_ashi",
    "fast": (21, 1.0),
    "mid": (14, 2.0),
    "slow": (7, 3.0),
    "trail_target": "fast",
    "exit_mode": "one_red",
    "allow_short": False,
    "price_stop_exit": True,
    "exit_aligned_trail": False,
    "early_lock": False,
    #: Entry requires all three lines aligned AND a fresh alignment bar. Both
    #: halves matter: alignment alone would re-enter on every bar of a trend.
    "entry_rule": "all_three_aligned_and_fresh_transition",
    #: max(period) across the three lines; the "fast" line has the longest
    #: period because the names track multiplier responsiveness, not length.
    "warmup_bars": 21,
}

#: Signal timeframe per mode. This is the only dimension the five lanes are
#: allowed to differ on while they share ``supertrend_core_v1``.
MODE_SIGNAL_TIMEFRAMES: Final[Mapping[HorizonMode, str]] = {
    HorizonMode.ULTRA_SCALPING: "1m",
    HorizonMode.SCALPING: "5m",
    HorizonMode.INTRADAY: "15m",
    HorizonMode.OVERNIGHT: "60m",
    HorizonMode.SWING: "60m",
}

MODE_VERSIONS: Final[Mapping[HorizonMode, str]] = {
    HorizonMode.ULTRA_SCALPING: "supertrend_ultra_scalping_v1",
    HorizonMode.SCALPING: "supertrend_scalping_v1",
    HorizonMode.INTRADAY: "supertrend_intraday_v1",
    HorizonMode.OVERNIGHT: "supertrend_overnight_v1",
    HorizonMode.SWING: "supertrend_swing_v1",
}

#: The 1H lane that the historical engine actually ran. Recorded so a report
#: can say which lane the old evidence would have belonged to, without that
#: evidence being imported into the lane's forward sample.
HISTORICAL_TIMEFRAME: Final[str] = "60m"


@dataclass(frozen=True)
class ParityFinding:
    """One disagreement between the frozen record and the runtime."""

    field: str
    expected: Any
    actual: Any

    def __str__(self) -> str:  # pragma: no cover - message formatting
        return f"{self.field}: frozen={self.expected!r} runtime={self.actual!r}"


def audit_core_parity(cfg: SterlingKiteEngineConfig | None = None) -> list[ParityFinding]:
    """Compare the runtime config against the frozen record.

    Returns every disagreement rather than the first: a partial report invites
    a fix-one-rerun loop that hides how far the runtime has drifted.
    """
    runtime = cfg or SterlingKiteEngineConfig()
    findings: list[ParityFinding] = []

    for field in (
        "candle_basis",
        "trail_target",
        "exit_mode",
        "allow_short",
        "price_stop_exit",
        "exit_aligned_trail",
        "early_lock",
    ):
        actual = getattr(runtime, field)
        expected = FROZEN_CORE[field]
        if actual != expected:
            findings.append(ParityFinding(field, expected, actual))

    for line in ("fast", "mid", "slow"):
        actual = tuple(getattr(runtime, line))
        expected = tuple(FROZEN_CORE[line])
        if actual != expected:
            findings.append(ParityFinding(line, expected, actual))

    if runtime.warmup != FROZEN_CORE["warmup_bars"]:
        findings.append(
            ParityFinding("warmup", FROZEN_CORE["warmup_bars"], runtime.warmup)
        )

    if runtime.exit_red_count != 1:
        findings.append(ParityFinding("exit_red_count", 1, runtime.exit_red_count))
    if runtime.exit_needs_signal:
        findings.append(ParityFinding("exit_needs_signal", False, True))

    return findings


def core_is_frozen(cfg: SterlingKiteEngineConfig | None = None) -> bool:
    return not audit_core_parity(cfg)


def core_rule_hash() -> str:
    """Hash of the frozen record itself, stable across runtime edits.

    Hashing the live config instead would make the identity change silently
    whenever somebody edited a default — which is exactly the drift the frozen
    record exists to catch.
    """
    payload = {k: list(v) if isinstance(v, tuple) else v for k, v in FROZEN_CORE.items()}
    return stable_hash(payload)


def lane_rule_hash(mode: str | HorizonMode) -> str:
    """Rule hash for one SuperTrend lane: the frozen core plus its timeframe."""
    resolved = canonical_mode(mode)
    return stable_hash(
        {
            "core": core_rule_hash(),
            "core_version": STRATEGY_VERSION,
            "mode": resolved.value,
            "mode_version": MODE_VERSIONS[resolved],
            "signal_timeframe": MODE_SIGNAL_TIMEFRAMES[resolved],
        }
    )


def config_hash(cfg: SterlingKiteEngineConfig | None = None) -> str:
    """Hash of the operational config actually loaded, drift included.

    Distinct from ``core_rule_hash``: the rule hash says which strategy this is,
    the config hash says which settings produced a given row. A drifted runtime
    must still be able to record what it really ran.
    """
    runtime = cfg or SterlingKiteEngineConfig()
    payload = {
        field: getattr(runtime, field)
        for field in sorted(runtime.__dataclass_fields__)
    }
    return stable_hash(payload)


def identity_for(
    mode: str | HorizonMode,
    *,
    runtime_sha: str,
    release_tag: str,
    cfg: SterlingKiteEngineConfig | None = None,
) -> StrategyModeIdentity:
    """Authoritative identity for one SuperTrend lane."""
    resolved = canonical_mode(mode)
    return build_identity(
        strategy_id=STRATEGY_ID,
        strategy_version=STRATEGY_VERSION,
        mode=resolved,
        mode_version=MODE_VERSIONS[resolved],
        runtime_sha=runtime_sha,
        release_tag=release_tag,
        config_hash=config_hash(cfg),
        rule_hash=lane_rule_hash(resolved),
    )
