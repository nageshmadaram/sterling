"""Canonical five-mode horizon taxonomy shared by Snapback and SuperTrend.

Sterling historically grew three separate mode vocabularies:

* ``app.core.trading_mode.MODES``      — scalping / intraday / swing / positional
* ``app.engines.snapback.config``      — swing / scalp / intraday
* ad-hoc strings in API payloads and stored evidence rows

That is fine for display, and fatal for evidence. Two rows labelled ``scalp``
and ``scalping`` are the same experiment; two rows labelled ``positional`` and
``swing`` are NOT, and merging them silently manufactures a sample size nobody
measured. This module is the single canonical vocabulary that authoritative
evidence must use, plus the narrow, explicit alias table that maps the legacy
spellings onto it.

Nothing here changes strategy behaviour. It defines names, time budgets and
the refusal rules around them.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Mapping


class HorizonMode(StrEnum):
    """The only mode values an authoritative evidence row may carry."""

    ULTRA_SCALPING = "ultra_scalping"
    SCALPING = "scalping"
    INTRADAY = "intraday"
    OVERNIGHT = "overnight"
    SWING = "swing"


class TimelineState(StrEnum):
    """Where an open position sits inside its own declared time budget."""

    EARLY = "early"
    EXPECTED = "expected"
    EXTENDED = "extended"
    HARD_EXIT_DUE = "hard_exit_due"
    CLOSED = "closed"
    #: The horizon could not be computed. Never permits new exposure.
    UNKNOWN = "unknown"


class LaneState(StrEnum):
    """Operational state of one ``(strategy, mode)`` lane, independent of P&L."""

    DISABLED = "disabled"
    RESEARCH = "research"
    PAPER = "paper"
    SHADOW = "shadow"
    LIVE_MINIMUM = "live_minimum"
    LIVE_SCALED = "live_scaled"
    BLOCKED = "blocked"


class UnknownMode(ValueError):
    """A mode string that is neither canonical nor a declared alias.

    Raised rather than defaulted: guessing a mode assigns a trade to the wrong
    experiment, which is worse than refusing to record it.
    """


class AmbiguousLegacyMode(UnknownMode):
    """A legacy mode whose canonical meaning is genuinely undecided.

    ``positional`` is the live example. It predates the split between
    ``overnight`` (1–3 sessions) and ``swing`` (3–10 sessions) and the old
    config does not say which it meant, so neither mapping can be justified
    from the data. Mapping it either way would fabricate the very distinction
    the five-mode model exists to measure.
    """


#: Legacy spellings that have exactly one defensible canonical meaning.
#: Anything not listed here is either canonical already or refused.
_MODE_ALIASES: Mapping[str, HorizonMode] = {
    "scalp": HorizonMode.SCALPING,
    "scalping": HorizonMode.SCALPING,
    "ultra": HorizonMode.ULTRA_SCALPING,
    "ultra_scalp": HorizonMode.ULTRA_SCALPING,
    "ultrascalping": HorizonMode.ULTRA_SCALPING,
    "day": HorizonMode.INTRADAY,
    "intraday": HorizonMode.INTRADAY,
    "overnight": HorizonMode.OVERNIGHT,
    "overnight_trade": HorizonMode.OVERNIGHT,
    "swing": HorizonMode.SWING,
}

#: Legacy spellings that are known, but deliberately NOT mapped.
_AMBIGUOUS_LEGACY: frozenset[str] = frozenset({"positional", "position"})


def canonical_mode(value: str) -> HorizonMode:
    """Map a mode string onto the canonical enum, or refuse.

    Refusal is the point. ``positional`` raises :class:`AmbiguousLegacyMode`
    instead of quietly becoming ``swing``.
    """
    if isinstance(value, HorizonMode):
        return value
    key = (value or "").strip().lower()
    if key in _AMBIGUOUS_LEGACY:
        raise AmbiguousLegacyMode(
            f"legacy mode {value!r} has no single canonical meaning: it predates "
            "the overnight/swing split. Re-declare the lane explicitly."
        )
    try:
        return HorizonMode(key)
    except ValueError:
        pass
    try:
        return _MODE_ALIASES[key]
    except KeyError:
        raise UnknownMode(f"unknown trading mode {value!r}") from None


def try_canonical_mode(value: str) -> HorizonMode | None:
    """Non-raising variant, for display paths that must not blow up."""
    try:
        return canonical_mode(value)
    except UnknownMode:
        return None


def legacy_mode_of(value: str) -> str | None:
    """The original spelling, kept alongside the canonical mode for traceability.

    Returns ``None`` when the input was already canonical, so callers persist a
    ``legacy_mode`` column only when it carries information.
    """
    key = (value or "").strip().lower()
    canonical = try_canonical_mode(key)
    if canonical is None:
        return None
    return None if key == canonical.value else key


DurationUnit = Literal["seconds", "trading_sessions"]

#: Hard square-off for every mode that may not carry risk overnight (IST).
#: A strategy-specific rule may be stricter; it may never be looser.
SESSION_SQUARE_OFF_IST = "15:20"

#: NSE/BSE regular session length used as the intraday hard ceiling. In
#: practice ``force_close_time`` binds first; this exists so an entry recorded
#: with a broken clock still has a finite hard limit.
_SESSION_SECONDS = 6 * 3600 + 5 * 60  # 09:15 -> 15:20 IST


@dataclass(frozen=True)
class HorizonModeConfig:
    """The time-budget contract of one mode.

    This is a *budget*, not a profit target. A trade may exit far earlier for a
    stop, a reversal, a risk limit or a broker problem. ``hard_hold_limit`` is
    the latest permitted holding time, expressed in ``duration_unit``.

    Strategy rules do not belong here. A strategy extends this with its own
    frozen config, and both hash into the lane identity.
    """

    mode: HorizonMode

    signal_timeframe: str
    execution_timeframe: str

    expected_hold_min: int
    expected_hold_max: int

    duration_unit: DurationUnit

    hard_hold_limit: int

    allow_overnight: bool
    force_close_time: str | None

    review_interval_seconds: int

    max_new_entries_per_session: int
    max_concurrent_positions: int

    version: str

    def __post_init__(self) -> None:
        if self.expected_hold_min > self.expected_hold_max:
            raise ValueError(
                f"{self.mode}: expected_hold_min exceeds expected_hold_max"
            )
        if self.hard_hold_limit < self.expected_hold_max:
            raise ValueError(
                f"{self.mode}: hard_hold_limit is below the expected window; the "
                "budget would expire before the strategy is designed to finish"
            )
        if not self.allow_overnight and self.force_close_time is None:
            raise ValueError(
                f"{self.mode}: forbids overnight risk but declares no square-off "
                "time, so nothing would force the position flat"
            )


_MIN = 60

#: The canonical mode timelines. Values come straight from the five-mode
#: specification; changing one is a new ``mode_version``, never an edit.
MODE_TIMELINES: Mapping[HorizonMode, HorizonModeConfig] = {
    HorizonMode.ULTRA_SCALPING: HorizonModeConfig(
        mode=HorizonMode.ULTRA_SCALPING,
        signal_timeframe="1m",
        execution_timeframe="tick",
        expected_hold_min=1 * _MIN,
        expected_hold_max=10 * _MIN,
        duration_unit="seconds",
        hard_hold_limit=20 * _MIN,
        allow_overnight=False,
        force_close_time=SESSION_SQUARE_OFF_IST,
        review_interval_seconds=5,
        max_new_entries_per_session=12,
        max_concurrent_positions=1,
        version="ultra_scalping_v1",
    ),
    HorizonMode.SCALPING: HorizonModeConfig(
        mode=HorizonMode.SCALPING,
        signal_timeframe="5m",
        execution_timeframe="1m",
        expected_hold_min=10 * _MIN,
        expected_hold_max=45 * _MIN,
        duration_unit="seconds",
        hard_hold_limit=90 * _MIN,
        allow_overnight=False,
        force_close_time=SESSION_SQUARE_OFF_IST,
        review_interval_seconds=15,
        max_new_entries_per_session=6,
        max_concurrent_positions=2,
        version="scalping_v1",
    ),
    HorizonMode.INTRADAY: HorizonModeConfig(
        mode=HorizonMode.INTRADAY,
        signal_timeframe="15m",
        execution_timeframe="1m",
        expected_hold_min=45 * _MIN,
        expected_hold_max=4 * 3600,
        duration_unit="seconds",
        # The session square-off below binds first on any normal day.
        hard_hold_limit=_SESSION_SECONDS,
        allow_overnight=False,
        force_close_time=SESSION_SQUARE_OFF_IST,
        review_interval_seconds=60,
        max_new_entries_per_session=3,
        max_concurrent_positions=2,
        version="intraday_v1",
    ),
    HorizonMode.OVERNIGHT: HorizonModeConfig(
        mode=HorizonMode.OVERNIGHT,
        signal_timeframe="60m",
        execution_timeframe="1m",
        expected_hold_min=1,
        expected_hold_max=3,
        duration_unit="trading_sessions",
        hard_hold_limit=5,
        allow_overnight=True,
        force_close_time=None,
        review_interval_seconds=300,
        max_new_entries_per_session=2,
        max_concurrent_positions=3,
        version="overnight_v1",
    ),
    HorizonMode.SWING: HorizonModeConfig(
        mode=HorizonMode.SWING,
        signal_timeframe="60m",
        execution_timeframe="1m",
        expected_hold_min=3,
        expected_hold_max=10,
        duration_unit="trading_sessions",
        hard_hold_limit=15,
        allow_overnight=True,
        force_close_time=None,
        review_interval_seconds=900,
        max_new_entries_per_session=2,
        max_concurrent_positions=4,
        version="swing_v1",
    ),
}


def timeline_for(mode: HorizonMode | str) -> HorizonModeConfig:
    """The declared time budget of a mode. Raises on an unknown mode."""
    return MODE_TIMELINES[canonical_mode(mode)]


def session_bound_modes() -> frozenset[HorizonMode]:
    """Modes that must be flat by the session square-off.

    An exit failure in one of these is ``EXIT_FAILED`` / unresolved exposure.
    It is never a promotion to :attr:`HorizonMode.OVERNIGHT`: a position that
    survives the night because the exit broke is a defect, and relabelling it
    would hide the defect inside a different experiment's statistics.
    """
    return frozenset(
        m for m, cfg in MODE_TIMELINES.items() if not cfg.allow_overnight
    )
