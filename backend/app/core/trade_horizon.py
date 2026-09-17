"""The immutable time budget attached to one open position.

A position's horizon is decided once, at entry, from the mode config then in
force — and never re-derived afterwards. Re-reading the live config to answer
"when must this exit?" means an operator who edits a mode at 14:00 silently
moves the hard exit of every position already open, including ones that were
opened under a rule the operator has now rejected. The plan is therefore a
frozen snapshot, stamped with the mode version and calendar version that
produced it.

Two families of budget live here:

* second-based (Ultra Scalping, Scalping, Intraday) — a wall-clock window plus
  a hard exit that can never cross the session square-off;
* session-based (Overnight, Swing) — trading-session counts, computed through
  :mod:`app.core.session_calendar`, which refuses rather than guesses.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.core.horizon import (
    HorizonMode,
    HorizonModeConfig,
    TimelineState,
    canonical_mode,
    timeline_for,
)
from app.core.session_calendar import (
    CALENDAR_VERSION,
    TIMELINE_CALENDAR_UNKNOWN,
    CalendarUnavailable,
    add_trading_sessions,
    is_trading_session,
    sessions_between,
)
from app.core.strategy_identity import stable_hash

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_TIMEZONE = "Asia/Kolkata"


class HorizonUnavailable(RuntimeError):
    """No honest horizon can be computed, so no new exposure may open."""

    code = TIMELINE_CALENDAR_UNKNOWN


def _parse_clock(value: str) -> time:
    hour, minute = (int(part) for part in value.split(":", 1))
    return time(hour, minute)


@dataclass(frozen=True)
class TradeHorizonPlan:
    """What this position was designed to do with its time.

    Frozen on purpose. A later config change produces a new plan for new
    positions; it must not touch this one.
    """

    strategy_id: str
    mode: HorizonMode

    entry_at: datetime

    expected_window_start: datetime
    expected_window_end: datetime

    #: Wall-clock hard exit. ``None`` for session-based modes.
    hard_exit_at: datetime | None
    #: Last permitted trading session. ``None`` for second-based modes.
    hard_exit_session: date | None

    review_interval_seconds: int

    exchange: str
    timezone: str

    calendar_version: str
    mode_version: str

    # ── session-based bookkeeping (populated for Overnight and Swing) ──────
    entry_session_date: date | None = None
    expected_min_sessions: int | None = None
    expected_max_sessions: int | None = None
    hard_max_sessions: int | None = None

    #: Snapshot of the config hash that produced this plan, so a report can
    #: prove which mode revision governed the trade.
    mode_config_hash: str = ""

    def __post_init__(self) -> None:
        if self.entry_at.tzinfo is None:
            raise ValueError("entry_at must be timezone-aware")
        if self.expected_window_end < self.expected_window_start:
            raise ValueError("expected window ends before it starts")
        if self.hard_exit_at is not None and self.hard_exit_at < self.entry_at:
            raise ValueError("hard_exit_at precedes entry_at")
        if (self.hard_exit_at is None) == (self.hard_exit_session is None):
            raise ValueError(
                "exactly one of hard_exit_at / hard_exit_session must be set: "
                "a plan with neither has no hard limit, and one with both has "
                "two disagreeing limits"
            )

    @property
    def session_based(self) -> bool:
        return self.hard_exit_session is not None

    @property
    def plan_id(self) -> str:
        """Deterministic id, referenced by the trade intent and every event."""
        return stable_hash(
            {
                "strategy_id": self.strategy_id,
                "mode": self.mode.value,
                "entry_at": self.entry_at.isoformat(),
                "mode_version": self.mode_version,
                "calendar_version": self.calendar_version,
                "mode_config_hash": self.mode_config_hash,
                "exchange": self.exchange,
            }
        )

    # ── evaluation ────────────────────────────────────────────────────────

    def age_seconds(self, now: datetime) -> float:
        return (now.astimezone(self.entry_at.tzinfo) - self.entry_at).total_seconds()

    def sessions_elapsed(self, now: datetime) -> int:
        """Trading sessions since entry. Session-based modes only."""
        if not self.session_based or self.entry_session_date is None:
            raise HorizonUnavailable(
                f"{self.mode} is not session-based; use age_seconds()"
            )
        local = now.astimezone(ZoneInfo(self.timezone))
        return sessions_between(self.entry_session_date, local.date(), self.exchange)

    def state(self, now: datetime, *, closed: bool = False) -> TimelineState:
        """Where the position sits in its budget.

        Returns :attr:`TimelineState.UNKNOWN` only when the answer genuinely
        cannot be computed — which blocks new exposure upstream rather than
        being treated as "fine".
        """
        if closed:
            return TimelineState.CLOSED
        try:
            if self.session_based:
                return self._session_state(now)
            return self._clock_state(now)
        except (CalendarUnavailable, ValueError):
            return TimelineState.UNKNOWN

    def _clock_state(self, now: datetime) -> TimelineState:
        moment = now.astimezone(self.entry_at.tzinfo)
        assert self.hard_exit_at is not None
        if moment >= self.hard_exit_at:
            return TimelineState.HARD_EXIT_DUE
        if moment < self.expected_window_start:
            return TimelineState.EARLY
        if moment <= self.expected_window_end:
            return TimelineState.EXPECTED
        return TimelineState.EXTENDED

    def _session_state(self, now: datetime) -> TimelineState:
        elapsed = self.sessions_elapsed(now)
        assert self.hard_max_sessions is not None
        assert self.expected_min_sessions is not None
        assert self.expected_max_sessions is not None
        if elapsed >= self.hard_max_sessions:
            return TimelineState.HARD_EXIT_DUE
        if elapsed < self.expected_min_sessions:
            return TimelineState.EARLY
        if elapsed <= self.expected_max_sessions:
            return TimelineState.EXPECTED
        return TimelineState.EXTENDED

    def hard_exit_due(self, now: datetime, *, closed: bool = False) -> bool:
        """Must this position be flat now?

        ``UNKNOWN`` counts as due. A horizon that stopped being computable is
        not a reason to keep carrying risk.
        """
        state = self.state(now, closed=closed)
        return state in (TimelineState.HARD_EXIT_DUE, TimelineState.UNKNOWN)

    # ── presentation ──────────────────────────────────────────────────────

    def describe(self, now: datetime, *, closed: bool = False) -> dict[str, Any]:
        """The fields the position row and the operator dashboard must show."""
        state = self.state(now, closed=closed)
        row: dict[str, Any] = {
            "strategy": self.strategy_id,
            "mode": self.mode.value,
            "mode_version": self.mode_version,
            "entry_at": self.entry_at.isoformat(),
            "expected_window_start": self.expected_window_start.isoformat(),
            "expected_window_end": self.expected_window_end.isoformat(),
            "review_interval_seconds": self.review_interval_seconds,
            "exchange": self.exchange,
            "timezone": self.timezone,
            "calendar_version": self.calendar_version,
            "timeline_state": state.value,
            "plan_id": self.plan_id,
        }
        if self.session_based:
            row["hard_exit_session"] = self.hard_exit_session.isoformat()  # type: ignore[union-attr]
            row["expected_sessions"] = [
                self.expected_min_sessions,
                self.expected_max_sessions,
            ]
            row["hard_max_sessions"] = self.hard_max_sessions
            try:
                row["sessions_elapsed"] = self.sessions_elapsed(now)
            except (CalendarUnavailable, HorizonUnavailable):
                row["sessions_elapsed"] = None
        else:
            row["hard_exit_at"] = self.hard_exit_at.isoformat()  # type: ignore[union-attr]
            row["age_seconds"] = max(0.0, self.age_seconds(now))
        return row


def _config_hash(cfg: HorizonModeConfig) -> str:
    return stable_hash(
        {
            "mode": cfg.mode.value,
            "signal_timeframe": cfg.signal_timeframe,
            "execution_timeframe": cfg.execution_timeframe,
            "expected_hold_min": cfg.expected_hold_min,
            "expected_hold_max": cfg.expected_hold_max,
            "duration_unit": cfg.duration_unit,
            "hard_hold_limit": cfg.hard_hold_limit,
            "allow_overnight": cfg.allow_overnight,
            "force_close_time": cfg.force_close_time,
            "version": cfg.version,
        }
    )


def build_horizon_plan(
    *,
    strategy_id: str,
    mode: str | HorizonMode,
    entry_at: datetime,
    exchange: str = "NFO",
    config: HorizonModeConfig | None = None,
    timezone: str = DEFAULT_TIMEZONE,
) -> TradeHorizonPlan:
    """Freeze the horizon for a position opening now.

    Raises :class:`HorizonUnavailable` when no honest horizon exists — an
    unknown calendar, or a session-bound entry placed after the square-off. The
    caller must translate that into a refusal, never into a default horizon.
    """
    resolved = canonical_mode(mode)
    cfg = config or timeline_for(resolved)
    if cfg.mode is not resolved:
        raise ValueError(f"config is for {cfg.mode}, not {resolved}")
    if entry_at.tzinfo is None:
        raise ValueError("entry_at must be timezone-aware")

    tz = ZoneInfo(timezone)
    local_entry = entry_at.astimezone(tz)

    if cfg.duration_unit == "seconds":
        return _clock_plan(strategy_id, cfg, local_entry, exchange, tz, timezone)
    return _session_plan(strategy_id, cfg, local_entry, exchange, tz, timezone)


def _clock_plan(
    strategy_id: str,
    cfg: HorizonModeConfig,
    entry: datetime,
    exchange: str,
    tz: ZoneInfo,
    timezone: str,
) -> TradeHorizonPlan:
    hard = entry + timedelta(seconds=cfg.hard_hold_limit)

    if cfg.force_close_time is not None:
        square_off = datetime.combine(
            entry.date(), _parse_clock(cfg.force_close_time), tzinfo=tz
        )
        if square_off <= entry:
            # An Ultra/Scalping/Intraday entry placed at or after the square-off
            # has nowhere to live: honouring the mode's own clock budget would
            # carry it overnight, which the mode forbids. Refuse the entry
            # rather than silently shorten or silently extend it.
            raise HorizonUnavailable(
                f"{cfg.mode} entry at {entry.isoformat()} is at or after the "
                f"{cfg.force_close_time} square-off; no in-session horizon exists"
            )
        # The square-off is a ceiling, never an extension.
        hard = min(hard, square_off)

    window_start = entry + timedelta(seconds=cfg.expected_hold_min)
    if window_start > hard:
        # Not even the minimum designed holding time fits before the square-off.
        # Opening anyway would book a trade into this lane that the lane's own
        # rules could never have run, and its exit would always read as a forced
        # square-off rather than a strategy decision.
        raise HorizonUnavailable(
            f"{cfg.mode} entry at {entry.isoformat()} leaves less than its "
            f"minimum {cfg.expected_hold_min}s holding window before "
            f"{cfg.force_close_time or 'the hard limit'}"
        )

    return TradeHorizonPlan(
        strategy_id=strategy_id,
        mode=cfg.mode,
        entry_at=entry,
        expected_window_start=window_start,
        expected_window_end=min(
            entry + timedelta(seconds=cfg.expected_hold_max), hard
        ),
        hard_exit_at=hard,
        hard_exit_session=None,
        review_interval_seconds=cfg.review_interval_seconds,
        exchange=exchange.upper(),
        timezone=timezone,
        calendar_version=CALENDAR_VERSION,
        mode_version=cfg.version,
        mode_config_hash=_config_hash(cfg),
    )


def _session_plan(
    strategy_id: str,
    cfg: HorizonModeConfig,
    entry: datetime,
    exchange: str,
    tz: ZoneInfo,
    timezone: str,
) -> TradeHorizonPlan:
    entry_day = entry.date()
    try:
        if not is_trading_session(entry_day, exchange):
            raise HorizonUnavailable(
                f"{cfg.mode} entry on {entry_day.isoformat()} is not a trading "
                f"session on {exchange}"
            )
        hard_session = add_trading_sessions(
            entry_day, cfg.hard_hold_limit, exchange
        )
        expected_start = add_trading_sessions(
            entry_day, cfg.expected_hold_min, exchange
        )
        expected_end = add_trading_sessions(
            entry_day, cfg.expected_hold_max, exchange
        )
    except CalendarUnavailable as exc:
        # The declared refusal: a Swing entered in late December cannot reach
        # its 15th session inside the verified holiday list, so it must not open.
        raise HorizonUnavailable(str(exc)) from exc

    open_of = time(9, 15)
    return TradeHorizonPlan(
        strategy_id=strategy_id,
        mode=cfg.mode,
        entry_at=entry,
        expected_window_start=datetime.combine(expected_start, open_of, tzinfo=tz),
        expected_window_end=datetime.combine(expected_end, open_of, tzinfo=tz),
        hard_exit_at=None,
        hard_exit_session=hard_session,
        review_interval_seconds=cfg.review_interval_seconds,
        exchange=exchange.upper(),
        timezone=timezone,
        calendar_version=CALENDAR_VERSION,
        mode_version=cfg.version,
        entry_session_date=entry_day,
        expected_min_sessions=cfg.expected_hold_min,
        expected_max_sessions=cfg.expected_hold_max,
        hard_max_sessions=cfg.hard_hold_limit,
        mode_config_hash=_config_hash(cfg),
    )
