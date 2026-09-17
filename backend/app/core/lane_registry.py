"""The ten strategy-mode lanes, their operational state, and the origination gate.

A lane is an experiment, not a UI toggle. It carries its own rules, its own
evidence and its own verdict, and none of that may leak sideways. This module
holds the declared state of each lane and the single function that decides
whether one may open new exposure.

Three independent conditions must all hold before a lane originates:

1. the strategy is in the focused set (:mod:`app.core.focus`);
2. the lane's rules are actually defined — a lane whose entry, exit and risk
   rules have not been written down cannot produce attributable evidence, so
   it must not trade even while its state reads RESEARCH;
3. the lane's state permits origination, and real-money states additionally
   require the global live switch.

Each is checked separately and reports its own reason. Collapsing them into
one boolean is how "why did nothing trade today?" becomes unanswerable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Iterator, Mapping

from app.core.focus import FOCUSABLE, FocusPolicy, focus_policy
from app.core.horizon import HorizonMode, LaneState, canonical_mode

#: Real-money execution is globally disabled until a lane is explicitly
#: promoted by evidence. This is a code-level floor, not a config default: no
#: environment variable and no lane state can lift it on its own.
LIVE_EXECUTION_ENABLED: Final[bool] = False

#: States in which a lane may create new exposure at all.
_ORIGINATING_STATES: Final[frozenset[LaneState]] = frozenset(
    {
        LaneState.PAPER,
        LaneState.SHADOW,
        LaneState.LIVE_MINIMUM,
        LaneState.LIVE_SCALED,
    }
)

#: States that place real capital at risk.
_LIVE_STATES: Final[frozenset[LaneState]] = frozenset(
    {LaneState.LIVE_MINIMUM, LaneState.LIVE_SCALED}
)


@dataclass(frozen=True)
class LaneDefinition:
    """Declared state of one ``(strategy, mode)`` lane."""

    strategy_id: str
    mode: HorizonMode
    state: LaneState

    #: Are this lane's entry, exit, sizing and risk rules written down and
    #: hashable? ``False`` means the lane exists as a plan, not as a strategy.
    rules_defined: bool

    #: Why the lane is where it is. Shown in the dashboard and the doctor
    #: report, so an operator never has to read this file to understand a
    #: refusal.
    note: str

    @property
    def lane_key(self) -> str:
        return f"{self.strategy_id}:{self.mode.value}"


def _lane(strategy: str, mode: HorizonMode, state: LaneState, defined: bool, note: str):
    return LaneDefinition(
        strategy_id=strategy,
        mode=mode,
        state=state,
        rules_defined=defined,
        note=note,
    )


_SNAPBACK: Final[tuple[LaneDefinition, ...]] = (
    _lane(
        "snapback",
        HorizonMode.ULTRA_SCALPING,
        LaneState.RESEARCH,
        False,
        "1m reversal mechanics are sketched, not frozen; no rule hash yet",
    ),
    _lane(
        "snapback",
        HorizonMode.SCALPING,
        LaneState.PAPER,
        True,
        "canonicalised from the legacy 'scalp' path; forward sample starts at zero",
    ),
    _lane(
        "snapback",
        HorizonMode.INTRADAY,
        LaneState.RESEARCH,
        False,
        # There is no separate intraday strategy in the runtime today. The
        # engine branches on `trading_mode in ("scalp", "intraday")` and reads
        # the same scalp_* knobs for both, so "intraday" is the scalping rule
        # wearing a second label. Treating them as two lanes would demand two
        # 300-trade samples from one rule and let the same edge be counted
        # twice. The lane opens once it has rules of its own: a 5-15m signal
        # timeframe and a 45min-4h budget, as specified.
        "no distinct rules yet: shares the scalp_* path and knobs with scalping",
    ),
    _lane(
        "snapback",
        HorizonMode.OVERNIGHT,
        LaneState.RESEARCH,
        False,
        # The specification labels this lane RESEARCH and, in the dashboard
        # mock, DISABLED. Both readings agree on the only thing that matters:
        # it must not trade. It is kept RESEARCH per the lane section, and
        # blocked by rules_defined=False, because gap policy, DTE policy,
        # overnight theta, stop behaviour and hedging are all still undefined.
        "gap, DTE, theta, stop and hedge policy are undefined; cannot be hashed",
    ),
    _lane(
        "snapback",
        HorizonMode.SWING,
        LaneState.PAPER,
        True,
        "the frozen daily lineage; core must not change once forward collection runs",
    ),
)

_SUPERTREND: Final[tuple[LaneDefinition, ...]] = (
    _lane(
        "supertrend",
        HorizonMode.ULTRA_SCALPING,
        LaneState.RESEARCH,
        True,
        "1m Heikin-Ashi, same triple-ST alignment rule; no evidence yet",
    ),
    _lane(
        "supertrend",
        HorizonMode.SCALPING,
        LaneState.RESEARCH,
        True,
        "5m Heikin-Ashi, same triple-ST alignment rule; no evidence yet",
    ),
    _lane(
        "supertrend",
        HorizonMode.INTRADAY,
        LaneState.RESEARCH,
        True,
        "15m Heikin-Ashi; hard square-off 15:20 IST; no evidence yet",
    ),
    _lane(
        "supertrend",
        HorizonMode.OVERNIGHT,
        LaneState.RESEARCH,
        True,
        "60m Heikin-Ashi; gap, theta and weekend exposure evidence still missing",
    ),
    _lane(
        "supertrend",
        HorizonMode.SWING,
        LaneState.RESEARCH,
        True,
        # The 1H engine has run for a long time, but its historical results
        # belong to no canonical lane. Promoting this lane to PAPER on the
        # strength of that history would import a sample this lane never
        # collected. It waits for the freeze audit and its own forward sample.
        "1H engine is the candidate; awaits the parity audit and a fresh sample",
    ),
)

LANES: Final[Mapping[str, LaneDefinition]] = {
    lane.lane_key: lane for lane in (*_SNAPBACK, *_SUPERTREND)
}


class UnknownLane(KeyError):
    """No such strategy-mode lane."""


def lane_key(strategy_id: str, mode: str | HorizonMode) -> str:
    return f"{strategy_id.strip().lower()}:{canonical_mode(mode).value}"


def get_lane(strategy_id: str, mode: str | HorizonMode) -> LaneDefinition:
    key = lane_key(strategy_id, mode)
    try:
        return LANES[key]
    except KeyError:
        raise UnknownLane(key) from None


def all_lanes() -> Iterator[LaneDefinition]:
    yield from LANES.values()


def lanes_for(strategy_id: str) -> tuple[LaneDefinition, ...]:
    name = strategy_id.strip().lower()
    return tuple(lane for lane in LANES.values() if lane.strategy_id == name)


@dataclass(frozen=True)
class OriginationDecision:
    """Whether a lane may open new exposure, and why not when it may not."""

    allowed: bool
    lane_key: str
    #: Stable refusal code, safe to record in evidence and assert on in tests.
    reason: str = ""
    detail: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.allowed


def may_originate(
    strategy_id: str,
    mode: str | HorizonMode,
    *,
    policy: FocusPolicy | None = None,
    live_enabled: bool | None = None,
) -> OriginationDecision:
    """May this lane open new exposure right now?

    Refusals are ordered so the most fundamental reason wins: an unfocused
    strategy is reported as unfocused even if its lane is also disabled, which
    is what the operator needs to act on.
    """
    try:
        lane = get_lane(strategy_id, mode)
    except UnknownLane as exc:
        return OriginationDecision(
            False, str(exc.args[0]), "UNKNOWN_LANE", "no such strategy-mode lane"
        )

    focus = policy if policy is not None else focus_policy()
    if not focus.may_originate(lane.strategy_id):
        return OriginationDecision(
            False,
            lane.lane_key,
            "STRATEGY_NOT_FOCUSED",
            focus.refusal_reason(lane.strategy_id) or "",
        )

    if not lane.rules_defined:
        return OriginationDecision(
            False, lane.lane_key, "LANE_RULES_UNDEFINED", lane.note
        )

    if lane.state not in _ORIGINATING_STATES:
        return OriginationDecision(
            False,
            lane.lane_key,
            "LANE_STATE_BLOCKS_ORIGINATION",
            f"state={lane.state.value}: {lane.note}",
        )

    live = LIVE_EXECUTION_ENABLED if live_enabled is None else live_enabled
    if lane.state in _LIVE_STATES and not live:
        return OriginationDecision(
            False,
            lane.lane_key,
            "LIVE_EXECUTION_DISABLED",
            "real-money execution is globally disabled until promotion",
        )

    return OriginationDecision(True, lane.lane_key)


def dashboard_rows() -> list[dict[str, object]]:
    """One row per lane for the operator dashboard."""
    rows: list[dict[str, object]] = []
    for strategy in sorted(FOCUSABLE):
        for lane in lanes_for(strategy):
            decision = may_originate(lane.strategy_id, lane.mode)
            rows.append(
                {
                    "strategy": lane.strategy_id,
                    "mode": lane.mode.value,
                    "lane_key": lane.lane_key,
                    "state": lane.state.value,
                    "rules_defined": lane.rules_defined,
                    "may_originate": decision.allowed,
                    "reason": decision.reason,
                    "note": lane.note,
                }
            )
    return rows
