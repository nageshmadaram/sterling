"""Configuration for the Gamma Move strategy.

Every threshold here was measured, not guessed. The calibration ran on 598 NSE
stock-option contracts across 104 underlyings -- 193,135 fifteen-minute bars
carrying real open interest, plus 35,020 daily spot bars -- pulled 2026-08-26.
The full record is ``backend/study/gamma_move/`` and
``docs/strategy/gamma-move/VALIDATION_REPORT.md``.

The headline result is not the one the source predicts, and the defaults encode
it rather than the story:

* **The entry triple alone has no measurable edge.** Bars passing it reached a
  30% favourable excursion within two sessions 24.7% of the time [20.9, 28.9]
  against an unconditional 21.7% [21.5, 21.9]. The intervals overlap.
* **The level filter is what carries it.** Restricting the same triple to bars
  where spot sat within 1% of a confirmed level lifted that to 46.2%
  [31.6, 61.4] -- a lower bound above the baseline's upper bound. (Re-measured
  through the shipped :func:`levels.find_levels` after a plateau bug in the
  pivot rule was fixed; the earlier figure through the study's own copy was
  45.0% [30.7, 60.2].)
* **The regime multiplier was actively harmful at the obvious default.**
  SuperTrend at multiplier 3.0 made agreeing trades *worse* than disagreeing
  ones (-3.3pp at period 10). At multiplier 2.0 the sign flips and holds
  positive across every period tested (+5.1 to +7.0pp). 2.0 is the default for
  that reason and 3.0 is the trap it was chosen over.

So ``level_proximity_pct`` is the load-bearing setting in this file, and
loosening it is not a small change -- it is the difference between a measured
edge and none.

Validation is here rather than at the API boundary so a config persisted by an
older build, or edited straight in the database, cannot become a trading config
the engine rejects mid-session.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Optional

from app.engines.option_contracts import EXPIRY_SELECTIONS, EXPIRY_SERIES

LEVEL_TIMEFRAMES: frozenset[str] = frozenset({"day", "60minute", "15minute"})
TRIGGER_TIMEFRAMES: frozenset[str] = frozenset({"5minute", "15minute", "30minute"})
EXIT_POLICIES: frozenset[str] = frozenset({"TIME_STOP", "PERCENT_TARGET", "TRAILING_STOP"})
STOP_BASES: frozenset[str] = frozenset({"POINTS", "PERCENT"})
SIZING_MODES: frozenset[str] = frozenset({"LOTS", "RISK_PCT"})
#: Where the protective stop lives, with the same vocabulary and default as the
#: SuperTrend engine's ``stop_mode``. ``broker`` is a GTT at Zerodha that survives
#: this process dying; ``monitor`` is our own tick loop, which exits intrabar but
#: only while we are alive. ``both`` is the production answer and the default.
STOP_MODES: frozenset[str] = frozenset({"broker", "monitor", "both"})
DATA_SOURCES: frozenset[str] = frozenset({"kite"})

#: Exit policies that have never been validated against anything. The source
#: gives no exit rule at all -- its 2x and 3x figures are outcomes, not a rule --
#: so only the time stop it does support may run with real money.
RESEARCH_ONLY_EXIT_POLICIES: frozenset[str] = frozenset({"PERCENT_TARGET", "TRAILING_STOP"})

#: Bars per session on a 15-minute chart, 09:15-15:30. Used to express holding
#: periods in sessions rather than in bars.
BARS_PER_SESSION: dict[str, int] = {"5minute": 75, "15minute": 25, "30minute": 13}

#: What the calibration measured, published so the API can show provenance
#: beside each number instead of asking the reader to trust it.
CALIBRATION = {
    "sample": "598 contracts / 104 underlyings / 193,135 15m OI bars, 2026-08-26",
    "level_proximity_pct": "1.0 — MFE>=30% 46.2% [31.6,61.4] vs 21.7% [21.5,21.9] baseline (n=39)",
    "min_oi_drop_pct": "3.0 — the 98.6th percentile of observed bar-on-bar OI drops",
    "volume_spike_mult": "2.5 — the 87th percentile of volume against a 20-bar mean",
    "min_price_gain_pct": "2.0 — the 93rd percentile of bar-on-bar premium change",
    "regime_multiplier": "2.0 — +5.1pp; multiplier 3.0 measured -3.3pp, i.e. inverted",
    "min_option_premium": "10.0 — below this the 0.05 tick is a >0.5% price quantum",
    "stop_percent": "30.0 — hit by 16% of calibrated signals; a 20% stop by 41%",
}

#: Fields whose defaults came from the calibration run above. Anything NOT in
#: this set is a judgement call, and the UI says so.
CALIBRATED_FIELDS: frozenset[str] = frozenset(CALIBRATION) - {"sample"}


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
class GammaMoveConfig:
    """Immutable strategy configuration. Construct, then :meth:`validate`."""

    #: Whether this engine scans and may trade. Defaults ON.
    #:
    #: It is not a safety device and does not pretend to be one. What stands
    #: between this engine and real money is the account's paper/live setting,
    #: the engine's manual/auto setting, the kill switch, and the risk caps
    #: below -- all of which apply whatever this is set to. An engine shipped
    #: off just does nothing until somebody finds the toggle, and a switch whose
    #: only effect is to hide the strategy from its own operator is not caution.
    #:
    #: What it DOES mean: with the account LIVE and the engine on AUTO, an
    #: enabled engine trades. That is the correct reading of "enabled", and it
    #: is the combination to think about -- not this flag on its own.
    enabled: bool = True

    # --- universe -----------------------------------------------------------
    # Same field names, semantics and liquidity boundary as every other engine
    # here. An earlier draft invented `max_universe = 150`, which was both an
    # arbitrary number and a way past the curated registry -- the whole point of
    # that registry is that "arbitrary or thin F&O names cannot be included",
    # and a 2x on a contract you cannot exit is not a 2x.
    #
    # Storage is per-engine because engines legitimately scan different
    # universes; what is shared is the vocabulary and the eligible set.
    scan_stocks: tuple[str, ...] = ()
    #: True = every eligible high-liquidity stock, never every listed F&O name.
    scan_all_stocks: bool = True
    #: Master switch above the stock list, as in the SuperTrend engine.
    stock_contracts: bool = True
    #: Empty by design: every worked example in the source is a stock, and the
    #: mechanism needs writers concentrated at one strike, which an index chain
    #: spreads across many. Listed so indices are a choice, not an omission.
    scan_indices: tuple[str, ...] = ()
    min_option_oi: int = 50_000
    min_option_volume: int = 1_000
    #: MEASURED. At a 0.05 tick, a 0.73 option moves in 7% steps, so
    #: `min_price_gain_pct` would be measuring tick granularity rather than a
    #: gamma move. The source's own entries were at 75, 540 and 600.
    min_option_premium: float = 10.0
    max_spread_pct: float = 3.0

    # --- levels -------------------------------------------------------------
    level_timeframe: str = "day"
    level_lookback_days: int = 120
    pivot_lookback: int = 5
    level_cluster_pct: float = 0.75
    min_level_touches: int = 2
    #: MEASURED, and the single most important number in this file. See module
    #: docstring. Must be > 0: zero would mean "exactly on the level", which no
    #: tick ever is, so the strategy would silently never fire.
    level_proximity_pct: float = 1.0

    # --- strike -------------------------------------------------------------
    strike_window_pct: float = 2.0
    max_candidates: int = 25
    #: Source rule, not calibrated. The strike must be the chain-max OI on the
    #: same expiry and option type — a local max in a sampled window is not a wall.
    require_chain_max_oi: bool = True
    #: Source rule, not calibrated. Spot must have broken through (or sit at)
    #: the wall, inside ``level_proximity_pct``.
    require_spot_through_strike: bool = True

    # --- contracts ----------------------------------------------------------
    #: Same names, order and meaning as every other engine's contract settings.
    #: An earlier draft called these `min_days_to_expiry`/`max_days_to_expiry`,
    #: which is the same idea under a private name -- one more vocabulary for a
    #: reader to hold.
    #:
    #: The source trades only the last week or two of a contract. NSE stock
    #: options are monthly-only, so this window is roughly the 15th onward.
    expiry_selection: str = "nearest"
    expiry_dte_min: int = 0
    expiry_dte_max: int = 14
    #: On expiry day the open-interest signal degenerates into settlement
    #: mechanics and the premium is nearly all gamma already -- a different
    #: trade wearing this one's name. Previously expressed as
    #: `min_days_to_expiry = 1`, which said the same thing less clearly.
    avoid_expiry_day: bool = True
    #: Which listed expiries the contract picker offers, shared vocabulary with
    #: the other engines. Stocks are monthly-only on NSE.
    scan_expiries_indices: tuple[str, ...] = ("weekly", "monthly")
    scan_expiries_stocks: tuple[str, ...] = ("monthly",)
    #: Which *listed* contracts, by rank, the Option contracts picker selects —
    #: the same storage the SuperTrend engine's picker writes, so the control is
    #: literally the same one rather than a lookalike.
    #:
    #: Ranks, not dates: "nearest listed" is rank 0 whatever date the exchange
    #: has it on, and the exact date differs between instruments. Expired
    #: contracts drop out on their own, and nothing is inferred from weekdays or
    #: holidays.
    scan_weekly_series_indices: tuple[int, ...] = (0, 1, 2, 3)
    scan_monthly_series_indices: tuple[int, ...] = (0, 1)
    scan_monthly_series_stocks: tuple[int, ...] = (0, 1)

    # --- trigger ------------------------------------------------------------
    trigger_timeframe: str = "15minute"
    volume_lookback: int = 20
    min_oi_drop_pct: float = 3.0          # MEASURED
    volume_spike_mult: float = 2.5        # MEASURED
    min_price_gain_pct: float = 2.0       # MEASURED
    confirm_bars: int = 1

    # --- regime -------------------------------------------------------------
    regime_enabled: bool = True
    regime_timeframe: str = "day"
    regime_period: int = 10
    regime_multiplier: float = 2.0        # MEASURED — 3.0 inverts the gate

    # --- stop ---------------------------------------------------------------
    stop_basis: str = "PERCENT"
    swing_lookback: int = 6
    stop_percent: float = 30.0            # MEASURED
    stop_points: float = 0.0

    # --- exit ---------------------------------------------------------------
    exit_policy: str = "TIME_STOP"
    max_hold_days: int = 2
    target_pct: float = 0.0
    trail_pct: float = 0.0
    trail_start_pct: float = 0.0
    close_at_session_end: bool = False
    #: Broker GTT + our tick monitor. Not "NONE": an option long with nothing
    #: watching it is the state this engine must never be in, and the previous
    #: default advertised a protection mode that no code implemented.
    stop_mode: str = "both"

    # --- session ------------------------------------------------------------
    #: Not 09:15. The first bar of a session has no prior bar inside the same
    #: session, and differencing OI across the boundary produces a phantom
    #: unwind -- measured at 2.95% of boundaries versus 0.85% within a session.
    session_start: str = "09:30"
    session_end: str = "15:15"
    scan_interval_seconds: int = 300

    # --- risk ---------------------------------------------------------------
    sizing_mode: str = "RISK_PCT"
    risk_per_trade_pct: float = 1.0
    capital_inr: float = 500_000.0
    lots: int = 0
    max_concurrent_positions: int = 3
    max_new_trades_per_day: int = 2
    #: One lot of a typical NSE stock option costs more than Rs 25,000 in
    #: premium (RELIANCE 500 x ~53 = Rs 26,500 in the calibration sample), and a
    #: cap below one lot silently refuses every trade while looking like a
    #: risk setting rather than an off switch.
    max_premium_at_risk_inr: float = 60_000.0
    daily_loss_limit_inr: float = 10_000.0
    descale_after_losses: int = 3
    descale_factor: float = 0.5
    rescale_after_wins: int = 2

    # --- plumbing -----------------------------------------------------------
    # There is deliberately no `execution_mode` here.
    #
    # Paper-vs-live for Kite is `account.is_paper`, set from the Trading Mode
    # panel, and `KiteClient` already simulates every order when it is on. A
    # second copy on this config would be duplicated storage of a setting that
    # has a home -- the exact failure this codebase keeps having to undo -- and
    # would let the engine believe it was papering while the client traded for
    # real, or the reverse. Likewise manual-vs-auto is the engine's
    # `auto_execute`. Both are READ where needed and never stored here.
    data_source: str = "kite"
    auto_execute: bool = False

    # ------------------------------------------------------------------ rules
    def validate(self) -> "GammaMoveConfig":
        for name, vocab in (("level_timeframe", LEVEL_TIMEFRAMES),
                            ("trigger_timeframe", TRIGGER_TIMEFRAMES),
                            ("regime_timeframe", LEVEL_TIMEFRAMES),
                            ("exit_policy", EXIT_POLICIES),
                            ("stop_basis", STOP_BASES),
                            ("sizing_mode", SIZING_MODES),
                            ("stop_mode", STOP_MODES),
                            ("data_source", DATA_SOURCES)):
            if getattr(self, name) not in vocab:
                raise ValueError(f"{name} must be one of {sorted(vocab)}")

        # Thresholds. None of these may be zero: a zero threshold does not
        # "disable" its condition, it makes the condition trivially true, which
        # silently deletes a third of the entry rule while looking like a
        # setting. Where a rule can genuinely be switched off it has a boolean.
        if self.level_proximity_pct <= 0:
            raise ValueError(
                "level_proximity_pct must be > 0 — it is the measured edge in this "
                "strategy, and 0 would mean 'exactly on the level', which never happens")
        if self.min_oi_drop_pct <= 0:
            raise ValueError("min_oi_drop_pct must be > 0")
        if self.volume_spike_mult <= 1.0:
            raise ValueError(
                "volume_spike_mult must be > 1.0 — at or below 1 every bar is "
                "'abnormal' and the volume condition stops filtering anything")
        if self.min_price_gain_pct <= 0:
            raise ValueError("min_price_gain_pct must be > 0")
        if not 1 <= self.confirm_bars <= 3:
            raise ValueError("confirm_bars must be between 1 and 3")
        if self.volume_lookback < 5:
            raise ValueError("volume_lookback must be >= 5")
        if self.pivot_lookback < 2:
            raise ValueError("pivot_lookback must be >= 2")
        if self.level_cluster_pct <= 0:
            raise ValueError("level_cluster_pct must be > 0")
        if self.min_level_touches < 1:
            raise ValueError("min_level_touches must be >= 1")
        if self.level_lookback_days < 30:
            raise ValueError("level_lookback_days must be >= 30 to find any pivots")
        if self.strike_window_pct <= 0:
            raise ValueError("strike_window_pct must be > 0")
        if self.max_candidates < 1:
            raise ValueError("max_candidates must be >= 1")
        # The curated registry is the eligible set for every engine here. An
        # arbitrary or thin name is refused rather than silently dropped, so a
        # typo in a symbol list cannot look like a quiet market.
        from app.services.kite_engine.stock_registry import HIGH_LIQUIDITY_STOCK_NAMES
        unknown = sorted(set(n.upper() for n in self.scan_stocks)
                         - set(HIGH_LIQUIDITY_STOCK_NAMES))
        if unknown:
            raise ValueError(
                f"scan_stocks contains names outside the curated high-liquidity "
                f"registry: {', '.join(unknown)}")
        if not self.stock_contracts and not self.scan_indices:
            raise ValueError(
                "nothing to scan: stock_contracts is off and no indices are selected")

        # Contracts. Same rules and wording as the other engines.
        if self.expiry_selection.strip().lower() not in EXPIRY_SELECTIONS:
            raise ValueError(f"expiry_selection must be one of {sorted(EXPIRY_SELECTIONS)}")
        if self.expiry_dte_min < 0:
            raise ValueError("expiry_dte_min must be zero or greater")
        if self.expiry_dte_max < self.expiry_dte_min:
            raise ValueError(
                "expiry_dte_max must be greater than or equal to expiry_dte_min")
        # `expiry_dte_max = 0` with avoid_expiry_day on leaves nothing eligible:
        # the only day in range is the one being excluded.
        if self.avoid_expiry_day and self.expiry_dte_min == 0 and self.expiry_dte_max == 0:
            raise ValueError(
                "avoid_expiry_day leaves no eligible expiry when the DTE range is 0-0")
        for name in ("scan_expiries_indices", "scan_expiries_stocks"):
            bad = sorted(set(getattr(self, name)) - EXPIRY_SERIES)
            if bad:
                raise ValueError(f"{name} must be drawn from {sorted(EXPIRY_SERIES)}")
        for name, limit in (("scan_weekly_series_indices", 4),
                            ("scan_monthly_series_indices", 2),
                            ("scan_monthly_series_stocks", 2)):
            ranks = getattr(self, name)
            if any(not isinstance(r, int) or r < 0 or r >= limit for r in ranks):
                raise ValueError(f"{name} ranks must be between 0 and {limit - 1}")
            if len(set(ranks)) != len(ranks):
                raise ValueError(f"{name} contains a duplicate rank")

        if self.regime_period < 2:
            raise ValueError("regime_period must be >= 2")
        if self.regime_multiplier <= 0:
            raise ValueError("regime_multiplier must be > 0")

        if self.swing_lookback < 2:
            raise ValueError("swing_lookback must be >= 2")
        if self.stop_percent >= 100:
            raise ValueError("stop_percent must be below 100 — a 100% stop is the premium")
        for name in ("stop_percent", "stop_points", "target_pct", "trail_pct",
                     "trail_start_pct", "risk_per_trade_pct"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.max_hold_days < 1:
            raise ValueError("max_hold_days must be >= 1")
        if self.exit_policy == "PERCENT_TARGET" and self.target_pct <= 0:
            raise ValueError("exit_policy=PERCENT_TARGET requires target_pct > 0")
        if self.exit_policy == "TRAILING_STOP" and self.trail_pct <= 0:
            raise ValueError("exit_policy=TRAILING_STOP requires trail_pct > 0")

        start = _hhmm(self.session_start, "session_start")
        end = _hhmm(self.session_end, "session_end")
        if start >= end:
            raise ValueError("session_start must be before session_end")
        if self.scan_interval_seconds < 60:
            raise ValueError("scan_interval_seconds must be >= 60")

        if self.lots < 0:
            raise ValueError("lots cannot be negative")
        if self.capital_inr <= 0:
            raise ValueError("capital_inr must be > 0")
        if self.max_concurrent_positions < 1:
            raise ValueError("max_concurrent_positions must be >= 1")
        if self.max_new_trades_per_day < 1:
            raise ValueError("max_new_trades_per_day must be >= 1")
        if self.max_premium_at_risk_inr <= 0:
            raise ValueError("max_premium_at_risk_inr must be > 0")
        if self.daily_loss_limit_inr <= 0:
            raise ValueError("daily_loss_limit_inr must be > 0")
        if self.descale_after_losses < 1:
            raise ValueError("descale_after_losses must be >= 1")
        if not 0 < self.descale_factor <= 1:
            raise ValueError("descale_factor must be in (0, 1]")
        if self.rescale_after_wins < 1:
            raise ValueError("rescale_after_wins must be >= 1")
        if self.min_option_premium < 0:
            raise ValueError("min_option_premium cannot be negative")

        # --- engineering invariants, and they are not conditional ------------
        #
        # These used to sit behind `execution_mode == "live"`. That was wrong
        # twice over: the mode was duplicated storage of `account.is_paper`, and
        # a rule that only holds when the money is real is a rule the paper
        # results were never measured under. A paper run that trades unprotected
        # is not a rehearsal of the live one.
        if self.stop_percent <= 0 and self.stop_points <= 0:
            raise ValueError(
                "a stop is required: this engine buys options, where the maximum "
                "loss without one is the entire premium")
        # `stop_mode == "monitor"` is allowed and is not silently equivalent to
        # the others: it means nothing sits at the broker, so if this process
        # dies while holding, the position is unprotected until it comes back.
        # The engine surfaces that as a warning rather than refusing it, because
        # a research run legitimately may not want to leave GTTs behind.
        return self

    def warnings(self) -> list[str]:
        """Configured choices worth saying out loud. Not errors.

        The difference matters: `validate()` refuses what is unsound, and this
        reports what is merely risky, so the operator sees a sentence instead of
        discovering it from a fill.
        """
        out: list[str] = []
        if self.stop_mode == "monitor":
            out.append("stop_mode=monitor leaves nothing at the broker — if this "
                       "process dies while holding, the position is unprotected")
        if self.exit_policy in RESEARCH_ONLY_EXIT_POLICIES:
            out.append(f"exit_policy={self.exit_policy} is not supported by the source, "
                       "which gives no exit rule at all — only TIME_STOP is")
        if self.stop_basis == "POINTS":
            out.append("stop_basis=POINTS on premiums that run from ~10 to ~600 means "
                       "the same number is a 5% risk at one end and 100% at the other")
        if not self.regime_enabled:
            out.append("the trend gate is off, and the source names a corrective market "
                       "as the flaw that broke this strategy")
        return out
    # ------------------------------------------------------------- accessors
    @property
    def size_is_set(self) -> bool:
        return (self.lots > 0) if self.sizing_mode == "LOTS" else (self.risk_per_trade_pct > 0)

    @property
    def bars_per_session(self) -> int:
        return BARS_PER_SESSION.get(self.trigger_timeframe, 25)

    def stop_distance_inr(self, reference: float) -> float:
        """The initial stop distance in rupees, whichever way it was expressed."""
        if self.stop_basis == "PERCENT":
            return abs(float(reference)) * (self.stop_percent / 100.0)
        return float(self.stop_points)

    def effective_quantity(self, lot_size: int, lots: int) -> int:
        return max(0, int(lots)) * max(1, int(lot_size or 1))

    def sizing_blocker(self, lot_size: int, lots: int) -> Optional[str]:
        """Why this size cannot be traded, in words, or None if it can.

        One function, asked by both the board and arm(), so they cannot disagree
        about a size the broker would refuse.
        """
        lot = max(1, int(lot_size or 1))
        if lots <= 0:
            return ("lots not set" if self.sizing_mode == "LOTS"
                    else "risk budget is too small for one lot")
        qty = self.effective_quantity(lot, lots)
        if qty <= 0:
            return "size resolves to zero quantity"
        return None

    def as_dict(self) -> dict:
        out = {}
        for f in fields(self):
            v = getattr(self, f.name)
            out[f.name] = list(v) if isinstance(v, tuple) else v
        return out

    @classmethod
    def field_names(cls) -> frozenset[str]:
        return frozenset(f.name for f in fields(cls))
