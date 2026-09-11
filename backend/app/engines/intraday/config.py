"""Knobs for the three intraday strategies, in one config.

They share a universe, a session window, a timeframe and a contract picker, and
they differ only in what makes them fire. Three separate config objects would
have meant three copies of the shared half and three places for it to drift —
the recurring bug class in this codebase is a setting that exists twice and is
honoured once.

Field names are prefixed by strategy (``pb_`` pivot break, ``rb_`` MA ribbon,
``vs_`` VWAP/SuperTrend) so the settings UI can group them without a mapping
table, and the shared fields carry no prefix.

NOTHING HERE IS CALIBRATED. Every default is either the number the operator
specified or a judgement call, and ``CALIBRATED_FIELDS`` is deliberately empty
so the settings page marks each figure as unmeasured rather than rendering a
bare number a reader may reasonably take for a result.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Literal

from app.engines.option_contracts import EXPIRY_SERIES, MONEYNESS

TIMEFRAMES: frozenset[str] = frozenset({"1m", "3m", "5m", "10m", "15m", "30m"})
PIVOT_TYPES: frozenset[str] = frozenset({"fibonacci", "classic"})
PIVOT_PERIODS: frozenset[str] = frozenset({"day", "week"})
STOP_SOURCES: frozenset[str] = frozenset({"vwap", "supertrend", "wider"})
TRAIL_MODES: frozenset[str] = frozenset({"none", "breakeven", "atr", "structure"})
SIZING_MODES: frozenset[str] = frozenset({"RISK_PCT", "LOTS"})
#: Where the exit lives. ``both`` is the production answer — a resting GTT that
#: survives this process dying, plus a tick loop that can exit intrabar.
STOP_MODES: frozenset[str] = frozenset({"broker", "monitor", "both"})

#: Published so the UI never types a strategy id by hand.
STRATEGY_KEYS: tuple[str, ...] = ("pivot_break", "ma_ribbon", "vwap_supertrend")

CALIBRATION: dict[str, str] = {
    "sample": "none — no walk-forward run has been done on these three strategies",
}
CALIBRATED_FIELDS: frozenset[str] = frozenset()

_INDEX_DEFAULTS = ("NIFTY", "BANKNIFTY")


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
class IntradayConfig:
    # ── shared ────────────────────────────────────────────────────────────────
    enabled: bool = True
    #: Auto-execution is OFF and stays off until a walk-forward run exists.
    #: Every engine in this repo that shipped with it on was turned off again.
    auto_execute: bool = False
    timeframe: str = "5m"
    scan_indices: tuple[str, ...] = _INDEX_DEFAULTS
    scan_stocks: tuple[str, ...] = ()
    scan_all_stocks: bool = False
    #: Bars of history before any strategy may fire. The slowest input is the
    #: 55 EMA, which is not merely undefined before bar 55 — it is *wrong*, and
    #: a seeded EMA reads plausible while it is still wrong. 80 gives it room.
    warmup_bars: int = 80
    #: No entry before this: the first 5-minute bars of the session carry the
    #: opening auction, where a pivot break and a ribbon cross are both noise.
    session_start: str = "09:20"
    #: No NEW entry after this. Exits are never gated by it.
    no_entry_after: str = "15:00"
    session_end: str = "15:15"
    #: Bars a symbol must wait after a fired signal before the same strategy
    #: may fire again on it. Without this a break that oscillates across the
    #: level fires on every bar it recrosses.
    cooldown_bars: int = 6
    max_signals_per_symbol_per_day: int = 3
    #: Contract picking. Only the SERIES, because that is the axis the strike
    #: resolver actually takes — an `expiry_selection` knob alongside it would
    #: be a second control for one decision, honoured by neither.
    expiry_series_indices: tuple[str, ...] = ("weekly",)
    expiry_series_stocks: tuple[str, ...] = ("monthly",)
    moneyness: str = "ATM"
    min_option_oi: float = 0.0
    min_option_volume: float = 0.0
    min_option_premium: float = 5.0
    max_spread_pct: float = 2.0
    #: ── Sizing ────────────────────────────────────────────────────────────
    #: RISK_PCT sizes so the distance to the stop costs a set share of capital,
    #: and BLOCKS when even one lot breaks the budget rather than quietly
    #: turning the percentage into a suggestion.
    sizing_mode: str = "RISK_PCT"
    risk_per_trade_pct: float = 1.0
    capital_inr: float = 100_000.0
    #: Ceiling in LOTS, and the fixed size when ``sizing_mode`` is LOTS.
    #: Measured elsewhere in this repo: max_lots is the real sizer, risk_pct is
    #: a drawdown dial. Both are here because they do different jobs.
    lots: int = 1
    max_lots: int = 5
    #: Take the minimum lot even when it breaks the risk budget. Off: a signal
    #: that cannot be sized within the cap is skipped, not squeezed in.
    allow_min_lot_over_risk: bool = False

    #: ── Protection ────────────────────────────────────────────────────────
    stop_mode: str = "both"
    #: The spot stop converted to a premium stop needs a delta. Without a
    #: quoted one this is the fallback: the premium stop sits this far below
    #: entry. Conservative on purpose — an option loses premium faster than
    #: spot moves, so a spot-derived stop that assumes delta 1.0 never fires.
    premium_stop_pct: float = 30.0
    #: Give back at most this much of the best premium seen. This is the trail
    #: that actually protects an option position: the spot rule can still be
    #: intact while the premium has round-tripped, which is exactly where an
    #: open drawdown builds.
    premium_trail_pct: float = 25.0
    #: R banked before the stop moves to breakeven, and before the premium
    #: ratchet starts. Below this the trade has not earned a tighter stop.
    trail_activate_r: float = 1.0
    #: Flatten everything at the session close. An intraday strategy holding
    #: overnight is a different strategy.
    close_at_session_end: bool = True

    #: ── Risk limits ───────────────────────────────────────────────────────
    max_concurrent_positions: int = 3
    max_new_trades_per_day: int = 6
    #: Rupees. 0 = off. Counted on this engine's own realised P&L for the day.
    daily_loss_limit_inr: float = 0.0
    #: Halve the size after this many consecutive losses. 0 = off.
    descale_after_losses: int = 2
    scan_interval_seconds: int = 60

    # ── 1. pivot_break — EMA9 + Fibonacci pivot breakout ──────────────────────
    pb_enabled: bool = True
    pb_ema_length: int = 9
    pb_pivot_type: str = "fibonacci"
    pb_pivot_period: str = "day"
    #: "Green (possibly strong)" made testable: body as a share of the bar's
    #: whole range. A doji that pokes through a pivot and closes above it is a
    #: break on the letter of the rule and a coin flip in fact.
    pb_min_body_pct: float = 55.0
    #: And strong relative to what the instrument has been doing — body against
    #: the 14-bar ATR. Body-percent alone passes a tiny bar in a dead tape.
    pb_min_body_atr: float = 0.5
    #: The break must be FRESH: the prior bar closed on the other side of both
    #: the EMA and the pivot. Without this every subsequent bar above the level
    #: is also a "break" and the engine re-enters an old move at a worse price.
    pb_require_fresh_break: bool = True
    #: Require the close to clear the level by this share of ATR, so a tick
    #: through a pivot is not a break. 0 disables the buffer.
    pb_break_buffer_atr: float = 0.10
    pb_atr_length: int = 14
    pb_target_r: float = 2.0
    pb_target2_r: float = 3.0
    pb_trail_mode: str = "structure"
    #: Move the stop to entry once this many R is banked, then trail.
    pb_breakeven_at_r: float = 1.0
    pb_trail_atr_mult: float = 1.5
    #: Reject a setup whose own candle is so wide the stop is unusable. This is
    #: a filter, not a stop override: silently shrinking the stop to fit would
    #: make the engine claim a risk it is not actually taking.
    pb_max_stop_pct: float = 1.2

    #: ── Dynamic stops and targets (all three strategies) ──────────────────
    #: The stated stops are structural — a candle's low, the slow EMA, VWAP —
    #: and a structural level can land inside the noise on a quiet bar. This
    #: widens such a stop to a floor expressed in ATR. It only ever WIDENS:
    #: tightening a rule's own stop would be trading a different strategy, and
    #: a stop inside the spread is not a stop, it is a fee.
    dynamic_stops: bool = True
    stop_atr_floor_mult: float = 0.5
    #: And the matching ceiling. A structural stop further than this is not
    #: refused here (each strategy has its own cap) but is reported on the row.
    stop_atr_cap_mult: float = 4.0
    #: Extends a target that volatility has already overtaken. Like the stop
    #: floor it only ever moves the target AWAY: a rule that says 1:2 gets at
    #: least 1:2. 0 disables.
    dynamic_targets: bool = True
    target_atr_mult: float = 2.0

    # ── 2. ma_ribbon — EMA 8 / 13 / 21 / 55 full cross ────────────────────────
    rb_enabled: bool = True
    rb_ema_fast: int = 8        # blue
    rb_ema_1: int = 13          # green
    rb_ema_2: int = 21          # yellow
    rb_ema_slow: int = 55       # red
    #: The whole rule. The 55 must sit below (or above) EVERY other line — a
    #: cross of one line is explicitly not a signal, which is the instruction
    #: this engine was given and the thing a naive "slow crossed fast" check
    #: gets wrong.
    rb_require_full_cross: bool = True
    #: Closes the full cross must hold for before it counts. 1 = the bar it
    #: completes on.
    rb_confirm_bars: int = 1
    #: Minimum ribbon separation — the spread between the outermost lines as a
    #: percentage of price. In a flat tape the four EMAs braid, and a "full
    #: cross" happens several times an hour without a trend behind it.
    rb_min_spread_pct: float = 0.08
    #: Hold until the opposite full cross. This is the strategy's stated exit,
    #: and it has no price stop of its own, so one is added below.
    rb_exit_on_opposite_cross: bool = True
    #: A ribbon trade with no price stop is open-ended risk between crosses.
    #: The stop rides the slow line by this ATR multiple; 0 leaves the position
    #: with the cross as its only exit, which is the literal rule.
    rb_stop_atr_mult: float = 1.5
    rb_atr_length: int = 14
    rb_target_r: float = 3.0

    # ── 3. vwap_supertrend — SuperTrend flip confirmed by VWAP side ───────────
    vs_enabled: bool = True
    vs_atr_length: int = 18
    vs_factor: float = 1.46
    #: Fixed objective, in points, as specified.
    vs_target_points: float = 20.0
    vs_stop_source: str = "vwap"
    #: A VWAP stop is whatever distance VWAP happens to be — on a wide bar that
    #: is a 1:0.2 trade. Beyond this the setup is skipped rather than resized.
    vs_max_stop_points: float = 40.0
    #: And below this the stop is inside the noise; a flip that closes right on
    #: VWAP would otherwise be stopped by the next tick.
    vs_min_stop_points: float = 5.0
    vs_trail_after_points: float = 12.0
    #: Refuse to trade a VWAP computed with no volume. Kite reports zero volume
    #: on index spot, and a zero-weight VWAP collapses onto the typical price —
    #: which makes "closed below VWAP" true or false essentially at random.
    #: This exact failure silenced an earlier engine here for its whole life.
    vs_require_volume_vwap: bool = True
    #: The flip must be FRESH — the SuperTrend changed colour on this bar, not
    #: five bars ago. "Whenever SuperTrend changes to red" is an event.
    vs_require_fresh_flip: bool = True
    #: Bars the close may lag the flip and still count, for a flip whose bar
    #: closed on the wrong side of VWAP but the next one confirmed. 0 = the
    #: flip bar itself must close on the right side.
    vs_confirm_within_bars: int = 1
    #: The 20-point objective is the one target in this pack that is NOT
    #: volatility-aware: 20 points is a whole move on FINNIFTY and a rounding
    #: error on a fast BANKNIFTY day. This lifts it to the ATR target when ATR
    #: is larger, and never lowers it.
    vs_dynamic_target: bool = True
    vs_target_atr_mult: float = 2.0

    # ------------------------------------------------------------------ checks

    def validate(self) -> "IntradayConfig":
        if self.timeframe not in TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {sorted(TIMEFRAMES)}")
        if self.pb_pivot_type not in PIVOT_TYPES:
            raise ValueError(f"pb_pivot_type must be one of {sorted(PIVOT_TYPES)}")
        if self.pb_pivot_period not in PIVOT_PERIODS:
            raise ValueError(f"pb_pivot_period must be one of {sorted(PIVOT_PERIODS)}")
        if self.pb_trail_mode not in TRAIL_MODES:
            raise ValueError(f"pb_trail_mode must be one of {sorted(TRAIL_MODES)}")
        if self.vs_stop_source not in STOP_SOURCES:
            raise ValueError(f"vs_stop_source must be one of {sorted(STOP_SOURCES)}")
        if self.sizing_mode not in SIZING_MODES:
            raise ValueError(f"sizing_mode must be one of {sorted(SIZING_MODES)}")
        if self.stop_mode not in STOP_MODES:
            raise ValueError(f"stop_mode must be one of {sorted(STOP_MODES)}")
        if self.moneyness not in MONEYNESS:
            raise ValueError(f"moneyness must be one of {sorted(MONEYNESS)}")
        for name in ("expiry_series_indices", "expiry_series_stocks"):
            bad = sorted(set(getattr(self, name)) - EXPIRY_SERIES)
            if bad:
                raise ValueError(f"{name}: unknown series {bad}")

        # Periods. A zero or negative length is not "off" — it makes the EMA
        # and ATR arithmetic silently wrong, which is the exact failure mode
        # that has cost this repo a strategy before.
        for name in ("pb_ema_length", "pb_atr_length", "rb_ema_fast", "rb_ema_1",
                     "rb_ema_2", "rb_ema_slow", "rb_atr_length", "vs_atr_length",
                     "warmup_bars", "lots", "max_lots", "max_concurrent_positions",
                     "scan_interval_seconds", "max_signals_per_symbol_per_day",
                     "max_new_trades_per_day"):
            if int(getattr(self, name)) < 1:
                raise ValueError(f"{name} must be >= 1")
        for name in ("cooldown_bars", "rb_confirm_bars", "vs_confirm_within_bars",
                     "descale_after_losses"):
            if int(getattr(self, name)) < 0:
                raise ValueError(f"{name} must be >= 0")

        # Sizing. A zero here is not "off" — it is a division or a budget of
        # nothing, and the failure surfaces mid-session as a blocked entry with
        # an unreadable reason.
        if not (0 < self.risk_per_trade_pct <= 100):
            raise ValueError("risk_per_trade_pct must be a percentage in (0, 100]")
        if self.capital_inr <= 0:
            raise ValueError("capital_inr must be > 0")
        if self.lots > self.max_lots:
            raise ValueError("lots must not exceed max_lots")
        if not (0 < self.premium_stop_pct < 100):
            raise ValueError("premium_stop_pct must be a percentage in (0, 100)")
        if not (0 < self.premium_trail_pct < 100):
            raise ValueError("premium_trail_pct must be a percentage in (0, 100)")
        if self.trail_activate_r <= 0:
            raise ValueError("trail_activate_r must be > 0")
        if self.daily_loss_limit_inr < 0:
            raise ValueError("daily_loss_limit_inr must be >= 0 (0 = off)")

        # Dynamic stops and targets.
        for name in ("stop_atr_floor_mult", "stop_atr_cap_mult", "target_atr_mult",
                     "vs_target_atr_mult"):
            if float(getattr(self, name)) < 0:
                raise ValueError(f"{name} must be >= 0")
        if self.stop_atr_cap_mult and self.stop_atr_cap_mult <= self.stop_atr_floor_mult:
            raise ValueError("stop_atr_cap_mult must exceed stop_atr_floor_mult")

        if not (self.rb_ema_fast < self.rb_ema_1 < self.rb_ema_2 < self.rb_ema_slow):
            raise ValueError("ribbon lengths must increase: "
                             "rb_ema_fast < rb_ema_1 < rb_ema_2 < rb_ema_slow")
        if self.warmup_bars < self.rb_ema_slow + 10:
            raise ValueError("warmup_bars must exceed rb_ema_slow by at least 10 — "
                             "a seeded EMA reads plausible while it is still wrong")

        if self.vs_factor <= 0:
            raise ValueError("vs_factor must be > 0")
        if self.vs_target_points <= 0:
            raise ValueError("vs_target_points must be > 0")
        if self.vs_min_stop_points <= 0 or self.vs_max_stop_points <= self.vs_min_stop_points:
            raise ValueError("vs_max_stop_points must exceed vs_min_stop_points, both > 0")
        if not (0.0 <= self.pb_min_body_pct <= 100.0):
            raise ValueError("pb_min_body_pct must be a percentage in [0, 100]")
        for name in ("pb_min_body_atr", "pb_break_buffer_atr", "pb_trail_atr_mult",
                     "rb_min_spread_pct", "rb_stop_atr_mult", "vs_trail_after_points",
                     "min_option_premium", "max_spread_pct", "min_option_oi",
                     "min_option_volume"):
            if float(getattr(self, name)) < 0:
                raise ValueError(f"{name} must be >= 0")
        for name in ("pb_target_r", "pb_target2_r", "rb_target_r", "pb_max_stop_pct",
                     "pb_breakeven_at_r"):
            if float(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be > 0")
        if self.pb_target2_r <= self.pb_target_r:
            raise ValueError("pb_target2_r must exceed pb_target_r")

        start = _hhmm(self.session_start, "session_start")
        cut = _hhmm(self.no_entry_after, "no_entry_after")
        end = _hhmm(self.session_end, "session_end")
        if not (start < cut <= end):
            raise ValueError("session_start < no_entry_after <= session_end is required")

        if not (self.scan_indices or self.scan_stocks or self.scan_all_stocks):
            raise ValueError("nothing to scan: pick at least one index or stock")
        return self

    def warnings(self) -> list[str]:
        """Risky-but-allowed choices, said out loud. Not errors."""
        out: list[str] = []
        if self.stop_mode == "monitor":
            out.append("stop_mode=monitor leaves nothing at the broker — if this "
                       "process dies while holding, the position is unprotected")
        if not self.close_at_session_end:
            out.append("close_at_session_end is OFF: an intraday strategy will "
                       "hold overnight, which is a different strategy with "
                       "overnight gap risk none of these three was written for")
        if self.allow_min_lot_over_risk:
            out.append("allow_min_lot_over_risk is ON, so a signal that cannot be "
                       "sized inside risk_per_trade_pct is taken at one lot anyway")
        if self.daily_loss_limit_inr == 0:
            out.append("daily_loss_limit_inr is 0, so this engine has no daily "
                       "stop of its own — only the account-wide breaker")
        if not self.dynamic_stops:
            out.append("dynamic_stops is OFF: a structural stop that lands inside "
                       "the spread will be taken out by the next tick")
        if self.auto_execute:
            out.append("auto_execute is ON and none of these three strategies has a "
                       "walk-forward result — every number here is a judgement call")
        if not self.vs_require_volume_vwap:
            out.append("vs_require_volume_vwap is OFF: on index spot Kite reports no "
                       "volume, so VWAP collapses onto the typical price and the "
                       "close-vs-VWAP test becomes close-vs-itself")
        if self.rb_stop_atr_mult == 0:
            out.append("rb_stop_atr_mult=0 leaves the ribbon trade with no price stop — "
                       "it exits only on the opposite full cross, which can be days")
        if not self.rb_require_full_cross:
            out.append("rb_require_full_cross is OFF, so a cross of ONE line fires — "
                       "the strategy explicitly says not to trade that")
        if not self.pb_require_fresh_break:
            out.append("pb_require_fresh_break is OFF: every bar that stays above the "
                       "level re-fires, which enters an old move at a worse price")
        if self.pb_max_stop_pct >= 3.0:
            out.append(f"pb_max_stop_pct={self.pb_max_stop_pct} accepts very wide signal "
                       "candles; the 1:2 target is then a long way away")
        return out

    def enabled_strategies(self) -> tuple[str, ...]:
        flags = {"pivot_break": self.pb_enabled, "ma_ribbon": self.rb_enabled,
                 "vwap_supertrend": self.vs_enabled}
        return tuple(k for k in STRATEGY_KEYS if flags[k])

    @property
    def timeframe_minutes(self) -> int:
        return {"1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30}[self.timeframe]

    def as_dict(self) -> dict:
        out: dict = {}
        for f in fields(self):
            v = getattr(self, f.name)
            out[f.name] = list(v) if isinstance(v, tuple) else v
        return out

    @classmethod
    def field_names(cls) -> frozenset[str]:
        return frozenset(f.name for f in fields(cls))


#: Config keys that hold sequences, so the store round-trips them without a
#: per-call list of names repeated in three different modules. Declared out here
#: on purpose: an annotated class attribute on a dataclass becomes a FIELD, and
#: it would then be published as a setting and persisted with every config.
TUPLE_FIELDS: tuple[str, ...] = ("scan_indices", "scan_stocks",
                                 "expiry_series_indices", "expiry_series_stocks")
