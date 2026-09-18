"""`supertrend_directional_v1`: the same signal, expressed delta-1.

Sterling's own 7.5-year study over real underlyings found 0/60 long-option
configurations net-positive out-of-sample and none with a profit factor above
1.0, while the same directional signal was modestly positive when represented
as a delta-1 instrument. The honest reading is that the signal is not the thing
that failed — the wrapper is. A bought option has to beat premium decay before
it makes a rupee; a future does not.

So this is a challenger, not an edit. ``supertrend_core_v1`` is untouched: same
Heikin-Ashi basis, same three parameter sets, same fresh-alignment entry, same
trail. What changes is the vehicle, and because the vehicle changes, the
identity changes and the sample starts at zero. Nothing collected by the
long-option lanes may be carried across, in either direction.

Live order submission is disabled here structurally, not by configuration. The
challenger produces shadow intents; there is no method on it that reaches a
broker.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Mapping

from app.core.execution_vehicle import ExecutionVehicle, VehiclePolicy
from app.core.horizon import HorizonMode, canonical_mode
from app.core.strategy_identity import (
    IdentityError,
    StrategyModeIdentity,
    build_identity,
    challenger_id,
    stable_hash,
)
from app.engines.sterling_kite_engine.lanes import (
    MODE_SIGNAL_TIMEFRAMES,
    MODE_VERSIONS,
    STRATEGY_ID,
    STRATEGY_VERSION,
    core_rule_hash,
    config_hash,
)

__all__ = [
    "CHALLENGER_SLUG",
    "CHALLENGER_VERSION",
    "VEHICLE_POLICY",
    "DirectionalChallenger",
    "identity_for",
    "challenger_manifest",
]

#: Names what changed, per the challenger-slug contract.
CHALLENGER_SLUG: Final[str] = "c01_directional_delta1"
CHALLENGER_VERSION: Final[str] = "supertrend_directional_v1"

#: Track C. Never A: this is a challenger to the long-option wrapper, and the
#: two must stay separable forever.
TRACK: Final[str] = "C"

#: Real orders are off at the code level. A challenger with zero trades has no
#: evidence, and a vehicle change is exactly when a system is least entitled to
#: assume its execution assumptions still hold.
LIVE_ORDERS_ENABLED: Final[bool] = False

#: The predeclared vehicle contract. Product and roll are frozen here rather
#: than inherited from the broker: an NRML/MIS default flipping would silently
#: turn a swing lane into an intraday one.
VEHICLE_POLICY: Final[VehiclePolicy] = VehiclePolicy(
    vehicle=ExecutionVehicle.FUTURES,
    instrument_class="INDEX_FUT",
    product="NRML",
    roll_policy="near_month; roll on the expiry-week Tuesday close to the next month",
    allows_short=True,
    research_only=False,
    notes=(
        "Delta-1 representation of supertrend_core_v1. Chosen because the "
        "long-option wrapper was 0/60 OOS net-positive while the underlying "
        "signal was modestly positive delta-1."
    ),
)

#: The first lane to build. One vehicle experiment understood beats five
#: started: Swing is closest to the 1H research the hypothesis came from.
PRIORITY_MODE: Final[HorizonMode] = HorizonMode.SWING

#: What this challenger changes relative to the Track-A lane, in full. Hashed
#: into the rule hash so any edit to the list is a new identity.
CHALLENGER_RULES: Final[Mapping[str, Any]] = {
    "signal_core": STRATEGY_VERSION,
    "execution_vehicle": ExecutionVehicle.FUTURES.value,
    "direction": "bull_signal_long; bear_signal_short",
    "entry": "fresh finalized signal only; no stale or replayed entry",
    "exit": "the frozen trail/exit contract, translated to the vehicle price domain",
    "costs": "statutory fees + brokerage + observed slippage",
    "margin": "broker-observed span/exposure margin only",
    "protection": "predeclared stop; broker-confirmed before any live order",
}


def lane_rule_hash(mode: str | HorizonMode) -> str:
    """The challenger's rule hash: the frozen core, its timeframe, the vehicle."""
    resolved = canonical_mode(mode)
    return stable_hash(
        {
            "core": core_rule_hash(),
            "core_version": STRATEGY_VERSION,
            "mode": resolved.value,
            "mode_version": MODE_VERSIONS[resolved],
            "signal_timeframe": MODE_SIGNAL_TIMEFRAMES[resolved],
            "challenger": CHALLENGER_SLUG,
            "challenger_rules": dict(CHALLENGER_RULES),
            "vehicle_contract_hash": VEHICLE_POLICY.vehicle_contract_hash,
        }
    )


def identity_for(
    mode: str | HorizonMode = PRIORITY_MODE,
    *,
    runtime_sha: str,
    release_tag: str,
) -> StrategyModeIdentity:
    """Authoritative identity for one directional-challenger lane.

    Differs from the Track-A identity in track, mode_version and rule hash, so
    ``identity_hash`` cannot collide with the long-option lane's even for the
    same mode on the same build.
    """
    resolved = canonical_mode(mode)
    base = MODE_VERSIONS[resolved]
    # Validates the slug shape; the built identity uses the same suffix.
    challenger_id(base, CHALLENGER_SLUG)
    return build_identity(
        strategy_id=STRATEGY_ID,
        strategy_version=STRATEGY_VERSION,
        mode=resolved,
        mode_version=base,
        runtime_sha=runtime_sha,
        release_tag=release_tag,
        config_hash=config_hash(),
        rule_hash=lane_rule_hash(resolved),
        track=TRACK,
        challenger=CHALLENGER_SLUG,
    )


class LiveOrdersDisabled(RuntimeError):
    """Something tried to send a real order through the challenger."""


@dataclass(frozen=True)
class DirectionalSignal:
    """One finalized SuperTrend signal, in the underlying's own terms."""

    symbol: str
    direction: str  # "bull" or "bear"
    signal_at: str
    underlying_price: float
    #: True only for a bar that closed. A replayed or forming bar is not an entry.
    finalized: bool = False


@dataclass(frozen=True)
class DirectionalChallenger:
    """Turns a signal into a shadow intent. Cannot reach a broker."""

    mode: HorizonMode = PRIORITY_MODE
    vehicle_policy: VehiclePolicy = VEHICLE_POLICY
    lot_size: int = 1

    @property
    def lane_key(self) -> str:
        return f"{STRATEGY_ID}:{canonical_mode(self.mode).value}"

    @property
    def challenger_key(self) -> str:
        """Lane plus challenger. The grouping key for this experiment's evidence."""
        return f"{self.lane_key}#{CHALLENGER_SLUG}"

    def side_for(self, signal: DirectionalSignal) -> str:
        direction = signal.direction.strip().lower()
        if direction == "bull":
            return "BUY"
        if direction == "bear":
            if not self.vehicle_policy.allows_short:
                raise IdentityError(
                    "the vehicle does not permit shorting, so a bear signal has no "
                    "expression; declare a different vehicle rather than skipping it"
                )
            return "SELL"
        raise IdentityError(f"unknown signal direction {signal.direction!r}")

    def shadow_intent(
        self,
        signal: DirectionalSignal,
        *,
        session_date: str,
        contract: str | None,
        quantity: int | None = None,
        selected_at: str | None = None,
    ):
        """Build a :class:`~app.services.shadow_execution.ShadowIntent`.

        Refuses a non-finalized signal. Entering on a forming bar is how a
        backtest's edge evaporates live, and the frozen core's entry rule is
        explicit that only a fresh *finalized* alignment counts.
        """
        from app.services.shadow_execution import ShadowIntent

        if not signal.finalized:
            raise IdentityError(
                f"{signal.symbol}: signal at {signal.signal_at} is not finalized; "
                "the challenger does not enter on a forming bar"
            )
        side = self.side_for(signal)
        return ShadowIntent(
            lane_key=self.challenger_key,
            session_date=session_date,
            signal_at=signal.signal_at,
            contract=contract,
            quantity=int(quantity or self.lot_size),
            reference_price=signal.underlying_price,
            execution_vehicle=self.vehicle_policy.vehicle,
            selected_at=selected_at,
            notes=f"{side} {signal.symbol} ({self.vehicle_policy.instrument_class})",
        )

    def place_order(self, *_: Any, **__: Any):
        """Always refuses. Present so the refusal is explicit, not an AttributeError."""
        raise LiveOrdersDisabled(
            "supertrend_directional_v1 has zero evidence and live orders are "
            "disabled; it originates shadow intents only"
        )


def challenger_manifest(*, runtime_sha: str, release_tag: str, mode: str | HorizonMode = PRIORITY_MODE) -> dict[str, Any]:
    """The manifest row for this challenger, vehicle fields included."""
    identity = identity_for(mode, runtime_sha=runtime_sha, release_tag=release_tag)
    row: dict[str, Any] = {
        "challenger": CHALLENGER_SLUG,
        "challenger_version": CHALLENGER_VERSION,
        "signal_core": STRATEGY_VERSION,
        "track": TRACK,
        "sample": 0,
        "live_orders": "DISABLED" if not LIVE_ORDERS_ENABLED else "ENABLED",
        "rules": dict(CHALLENGER_RULES),
        "identity": identity.as_row(),
    }
    row.update(VEHICLE_POLICY.as_row())
    return row
