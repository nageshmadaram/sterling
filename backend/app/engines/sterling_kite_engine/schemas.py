"""API/UI pydantic models for the Kite Sterling Kite Engine."""
from __future__ import annotations

from typing import List, Literal, Optional
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, field_validator
from pydantic import BaseModel, Field, field_validator

from app.engines.sterling_kite_engine.config import ExitMode
from app.engines.navigator.schemas import NavigatorDecision  # noqa: F401 — used in EngineSignalRow.navigator


class AlignmentChip(BaseModel):
    fast: int  # +1 / -1 / 0
    mid: int
    slow: int


class OptionLeg(BaseModel):
    moneyness: str  # ATM / ITM1-5 / OTM1-5
    option_type: str  # CE / PE
    option_symbol: str
    strike: float
    expiry: str
    lot_size: Optional[int] = None
    premium_spot: Optional[float] = None   # entry premium (fill reference) — the Entry column
    premium_sl: Optional[float] = None     # live ratcheting trail stop — the TSL column
    entry_sl: Optional[float] = None       # initial hard stop at the entry bar (fast ST line) — the SL column
    # Premium level of the row's ``target`` (see EngineSignalRow.target). None for
    # SuperTrend signals, which are trend-following and have no fixed target.
    premium_target: Optional[float] = None
    token: Optional[int] = None
    is_active: bool = False   # this contract's SuperTrend is still aligned on the latest bar
    # Contract-local evidence. The grouped parent is a display/sort summary only.
    signal_timestamp_ms: Optional[int] = None
    entry_timestamp_ms: Optional[int] = None
    alignment: Optional[AlignmentChip] = None
    exit_state: Optional[str] = None
    #: This CONTRACT's own live red count. A derivatives row runs the SuperTrend on each
    #: contract's OWN premium series, and `_compile_rows` then groups the legs under a
    #: single parent — so the parent's count belongs to whichever leg happened to arrive
    #: first. A position holding any other strike must read its own leg, not that one.
    #: None = unknown (see EngineSignalRow.current_reds).
    current_reds: Optional[int] = None
    resolution_note: Optional[str] = None


class EngineSignalRow(BaseModel):
    underlying: str
    token: int
    exchange: str  # option exchange (NFO / BFO)
    regime: Literal["BULL", "BEAR"]
    alignment: AlignmentChip
    direction: Literal["long", "short"]
    option_type: Literal["CE", "PE"]
    legs: List[OptionLeg] = []  # one per selected moneyness (ATM/ITM1-5/OTM1-5)
    spot: float
    stop_loss: float          # live ratcheting trail stop (underlying pts for spot/confluence) — TSL column
    # Initial hard stop at the entry bar = the validated fast ST line at the trigger.
    # Underlying pts for spot/confluence rows; premium for derivatives (leg-level too).
    # None for legacy cached rows. Surfaced as the signal table's SL column.
    entry_sl: Optional[float] = None
    # Live red-counter progress at the latest bar, "<reds>/<threshold> red" (threshold
    # from exit_mode). The Exit column; None for legacy cached rows.
    exit_state: Optional[str] = None
    #: How many of the three SuperTrend lines are against ``direction`` ON THE LATEST
    #: CLOSED BAR — always live, never frozen. ``alignment`` is the ENTRY bar's chip and
    #: ``exit_state`` freezes at the exit bar for an ended row, so neither can drive the
    #: red-count exit of an OPEN position. Reading the entry chip instead is what made
    #: every bear position report 3/3 the moment it opened.
    #:
    #: None means UNKNOWN — a row hydrated from a cache written before this field
    #: existed. It must NOT read as 0: zero means "nothing against us" and would
    #: overwrite a real count of 2 or 3, disarming the red exit one tick before it
    #: fired. Consumers leave the last known count alone instead.
    current_reds: Optional[int] = None
    # Why this entry ended, when it has ("trail breach (≤ 1000.63)" / "red count exit
    # 3/3 (three_red_signal)"). None while the trade is still running. The red counter
    # and the trailing stop are independent rules and either can end a trade, so
    # ``exit_state`` alone cannot explain an ended row — it only reports the counter.
    exit_reason: Optional[str] = None
    # Profit objective in the same units as ``entry_sl``. Deliberately None for every
    # SuperTrend row: that strategy is trend-following and exits on the trail plus the
    # red counter, so quoting a target would invent a rule the engine does not run.
    # Navigator-originated rows DO carry one — its AVWAP stop/target proposal is an
    # R-multiple of the accepted stop, so the target is part of the signal.
    target: Optional[float] = None
    score: float
    timestamp_ms: int
    # Underlying spot at the trigger bar. For "spot"-source signals this equals
    # ``spot``; for "derivatives" signals ``spot`` carries the option premium (and is
    # zeroed during leg grouping), so the underlying spot is captured separately here
    # from the underlying's 1H candle at the trigger timestamp. None when the
    # underlying candle for that bar wasn't available.
    underlying_spot: Optional[float] = None
    # is_active = the SuperTrend is STILL aligned on the latest closed bar (trade is
    # running), vs. a stale entry whose trend has since broken. is_fresh = entered on
    # the latest bar (the live "ready now" trigger). For grouped derivative rows these
    # are OR'd across the legs; per-contract liveness is on each OptionLeg.is_active.
    is_active: bool = False
    is_fresh: bool = False
    # "spot" = SuperTrend on the underlying chart (legs are candidate strikes to BUY);
    # "derivatives" = SuperTrend on this contract's OWN premium chart (single leg, BUY-only);
    # "confluence" = underlying fired AND the leg's own premium confirmed (merged row);
    # "navigator" = Navigator Signal Origination — no SuperTrend trigger at all, surfaced
    # purely from Navigator's own AVWAP+volatility evidence (see 2026-07-28 design doc).
    source: Literal["spot", "derivatives", "confluence", "navigator"] = "spot"
    # Trend-quality readings at the entry bar (for the optional directional-mode
    # entry filters). None when not computed; never gates anything unless adx_min /
    # atr_pct_min are set in the engine config.
    adx: Optional[float] = None
    atr_pct: Optional[float] = None
    # Sterling Value-Flow Navigator (optional, off by default). None when
    # Navigator is disabled for this user, or before it has evidence for
    # this row. Never changes `score`/`source`/`is_active`/`is_fresh` above —
    # those remain exactly as the base engine computed them. Old cached rows
    # without this field deserialize fine (defaults to None).
    navigator: Optional["NavigatorDecision"] = None
    # Populated when a SuperTrend/Navigator setup is valid but no option leg could
    # be resolved from the selected strike/expiry settings. This is not a
    # liquidity verdict; true liquidity gates run later on live quote/depth data.
    resolution_reason: Optional[str] = None
    # This row came from the market REPLAY, not from the live scanner. The two
    # are merged into one table while a replay holds the session view, so
    # without this a trader cannot tell a recording from the market. Defaults
    # to False, so every live and cached row is unaffected.
    is_replay: bool = False


class SignalsResponse(BaseModel):
    generated_ms: int
    scanning: bool
    scanning_label: str = ""
    rows: List[EngineSignalRow]
    next_scan_ms: int = 0
    auto_scan: bool = False
    market_open: bool = True
    # "live" | "replay" | "replay_review". Anything but "live" means at least
    # some rows in this response are simulated, and `replay_live` says whether
    # the replay is still playing or is a finished session held for review.
    feed_mode: Literal["live", "replay", "replay_review"] = "live"
    replay_live: bool = False


class OpenPositionRecord(BaseModel):
    symbol: str
    exchange: str
    token: int = 0
    qty: int = 0
    lot_size: int = 0
    entry_premium: float = 0.0
    fill_price: float = 0.0
    stop_premium: float = 0.0
    status: str = ""
    direction: str = "long"
    vehicle: str = "otm_options"
    underlying: str = ""
    opened_ms: int = 0
    exit_reason: str = ""
    order_id: str = ""
    exit_pending: bool = False
    pnl_reconciliation_required: bool = False
    exit_mode: str = "one_red"  # the exit counter rule active when this position was opened (remembers choice for display + audit)
    current_red_count: int = 0
    exit_threshold: int = 1  # 1/2/3 based on exit_mode at last update; used for health display


class OpenPositionsResponse(BaseModel):
    positions: List[OpenPositionRecord]


class SetupPoint(BaseModel):
    time: int  # epoch seconds (lightweight-charts)
    open: float
    high: float
    low: float
    close: float


class SetupLine(BaseModel):
    time: int  # epoch seconds
    value: float


class SetupChart(BaseModel):
    underlying: str
    candles: List[SetupPoint]  # Heikin-Ashi candles
    st_fast: List[SetupLine]
    st_mid: List[SetupLine]
    st_slow: List[SetupLine]
    entry_index: Optional[int] = None  # bar index of the fresh transition
    trail_target: str
    exit_mode: str = "two_red"  # for viz of current exit threshold


class ActivityEvent(BaseModel):
    ts_ms: int
    kind: str  # scan_start | scan_done | order_placed | order_blocked | order_failed | error | info
    message: str


class ActivityResponse(BaseModel):
    events: List[ActivityEvent]
    scanning: bool
    auto_scan: bool
    last_scan_ms: int
    next_scan_ms: int
    signal_count: int
    scanning_label: str = ""
    # True only during NSE/BSE session. When auto_scan is on but the market is
    # closed the loop intentionally pauses, so next_scan_ms goes stale — the UI
    # uses this to say "market closed" instead of a misleading "next due now".
    market_open: bool = True


class DepthLevel(BaseModel):
    price: float
    quantity: int
    orders: int


class OptionDetail(BaseModel):
    moneyness: str
    option_type: str
    option_symbol: str
    strike: float
    expiry: str
    lot_size: Optional[int] = None
    dte: int = 0
    last_price: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    iv: float = 0.0  # decimal (0.18 = 18%)
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    #: False when Black-Scholes could not be evaluated and `delta` is only the
    #: intrinsic sign (±1.00 / 0.00) with the other greeks zeroed. Consumers must
    #: gate ranking and "best strike" badges on this rather than guessing from
    #: `iv > 0` — a fabricated delta of 1.00 outranks every real one.
    greeks_solved: bool = True
    depth_buy: List[DepthLevel] = []
    depth_sell: List[DepthLevel] = []
    # The signal's own premium levels for this leg, mirroring the board's
    # Entry / SL / TSL / Target columns. None when the leg was never hydrated
    # (see ScanDiag.premium_missing) — never 0.0 as a stand-in for "unknown".
    entry_premium: Optional[float] = None
    initial_stop_premium: Optional[float] = None
    trail_stop_premium: Optional[float] = None
    target_premium: Optional[float] = None
    is_active: bool = False


class EngineDetailResponse(BaseModel):
    underlying: str
    token: int
    exchange: str  # option exchange (NFO/BFO)
    direction: Literal["long", "short"]
    regime: Literal["BULL", "BEAR"]
    alignment: AlignmentChip
    option_type: Literal["CE", "PE"]
    triggered_ms: int
    spot_at_trigger: float
    spot_now: float
    stop_loss: float
    options: List[OptionDetail]
    resolution_reason: Optional[str] = None
    # Which engine owns this row, and its live state. The dock is opened from a board
    # that mixes SuperTrend and Navigator rows, so without `source` it cannot tell the
    # user whose signal they are looking at or which actions apply.
    source: Literal["spot", "derivatives", "confluence", "navigator"] = "spot"
    score: float = 0.0
    entry_sl: Optional[float] = None
    target: Optional[float] = None
    exit_state: Optional[str] = None
    exit_reason: Optional[str] = None
    is_active: bool = False
    is_fresh: bool = False
    adx: Optional[float] = None
    atr_pct: Optional[float] = None
    # Navigator's fused decision for this row when it has one — the same object the
    # board renders as the "Nav …" badge, so the dock can show WHY it says that.
    navigator: Optional["NavigatorDecision"] = None


class EngineOrderRequest(BaseModel):
    option_symbol: str
    exchange: str  # NFO / BFO
    side: Literal["BUY", "SELL"]
    quantity: int
    order_type: str = "MARKET"
    product: str = "NRML"


class EngineOrderResponse(BaseModel):
    order_id: str
    status: str  # ok | duplicate
    message: str
    #: Whether anything will exit this position without the user acting. A BUY that
    #: could not be armed still returns status "ok" — the order IS live — so this is
    #: the only field that distinguishes a protected entry from a bare one, and the
    #: board must show the difference instead of rendering an SL/TSL/Target beside a
    #: position that has none.
    protected: bool = True
    #: Human-readable detail: what was armed, or why nothing was.
    protection: str = ""


class HistorySignal(BaseModel):
    """A past entry transition found by replaying the engine over a date window."""
    ts_ms: int
    underlying: str
    source: Literal["spot", "derivatives"]
    direction: Literal["long", "short"]
    option_type: Literal["CE", "PE"]
    option_symbol: str = ""   # derivatives only
    moneyness: str = ""       # derivatives only
    entry_price: float
    stop_loss: float
    is_now: bool = False      # fresh entry on the latest closed bar (the live "ready" signal)


class HistoryResponse(BaseModel):
    generated_ms: int
    from_ms: int
    to_ms: int
    scan_source: str
    signals: List[HistorySignal]


Vehicle = Literal["otm_options", "deep_itm_options", "futures"]
DeepItmMoneyness = Literal["ITM5", "ITM10", "ITM15", "ITM20"]


class EngineConfigModel(BaseModel):
    # Master gate. True (default) = Sterling Kite Engine active (scanning,
    # signals, auto-execute). False = engine OFF; the Kite platform runs as normal
    # (manual trading only). Toggled from the Connect tab.
    engine_enabled: bool = True
    # Which ST line trails the stop. "fast" (tightest band) is the most OOS-robust
    # exit in the 7.5y sweep and bleeds the least theta; "mid"/"slow" stay selectable.
    trail_target: Literal["fast", "mid", "slow"] = "fast"
    # ── Auto-exit mode ────────────────────────────────────────────────────────
    # Controls how many red SuperTrend lines trigger auto-exit:
    #   one_red          — ANY one line red → exit (tightest, default/legacy)
    #   two_red          — any TWO lines red → exit
    #   three_red        — ALL THREE lines red → exit (full reversal)
    #   three_red_signal — all three red AND a fresh counter-arrow → exit (loosest)
    # MEASURED best on real 7.5y IS/OOS (study/kite_st_exit_mode_sweep.py): one_red
    # beats two_red/three_red on both delta1 and options lenses (see config.py). Was
    # "two_red" (asserted, never measured). Looser modes stay selectable.
    exit_mode: ExitMode = "one_red"
    # Opt-in: anchor the price stop to the exit_mode-th ST line (one_red→fast,
    # two_red→mid, three_red→slow) so the stop breach coincides with the red count
    # instead of the tightest line pre-empting it. OFF (default) = validated fast trail.
    exit_aligned_trail: bool = False
    # Treat the trailing stop as a real exit, not a display value: an entry is dead the
    # first bar price trades through the trail, whichever comes first with the red
    # counter. Turn OFF only to reproduce the old red-counter-only behaviour, where a
    # position under two_red/three_red could sit indefinitely below its own stop.
    price_stop_exit: bool = True
    # multi-select: scan resolves a leg for EACH selected moneyness (ITM into the
    # money, OTM out of the money). Defaults to the full ATM→ITM→OTM ladder.
    strike_moneyness: List[Literal["ATM", "ITM1", "ITM2", "ITM3", "ITM4", "ITM5", "OTM1", "OTM2", "OTM3", "OTM4", "OTM5"]] = [
        "ITM1", "ATM", "OTM1"]
    # Where the SuperTrend runs: "spot" = underlying chart (legs are candidate strikes);
    # "derivatives" = each selected contract's own premium chart (BUY-only); "both".
    # Default "spot": it is the only source with an OOS-durable edge in the 7.5y study
    # (delta-1 OOS-positive on 4/4 indices), gives both directions, and is now fully
    # stop-protected (delta-translated premium stop). Premium-chart ("derivatives")
    # signals are unvalidated and structurally unvalidatable over history; keep that
    # mode for confirmation/manual use. "both" runs both but auto-exec then guards
    # per-underlying to avoid stacking the same move (see service._make_place_cb).
    # "confluence" = highest conviction: emit a strike only when the underlying fires a
    # fresh entry AND that option's own premium ST also confirms (merged row).
    scan_source: Literal["spot", "derivatives", "both", "confluence"] = "spot"
    # Option expiries to scan — weekly, monthly, or both. Defaults to all.
    scan_expiries: List[Literal["weekly", "monthly"]] = ["weekly", "monthly"]
    # Per-category override: indices may be weekly/monthly; single-stock
    # derivatives are exchange-listed monthly contracts only.
    scan_expiries_indices: Optional[List[Literal["weekly", "monthly"]]] = None
    scan_expiries_stocks: Optional[List[Literal["monthly"]]] = ["monthly"]
    # Granular universe selection — applied to BOTH the spot and derivatives scans.
    # Indices are kept by display name; stocks by name, unless scan_all_stocks is set.
    scan_indices: List[str] = ["NIFTY 50", "NIFTY BANK", "NIFTY FIN SERVICE", "SENSEX"]
    scan_stocks: List[str] = []
    scan_all_stocks: bool = False  # default preserves the full spot universe
    #: Master switch above the stock list. False = leave single-stock
    #: underlyings out of the scan entirely: no stock contracts are resolved and
    #: no stock rows appear. Indices are unaffected.
    #:
    #: Single-stock derivatives list a monthly cycle only, settle physically at
    #: expiry, and account for most of the scan cost, so "indices only" is worth
    #: one switch rather than un-ticking every name. True keeps today's behaviour.
    scan_stock_contracts: bool = True
    auto_execute: bool = False
    # ── Per-trade risk sizing (workstream F) ──────────────────────────────────
    # When on, auto-exec sizes lots so premium-at-risk ((entry − stop) × qty) stays
    # within risk_pct% of available FO capital, capped by max_lots and affordable
    # margin. When off, falls back to a single lot.
    risk_sizing: bool = True
    risk_pct: float = 1.0          # % of available FO capital risked per trade
    max_lots: int = 10             # hard ceiling on auto-exec lots per order
    #: What to do when even ONE lot breaks the risk budget.
    #:
    #: Off (default): skip the entry, because no tradable size honours ``risk_pct``.
    #: On: take the single minimum lot anyway, accepting more risk than the setting
    #: states. That was the old unconditional behaviour and it made ``risk_pct``
    #: advisory — the smallest F&O lot on an index option can carry many times a 1%
    #: budget on a modest account. Turning it on is a choice to prefer taking the
    #: signal over holding the cap; leaving it off keeps the cap meaningful.
    allow_min_lot_over_risk: bool = False
    #: How many 1H bars a derivative contract's own latest bar may lag the
    #: underlying's and still be auto-executed. 0 (default) means it must be current.
    #:
    #: A derivatives signal is "fresh" when it fired on the last bar that CONTRACT
    #: has — which says nothing about when that bar was. An illiquid strike that last
    #: printed hours ago still presents its old bar as the latest, so the transition
    #: reads as a live trigger long after the fact and a market order fills nowhere
    #: near the premium on screen. Raising this trades that protection for entries on
    #: thinner strikes. Display is never affected; only the automatic order is held.
    max_contract_staleness_bars: int = 0
    # ── Expiry window ──────────────────────────────────────────────────────────
    # The same three settings every other engine's Contracts section carries,
    # under the same names, so one vocabulary covers every strategy page.
    # `expiry_dte_min`/`expiry_dte_max` bound eligibility; `avoid_expiry_day`
    # excludes the contract expiring today.
    #
    # Defaults are permissive so an existing config resolves exactly the
    # contracts it resolved before — this adds a control, not a policy.
    expiry_dte_min: int = 0
    expiry_dte_max: int = 400
    avoid_expiry_day: bool = False
    # ── Expiry square-off guard ────────────────────────────────────────────────
    # Market-exit an auto-exec option position when its contract comes within this
    # many calendar days of expiry, so a weekly can't ride into expiry unmanaged
    # (median signal hold ≈ 3.7d, p90 ≈ 10d, vs a weekly's ~5 sessions). 0 disables.
    # Applies to options only (futures roll rather than square off → empty expiry).
    expiry_square_off_days: int = 1
    # ── Time-stop (opt-in, default off) ────────────────────────────────────────
    # Square off an auto-exec position after it has been held this many 1H bars.
    # The exit-mechanics sweep (study/kite_st_exit_sweep.py, real 7.5y) found a
    # ~48-bar cap is the one robust, cross-lens improvement — it curbs theta bleed on
    # long-option holds (options-lens mean OOS −134% → −32%). Default 0 = off, because
    # the sweep's IS→OOS rank corr is negative (specific configs overfit) and the
    # delta-1 benefit is marginal; it mainly helps the long-OTM-options vehicle. 0 = off.
    time_stop_bars: int = 0
    # ── Protective stop mode (workstreams C/D) ────────────────────────────────
    # "broker"  = place a GTT/SL-M stop at Zerodha at entry (survives server death)
    # "monitor" = tick-driven WS monitor exits on trail breach (intrabar, server-side)
    # "both"    = both (default; defense in depth for real money)
    stop_mode: Literal["broker", "monitor", "both"] = "both"
    #: Arm a HAND-PLACED order the same way an auto-executed one is armed: registry
    #: entry, `stop_mode` protection, expiry square-off — with the stop read off the
    #: board's own plan for that contract. Default on, because the board already
    #: displays an SL/TSL beside a manual position and that display has to be true.
    #: Turning it off means a manual entry is yours to manage; the order response and
    #: the activity log then say UNPROTECTED rather than implying a stop.
    protect_manual_orders: bool = True

    # ── Directional mode (additive, opt-in) ───────────────────────────────────
    # Master toggle. False ⇒ existing engine, untouched (byte-identical).
    # True ⇒ monetize the signal through the selected vehicle instead.
    directional_mode: bool = False
    # Which vehicle to trade when directional_mode is ON.
    # otm_options = existing behavior; deep_itm_options = high-delta ITM;
    # futures = index futures (delta-1, two-sided).
    vehicle: Vehicle = "otm_options"
    # Which vehicles the user has enabled (checkboxes in the UI). The active
    # `vehicle` must be in this set. Futures is opt-in — default is options only.
    enabled_vehicles: List[Vehicle] = ["otm_options", "deep_itm_options"]
    # ── Deep-ITM options config ───────────────────────────────────────────────
    # How many strike steps into the money (ITM5 / ITM10 / ITM15 / ITM20).
    itm_depth: Optional[DeepItmMoneyness] = "ITM10"
    # Alternatively, pick the strike nearest to a target BS delta (overrides itm_depth).
    target_delta: Optional[float] = None   # e.g. 0.90 for ~delta-0.9
    # ── Futures config ────────────────────────────────────────────────────────
    futures_expiry: Literal["near", "next"] = "near"
    # ── Entry quality filters (Phase-0 survivors; None = off) ─────────────────
    adx_min: Optional[float] = None          # minimum ADX to allow entry (e.g. 20)
    atr_pct_min: Optional[float] = None      # minimum ATR percentile (e.g. 50)
    # ── Session / liquidity entry gates (auto-exec; opt-in, default off) ──────
    # Block NEW auto-exec entries in the last N minutes before the applicable continuous close so a
    # fresh late-session signal doesn't enter straight into an overnight index gap
    # (there is no INR daily-loss breaker behind an overnight hold). 0 = off.
    block_entry_minutes_before_close: int = 0
    # Skip an auto-exec entry whose chosen option leg is too illiquid to trade well:
    # bid-ask spread wider than this % of mid, or open interest below this floor.
    # None = off (the scanner itself makes no quote calls; these add one at entry).
    max_spread_pct: Optional[float] = None   # e.g. 5.0 → reject > 5% quoted spread
    min_oi: Optional[int] = None             # e.g. 100 → reject thin strikes
    # ── INR daily-loss breaker (auto-exec; opt-in, default off) ───────────────
    # Halt NEW auto-exec entries once realized losses for the IST day reach this % of
    # available F&O capital. Fills the gap left by the USD daily-loss breaker being
    # None = off. Only ever blocks entries; never force-closes.
    max_daily_loss_pct: Optional[float] = None
    # ── Risk infrastructure wiring ────────────────────────────────────────────
    # Wires the drawdown circuit breaker + correlation penalty into sizing.
    wire_risk_infra: bool = False

    @field_validator("risk_pct")
    @classmethod
    def _risk_pct_bounds(cls, v):
        # Clamp to a sane 0.1%–25% band; 0/negative would size to nothing.
        return min(25.0, max(0.1, float(v)))

    @field_validator("max_lots")
    @classmethod
    def _max_lots_bounds(cls, v):
        return min(500, max(1, int(v)))

    @field_validator("strike_moneyness")
    @classmethod
    def _at_least_one_moneyness(cls, v):
        return v or ["ITM1", "ATM", "OTM1"]

    @field_validator("scan_expiries")
    @classmethod
    def _at_least_one_expiry(cls, v):
        return v or ["weekly", "monthly"]

    @field_validator("scan_expiries_stocks", mode="before")
    @classmethod
    def _stocks_are_monthly_only(cls, v):
        # Accept stale clients/saved configs but never preserve an invented weekly
        # single-stock series in the validated API model.
        return ["monthly"]

    @field_validator("target_delta")
    @classmethod
    def _target_delta_bounds(cls, v):
        if v is None:
            return v
        # Any delta in (0,1) is a valid option strike target. OTM buys sit ~0.20–0.45,
        # ATM ~0.50, deep-ITM ~0.80+. The resolver (pick_by_delta) simply picks the
        # nearest strike, so the full band is allowed.
        return min(0.99, max(0.05, float(v)))

    @field_validator("adx_min")
    @classmethod
    def _adx_bounds(cls, v):
        if v is None:
            return v
        return min(50.0, max(5.0, float(v)))

    @field_validator("atr_pct_min")
    @classmethod
    def _atr_pct_bounds(cls, v):
        if v is None:
            return v
        return min(95.0, max(10.0, float(v)))

    @classmethod
    def production(cls) -> "EngineConfigModel":
        """Validated, fail-safe production configuration preset.

        Hardened defaults from 7.5y IS/OOS parameter sweeps:
        - trail_target = 'fast' (mult 1.0, 4/4 indices OOS positive)
        - exit_mode = 'one_red' (tightest validated exit)
        - price_stop_exit = True (enforces price stop breach)
        - adx_min = 25.0 (filters false signals, win rate 46% -> 60%)
        - time_stop_bars = 48 (theta protection on long options)
        - max_daily_loss_pct = 2.0 (hard 2% daily loss circuit breaker)
        - wire_risk_infra = True (drawdown breaker + correlation penalty)
        - block_entry_minutes_before_close = 15 (no fresh late-session entries)
        - max_spread_pct = 5.0 (spread filter to avoid illiquid options)
        - min_oi = 100 (minimum open interest floor)
        - risk_sizing = True, risk_pct = 1.0, max_lots = 10
        - stop_mode = 'both' (broker GTT + server tick monitor)
        """
        return cls(
            trail_target="fast",
            exit_mode="one_red",
            exit_aligned_trail=False,
            price_stop_exit=True,
            adx_min=25.0,
            time_stop_bars=48,
            max_daily_loss_pct=2.0,
            wire_risk_infra=True,
            block_entry_minutes_before_close=15,
            max_spread_pct=5.0,
            min_oi=100,
            stop_mode="both",
            risk_sizing=True,
            risk_pct=1.0,
            max_lots=10,
            allow_min_lot_over_risk=False,
            scan_stock_contracts=True,
            scan_stocks=[],
            scan_all_stocks=False,
        )


class ReadinessCheckItem(BaseModel):
    name: str
    status: Literal["ok", "warning", "blocked"]
    detail: str
    data: Optional[Dict[str, Any]] = None


class ReadinessResponse(BaseModel):
    ready_for_live: bool
    is_live_account: bool
    account_label: str
    checks: Dict[str, ReadinessCheckItem]
    blockers: List[str]
    warnings: List[str]
    timestamp_ms: int


class EmergencyActionResponse(BaseModel):
    status: str
    message: str
    positions_count: int = 0
    squared_off: int = 0
    failed: int = 0
    details: List[Dict[str, Any]] = Field(default_factory=list)


# ── Options backtest (workstream H) ──────────────────────────────────────────
class BacktestRequest(BaseModel):
    # What to test. For synthetic/both, the symbol is the UNDERLYING (e.g.
    # "NIFTY 50"); for real, it is an option tradingsymbol (e.g. "NIFTY24JUN24000CE").
    symbol: str
    data_mode: Literal["synthetic", "real", "both"] = "both"
    trail_target: Literal["fast", "mid", "slow"] = "fast"
    # Exit counter (how many red ST lines trigger the exit), mirrors the live engine.
    # The backtest exit IS this red-count rule; trail_target is retained for the live
    # stop level but does not change the backtest exit.
    exit_mode: ExitMode = "two_red"
    lookback_bars: int = 2000          # 1H bars (synthetic can reach back years)
    starting_capital: float = 100_000.0
    qty: int = 50                      # one lot (lot_size) — value/risk scales with this
    # Synthetic-only knobs:
    iv: float = 0.18                   # fixed IV assumption for BS pricing (decimal)
    dte_days: float = 7.0              # weekly option horizon at entry
    moneyness_offset_pct: float = 0.0  # 0 = ATM; +ve = OTM, signed by direction
    # Cost overrides (None = schedule defaults):
    slippage_pct: Optional[float] = None
    brokerage_per_order: Optional[float] = None

    @field_validator("lookback_bars")
    @classmethod
    def _bars_bounds(cls, v):
        return min(10_000, max(100, int(v)))


class BacktestTradeModel(BaseModel):
    entry_ms: int
    exit_ms: int
    direction: str
    entry_premium: float
    exit_premium: float
    qty: int
    gross_pnl: float
    costs: float
    net_pnl: float
    bars_held: int
    exit_reason: str


class BacktestStatsModel(BaseModel):
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    gross_pnl: float = 0.0
    total_costs: float = 0.0
    net_pnl: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    return_pct: float = 0.0
    final_capital: float = 0.0


class BacktestRunModel(BaseModel):
    mode: str
    caveat: str = ""
    trades: List[BacktestTradeModel] = []
    equity_curve: List[float] = []
    stats: BacktestStatsModel


class BacktestResponse(BaseModel):
    symbol: str
    data_mode: str
    generated_ms: int
    runs: List[BacktestRunModel]          # one per executed mode (synthetic / real)
    # When data_mode="both": mean abs % drift of the live contract's modeled-vs-real
    # premium (a calibration sanity check on the synthetic assumption).
    bs_vs_real_drift_pct: Optional[float] = None
    notes: List[str] = []
