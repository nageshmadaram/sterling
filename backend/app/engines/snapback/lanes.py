"""Snapback's strategy-mode lanes and their identities.

Maps the engine's legacy mode vocabulary onto the canonical five and produces
a :class:`~app.core.strategy_identity.StrategyModeIdentity` per lane.

One correction lives here. ``manifest.compute_rule_hash`` hashes only the daily
swing rules — lookback, stretch, market filter, delta, hold days, runner,
hedge — and no mode field and no ``scalp_*`` knob. So the scalping and swing
lanes currently produce the *same* rule hash from genuinely different rules,
and a report reading that column would conclude the two lanes ran identical
strategies. The lane rule hash below covers the core rules, the canonical mode,
and the mode's own knobs, so two lanes can never collide.

The manifest hash itself is left untouched: it is bound to a frozen commit and
verified against stored evidence, and changing it would invalidate records that
are correct.
"""
from __future__ import annotations

from typing import Any, Final, Mapping

from app.core.horizon import HorizonMode, canonical_mode, legacy_mode_of
from app.core.strategy_identity import StrategyModeIdentity, build_identity, stable_hash
from app.engines.snapback.manifest import compute_config_hash, compute_rule_hash

STRATEGY_ID: Final[str] = "snapback"
STRATEGY_VERSION: Final[str] = "snapback_core_v1"

#: Legacy engine mode -> canonical mode. ``scalp`` is the only rename.
LEGACY_MODE_MAP: Final[Mapping[str, HorizonMode]] = {
    "swing": HorizonMode.SWING,
    "scalp": HorizonMode.SCALPING,
    "intraday": HorizonMode.INTRADAY,
}

#: Canonical mode -> the engine's own ``trading_mode`` string, for the reverse
#: direction. Modes the engine has never had are absent rather than guessed.
ENGINE_MODE_MAP: Final[Mapping[HorizonMode, str]] = {
    HorizonMode.SWING: "swing",
    HorizonMode.SCALPING: "scalp",
    HorizonMode.INTRADAY: "intraday",
}

MODE_VERSIONS: Final[Mapping[HorizonMode, str]] = {
    HorizonMode.ULTRA_SCALPING: "snapback_ultra_scalping_v0",
    HorizonMode.SCALPING: "snapback_scalping_v1",
    HorizonMode.INTRADAY: "snapback_intraday_v0",
    HorizonMode.OVERNIGHT: "snapback_overnight_v0",
    HorizonMode.SWING: "snapback_swing_v1",
}

#: Config fields that belong to each mode's own rules. A ``v0`` mode has none
#: because its rules do not exist yet; that is why it cannot originate.
_MODE_RULE_FIELDS: Final[Mapping[HorizonMode, tuple[str, ...]]] = {
    HorizonMode.SWING: ("hold_days", "runner_mult", "target_delta"),
    HorizonMode.SCALPING: (
        "scalp_timeframe_minutes",
        "scalp_target_points",
        "scalp_stop_points",
        "scalp_trail_points",
        "scalp_lock_points",
        "scalp_max_hold_bars",
        "scalp_runner_max_bars",
        "scalp_cooldown_bars",
        "scalp_max_trades_per_day",
        "scalp_max_adx",
        "scalp_min_relative_volume",
        "scalp_entry_start_minute",
        "scalp_entry_end_minute",
        "scalp_square_off_minute",
        "scalp_min_net_rr",
    ),
    # Intentionally the same fields as scalping: the runtime genuinely shares
    # them. The canonical mode in the hash is what keeps the two lanes apart,
    # and this lane cannot originate until it has rules of its own.
    HorizonMode.INTRADAY: (),
    HorizonMode.ULTRA_SCALPING: (),
    HorizonMode.OVERNIGHT: (),
}


def canonical_mode_of_engine(engine_mode: str) -> HorizonMode:
    """Canonical mode for one of the engine's own ``trading_mode`` strings."""
    return canonical_mode(engine_mode)


def engine_mode_of(mode: str | HorizonMode) -> str | None:
    """The engine's ``trading_mode`` string, or ``None`` if it has no such mode."""
    return ENGINE_MODE_MAP.get(canonical_mode(mode))


def lane_rule_hash(mode: str | HorizonMode, cfg: Any = None) -> str:
    """Rule hash that distinguishes lanes, unlike the shared manifest hash."""
    resolved = canonical_mode(mode)
    if cfg is None:
        from app.engines.snapback.config import SnapbackConfig

        cfg = SnapbackConfig()
    mode_rules = {
        field: getattr(cfg, field, None)
        for field in _MODE_RULE_FIELDS.get(resolved, ())
    }
    return stable_hash(
        {
            "core": compute_rule_hash(cfg),
            "mode": resolved.value,
            "mode_version": MODE_VERSIONS[resolved],
            "mode_rules": mode_rules,
        }
    )


def identity_for(
    mode: str | HorizonMode,
    *,
    runtime_sha: str,
    release_tag: str,
    cfg: Any = None,
) -> StrategyModeIdentity:
    """Build the authoritative identity for one Snapback lane."""
    resolved = canonical_mode(mode)
    identity = build_identity(
        strategy_id=STRATEGY_ID,
        strategy_version=STRATEGY_VERSION,
        mode=resolved,
        mode_version=MODE_VERSIONS[resolved],
        runtime_sha=runtime_sha,
        release_tag=release_tag,
        config_hash=compute_config_hash(cfg),
        rule_hash=lane_rule_hash(resolved, cfg),
    )
    legacy = legacy_mode_of(mode) if isinstance(mode, str) else None
    if legacy is None and resolved in ENGINE_MODE_MAP:
        engine_name = ENGINE_MODE_MAP[resolved]
        legacy = engine_name if engine_name != resolved.value else None
    if legacy == identity.legacy_mode:
        return identity
    return StrategyModeIdentity(
        strategy_id=identity.strategy_id,
        strategy_version=identity.strategy_version,
        mode=identity.mode,
        mode_version=identity.mode_version,
        runtime_sha=identity.runtime_sha,
        release_tag=identity.release_tag,
        config_hash=identity.config_hash,
        rule_hash=identity.rule_hash,
        evidence_schema_version=identity.evidence_schema_version,
        legacy_mode=legacy,
    )
