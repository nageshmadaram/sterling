"""Causal opening-volume leader detection for Indian cash equities.

The engine implements the observable, documented core of ORION Scan without
copying its UI or pretending to know its private scoring weights:

* compare today's completed 09:15 one-minute volume with the same stock's
  09:15 volume over the preceding ten sessions;
* classify the resulting relative volume at 2x/3x/5x/10x;
* take direction from the 09:15 candle colour;
* track the first later breach of that candle's high or low; and
* expose, rather than silently discard, liquidity and candle-quality failures.

This module is pure and performs no broker I/O or order submission. Broker
quotes, the transparent Sterling decision model, and guarded execution are
separate service layers. The proprietary site's unpublished score remains
distinct from Sterling's inspectable replacement.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from itertools import pairwise
from math import isfinite
from statistics import mean
from zoneinfo import ZoneInfo

from app.engines.option_contracts import Bar

IST = ZoneInfo("Asia/Kolkata")
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)


class LeaderDirection(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    NEUTRAL = "NEUTRAL"


class LeaderTier(str, Enum):
    WEAK = "weak"
    WATCH = "watch"
    SPURT = "spurt"
    STRONG = "strong"
    EXPLOSIVE = "explosive"


class CandleQuality(str, Enum):
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class LiquidityState(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class EntryPhase(str, Enum):
    OPENING = "opening"
    PLANNING = "planning"
    PRIME = "prime"
    MANAGE = "manage"
    DECAY = "decay"
    NO_NEW_ENTRY = "no_new_entry"
    EXIT = "exit"
    FLAT = "flat"
    CLOSED = "closed"


class ChaseState(str, Enum):
    """Distance of the latest completed price from an aligned ORB trigger."""

    NO_ALIGNED_BREAK = "no_aligned_break"
    RETEST = "retest"
    PREFERRED = "preferred"
    CAUTION = "caution"
    CHASE = "chase"


class ValidationState(str, Enum):
    PENDING = "pending"
    PASS = "pass"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class OpeningVolumeConfig:
    """Versioned thresholds for the opening-volume signal contract.

    RVOL tier boundaries, the ten-session same-minute baseline, and the Layer-1
    liquidity floors are directly observable in the source material.  Candle
    body/close-location boundaries are a transparent local implementation of
    the documented "fat body, close near the extreme" condition; they are not
    asserted to equal the source site's private formula.
    """

    baseline_sessions: int = 10
    turnover_sessions: int = 20
    min_turnover_bars_per_session: int = 300
    watch_rvol: float = 2.0
    spurt_rvol: float = 3.0
    strong_rvol: float = 5.0
    explosive_rvol: float = 10.0
    min_price_inr: float = 100.0
    min_average_turnover_inr: float = 20_000_000.0
    moderate_body_fraction: float = 0.35
    strong_body_fraction: float = 0.60
    moderate_close_location: float = 0.60
    strong_close_location: float = 0.75
    orb_fresh_minutes: int = 5
    preferred_orb_distance_pct: float = 0.5
    max_orb_distance_pct: float = 1.0
    max_stop_distance_pct: float = 1.5
    hold_check_minutes: int = 5
    follow_through_pct: float = 1.0
    follow_through_minutes: int = 60

    def validate(self) -> OpeningVolumeConfig:
        if self.baseline_sessions < 1:
            raise ValueError("baseline_sessions must be >= 1")
        if self.turnover_sessions < 1:
            raise ValueError("turnover_sessions must be >= 1")
        if self.min_turnover_bars_per_session < 1:
            raise ValueError("min_turnover_bars_per_session must be >= 1")
        thresholds = (
            self.watch_rvol,
            self.spurt_rvol,
            self.strong_rvol,
            self.explosive_rvol,
        )
        if not all(isfinite(v) and v > 0 for v in thresholds):
            raise ValueError("RVOL thresholds must be finite and greater than zero")
        if not all(a < b for a, b in pairwise(thresholds)):
            raise ValueError(
                "RVOL thresholds must satisfy watch < spurt < strong < explosive"
            )
        if not (isfinite(self.min_price_inr) and self.min_price_inr >= 0):
            raise ValueError("min_price_inr must be finite and non-negative")
        if not (
            isfinite(self.min_average_turnover_inr)
            and self.min_average_turnover_inr >= 0
        ):
            raise ValueError("min_average_turnover_inr must be finite and non-negative")
        fractions = (
            self.moderate_body_fraction,
            self.strong_body_fraction,
            self.moderate_close_location,
            self.strong_close_location,
        )
        if not all(isfinite(v) and 0 <= v <= 1 for v in fractions):
            raise ValueError("candle-quality fractions must be between zero and one")
        if self.moderate_body_fraction > self.strong_body_fraction:
            raise ValueError(
                "moderate_body_fraction cannot exceed strong_body_fraction"
            )
        if self.moderate_close_location > self.strong_close_location:
            raise ValueError(
                "moderate_close_location cannot exceed strong_close_location"
            )
        if self.orb_fresh_minutes < 0:
            raise ValueError("orb_fresh_minutes must be non-negative")
        if self.hold_check_minutes < 1:
            raise ValueError("hold_check_minutes must be >= 1")
        if self.follow_through_minutes < 1:
            raise ValueError("follow_through_minutes must be >= 1")
        distance_thresholds = (
            self.preferred_orb_distance_pct,
            self.max_orb_distance_pct,
            self.max_stop_distance_pct,
            self.follow_through_pct,
        )
        if not all(isfinite(v) and v >= 0 for v in distance_thresholds):
            raise ValueError("entry distance thresholds must be finite and non-negative")
        if self.preferred_orb_distance_pct > self.max_orb_distance_pct:
            raise ValueError(
                "preferred_orb_distance_pct cannot exceed max_orb_distance_pct"
            )
        return self


@dataclass(frozen=True)
class LeaderSignal:
    symbol: str
    session_date: date
    signal_time: datetime
    observed_at: datetime
    direction: LeaderDirection
    tier: LeaderTier
    rvol: float
    opening_volume: float
    average_opening_volume: float
    baseline_session_count: int
    opening_open: float
    opening_high: float
    opening_low: float
    opening_close: float
    current_price: float
    previous_close: float | None
    day_change_pct: float | None
    gap_pct: float | None
    body_pct: float
    range_pct: float
    body_fraction: float
    close_location: float
    candle_quality: CandleQuality
    average_turnover_inr: float | None
    turnover_session_count: int
    liquidity_state: LiquidityState
    liquidity_reasons: tuple[str, ...]
    orb_break_side: LeaderDirection | None
    orb_break_time: datetime | None
    orb_cumulative_volume: float | None
    orb_aligned: bool
    orb_immediate: bool
    combo: bool
    session_high: float
    session_low: float
    orb_break_level: float | None
    orb_age_minutes: int | None
    orb_fresh: bool
    orb_distance_pct: float | None
    chase_state: ChaseState
    protective_stop_price: float | None
    stop_distance_pct: float | None
    stop_too_wide: bool | None
    consecutive_leader_days: int | None
    third_day_repeat: bool | None
    hold_5m_status: ValidationState
    hold_5m_check_time: datetime | None
    hold_5m_price: float | None
    move_1pct_within_60m: bool | None
    move_1pct_time: datetime | None
    intraday_vwap: float | None
    vwap_aligned: bool | None
    previous_day_high: float | None
    previous_day_low: float | None
    pdh_pdl_break_aligned: bool | None
    rsi_14_1m: float | None
    rally_aligned: bool
    rise_from_low_pct: float
    fall_from_high_pct: float
    is_leader: bool
    passes_quality_filters: bool
    entry_phase: EntryPhase

    @property
    def signal_key(self) -> str:
        return f"opening-volume:{self.session_date.isoformat()}:{self.symbol}:{self.direction.value}"

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload.update(
            {
                "session_date": self.session_date.isoformat(),
                "signal_time": self.signal_time.isoformat(),
                # Keep the original causal volume event explicit while also
                # exposing the first actionable ORB event used by ORION cards.
                # ``signal_time`` remains the backwards-compatible alias for
                # the completed 09:15 volume candle.
                "volume_signal_time": self.signal_time.isoformat(),
                "actionable_signal_time": self.orb_break_time.isoformat()
                if self.orb_break_time
                else None,
                "observed_at": self.observed_at.isoformat(),
                "direction": self.direction.value,
                "tier": self.tier.value,
                "candle_quality": self.candle_quality.value,
                "liquidity_state": self.liquidity_state.value,
                "liquidity_reasons": list(self.liquidity_reasons),
                "orb_break_side": self.orb_break_side.value
                if self.orb_break_side
                else None,
                "orb_break_time": self.orb_break_time.isoformat()
                if self.orb_break_time
                else None,
                "entry_phase": self.entry_phase.value,
                "chase_state": self.chase_state.value,
                "hold_5m_status": self.hold_5m_status.value,
                "hold_5m_check_time": self.hold_5m_check_time.isoformat()
                if self.hold_5m_check_time
                else None,
                "move_1pct_time": self.move_1pct_time.isoformat()
                if self.move_1pct_time
                else None,
                "signal_key": self.signal_key,
            }
        )
        return payload


def _as_ist(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=IST)
    return timestamp.astimezone(IST)


def _validate_bar(bar: Bar) -> None:
    values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
    if not all(isfinite(float(value)) for value in values):
        raise ValueError("bars must contain finite OHLCV values")
    if min(bar.open, bar.high, bar.low, bar.close) <= 0:
        raise ValueError("bar prices must be greater than zero")
    if bar.volume < 0:
        raise ValueError("bar volume cannot be negative")
    if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
        raise ValueError("bar high/low do not contain open and close")


def _session_rows(
    bars: Iterable[Bar],
    *,
    as_of: datetime | None = None,
) -> dict[date, list[Bar]]:
    rows: dict[date, dict[datetime, Bar]] = {}
    observed_at = _as_ist(as_of) if as_of is not None else None
    for bar in bars:
        timestamp = _as_ist(bar.timestamp).replace(microsecond=0)
        if not (SESSION_OPEN <= timestamp.time() < SESSION_CLOSE):
            continue
        if (
            observed_at is not None
            and timestamp.replace(second=0, microsecond=0) + timedelta(minutes=1)
            > observed_at
        ):
            continue
        _validate_bar(bar)
        # Provider retries can return the same candle twice.  The latest copy is
        # authoritative and prevents duplicate volume from contaminating RVOL.
        rows.setdefault(timestamp.date(), {})[timestamp] = bar
    return {
        session: sorted(values.values(), key=lambda bar: _as_ist(bar.timestamp))
        for session, values in rows.items()
    }


def _completed_rows(
    bars: Sequence[Bar], as_of: datetime, interval_minutes: int = 1
) -> list[Bar]:
    now = _as_ist(as_of)
    return [
        bar
        for bar in bars
        if _as_ist(bar.timestamp).replace(second=0, microsecond=0)
        + timedelta(minutes=interval_minutes)
        <= now
    ]


def _opening_bar(rows: Sequence[Bar]) -> Bar | None:
    return next(
        (
            bar
            for bar in rows
            if _as_ist(bar.timestamp).hour == SESSION_OPEN.hour
            and _as_ist(bar.timestamp).minute == SESSION_OPEN.minute
        ),
        None,
    )


def classify_tier(
    rvol: float,
    config: OpeningVolumeConfig | None = None,
) -> LeaderTier:
    config = config or OpeningVolumeConfig()
    config.validate()
    if not isfinite(rvol) or rvol < 0:
        raise ValueError("rvol must be finite and non-negative")
    if rvol >= config.explosive_rvol:
        return LeaderTier.EXPLOSIVE
    if rvol >= config.strong_rvol:
        return LeaderTier.STRONG
    if rvol >= config.spurt_rvol:
        return LeaderTier.SPURT
    if rvol >= config.watch_rvol:
        return LeaderTier.WATCH
    return LeaderTier.WEAK


def _direction(bar: Bar) -> LeaderDirection:
    if bar.close > bar.open:
        return LeaderDirection.UP
    if bar.close < bar.open:
        return LeaderDirection.DOWN
    return LeaderDirection.NEUTRAL


def _candle_quality(
    bar: Bar,
    direction: LeaderDirection,
    config: OpeningVolumeConfig,
) -> tuple[CandleQuality, float, float]:
    candle_range = max(bar.high - bar.low, 0.0)
    if candle_range <= 0 or direction is LeaderDirection.NEUTRAL:
        return CandleQuality.WEAK, 0.0, 0.5
    body_fraction = abs(bar.close - bar.open) / candle_range
    close_location = (
        (bar.close - bar.low) / candle_range
        if direction is LeaderDirection.UP
        else (bar.high - bar.close) / candle_range
    )
    if (
        body_fraction >= config.strong_body_fraction
        and close_location >= config.strong_close_location
    ):
        quality = CandleQuality.STRONG
    elif (
        body_fraction >= config.moderate_body_fraction
        and close_location >= config.moderate_close_location
    ):
        quality = CandleQuality.MODERATE
    else:
        quality = CandleQuality.WEAK
    return quality, body_fraction, close_location


def _entry_phase(at: datetime) -> EntryPhase:
    value = _as_ist(at)
    if value.weekday() >= 5:
        return EntryPhase.CLOSED
    current = value.time()
    if current < time(9, 15) or current >= time(15, 30):
        return EntryPhase.CLOSED
    if current < time(9, 16):
        return EntryPhase.OPENING
    if current < time(9, 25):
        return EntryPhase.PLANNING
    if current < time(9, 40):
        return EntryPhase.PRIME
    if current < time(10, 30):
        return EntryPhase.MANAGE
    if current < time(11, 30):
        return EntryPhase.DECAY
    if current < time(13, 45):
        return EntryPhase.NO_NEW_ENTRY
    if current < time(15, 15):
        return EntryPhase.EXIT
    return EntryPhase.FLAT


def _average_turnover(
    sessions: Mapping[date, Sequence[Bar]],
    before: date,
    config: OpeningVolumeConfig,
) -> tuple[float | None, int]:
    totals: list[float] = []
    for session in sorted((d for d in sessions if d < before), reverse=True):
        rows = sessions[session]
        if len(rows) < config.min_turnover_bars_per_session:
            continue
        total = sum(
            ((bar.high + bar.low + bar.close) / 3.0) * max(bar.volume, 0.0)
            for bar in rows
        )
        if total > 0:
            totals.append(total)
        if len(totals) >= config.turnover_sessions:
            break
    if len(totals) < config.turnover_sessions:
        return None, len(totals)
    return mean(totals), len(totals)


def _liquidity(
    current_price: float,
    average_turnover_inr: float | None,
    turnover_session_count: int,
    config: OpeningVolumeConfig,
) -> tuple[LiquidityState, tuple[str, ...]]:
    reasons: list[str] = []
    if current_price < config.min_price_inr:
        reasons.append(f"price below INR {config.min_price_inr:g}")
    if average_turnover_inr is None:
        reasons.append(
            f"fewer than {config.turnover_sessions} complete prior turnover sessions "
            f"({turnover_session_count} available)"
        )
    elif average_turnover_inr < config.min_average_turnover_inr:
        reasons.append(
            f"average turnover below INR {config.min_average_turnover_inr:g}"
        )
    if current_price < config.min_price_inr or (
        average_turnover_inr is not None
        and average_turnover_inr < config.min_average_turnover_inr
    ):
        return LiquidityState.FAIL, tuple(reasons)
    if average_turnover_inr is None:
        return LiquidityState.UNKNOWN, tuple(reasons)
    return LiquidityState.PASS, ()


def _first_orb_break(
    rows: Sequence[Bar],
    opening: Bar,
    direction: LeaderDirection,
) -> tuple[LeaderDirection | None, datetime | None, float | None, bool]:
    cumulative = max(opening.volume, 0.0)
    opening_ts = _as_ist(opening.timestamp)
    for bar in rows:
        timestamp = _as_ist(bar.timestamp)
        if timestamp <= opening_ts:
            continue
        cumulative += max(bar.volume, 0.0)
        breaks_up = bar.high > opening.high
        breaks_down = bar.low < opening.low
        if not (breaks_up or breaks_down):
            continue
        if breaks_up and breaks_down:
            # Minute OHLC cannot tell which boundary traded first.  Inferring a
            # side from candle colour would fabricate ordering and can create a
            # false aligned COMBO, so keep the event explicitly ambiguous.
            side = None
        elif breaks_up:
            side = LeaderDirection.UP
        else:
            side = LeaderDirection.DOWN
        aligned = side is not None and side is direction
        return side, timestamp, cumulative, aligned
    return None, None, None, False


def _directional_move_pct(
    price: float,
    reference: float,
    direction: LeaderDirection,
) -> float:
    if direction is LeaderDirection.DOWN:
        return (reference - price) / reference * 100.0
    return (price - reference) / reference * 100.0


def _entry_context(
    rows: Sequence[Bar],
    opening: Bar,
    direction: LeaderDirection,
    orb_side: LeaderDirection | None,
    orb_time: datetime | None,
    observed_at: datetime,
    config: OpeningVolumeConfig,
) -> dict[str, object]:
    """Calculate the documented freshness, chase, stop, and follow-through facts.

    ORION does not publish the fill price used by its experimental Momentum Lab.
    These fields therefore use the visible 09:15 ORB boundary as an explicit,
    deterministic entry reference rather than inventing a hidden fill.
    """

    if (
        orb_time is None
        or orb_side is None
        or orb_side is not direction
        or direction is LeaderDirection.NEUTRAL
    ):
        return {
            "orb_break_level": None,
            "orb_age_minutes": None,
            "orb_fresh": False,
            "orb_distance_pct": None,
            "chase_state": ChaseState.NO_ALIGNED_BREAK,
            "protective_stop_price": None,
            "stop_distance_pct": None,
            "stop_too_wide": None,
            "hold_5m_status": ValidationState.UNAVAILABLE,
            "hold_5m_check_time": None,
            "hold_5m_price": None,
            "move_1pct_within_60m": None,
            "move_1pct_time": None,
        }

    level = opening.high if direction is LeaderDirection.UP else opening.low
    stop = opening.low if direction is LeaderDirection.UP else opening.high
    latest = rows[-1]
    distance = _directional_move_pct(latest.close, level, direction)
    if distance < 0:
        chase_state = ChaseState.RETEST
    elif distance <= config.preferred_orb_distance_pct:
        chase_state = ChaseState.PREFERRED
    elif distance <= config.max_orb_distance_pct:
        chase_state = ChaseState.CAUTION
    else:
        chase_state = ChaseState.CHASE

    age_minutes = max(
        0,
        int((_as_ist(observed_at) - _as_ist(orb_time)).total_seconds() // 60),
    )
    stop_distance = abs(level - stop) / level * 100.0

    check_time = _as_ist(orb_time).replace(second=0, microsecond=0) + timedelta(
        minutes=config.hold_check_minutes
    )
    check_bar = next(
        (
            bar
            for bar in rows
            if _as_ist(bar.timestamp).replace(second=0, microsecond=0) == check_time
        ),
        None,
    )
    if check_bar is not None:
        held = (
            check_bar.close >= level
            if direction is LeaderDirection.UP
            else check_bar.close <= level
        )
        hold_status = ValidationState.PASS if held else ValidationState.FAIL
        hold_price: float | None = check_bar.close
    elif _as_ist(observed_at) < check_time + timedelta(minutes=1):
        hold_status = ValidationState.PENDING
        hold_price = None
    else:
        hold_status = ValidationState.UNAVAILABLE
        hold_price = None

    follow_deadline = _as_ist(orb_time) + timedelta(
        minutes=config.follow_through_minutes
    )
    target = config.follow_through_pct / 100.0
    move_time: datetime | None = None
    for bar in rows:
        timestamp = _as_ist(bar.timestamp)
        if timestamp < _as_ist(orb_time) or timestamp > follow_deadline:
            continue
        hit = (
            bar.high >= level * (1.0 + target)
            if direction is LeaderDirection.UP
            else bar.low <= level * (1.0 - target)
        )
        if hit:
            move_time = timestamp
            break
    if move_time is not None:
        moved_within_window: bool | None = True
    elif _as_ist(observed_at) >= follow_deadline + timedelta(minutes=1):
        moved_within_window = False
    else:
        moved_within_window = None

    return {
        "orb_break_level": level,
        "orb_age_minutes": age_minutes,
        "orb_fresh": age_minutes <= config.orb_fresh_minutes,
        "orb_distance_pct": distance,
        "chase_state": chase_state,
        "protective_stop_price": stop,
        "stop_distance_pct": stop_distance,
        "stop_too_wide": stop_distance > config.max_stop_distance_pct,
        "hold_5m_status": hold_status,
        "hold_5m_check_time": check_time,
        "hold_5m_price": hold_price,
        "move_1pct_within_60m": moved_within_window,
        "move_1pct_time": move_time,
    }


def _repeat_profile(
    sessions: Mapping[date, Sequence[Bar]],
    current_date: date,
    config: OpeningVolumeConfig,
    current_tier: LeaderTier,
) -> tuple[int | None, bool | None]:
    """Return consecutive leader days and the documented third-day trap.

    A definitive negative requires enough older sessions to recompute each
    preceding day's own ten-session baseline.  Missing history is returned as
    unknown, never silently treated as a clean first-day setup.
    """

    leader_tiers = {
        LeaderTier.SPURT,
        LeaderTier.STRONG,
        LeaderTier.EXPLOSIVE,
    }
    if current_tier not in leader_tiers:
        return 0, False
    dated_openings = [
        (session, opening)
        for session in sorted(d for d in sessions if d <= current_date)
        if (opening := _opening_bar(sessions[session])) is not None
        and opening.volume > 0
    ]
    current_index = next(
        (
            index
            for index, (session, _) in enumerate(dated_openings)
            if session == current_date
        ),
        None,
    )
    if current_index is None:
        return None, None

    streak = 0
    for index in range(current_index, -1, -1):
        if index < config.baseline_sessions:
            return (streak, True) if streak >= 3 else (None, None)
        baseline = mean(
            opening.volume
            for _, opening in dated_openings[
                index - config.baseline_sessions : index
            ]
        )
        tier = classify_tier(dated_openings[index][1].volume / baseline, config)
        if tier not in leader_tiers:
            return streak, streak >= 3
        streak += 1
    return (streak, True) if streak >= 3 else (None, None)


def _rsi_14(closes: Sequence[float]) -> float | None:
    if len(closes) < 15:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in pairwise(closes[-15:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    average_gain = mean(gains)
    average_loss = mean(losses)
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    relative_strength = average_gain / average_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def _intraday_evidence(
    current_rows: Sequence[Bar],
    previous_rows: Sequence[Bar] | None,
    direction: LeaderDirection,
) -> dict[str, float | bool | None]:
    volume = sum(max(bar.volume, 0.0) for bar in current_rows)
    vwap = (
        sum(
            ((bar.high + bar.low + bar.close) / 3.0) * max(bar.volume, 0.0)
            for bar in current_rows
        )
        / volume
        if volume > 0
        else None
    )
    latest = current_rows[-1]
    vwap_aligned = (
        None
        if vwap is None or direction is LeaderDirection.NEUTRAL
        else latest.close >= vwap
        if direction is LeaderDirection.UP
        else latest.close <= vwap
    )
    previous_high = max((bar.high for bar in previous_rows or []), default=None)
    previous_low = min((bar.low for bar in previous_rows or []), default=None)
    if (
        direction is LeaderDirection.NEUTRAL
        or previous_high is None
        or previous_low is None
    ):
        pdh_pdl_aligned: bool | None = None
    elif direction is LeaderDirection.UP:
        pdh_pdl_aligned = max(bar.high for bar in current_rows) > previous_high
    else:
        pdh_pdl_aligned = min(bar.low for bar in current_rows) < previous_low
    return {
        "intraday_vwap": vwap,
        "vwap_aligned": vwap_aligned,
        "previous_day_high": previous_high,
        "previous_day_low": previous_low,
        "pdh_pdl_break_aligned": pdh_pdl_aligned,
        "rsi_14_1m": _rsi_14([bar.close for bar in current_rows]),
    }


def evaluate_leader(
    symbol: str,
    bars: Sequence[Bar],
    *,
    as_of: datetime,
    config: OpeningVolumeConfig | None = None,
    average_turnover_inr: float | None = None,
) -> LeaderSignal:
    """Evaluate one symbol from causally available one-minute bars.

    ``bars`` must include today's 09:15 candle and prior sessions.  Only bars
    closed by ``as_of`` participate.  Baseline sessions are strictly earlier
    than today's session, preventing the evaluated candle from leaking into its
    own normal.  ``average_turnover_inr`` may be supplied by an authoritative
    daily-data source; otherwise it is reconstructed from complete minute days.
    """

    config = (config or OpeningVolumeConfig()).validate()
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise ValueError("symbol is required")
    now = _as_ist(as_of)
    sessions = _session_rows(bars, as_of=now)
    current_date = now.date()
    current_rows = _completed_rows(sessions.get(current_date, []), now)
    opening = _opening_bar(current_rows)
    if opening is None:
        if now.time() < time(9, 16):
            raise ValueError("the 09:15 one-minute candle is not complete")
        raise ValueError("completed 09:15 one-minute candle is missing")

    prior_openings: list[Bar] = []
    for session in sorted((d for d in sessions if d < current_date), reverse=True):
        candidate = _opening_bar(sessions[session])
        if candidate is not None and candidate.volume > 0:
            prior_openings.append(candidate)
        if len(prior_openings) >= config.baseline_sessions:
            break
    if len(prior_openings) < config.baseline_sessions:
        raise ValueError(
            f"fewer than {config.baseline_sessions} valid prior 09:15 volume sessions "
            f"({len(prior_openings)} available)"
        )

    baseline = mean(bar.volume for bar in prior_openings)
    if baseline <= 0:
        raise ValueError("prior 09:15 volume baseline is zero")
    rvol = max(opening.volume, 0.0) / baseline
    tier = classify_tier(rvol, config)
    direction = _direction(opening)
    quality, body_fraction, close_location = _candle_quality(opening, direction, config)

    prior_sessions = sorted(d for d in sessions if d < current_date)
    previous_close = sessions[prior_sessions[-1]][-1].close if prior_sessions else None
    latest = current_rows[-1]
    day_change_pct = (
        (latest.close / previous_close - 1.0) * 100.0
        if previous_close and previous_close > 0
        else None
    )
    gap_pct = (
        (opening.open / previous_close - 1.0) * 100.0
        if previous_close and previous_close > 0
        else None
    )
    candle_range = max(opening.high - opening.low, 0.0)
    body_pct = abs(opening.close - opening.open) / opening.open * 100.0
    range_pct = candle_range / opening.open * 100.0

    computed_turnover, turnover_count = _average_turnover(
        sessions, current_date, config
    )
    if average_turnover_inr is not None:
        if not isfinite(average_turnover_inr) or average_turnover_inr < 0:
            raise ValueError("average_turnover_inr must be finite and non-negative")
        computed_turnover = average_turnover_inr
        turnover_count = config.turnover_sessions
    liquidity_state, liquidity_reasons = _liquidity(
        latest.close,
        computed_turnover,
        turnover_count,
        config,
    )

    orb_side, orb_time, orb_volume, orb_aligned = _first_orb_break(
        current_rows,
        opening,
        direction,
    )
    is_leader = tier in {
        LeaderTier.SPURT,
        LeaderTier.STRONG,
        LeaderTier.EXPLOSIVE,
    }
    passes_quality = (
        is_leader
        and direction is not LeaderDirection.NEUTRAL
        and liquidity_state is LiquidityState.PASS
        and quality is not CandleQuality.WEAK
    )
    # ORION's frozen 2026-09-04 cards establish that aligned breaks at 09:17,
    # 09:22, 11:13, and 15:02 can all be COMBO.  Therefore COMBO is not an
    # "09:16 only" predicate.  The observable rule is a volume leader whose
    # first ORB breach agrees with the opening-candle direction and passes the
    # published Layer-1 liquidity gate.  Freshness remains entry-risk context.
    orb_immediate = orb_time is not None and orb_time.replace(
        second=0, microsecond=0
    ) == _as_ist(opening.timestamp).replace(second=0, microsecond=0) + timedelta(
        minutes=1
    )
    combo = (
        is_leader
        and liquidity_state is LiquidityState.PASS
        and direction is not LeaderDirection.NEUTRAL
        and orb_aligned
    )
    session_low = min(bar.low for bar in current_rows)
    session_high = max(bar.high for bar in current_rows)
    rise_from_low_pct = (latest.close / session_low - 1.0) * 100.0
    fall_from_high_pct = (session_high - latest.close) / session_high * 100.0
    entry_context = _entry_context(
        current_rows,
        opening,
        direction,
        orb_side,
        orb_time,
        now,
        config,
    )
    consecutive_days, third_day_repeat = _repeat_profile(
        sessions,
        current_date,
        config,
        tier,
    )
    previous_rows = sessions[prior_sessions[-1]] if prior_sessions else None
    intraday_evidence = _intraday_evidence(
        current_rows,
        previous_rows,
        direction,
    )
    rally_aligned = bool(
        day_change_pct is not None
        and (
            (direction is LeaderDirection.UP and day_change_pct >= 2.0)
            or (direction is LeaderDirection.DOWN and day_change_pct <= -2.0)
        )
    )

    return LeaderSignal(
        symbol=normalized_symbol,
        session_date=current_date,
        signal_time=_as_ist(opening.timestamp),
        observed_at=now,
        direction=direction,
        tier=tier,
        rvol=rvol,
        opening_volume=opening.volume,
        average_opening_volume=baseline,
        baseline_session_count=len(prior_openings),
        opening_open=opening.open,
        opening_high=opening.high,
        opening_low=opening.low,
        opening_close=opening.close,
        current_price=latest.close,
        previous_close=previous_close,
        day_change_pct=day_change_pct,
        gap_pct=gap_pct,
        body_pct=body_pct,
        range_pct=range_pct,
        body_fraction=body_fraction,
        close_location=close_location,
        candle_quality=quality,
        average_turnover_inr=computed_turnover,
        turnover_session_count=turnover_count,
        liquidity_state=liquidity_state,
        liquidity_reasons=liquidity_reasons,
        orb_break_side=orb_side,
        orb_break_time=orb_time,
        orb_cumulative_volume=orb_volume,
        orb_aligned=orb_aligned,
        orb_immediate=orb_immediate,
        combo=combo,
        session_high=session_high,
        session_low=session_low,
        orb_break_level=entry_context["orb_break_level"],
        orb_age_minutes=entry_context["orb_age_minutes"],
        orb_fresh=bool(entry_context["orb_fresh"]),
        orb_distance_pct=entry_context["orb_distance_pct"],
        chase_state=entry_context["chase_state"],
        protective_stop_price=entry_context["protective_stop_price"],
        stop_distance_pct=entry_context["stop_distance_pct"],
        stop_too_wide=entry_context["stop_too_wide"],
        consecutive_leader_days=consecutive_days,
        third_day_repeat=third_day_repeat,
        hold_5m_status=entry_context["hold_5m_status"],
        hold_5m_check_time=entry_context["hold_5m_check_time"],
        hold_5m_price=entry_context["hold_5m_price"],
        move_1pct_within_60m=entry_context["move_1pct_within_60m"],
        move_1pct_time=entry_context["move_1pct_time"],
        intraday_vwap=intraday_evidence["intraday_vwap"],
        vwap_aligned=intraday_evidence["vwap_aligned"],
        previous_day_high=intraday_evidence["previous_day_high"],
        previous_day_low=intraday_evidence["previous_day_low"],
        pdh_pdl_break_aligned=intraday_evidence["pdh_pdl_break_aligned"],
        rsi_14_1m=intraday_evidence["rsi_14_1m"],
        rally_aligned=rally_aligned,
        rise_from_low_pct=rise_from_low_pct,
        fall_from_high_pct=fall_from_high_pct,
        is_leader=is_leader,
        passes_quality_filters=passes_quality,
        entry_phase=_entry_phase(now),
    )


_TIER_RANK = {
    LeaderTier.WEAK: 0,
    LeaderTier.WATCH: 1,
    LeaderTier.SPURT: 2,
    LeaderTier.STRONG: 3,
    LeaderTier.EXPLOSIVE: 4,
}
_QUALITY_RANK = {
    CandleQuality.WEAK: 0,
    CandleQuality.MODERATE: 1,
    CandleQuality.STRONG: 2,
}


def rank_leaders(signals: Iterable[LeaderSignal]) -> list[LeaderSignal]:
    """Rank deterministically without inventing the source site's private score."""

    return sorted(
        signals,
        key=lambda signal: (
            -_TIER_RANK[signal.tier],
            -int(signal.combo),
            -int(signal.passes_quality_filters),
            -_QUALITY_RANK[signal.candle_quality],
            -signal.rvol,
            signal.symbol,
        ),
    )


def scan_leaders(
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    *,
    as_of: datetime,
    config: OpeningVolumeConfig | None = None,
    include_watch: bool = False,
    average_turnover_by_symbol: Mapping[str, float] | None = None,
) -> tuple[list[LeaderSignal], dict[str, str]]:
    """Evaluate a universe, returning ranked candidates and explicit failures."""

    config = (config or OpeningVolumeConfig()).validate()
    signals: list[LeaderSignal] = []
    failures: dict[str, str] = {}
    turnover = average_turnover_by_symbol or {}
    for raw_symbol, bars in bars_by_symbol.items():
        symbol = raw_symbol.strip().upper()
        try:
            signal = evaluate_leader(
                symbol,
                bars,
                as_of=as_of,
                config=config,
                average_turnover_inr=turnover.get(symbol),
            )
        except (TypeError, ValueError) as exc:
            failures[symbol or raw_symbol] = str(exc)
            continue
        if signal.is_leader or (include_watch and signal.tier is LeaderTier.WATCH):
            signals.append(signal)
    return rank_leaders(signals), failures


STRATEGY_CONTRACT = {
    "id": "opening_volume_leaders",
    "version": "1.4.0",
    "execution": "guarded_account_mode",
    "documented_rules": [
        "completed 09:15 one-minute candle",
        "same-symbol 09:15 volume mean over 10 prior sessions",
        "WATCH >=2x; SPURT >=3x; STRONG >=5x; EXPLOSIVE >=10x",
        "direction from the 09:15 candle colour",
        "ORB from the 09:15 high/low with first later breach time",
        "Layer-1 price >= INR 100 and 20-session average turnover >= INR 2 crore",
        "ORB freshness <=5 minutes; preferred entry distance <=0.5%; chase >1%",
        "default protective stop at the opposite 09:15-candle boundary",
        "stop distance above about 1.5% is a halve-or-skip warning",
        "five-minute hold and +1% within 60 minutes are follow-through evidence",
        "third consecutive leader day is an explicit repeat-day trap",
        "nearest listed strike in the direction of the signal, with live premium and lot cost",
        "option premium guide: 30% stop and +50% first target",
        "risk guide: 1% per aligned idea, 0.5% in neutral breadth, 2R daily and 4R weekly caps",
        "card evidence includes 50-DMA trend, VWAP, PDH/PDL, RSI, follow-through, repeat volume, and sector",
    ],
    "local_transparent_rules": [
        "candle quality uses configurable body-fraction and close-location thresholds",
        "COMBO is a leader with Layer-1 pass and an aligned first ORB break; it is not restricted to 09:16",
        "volume_signal_time is the 09:15 candle and actionable_signal_time is the first ORB breach",
        "ranking uses tier, combo, quality, RVOL, then symbol",
        "hold and +1% follow-through use the visible ORB boundary as the entry reference",
        (
            "VWAP, previous-day break and one-minute RSI are evidence only and do "
            "not fabricate a conviction score"
        ),
        (
            "when several option expiries exist, the adapter uses the nearest "
            "non-expired listed expiry"
        ),
        "50-DMA and 52-week evidence use prior completed Kite daily candles",
        "0-100 Sterling score publishes every component, lower/upper bounds, and evidence coverage",
        "seven-factor Sterling conviction uses explicit repeat-volume, follow-through, RSI, and sector thresholds",
        "Sterling Momentum Box X/Y and COMBO predicates are versioned and inspectable",
        "automatic execution requires all strategy, account, quote, sizing, idempotency, and protection gates",
    ],
    "unknown_and_omitted": [
        "ORION proprietary numeric strength-score weights; Sterling publishes a separate replacement",
        "ORION private COMBO predicate; Sterling publishes a separate replacement",
        "ORION server-side Momentum Lab predicates; Sterling publishes separate Box X/Y rules",
        "ORION unpublished conviction thresholds; Sterling publishes explicit local thresholds",
        "NIFTY 200/500 membership without a current authoritative constituent feed",
    ],
}
