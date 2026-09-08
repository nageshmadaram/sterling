"""Shared exit counter logic for red-line based exits + trailing.

Extracted for unification between:
- sterling_kite_engine (real 3 ST on HA)
- directional (faked st_trends as all-same for compatibility)

Allows consistent:
- threshold from mode
- should_exit decision (with optional counter-signal for _signal modes)
- reason generation
- health display

Used by engine.manage, monitor.on_tick, scanner is_active, future directional.
"""
from typing import Literal

ExitMode = Literal["one_red", "two_red", "three_red", "three_red_signal"]


def get_exit_threshold(mode: ExitMode) -> int:
    """Number of red lines needed to trigger exit."""
    return {"one_red": 1, "two_red": 2, "three_red": 3, "three_red_signal": 3}[mode]


def exit_needs_counter_signal(mode: ExitMode) -> bool:
    """True for modes that also require a fresh opposing arrow."""
    return mode == "three_red_signal"


def should_exit_on_reds(
    red_count: int,
    mode: ExitMode,
    has_counter_arrow: bool = False,
) -> bool:
    """Core decision: enough reds (and optional arrow) to exit."""
    thresh = get_exit_threshold(mode)
    if red_count < thresh:
        return False
    if exit_needs_counter_signal(mode):
        return has_counter_arrow
    return True


def get_exit_reason(red_count: int, mode: ExitMode, base: str = "red_line") -> str:
    """Human + machine readable reason."""
    if mode == "three_red_signal":
        return "three_red_signal_exit"
    return f"{mode}_exit"  # one_red_exit, two_red_exit, three_red_exit


def compute_red_count_from_trends(trends: list[int], direction: str) -> int:
    """Generic: count reds against direction.
    trends: list of +1/-1 (e.g. st_trends or t_fast etc.)
    For long: against = -1
    For short: against = +1
    """
    against = -1 if direction == "long" else 1
    against = -1 if str(direction).lower() == "long" else 1
    return sum(1 for t in trends if t == against)


# Convenience for old directional (where st_trends are [trend, trend, trend])
def red_count_from_all_red(all_red: bool, direction: str) -> int:
    """Map legacy all_red to red_count (0 or 3)."""
    if not all_red:
        return 0
    # all red means full 3 against
    return 3
