"""Canonical execution-policy constants shared by backtest, paper, live and replay.

These are evidence-affecting and were previously hard-coded in more than one place,
which is how the runtime drifted from the validated rule: the peak ratcheted intraday
while the backtest advanced it on daily closes, and the expiry exit waited until DTE
<= 1 while the backtest left a six-day buffer.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import json

# The runner is closed while this much calendar time remains to expiry.
RUNNER_EXPIRY_BUFFER_CALENDAR_DAYS = 6

# The peak the 25% give-back trails is a completed daily close, never an intraday tick.
RUNNER_PEAK_SOURCE = "EOD_CLOSE"


def runner_should_exit_for_expiry(*, remaining_calendar_days: int) -> bool:
    """Whether a runner must be closed for approaching expiry."""
    return int(remaining_calendar_days) <= RUNNER_EXPIRY_BUFFER_CALENDAR_DAYS


def update_runner_peak(*, peak: float, observed_bid: float, source: str) -> float:
    """Advance the runner peak only from an accepted end-of-day close mark."""
    if str(source).upper() != RUNNER_PEAK_SOURCE:
        return float(peak)
    return max(float(peak), float(observed_bid))


@dataclass(frozen=True)
class SnapbackExecutionPolicy:
    """Every evidence-affecting execution constant, hashed as one identity."""

    version: str = "snapback_execution_v1"
    max_quote_age_ms: int = 2_000
    max_future_clock_skew_ms: int = 250
    entry_window_end_minutes: int = 30
    # Three missed 30-second runner cycles. Longer than this and the opening window
    # was not continuously observed, whatever the next quote looks like.
    entry_observation_max_gap_ms: int = 90_000
    option_slippage_bps: float = 20.0
    futures_slippage_bps: float = 5.0
    max_hedge_discretization_error_pct: float = 50.0
    runner_expiry_buffer_calendar_days: int = RUNNER_EXPIRY_BUFFER_CALENDAR_DAYS
    require_full_visible_depth: bool = True
    # The spot that sizes a hedge must be as fresh as the quotes it is combined with.
    underlying_context_max_age_ms: int = 2_000
    # The close mark must be OBSERVED inside this window, by provider time. The
    # finalizer then runs after it, from what was persisted.
    eod_observation_start: str = "15:29:00"
    eod_observation_end: str = "15:30:00"
    runner_peak_source: str = RUNNER_PEAK_SOURCE

    def as_dict(self) -> dict:
        return asdict(self)

    def policy_hash(self) -> str:
        encoded = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


EXECUTION_POLICY = SnapbackExecutionPolicy()
