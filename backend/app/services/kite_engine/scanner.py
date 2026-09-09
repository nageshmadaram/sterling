"""Throttled background scanner for the Kite Sterling Kite Engine.

Per active Kite user: fetch 1H candles for each universe item (cached, rate
throttled), drop the forming bar, run the broker-agnostic engine, and collect the
"ready" rows (fresh full-alignment transition on the latest closed bar). Strike
selection is done from the already-loaded option dumps; spot-source option
candidates are then hydrated with their own premium entry/stop snapshots.

Kite types are touched only here and in the endpoints; the engine package stays
broker-agnostic. Nothing here imports another engine's strategy logic.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.core.logging import get_logger
from app.domain.models import Candle
from app.engines.indicators.adx import adx as _adx
from app.engines.indicators.atr import atr_percentile as _atr_pct, compute_atr as _compute_atr
from app.engines.indicators.heikin_ashi import compute_heikin_ashi
from app.engines.common.exit_counter import get_exit_threshold, exit_needs_counter_signal
from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
from app.engines.sterling_kite_engine import exits
from app.engines.sterling_kite_engine.engine import SterlingKiteEngine
from app.engines.sterling_kite_engine.regime import compute_regime, entry_transitions
from app.engines.sterling_kite_engine.schemas import (
    AlignmentChip, EngineSignalRow, OptionLeg, SetupChart, SetupLine, SetupPoint,
)
from app.services.kite_engine.greeks import (
    black_scholes_greeks, bs_price, implied_vol, premium_stop_from_move,
)
from app.schemas.instruments import InstrumentMeta
from app.services.kite_engine.strikes import (
    expiry_window_of,
    ExpiryType, OptionPick, chain_rows_for, filter_liquid_contracts, pick_contracts, pick_strikes,
)
from app.services.kite_engine.universe import UniverseItem
from app.services.kite_engine import state, market_hours

log = get_logger(__name__)

_CANDLE_TTL_S = 180          # re-use cached 1H candles for ~3 min
_EMPTY_CANDLE_TTL_S = 25     # an empty result may be a 429 casualty — retry soon
_CONCURRENCY = 2             # stay UNDER Kite historical ~3 req/s (3 concurrent → 429s)
_TF_MS = 3_600_000           # 1H bar in ms
_LOOKBACK_BARS = 320
_IV_ASSUMPTION = 0.18

# Signals linger on the board for a couple of weeks after they fire — even once
# their SuperTrend de-aligns (the UI renders these struck-through as "ended") — so a
# setup entered before a weekend is still visible the following Monday instead of
# vanishing the instant the trend breaks. The UI buckets ended entries up to 15 days
# old ("Today / Yesterday / Last week / Last 15 days"), so the retention matches.
_SIGNAL_RETENTION_MS = 15 * 24 * 60 * 60 * 1000

_IST = timezone(timedelta(hours=5, minutes=30))

# place_cb(row, item) -> Awaitable: optional auto-exec hook injected by the endpoint.
PlaceCb = Callable[[EngineSignalRow, UniverseItem], Awaitable[None]]


# ── pure helpers (unit-tested) ──────────────────────────────────────────────
def drop_forming(candles: List[Candle], now_ms: Optional[int] = None, *,
                 allow_forming: bool = False, exchange: str | None = None,
                 cas_eligible: bool = False) -> List[Candle]:
    """Use only finalized raw bars; exclude auctions from continuous signals.

    Final session bars can be shorter than one hour. The optional forming mode
    is for display callers only and is never used in the execution scan.
    Invalid/order-inconsistent data fails closed instead of being repaired.
    """
    import math
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    previous = -1
    out = []
    for bar in candles:
        ts = bar.timestamp_ms
        values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
        if (ts <= previous or not all(math.isfinite(v) for v in values)
                or min(values[:4]) <= 0 or bar.volume < 0
                or bar.low > min(bar.open, bar.close)
                or bar.high < max(bar.open, bar.close)):
            return []
        previous = ts
        end_ms = ts + _TF_MS
        start = datetime.fromtimestamp(ts / 1000, _IST)
        if exchange is not None and start.date() >= market_hours.CAS_START:
            close = market_hours.continuous_close(start.date(), exchange,
                                                 cas_eligible=cas_eligible)
            if not (datetime.min.replace(hour=9, minute=15).time() <= start.time() < close):
                continue
            end = datetime.combine(start.date(), close, tzinfo=_IST)
            end_ms = min(end_ms, int(end.timestamp() * 1000))
        if ts <= now_ms and (allow_forming or end_ms <= now_ms):
            out.append(bar)
    return out


def _exit_state_str(r, direction: str, last_idx: int, cfg: SterlingKiteEngineConfig) -> str:
    """Red-counter progress at the latest bar as ``"<reds>/<threshold> red"``.

    ``reds`` = how many of the three ST lines are against the position now; threshold =
    the count that triggers the exit under ``exit_mode`` (one_red→1, two_red→2,
    three_red[_signal]→3). This is the live Exit-column readout.
    """
    reds = r.red_line_count(direction, last_idx)
    return f"{reds}/{get_exit_threshold(cfg.exit_mode)} red"


def _entry_sl_value(r, i: int, cfg: SterlingKiteEngineConfig) -> float:
    """Initial hard stop at the entry bar = the ``trail_target`` (validated fast) ST
    line at the trigger index. Static reference for the SL column (vs. the live TSL)."""
    return float(r.line(cfg.trail_target)[i])


def _dte_from_expiry(expiry: str, ref_ms: int) -> float:
    try:
        expiry_date = datetime.strptime(str(expiry)[:10], "%Y-%m-%d").date()
        ref_date = datetime.fromtimestamp(int(ref_ms) / 1000, _IST).date()
        return float(max(1, (expiry_date - ref_date).days))
    except Exception:  # noqa: BLE001
        return 7.0


def _leg_snapshot_key(row: EngineSignalRow, leg: OptionLeg) -> tuple:
    return (
        row.source,
        row.underlying,
        row.direction,
        row.option_type,
        int(row.timestamp_ms),
        leg.option_symbol,
    )


def _prior_leg_snapshots(rows: Sequence[EngineSignalRow]) -> Dict[tuple, OptionLeg]:
    out: Dict[tuple, OptionLeg] = {}
    for row in rows:
        for leg in row.legs:
            if leg.premium_spot is not None or leg.premium_sl is not None or leg.entry_sl is not None:
                out[_leg_snapshot_key(row, leg)] = leg
    return out


def _copy_prior_leg_snapshot(row: EngineSignalRow, prior: Dict[tuple, OptionLeg]) -> None:
    for leg in row.legs:
        old = prior.get(_leg_snapshot_key(row, leg))
        if old is None:
            continue
        if leg.premium_spot is None:
            leg.premium_spot = old.premium_spot
        if leg.premium_sl is None:
            leg.premium_sl = old.premium_sl
        if leg.entry_sl is None:
            leg.entry_sl = old.entry_sl


def _stamp_leg_premium_stops(row: EngineSignalRow, leg: OptionLeg) -> None:
    entry = float(leg.premium_spot or 0.0)
    if entry <= 0:
        return
    spot = float(row.underlying_spot or row.spot or 0.0)
    if spot <= 0:
        return
    dte = _dte_from_expiry(leg.expiry, row.timestamp_ms)
    # Prefer the IV the market actually implies at this entry premium over the flat
    # _IV_ASSUMPTION. A single 18% assumption is roughly right for index options and
    # badly wrong for stock options (25-45%), and delta is what converts the
    # underlying ST level into the premium SL/TSL shown in the table — so a wrong IV
    # shows the user a stop that is nowhere near where the premium would really be.
    iv = implied_vol(price=entry, spot=spot, strike=float(leg.strike),
                     dte_days=dte, option_type=leg.option_type)
    greeks = black_scholes_greeks(
        spot=spot,
        strike=float(leg.strike),
        dte_days=dte,
        iv=iv if iv > 0 else _IV_ASSUMPTION,
        option_type=leg.option_type,
    )
    delta = greeks.delta if greeks.delta != 0.0 else (
        0.5 if str(leg.option_type).upper().startswith("C") else -0.5
    )
    if (row.entry_sl or 0) > 0 and leg.entry_sl is None:
        leg.entry_sl = premium_stop_from_move(
            entry_premium=entry, delta=delta, spot=spot, trail_level=float(row.entry_sl)
        )
    if (row.stop_loss or 0) > 0:
        leg.premium_sl = premium_stop_from_move(
            entry_premium=entry, delta=delta, spot=spot, trail_level=float(row.stop_loss)
        )
    if (row.target or 0) > 0 and leg.premium_target is None:
        # Same first-order delta model as the stop, read in the profitable direction.
        leg.premium_target = premium_stop_from_move(
            entry_premium=entry, delta=delta, spot=spot, trail_level=float(row.target)
        )


def _retain_signals(eval_rows: List[EngineSignalRow], now_ms: int) -> List[EngineSignalRow]:
    """Filter one instrument's transitions to the rows worth showing.

    Keeps every still-running or just-fired entry, PLUS the single most-recent
    now-ended entry while it is still within the retention window. This replaces the
    old ``is_active or is_fresh`` drop so a recent setup doesn't disappear the moment
    its trend ends — it stays on the board (rendered struck-through as "ended") until
    it ages out or a newer transition on the same instrument supersedes it. Older
    de-aligned wiggles are dropped, keeping the board bounded and the cache stable
    across re-scans (each scan replays the same history deterministically).

    Auto-exec is unaffected: callers still fire only on the fresh (latest-bar) row.
    """
    if not eval_rows:
        return []
    latest_ts = max(int(r.timestamp_ms) for r in eval_rows)
    out: List[EngineSignalRow] = []
    for r in eval_rows:
        if r.is_active or r.is_fresh:
            out.append(r)
        elif int(r.timestamp_ms) == latest_ts and (now_ms - int(r.timestamp_ms)) <= _SIGNAL_RETENTION_MS:
            out.append(r)
    return out


def evaluate_item(
    engine: SterlingKiteEngine, item: UniverseItem,
    candles: Sequence[Candle], cfg: SterlingKiteEngineConfig,
) -> List[EngineSignalRow]:
    """Run the engine on closed candles; return all historical fresh transitions."""
    if len(candles) <= cfg.warmup + 1:
        return []
    o = np.array([c.open for c in candles], float)
    h = np.array([c.high for c in candles], float)
    l = np.array([c.low for c in candles], float)
    c = np.array([c.close for c in candles], float)
    r = compute_regime(o, h, l, c, cfg)
    longs, shorts = entry_transitions(r)

    rows = []
    indices = np.where(longs | shorts)[0]
    latest_ts = int(candles[-1].timestamp_ms)
    # Trend-quality readings for the optional directional-mode entry filters
    # (computed once over the raw OHLC; never gate anything on their own).
    adx_arr = _adx(h, l, c, 14)
    atr_arr = _compute_atr(h, l, c, 14)
    for i in indices:
        direction = "long" if longs[i] else "short"
        ts = int(candles[i].timestamp_ms)
        # A trade is "running" until either the red counter fires (per exit_mode) or
        # price trades through the trailing stop — whichever comes first. Once it fires
        # the trade is dead even if conditions reverse later (that's a new entry).
        last_idx = len(c) - 1
        is_stock = item.name.upper() not in {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "BANKEX", "MIDCPNIFTY"}
        exit_j, exit_reason = exits.resolve_exit(r, direction, int(i), last_idx, cfg, longs, shorts, is_stock=is_stock)
        active = exit_j is None
        # Freeze the readouts at the exit bar: a dead trade whose trail kept ratcheting
        # for days afterwards shows a stop it was never protected by.
        end_idx = last_idx if exit_j is None else int(exit_j)
        stop_loss = exits.reported_trail_level(r, direction, int(i), exit_j, last_idx, cfg)
        rows.append(EngineSignalRow(
            underlying=item.name, token=item.token, exchange=item.option_exchange,
            regime="BULL" if direction == "long" else "BEAR",
            alignment=AlignmentChip(fast=int(r.t_fast[i]), mid=int(r.t_mid[i]), slow=int(r.t_slow[i])),
            direction=direction, option_type="CE" if direction == "long" else "PE",
            spot=float(c[i]), stop_loss=stop_loss,
            entry_sl=_entry_sl_value(r, i, cfg),
            exit_state=_exit_state_str(r, direction, end_idx, cfg),
            # Deliberately last_idx, not end_idx: exit_state freezes at the exit bar so a
            # dead row stops moving, but an OPEN position needs the CURRENT count.
            current_reds=r.red_line_count(direction, last_idx),
            exit_reason=exit_reason or None,
            score=85.0, timestamp_ms=ts,
            is_active=active,
            is_fresh=(ts == latest_ts),
            adx=float(adx_arr[i]) if i < len(adx_arr) else None,
            atr_pct=float(_atr_pct(atr_arr[: i + 1])) if i >= 14 else None,
        ))
    return rows


# Fixed display/priority order: ATM first (the auto-exec primary), then ITM
# inwards, then OTM outwards — independent of the order the UI sends them in.
_MONEYNESS_ORDER = {"ATM": 0, "ITM1": 1, "ITM2": 2, "ITM3": 3, "ITM4": 4, "ITM5": 5,
                    "ITM10": 5.3, "ITM15": 5.6, "ITM20": 5.9,
                    "OTM1": 6, "OTM2": 7, "OTM3": 8, "OTM4": 9, "OTM5": 10}


def _compile_rows(rows: List[EngineSignalRow]) -> List[EngineSignalRow]:
    """Group derivative legs and de-duplicate into final display rows.

    MUST be idempotent and MUST NOT mutate ``rows``. ``_flush()`` calls this over
    the same accumulating scan list once per instrument that produces a signal, so
    anything written back onto an input row is read again by the next pass. An
    earlier version zeroed ``spot``/``stop_loss`` on the group parent in place and
    then re-derived that parent's leg premium from the now-zero ``spot``, wiping the
    Entry/TSL of exactly one leg per grouped derivatives row on every pass after the
    first (~1 blank leg per row in the live board).

    Idempotent means ``f(f(x)) == f(x)``, over OUTPUT rows and not merely over the
    same input twice: an already-grouped parent carries many legs, and
    ``held_contract_scan`` appends to the compiled ``us.rows`` and re-compiles. So
    every leg of every input row is folded in, not just ``legs[0]`` — reading only
    the first kept one strike per group and dropped the others, taking their per-leg
    ``current_reds`` (which the red-count exit reads) with them.
    """
    grouped_derivs: Dict[tuple, EngineSignalRow] = {}
    leg_ts: Dict[tuple, int] = {}
    final_rows: List[EngineSignalRow] = []
    for r in rows:
        if r.source != "derivatives":
            final_rows.append(r)
            continue
        key = (r.underlying, r.option_type)
        for source_leg in (r.legs or []):
            leg = source_leg.model_copy(deep=True)
            # Premiums are stamped at birth by evaluate_derivative_contract; fill them
            # here only for a leg that arrived without them (legacy cached rows). On a
            # re-compile the parent's spot/stop_loss are 0, so these stay no-ops.
            if not leg.premium_spot:
                leg.premium_spot = r.spot or leg.premium_spot
            if not leg.premium_sl:
                leg.premium_sl = r.stop_loss or leg.premium_sl
            leg.token = leg.token or r.token
            leg.signal_timestamp_ms = int(leg.signal_timestamp_ms or r.timestamp_ms)
            leg.entry_timestamp_ms = int(leg.entry_timestamp_ms or r.timestamp_ms)
            leg.alignment = leg.alignment or r.alignment
            leg.exit_state = leg.exit_state or r.exit_state
            if leg.current_reds is None:
                leg.current_reds = r.current_reds
            # Per-leg, not the row's: a parent's timestamp_ms is the MAX across its
            # legs, so using it to age-compare individual strikes would let the
            # newest leg's stamp shadow an older one on the next merge.
            stamp = int(leg.signal_timestamp_ms or r.timestamp_ms)
            sym_key = (*key, leg.option_symbol)
            if key not in grouped_derivs:
                parent = r.model_copy(deep=True)
                parent.spot = 0
                parent.stop_loss = 0
                parent.entry_sl = None   # per-leg (leg.entry_sl) is authoritative for grouped deriv rows
                parent.legs = [leg]
                grouped_derivs[key] = parent
                leg_ts[sym_key] = stamp
                continue
            parent = grouped_derivs[key]
            if sym_key not in leg_ts:
                parent.legs.append(leg)
                leg_ts[sym_key] = stamp
            elif stamp > leg_ts[sym_key]:
                for i, existing in enumerate(parent.legs):
                    if existing.option_symbol == leg.option_symbol:
                        parent.legs[i] = leg
                        break
                leg_ts[sym_key] = stamp
            parent.is_active = parent.is_active or leg.is_active
            parent.is_fresh = parent.is_fresh or r.is_fresh
            if r.timestamp_ms > parent.timestamp_ms:
                parent.timestamp_ms = r.timestamp_ms
                # keep the underlying-spot aligned with the displayed (latest) trigger bar
                if (r.underlying_spot or 0) > 0:
                    parent.underlying_spot = r.underlying_spot
    final_rows.extend(grouped_derivs.values())
    for r in final_rows:
        if r.source == "derivatives" and len(r.legs) > 1:
            r.legs.sort(key=lambda leg: _MONEYNESS_ORDER.get(leg.moneyness, 99))
    return final_rows


def live_red_counts(candles: Sequence[Candle], cfg: SterlingKiteEngineConfig) -> Dict[str, int]:
    """Red SuperTrend lines against each direction at the LATEST bar.

    Deliberately independent of whether this instrument produced a signal row. The
    red-count exit reads its number off the scan's rows, and a row only exists where
    there was an entry TRANSITION — so the moment the signal that opened a position
    ends, there is nothing left to refresh the count from and it holds its last value
    for the life of the position. The regime is computed either way; this just keeps
    the answer instead of discarding it.

    Empty when there are too few bars to run the engine, which the caller must treat
    as "cannot say" rather than as a zero — a zero reads as "nothing against us" and
    would disarm the exit outright.
    """
    if len(candles) <= cfg.warmup + 1:
        return {}
    o = np.array([c.open for c in candles], float)
    h = np.array([c.high for c in candles], float)
    l = np.array([c.low for c in candles], float)
    c = np.array([c.close for c in candles], float)
    r = compute_regime(o, h, l, c, cfg)
    last = len(c) - 1
    return {"long": int(r.red_line_count("long", last)),
            "short": int(r.red_line_count("short", last))}


def contract_bar_is_current(
    contract_ts: int, underlying_ts: int, *,
    max_stale_bars: int = 0, bar_ms: int = 3_600_000,
) -> bool:
    """Whether a derivative contract's latest bar is recent enough to trade on.

    ``is_fresh`` for a derivatives row means "the signal fired on the LAST bar this
    contract has" — which says nothing about WHEN that bar was. An illiquid strike
    that last printed at 11:00 still reports its 11:00 bar as the latest one at 15:00,
    so a transition there reads as a live trigger hours after the fact. Auto-exec then
    sends a MARKET order against a premium that has not been quoted since.

    The underlying always trades, so its latest 1H bar is the clock. A contract whose
    own latest bar keeps up with it is current; one that lags by more than
    ``max_stale_bars`` is not.

    An unknown underlying bar (0) returns True. The underlying candle fetch can fail
    while a quote-derived spot still carries the scan, and refusing every automatic
    entry because the clock is missing is a far larger action than the one this guard
    exists to prevent. Display is unaffected either way — the row still appears; only
    the automatic order is withheld.
    """
    if underlying_ts <= 0 or contract_ts <= 0 or bar_ms <= 0:
        return True
    lag_bars = (int(underlying_ts) - int(contract_ts)) / float(bar_ms)
    return lag_bars <= int(max_stale_bars) + 1e-9


def evaluate_derivative_contract(
    item: UniverseItem, moneyness: str, pick: OptionPick,
    candles: Sequence[Candle], cfg: SterlingKiteEngineConfig,
) -> List[EngineSignalRow]:
    """Run the Sterling Kite Engine on an option CONTRACT's own premium series.

    Options-buying only: emit a BUY signal on a fresh *uptrend* transition of the
    premium (a fresh downtrend is a holder's exit, not an entry, so it's ignored).
    A rising CE = bullish underlying (BULL); a rising PE = bearish underlying (BEAR).
    ``spot`` carries the premium last close and ``stop_loss`` the premium ST trail;
    ``token`` is the option's own instrument_token so a click opens its premium chart.

    Short-dated weeklies are NOT skipped — they're evaluated like everything else; the
    SuperTrend simply returns no signal until the contract has ~``warmup`` 1H bars (so a
    young weekly produces no signal, never a fabricated one).
    """
    if len(candles) <= 1:
        return []
    o = np.array([c.open for c in candles], float)
    h = np.array([c.high for c in candles], float)
    l = np.array([c.low for c in candles], float)
    c = np.array([c.close for c in candles], float)
    r = compute_regime(o, h, l, c, cfg)
    longs, shorts = entry_transitions(r)

    rows = []
    is_ce = pick.option_type == "CE"
    indices = np.where(longs)[0]
    latest_ts = int(candles[-1].timestamp_ms)
    for i in indices:
        ts = int(candles[i].timestamp_ms)
        # "running" until the red counter fires (per exit_mode) or the premium trades
        # through its trail. For derivatives all entries are long (BUY), so red = -1.
        last_idx = len(c) - 1
        is_stock = item.name.upper() not in {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "BANKEX", "MIDCPNIFTY"}
        exit_j, exit_reason = exits.resolve_exit(r, "long", int(i), last_idx, cfg, longs, shorts, is_stock=is_stock)
        active = exit_j is None
        end_idx = last_idx if exit_j is None else int(exit_j)
        stop_loss = exits.reported_trail_level(r, "long", int(i), exit_j, last_idx, cfg)
        entry_sl = _entry_sl_value(r, i, cfg)
        rows.append(EngineSignalRow(
            underlying=item.name, token=pick.token, exchange=item.option_exchange,
            regime="BULL" if is_ce else "BEAR",
            alignment=AlignmentChip(fast=int(r.t_fast[i]), mid=int(r.t_mid[i]), slow=int(r.t_slow[i])),
            direction="long", option_type=pick.option_type,
            legs=[OptionLeg(moneyness=moneyness, option_type=pick.option_type,
                            option_symbol=pick.option_symbol, strike=pick.strike,
                            expiry=pick.expiry, lot_size=pick.lot_size or None,
                            # Premium entry/trail belong on the leg from birth: the
                            # signal IS this contract's own premium series, and
                            # place_cb runs on the raw row before any grouping.
                            premium_spot=float(c[i]), premium_sl=stop_loss,
                            token=pick.token or None,
                            entry_sl=entry_sl, is_active=active,
                            signal_timestamp_ms=ts, entry_timestamp_ms=ts,
                            alignment=AlignmentChip(fast=int(r.t_fast[i]), mid=int(r.t_mid[i]), slow=int(r.t_slow[i])),
                            exit_state=_exit_state_str(r, "long", end_idx, cfg),
                            # Per CONTRACT: grouping puts many legs under one parent, so
                            # a position on any strike but the first must read its own.
                            current_reds=r.red_line_count("long", last_idx),
                            premium_target=None)],
            spot=float(c[i]), stop_loss=stop_loss, entry_sl=entry_sl,
            exit_state=_exit_state_str(r, "long", end_idx, cfg),
            # Live, at the latest bar. A derivatives row runs the ST on the CONTRACT's
            # own premium series, so "long" is the true signal direction for a PE too.
            current_reds=r.red_line_count("long", last_idx),
            exit_reason=exit_reason or None,
            score=85.0, timestamp_ms=ts, source="derivatives",
            is_active=active, is_fresh=(ts == latest_ts),
        ))
    return rows


def attach_strikes(
    row: EngineSignalRow, option_rows: Sequence[dict], *, option_name: str,
    moneynesses: Sequence[str] = ("ATM",), today: Optional[date] = None,
    expiry_types: Sequence[ExpiryType] = (),
    cfg: Optional[SterlingKiteEngineConfig] = None,
) -> EngineSignalRow:
    """Resolve and attach an option leg per selected moneyness from a raw dump.

    ``option_name`` is the option-chain underlying ("NIFTY"), which differs from
    ``row.underlying`` (the display name, e.g. "NIFTY 50") for indices. Legs are
    emitted in a fixed canonical order (ATM, ITM…, OTM…) regardless of request order.
    """
    today = today or datetime.now(_IST).date()
    chain = chain_rows_for(option_rows, option_name, today)
    is_stock = option_name.upper() not in {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "BANKEX", "MIDCPNIFTY"}
    chain = filter_liquid_contracts(chain, is_stock=is_stock)
    ordered = sorted(moneynesses, key=lambda m: _MONEYNESS_ORDER.get(m, 99))
    picks = pick_strikes(chain, spot=row.spot, direction=row.direction,
                         moneynesses=ordered, expiry_types=expiry_types, today=today,
                         **expiry_window_of(cfg or SterlingKiteEngineConfig()))
    row.resolution_reason = None
    if not picks:
        if not chain:
            row.resolution_reason = f"No listed option-chain rows were found for {option_name}."
        else:
            expiries = sorted({
                str(item.get("expiry_date") or item.get("expiry") or "")[:10]
                for item in chain
                if item.get("expiry_date") or item.get("expiry")
            })
            row.resolution_reason = (
                "No listed contract matched the selected strike and expiry series. "
                f"Available listed expiries: {', '.join(expiries[:8]) or 'none'}."
            )
    row.legs = [
        OptionLeg(moneyness=m, option_type=p.option_type, option_symbol=p.option_symbol,
                  strike=p.strike, expiry=p.expiry, lot_size=p.lot_size or None,
                  token=p.token or None,
                  is_active=bool(row.is_active),
                  signal_timestamp_ms=row.timestamp_ms,
                  entry_timestamp_ms=row.timestamp_ms)
        for m, p in picks
    ]
    return row


def option_order_args(row: EngineSignalRow, leg: Optional[OptionLeg] = None) -> Optional[dict]:
    """Pure mapping: a ready row (+ chosen leg) → Kite option-BUY order args.

    Options buying is always a BUY (a call for bull, a put for bear); quantity is
    one lot (``lot_size``). For auto-exec the primary leg is the one *nearest spot*
    (i.e. ATM when selected) — never a deep OTM contract just because it sorts
    first. The advisory ST trailing stop rides along as the SL trigger.
    """
    if leg is None and row.legs:
        reference_spot = float(row.underlying_spot or 0.0)
        min_strike = min((float(l.strike) for l in row.legs if l.strike), default=0.0)
        if reference_spot <= 0 and min_strike > 0 and float(row.spot or 0.0) > (min_strike * 0.3):
            reference_spot = float(row.spot)

        if reference_spot > 0:
            leg = min(row.legs, key=lambda l: abs(l.strike - reference_spot))
        else:
            # Fall back to canonical first leg (ATM) rather than selecting an absurd strike nearest 0
            leg = row.legs[0]
    if leg is None:
        return None
    return {
        "option_symbol": leg.option_symbol,
        "side": "buy",
        "size": int(leg.lot_size or 0),
        "lot_size": int(leg.lot_size or 0),
        "token": int(leg.token or 0),
        "exchange": row.exchange,
        # PREMIUM domain only. `row.stop_loss` is an UNDERLYING level for spot /
        # confluence / navigator rows (57,000 index points, not a ₹965 premium), so
        # falling back to it here would hand the order path a stop from the wrong
        # price domain. None means "no premium stop known yet"; the caller derives one
        # from a live quote (`_resolve_premium_stop`) before placing anything.
        "stop_loss": float(leg.premium_sl) if (leg.premium_sl or 0) > 0 else None,
        # Premium basis for risk sizing (workstream F). Derivatives legs carry the
        # option's own premium (premium_spot) + its ST trail (premium_sl); for spot
        # signals these may be None → risk-sizing degrades to a single lot.
        "entry_premium": float(leg.premium_spot) if leg.premium_spot is not None else None,
        "stop_premium": float(leg.premium_sl) if leg.premium_sl is not None else None,
    }


# ── per-user scan state ─────────────────────────────────────────────────────
@dataclass
class ScanDiag:
    """Per-scan breakdown — surfaced to the activity log so a silently-empty index
    candle fetch (the classic 'no index signals' symptom) is visible, not guessed."""
    universe: int = 0          # instruments in the scan universe
    indices: int = 0           # of those, how many are indices
    evaluated: int = 0         # had enough candles to run the engine
    no_data: int = 0           # skipped: no token / no or too-few candles (silent drops)
    index_evaluated: int = 0   # indices that had usable candles
    index_no_data: int = 0     # indices dropped for missing data
    index_fired: int = 0       # indices that produced a ready signal
    deriv_resolved: int = 0    # contracts resolved from option chains (pre-fetch)
    deriv_no_spot: int = 0     # underlyings skipped: spot price unresolved (candles+quote empty)
    deriv_charts: int = 0      # option contracts charted (had premium candles)
    deriv_no_data: int = 0     # contracts skipped: no token / empty premium fetch
    deriv_fired: int = 0       # contracts that produced a BUY signal
    #: Fired contracts whose auto-exec was refused because the contract's own last
    #: bar lags the underlying's — the signal is real but it is not CURRENT, and a
    #: market order would fill nowhere near the premium the row is quoting.
    deriv_stale_skipped: int = 0
    deriv_min_bars: int = 0    # premium-chart bar depth of charted contracts (history)
    deriv_max_bars: int = 0
    confluence_fired: int = 0  # merged rows where the underlying AND a leg's premium both fired
    # Premium hydration of underlying-signal candidate legs. A blank Entry/SL/TSL in
    # the table means premium_missing — the option's own history came back empty (or
    # rate-limited) and the row was too old to honestly use today's LTP as its entry.
    premium_ok: int = 0
    premium_missing: int = 0


@dataclass
class UserScan:
    engine: SterlingKiteEngine
    rows: List[EngineSignalRow] = field(default_factory=list)
    candle_cache: Dict[int, tuple] = field(default_factory=dict)  # token -> (mono_ts, candles)
    generated_ms: int = 0
    scanning: bool = False
    scanning_label: str = ""
    cancelled: bool = False
    diag: ScanDiag = field(default_factory=ScanDiag)
    # Internal de-duplication for the held-contract extension. Unlike the removed
    # per-contract report, this retains symbols only—no trace payload or API surface.
    scanned_contract_symbols: set[str] = field(default_factory=set)
    #: Live red-line counts for EVERY instrument this scan evaluated, whether or not
    #: it emitted a signal row. Rows only exist where there was an entry transition,
    #: so a position outlives the row that opened it and the red-count exit had
    #: nothing left to read. These two maps are what it reads instead.
    #: underlying display name → {"long": n, "short": n}
    underlying_reds: Dict[str, Dict[str, int]] = field(default_factory=dict)
    #: option tradingsymbol → count against a long-premium position (a derivatives
    #: signal runs on the contract's own premium, so "long" is its only direction)
    contract_reds: Dict[str, int] = field(default_factory=dict)
    # token → row index, lazily built and invalidated when a new scan lands
    # (keyed by generated_ms + row count). Turns detail lookup O(n)→O(1).
    _idx_key: tuple = (-1, -1)
    _row_by_token: Dict[int, "EngineSignalRow"] = field(default_factory=dict)

    def row_for_token(self, token: int, timestamp_ms: int = 0):
        """Return the scan row whose own token or one of whose leg tokens matches
        ``token`` (optionally also matching ``timestamp_ms``). O(1) via a cached
        index; falls back to a scan only on the rare token/timestamp-collision case."""
        key = (self.generated_ms, len(self.rows))
        if self._idx_key != key:
            idx: Dict[int, "EngineSignalRow"] = {}
            for r in self.rows:
                rt = getattr(r, "token", None)
                if rt is not None:
                    idx.setdefault(rt, r)
                for leg in r.legs:
                    lt = getattr(leg, "token", None)
                    if lt is not None:
                        idx.setdefault(lt, r)
            self._row_by_token = idx
            self._idx_key = key
        r = self._row_by_token.get(token)
        if timestamp_ms > 0 and r is not None:
            def _matches(x):
                if getattr(x, "token", None) == token and x.timestamp_ms == timestamp_ms:
                    return True
                return any(
                    getattr(l, "token", None) == token and timestamp_ms in {
                        int(getattr(l, "entry_timestamp_ms", 0) or 0),
                        int(getattr(l, "signal_timestamp_ms", 0) or 0),
                    }
                    for l in x.legs
                )
            if not _matches(r):
                return next((x for x in self.rows if _matches(x)), None)
        return r


def _inst(item: UniverseItem) -> InstrumentMeta:
    """Minimal InstrumentMeta for 1H candle fetch (only zerodha_token is read)."""
    return InstrumentMeta(
        underlying=item.tradingsymbol,
        tick_size=0.05, strike_step=1.0, exchange_currency="INR",
        index_name=item.name,
        has_options=True, exchange="zerodha",
        zerodha_token=item.token,
    )


class KiteEngineScanner:
    def __init__(self) -> None:
        self._users: Dict[str, UserScan] = {}

    def _hydrate_from_cache(self, us: UserScan, uid: str) -> None:
        """Restore rows persisted by a prior scan (DB-backed, survives restarts).
        Shared by _user() and snapshot() so neither creates a fresh empty
        UserScan that shadows already-good signals — a scan started by the
        auto-loop right after a restart used to do exactly that, wiping the
        board back to zero the instant it claimed the uid slot."""
        cached = state.load_signal_cache(uid)
        if cached:
            rows_data, gen_ms = cached
            try:
                us.rows = [EngineSignalRow(**r) for r in rows_data]
                us.generated_ms = gen_ms
            except Exception as _exc:
                log.debug("suppressed: %s", _exc)

    def _user(self, uid: str, cfg: SterlingKiteEngineConfig) -> UserScan:
        us = self._users.get(uid)
        if us is None:
            us = UserScan(engine=SterlingKiteEngine(cfg))
            self._hydrate_from_cache(us, uid)
            self._users[uid] = us
        return us

    def snapshot(self, uid: str) -> UserScan:
        us = self._users.get(uid)
        if us is not None:
            return us
        us = UserScan(engine=SterlingKiteEngine())
        self._hydrate_from_cache(us, uid)
        self._users[uid] = us
        return us

    def cancel(self, uid: str) -> bool:
        """Signal a running scan to stop. Returns True if a scan was running."""
        us = self._users.get(uid)
        if us and us.scanning:
            us.cancelled = True
            us.scanning = False
            us.scanning_label = "Cancelled"
            return True
        return False

    async def _fetch_candles(self, client, us: UserScan, token: int, name: str) -> List[Candle]:
        """Fetch + cache 1H candles by instrument_token. Works for underlyings AND
        option contracts (distinct token spaces, so the cache never collides).

        An EMPTY result is cached only briefly. ``get_candles`` returns ``[]`` both
        for a contract with genuinely no history and for a fetch that exhausted its
        429 retries, and the two are indistinguishable here — caching a rate-limit
        failure for the full TTL would blank every row that needs that contract for
        the rest of the scan.
        """
        hit = us.candle_cache.get(token)
        if hit and (time.monotonic() - hit[0]) < (_CANDLE_TTL_S if hit[1] else _EMPTY_CANDLE_TTL_S):
            return hit[1]
        inst = InstrumentMeta(
            underlying=name, tick_size=0.05, strike_step=1.0, exchange_currency="INR",
            index_name=name, has_options=True, exchange="zerodha",
            zerodha_token=token,
        )
        candles = await client.get_candles(inst, "1H", _LOOKBACK_BARS)
        us.candle_cache[token] = (time.monotonic(), candles)
        return candles

    async def _fetch_1h(self, client, us: UserScan, item: UniverseItem) -> List[Candle]:
        return await self._fetch_candles(client, us, item.token, item.tradingsymbol)

    async def _stamp_spot_leg_premiums(
        self, client, us: UserScan, row: EngineSignalRow,
        sem: Optional[asyncio.Semaphore] = None, diag: Optional["ScanDiag"] = None,
    ) -> None:
        """Hydrate underlying-signal candidate legs with premium entry, SL and TSL.

        Spot (and Navigator-originated) signals come off the UNDERLYING chart, but the
        board and the order path trade option contracts. Use the option's own 1H close
        at the signal timestamp as the entry premium; only a same-bar fresh signal may
        fall back to LTP — today's LTP is not what a signal from three sessions ago
        entered at, and stamping it would invent an entry price and a fake P&L.

        ``sem`` is the scan's Kite-historical throttle and is REQUIRED in production.
        Every other per-contract candle fetch in this scanner holds it; these did not,
        so a scan fanned out one unthrottled option-history request per candidate leg
        (hundreds) across all universe items at once, far past Kite's ~3 req/s
        historical cap. The resulting 429s were swallowed at debug level and surfaced
        only as blank Entry/SL/TSL cells.
        """
        if row.source not in ("spot", "navigator") or not row.legs:
            return

        missing_ltp: list[OptionLeg] = []
        for leg in row.legs:
            if (leg.premium_spot or 0) > 0:
                _stamp_leg_premium_stops(row, leg)
                continue
            entry_px = 0.0
            if leg.token:
                try:
                    if sem is not None:
                        async with sem:
                            candles = await self._fetch_candles(
                                client, us, int(leg.token), leg.option_symbol)
                    else:
                        candles = await self._fetch_candles(
                            client, us, int(leg.token), leg.option_symbol)
                    candidates = [
                        candle for candle in drop_forming(candles, exchange=row.exchange)
                        if int(candle.timestamp_ms) <= int(row.timestamp_ms)
                    ]
                    if candidates:
                        entry_px = float(max(candidates, key=lambda c: int(c.timestamp_ms)).close)
                    elif candles and not row.is_fresh:
                        entry_px = float(candles[0].open or candles[0].close)
                except Exception as exc:  # noqa: BLE001
                    log.debug("kite-engine spot premium history fail %s: %s", leg.option_symbol, exc)
            if entry_px <= 0 and not row.is_fresh and (row.spot or 0) > 0 and leg.strike:
                is_stock = str(row.underlying).upper() not in {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "BANKEX", "MIDCPNIFTY"}
                iv_est = 0.32 if is_stock else 0.18
                dte = _dte_from_expiry(leg.expiry, row.timestamp_ms)
                entry_px = bs_price(
                    spot=float(row.spot),
                    strike=float(leg.strike),
                    dte_days=max(dte, 1.0),
                    iv=iv_est,
                    option_type=leg.option_type,
                )
            if entry_px > 0:
                leg.premium_spot = entry_px
                _stamp_leg_premium_stops(row, leg)
            elif row.is_fresh:
                missing_ltp.append(leg)

        if missing_ltp:
            qkeys = [f"{row.exchange}:{leg.option_symbol}" for leg in missing_ltp]
            try:
                quotes = await client.get_ltp(qkeys)
            except Exception as exc:  # noqa: BLE001
                log.debug("kite-engine spot premium LTP fail: %s", exc)
                quotes = {}
            for leg in missing_ltp:
                qkey = f"{row.exchange}:{leg.option_symbol}"
                try:
                    entry_px = float((quotes.get(qkey) or {}).get("last_price") or 0.0)
                except (AttributeError, TypeError, ValueError):
                    entry_px = 0.0
                if entry_px <= 0:
                    continue
                leg.premium_spot = entry_px
                _stamp_leg_premium_stops(row, leg)

        if diag is not None:
            for leg in row.legs:
                if (leg.premium_spot or 0) > 0:
                    diag.premium_ok += 1
                else:
                    diag.premium_missing += 1

    async def stamp_leg_premiums(self, client, uid: str, row: EngineSignalRow) -> None:
        """Public hydrator for a row built OUTSIDE this scanner's own scan.

        Navigator originates its own rows and resolves their legs with
        :func:`attach_strikes`, so without this they reach the board with no Entry /
        SL / TSL at all. Reuses the per-user candle cache so a contract already read
        during the engine's scan costs nothing.
        """
        us = self._user(uid, state.get_config(uid))
        await self._stamp_spot_leg_premiums(client, us, row)

    async def scan(
        self, *, uid: str, client, universe: List[UniverseItem],
        nfo_rows: Sequence[dict], bfo_rows: Sequence[dict],
        cfg: SterlingKiteEngineConfig, moneyness: Sequence[str] = ("ATM",),
        expiry_types: Sequence[ExpiryType] = (),
        expiry_types_indices: Optional[Sequence[ExpiryType]] = None,
        expiry_types_stocks: Optional[Sequence[ExpiryType]] = None,
        place_cb: Optional[PlaceCb] = None,
        deriv_universe: Optional[List[UniverseItem]] = None,
        confluence_universe: Optional[List[UniverseItem]] = None,
        log_cb: Optional[Callable[[str], None]] = None,
        close_feed: Optional[Callable[[str, float], None]] = None,
    ) -> None:
        us = self._user(uid, cfg)
        us.engine.cfg = cfg
        us.scanning = True
        us.cancelled = False
        us.scanning_label = "Loading instruments…"
        us.scanned_contract_symbols.clear()
        try:
            sem = asyncio.Semaphore(_CONCURRENCY)
            today = datetime.now(_IST).date()
            now_ms = int(time.time() * 1000)  # retention cutoff for ended signals
            rows: List[EngineSignalRow] = []
            prior_premium_snapshots = _prior_leg_snapshots(us.rows)
            initial_active_confluence = {
                r.underlying: r for r in us.rows
                if getattr(r, "source", "") == "confluence" and getattr(r, "is_active", False)
            }
            diag = ScanDiag(universe=len(universe),
                            indices=sum(1 for i in universe if i.is_index))

            def _no_data(item: UniverseItem) -> None:
                diag.no_data += 1
                if item.is_index:
                    diag.index_no_data += 1

            # Flushes the rows accumulated so far into us.rows (+ DB cache) so the
            # frontend's poll sees each symbol's signal the moment it's found,
            # instead of waiting for the whole phase (spot / derivatives / confluence)
            # to finish. Defined up front so every phase's per-item loop can call it.
            def _flush() -> None:
                active_conf_underlyings = {r.underlying for r in rows if getattr(r, "source", "") == "confluence"}
                preserved = [
                    conf_row for und, conf_row in initial_active_confluence.items()
                    if und not in active_conf_underlyings
                ]
                us.rows = _compile_rows(rows + preserved)
                us.generated_ms = int(time.time() * 1000)
                model_rows = [r.model_dump() for r in us.rows]
                state.save_signal_cache(uid, model_rows, us.generated_ms)

            async def _one(item: UniverseItem) -> None:
                if us.cancelled:
                    return
                if not item.token:
                    _no_data(item)
                    return
                us.scanning_label = item.name
                if log_cb:
                    log_cb(f"Scanning spot: {item.name} ({item.exchange})")
                async with sem:
                    try:
                        candles = drop_forming(await self._fetch_1h(client, us, item), now_ms, exchange="NSE" if item.option_exchange == "NFO" else "BSE", cas_eligible=True)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("kite-engine scan candle fail %s: %s", item.name, exc)
                        _no_data(item)
                        return
                # Feed the correlation tracker the latest underlying close (opt-in).
                if close_feed and candles:
                    close_feed(item.name, float(candles[-1].close))
                # Too few bars to run the engine → a silent drop unless we record it.
                if len(candles) <= cfg.warmup + 1:
                    _no_data(item)
                    return
                # Keep this bar's red counts even when no transition fires below. An
                # open position outlives the row that opened it, and this is the only
                # thing that keeps its red-count exit alive once the signal ends.
                us.underlying_reds[item.name] = live_red_counts(candles, cfg)
                diag.evaluated += 1
                if item.is_index:
                    diag.index_evaluated += 1
                eval_rows = evaluate_item(us.engine, item, candles, cfg)
                if not eval_rows:
                    return
                option_rows = nfo_rows if item.option_exchange == "NFO" else bfo_rows
                _expiry = expiry_types_indices if (expiry_types_indices is not None and item.is_index) else expiry_types_stocks if (expiry_types_stocks is not None and not item.is_index) else expiry_types
                
                latest_ts = candles[-1].timestamp_ms
                # Surface running/just-fired setups PLUS the most-recent recently-ended
                # one, so signals don't vanish the instant a trend breaks (they show as
                # "ended" until they age out — see _retain_signals).
                new_rows = _retain_signals(eval_rows, now_ms)
                for row in new_rows:
                    attach_strikes(row, option_rows, option_name=item.tradingsymbol,
                                   moneynesses=moneyness, today=today,
                                   expiry_types=_expiry)
                    _copy_prior_leg_snapshot(row, prior_premium_snapshots)
                    await self._stamp_spot_leg_premiums(client, us, row, sem=sem, diag=diag)
                    rows.append(row)

                    is_fresh = (row.timestamp_ms == latest_ts)
                    if is_fresh:
                        if item.is_index:
                            diag.index_fired += 1
                        if place_cb is not None and row.legs:
                            try:
                                await place_cb(row, item)
                            except Exception as exc:  # noqa: BLE001
                                log.warning("kite-engine auto-exec fail %s: %s", item.name, exc)
                # Only worth a flush when this symbol actually produced a signal —
                # the common case (no setup) has nothing new to show.
                if new_rows:
                    _flush()

            # Each _one() call already flushes us.rows itself the moment it finds a
            # signal (see above) — nothing further to do once the spot phase drains.
            # NOTE: an unconditional _flush() here is a trap when universe is empty
            # (confluence/derivatives-only scan_source): it would stomp us.rows with
            # the still-empty local `rows` list before the confluence/derivatives
            # phases below get a chance to populate it, wiping the board mid-scan.
            await asyncio.gather(*[_one(i) for i in universe])

            # ── derivatives scan: triple-ST on each contract's OWN premium chart ──
            async def _deriv_one(item: UniverseItem) -> None:
                if us.cancelled:
                    return
                if not item.token:
                    return
                async with sem:
                    try:
                        under = drop_forming(await self._fetch_candles(
                            client, us, item.token, item.tradingsymbol), now_ms, exchange="NSE" if item.option_exchange == "NFO" else "BSE", cas_eligible=True)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("kite-engine deriv spot-anchor fail %s: %s", item.name, exc)
                        under = []
                
                spot = float(under[-1].close) if under else 0.0
                # Underlying spot at each 1H bar, so a derivative signal can report the
                # spot at its trigger timestamp (the premium chart alone never carries it).
                under_close_by_ts = {int(c.timestamp_ms): float(c.close) for c in under}
                # The underlying trades continuously, so its latest closed bar is the
                # clock every contract's own latest bar is measured against.
                under_latest_ts = int(under[-1].timestamp_ms) if under else 0
                if spot <= 0:
                    try:
                        # Quote by DISPLAY name (mirrors detail._spot_symbol): the LTP
                        # symbol is "NSE:NIFTY 50" / "BSE:SENSEX", NOT the option name
                        # ("NIFTY"), which is not a valid quote symbol and returns nothing.
                        qsym = f"BSE:{item.name}" if item.option_exchange == "BFO" else f"NSE:{item.name}"
                        q = await client.get_quote([qsym])
                        if q and qsym in q:
                            spot = float(q[qsym].get("last_price") or 0.0)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("kite-engine deriv spot fallback fail %s: %s", item.name, exc)

                if spot <= 0:
                    # Don't fail silently — a zero spot drops the whole chain (the classic
                    # "no derivative signals" symptom), so make it visible in the log + diag.
                    diag.deriv_no_spot += 1
                    if log_cb:
                        log_cb(f"⚠ {item.name}: spot unavailable (candles + quote both empty) — derivatives skipped")
                    return
                if close_feed:
                    close_feed(item.name, spot)
                option_rows = nfo_rows if item.option_exchange == "NFO" else bfo_rows
                chain = chain_rows_for(option_rows, item.tradingsymbol, today)
                _expiry = expiry_types_indices if (expiry_types_indices is not None and item.is_index) else expiry_types_stocks if (expiry_types_stocks is not None and not item.is_index) else expiry_types
                contracts = pick_contracts(chain, spot=spot, moneynesses=moneyness,
                                           expiry_types=_expiry, today=today)
                diag.deriv_resolved += len(contracts)

                async def _contract(m: str, pick) -> None:
                    if us.cancelled:
                        return
                    us.scanned_contract_symbols.add(pick.option_symbol)
                    if not pick.token:
                        diag.deriv_no_data += 1
                        return
                    # The label is progress the UI shows; the log line is for whoever
                    # happens to be tailing. Both were inside `if log_cb`, so on any
                    # sweep with nothing attached to the log stream the derivative
                    # label never advanced — it sat on whatever the last attached
                    # session left behind. The dock showed one contract for a whole
                    # scan while rows kept arriving, which reads as a frozen UI.
                    #
                    # The two spot paths (`_one`, `_confluence_one`) already set it
                    # outside the guard; this was the one that did not.
                    try:
                        ed = datetime.strptime(pick.expiry[:10], "%Y-%m-%d")
                        readable = f"{item.name} {ed.strftime('%b').upper()} {int(pick.strike)} {pick.option_type}"
                    except Exception:
                        readable = pick.option_symbol
                    if log_cb:
                        log_cb(f"Scanning derivative: {readable} ({m})")
                    async with sem:
                        try:
                            oc = drop_forming(await self._fetch_candles(
                                client, us, pick.token, pick.option_symbol), now_ms, exchange=item.option_exchange)
                        except Exception as exc:  # noqa: BLE001
                            log.warning("kite-engine deriv chart fail %s: %s", pick.option_symbol, exc)
                            diag.deriv_no_data += 1
                            return
                    # Set AFTER the fetch, not before it.
                    #
                    # These run concurrently under `sem`, so an assignment before the
                    # await records whichever coroutine STARTED last — and once the
                    # universe has finished launching, every worker is blocked on I/O
                    # and the label sits on that one contract for the rest of the
                    # sweep. Measured against the live dock: rows climbed 37 -> 49
                    # while the label held "TCS OCT 2300 PE" for thirty seconds.
                    #
                    # Assigning on completion makes it track work that actually
                    # finished, so it advances whenever anything lands.
                    us.scanning_label = readable
                    if not oc:
                        # Nothing returned. A short-but-present weekly is still
                        # charted below and is never treated as no-data.
                        diag.deriv_no_data += 1
                        return
                    diag.deriv_charts += 1
                    bars = len(oc)               # premium-history depth, to expose short weeklies
                    diag.deriv_min_bars = bars if diag.deriv_min_bars == 0 else min(diag.deriv_min_bars, bars)
                    diag.deriv_max_bars = max(diag.deriv_max_bars, bars)
                    # Same reason as the spot pass: a held contract keeps its exit
                    # alive off this, long after its own entry transition scrolled away.
                    deriv_reds = live_red_counts(oc, cfg)
                    if "long" in deriv_reds:
                        us.contract_reds[pick.option_symbol] = deriv_reds["long"]
                    drows = evaluate_derivative_contract(item, m, pick, oc, cfg)
                    latest_ts = oc[-1].timestamp_ms
                    # Keep running/just-fired entries plus the most-recent recently-ended
                    # one.
                    for drow in _retain_signals(drows, now_ms):
                        # Stamp the underlying spot at this signal's trigger bar (1H bars
                        # of premium and underlying share timestamps; fall back to the
                        # last bar at/before the trigger if there's no exact match).
                        drow.underlying_spot = under_close_by_ts.get(int(drow.timestamp_ms))
                        if drow.underlying_spot is None and under_close_by_ts:
                            prior = [ts for ts in under_close_by_ts if ts <= drow.timestamp_ms]
                            if prior:
                                drow.underlying_spot = under_close_by_ts[max(prior)]
                        rows.append(drow)
                        is_fresh = (drow.timestamp_ms == latest_ts)
                        if is_fresh:
                            diag.deriv_fired += 1
                            if place_cb is not None:  # auto-exec is universal (spot + derivatives)
                                # "Fired on its own last bar" is not the same as "fired
                                # now". Only the underlying's clock can tell them apart.
                                if not contract_bar_is_current(
                                        int(latest_ts), under_latest_ts,
                                        max_stale_bars=int(getattr(
                                            cfg, "max_contract_staleness_bars", 0) or 0)):
                                    diag.deriv_stale_skipped += 1
                                    if log_cb:
                                        lag_h = max(0, (under_latest_ts - int(latest_ts))) // 3_600_000
                                        log_cb(f"⚠ {pick.option_symbol}: signal is on a bar "
                                               f"{lag_h}h behind the underlying — shown, but "
                                               f"not auto-executed (contract has not traded since)")
                                else:
                                    try:
                                        await place_cb(drow, item)
                                    except Exception as exc:  # noqa: BLE001
                                        log.warning("kite-engine deriv auto-exec fail %s: %s", pick.option_symbol, exc)

                await asyncio.gather(*[_contract(m, p) for m, p in contracts])

                # Flush after this underlying's derivatives finish so signals
                # from already-scanned symbols appear immediately in the table.
                _flush()

            await asyncio.gather(*[_deriv_one(i) for i in (deriv_universe or [])])

            # ── confluence scan: underlying regime AND the leg's own premium ──────
            # A candidate strike is emitted only when the UNDERLYING fires a fresh
            # entry (spot regime, direction → CE/PE) AND that option's OWN premium
            # triple-ST also confirms (a running/fresh BUY). One merged row per
            # underlying signal, carrying only the confirmed legs — the highest-
            # conviction filter ("the index turned AND the option is actually moving").
            async def _confluence_one(item: UniverseItem) -> None:
                if us.cancelled:
                    return
                if not item.token:
                    _no_data(item)
                    return
                us.scanning_label = item.name
                if log_cb:
                    log_cb(f"Scanning confluence: {item.name} ({item.exchange})")
                async with sem:
                    try:
                        candles = drop_forming(await self._fetch_1h(client, us, item), now_ms, exchange="NSE" if item.option_exchange == "NFO" else "BSE", cas_eligible=True)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("kite-engine confluence candle fail %s: %s", item.name, exc)
                        _no_data(item)
                        return
                if close_feed and candles:
                    close_feed(item.name, float(candles[-1].close))
                if len(candles) <= cfg.warmup + 1:
                    _no_data(item)
                    return
                # As in the spot pass. Confluence is a whole scan_source of its own, so
                # without this an account running it would have no underlying reading at
                # all and every position's red counter would go back to freezing — the
                # `return` two lines below leaves early on the common no-signal path.
                us.underlying_reds[item.name] = live_red_counts(candles, cfg)
                diag.evaluated += 1
                if item.is_index:
                    diag.index_evaluated += 1
                eval_rows = evaluate_item(us.engine, item, candles, cfg)
                if not eval_rows:
                    return
                option_rows = nfo_rows if item.option_exchange == "NFO" else bfo_rows
                _expiry = expiry_types_indices if (expiry_types_indices is not None and item.is_index) else expiry_types_stocks if (expiry_types_stocks is not None and not item.is_index) else expiry_types
                chain = chain_rows_for(option_rows, item.tradingsymbol, today)
                ordered = sorted(moneyness, key=lambda m: _MONEYNESS_ORDER.get(m, 99))
                latest_ts = candles[-1].timestamp_ms
                prior_active_confluence = dict(initial_active_confluence)
                for r in rows:
                    if getattr(r, "source", "") == "confluence" and getattr(r, "is_active", False):
                        prior_active_confluence[r.underlying] = r
                retained_rows = _retain_signals(eval_rows, now_ms)

                # Confluence must exist on one bar; never join an old underlying
                # trigger to a premium trend observed later.
                for row in retained_rows:
                    if not row.is_fresh or int(row.timestamp_ms) != int(latest_ts):
                        if row.is_active and row.underlying in prior_active_confluence:
                            prior = prior_active_confluence[row.underlying]
                            row.source = "confluence"
                            row.legs = prior.legs
                            row.underlying_spot = row.spot
                            rows.append(row)
                        continue
                    # Candidate strikes for this signal's direction — the SAME picks
                    # attach_strikes resolves — then confirm each on its own premium.
                    picks = pick_strikes(chain, spot=row.spot, direction=row.direction,
                                         moneynesses=ordered, expiry_types=_expiry, today=today)
                    confirmed: List[OptionLeg] = []
                    for m, pick in picks:
                        if us.cancelled:
                            return
                        if not pick.token:
                            continue  # no premium series to confirm against
                        async with sem:
                            try:
                                oc = drop_forming(await self._fetch_candles(
                                    client, us, pick.token, pick.option_symbol), now_ms, exchange=item.option_exchange)
                            except Exception as exc:  # noqa: BLE001
                                log.warning("kite-engine confluence chart fail %s: %s",
                                            pick.option_symbol, exc)
                                continue
                        if len(oc) <= 1:
                            continue
                        if int(oc[-1].timestamp_ms) != int(latest_ts):
                            continue
                        diag.deriv_charts += 1
                        bars = len(oc)
                        diag.deriv_min_bars = bars if diag.deriv_min_bars == 0 else min(diag.deriv_min_bars, bars)
                        diag.deriv_max_bars = max(diag.deriv_max_bars, bars)
                        drows = evaluate_derivative_contract(item, m, pick, oc, cfg)
                        # Confirmed = the premium is CURRENTLY trending up (running/fresh
                        # BUY), not merely a stale historical entry.
                        live = [d for d in drows if d.is_active or d.is_fresh]
                        if not live:
                            continue
                        d = max(live, key=lambda x: x.timestamp_ms)
                        leg = d.legs[0]
                        # Entry basis = the option's CURRENT premium (last closed bar),
                        # NOT d.spot. d.spot is the premium at the leg's OWN ST entry bar;
                        # when the underlying fires fresh but the premium has been trending
                        # for several bars (is_active, not is_fresh) that is a stale historical
                        # price. Confluence enters NOW on the underlying's fresh signal, so the
                        # entry price is the current premium — using the stale value as
                        # premium_spot → entry_premium would show a fake unrealized gain and,
                        # if the WS fill postback is missed, book a wrong realized PnL into the
                        # INR daily-loss breaker. (For a fresh leg oc[-1].close == d.spot.)
                        leg.premium_spot = float(oc[-1].close)
                        leg.premium_sl = d.stop_loss
                        # This trade starts on the confluence bar, so do not inherit
                        # the static stop from an older standalone premium entry.
                        leg.entry_sl = d.stop_loss
                        leg.token = pick.token
                        leg.is_active = d.is_active
                        leg.entry_timestamp_ms = int(row.timestamp_ms)
                        confirmed.append(leg)
                    if not confirmed:
                        continue
                    confirmed.sort(key=lambda l: _MONEYNESS_ORDER.get(l.moneyness, 99))
                    row.source = "confluence"
                    row.legs = confirmed
                    row.underlying_spot = row.spot  # spot mode: `spot` IS the underlying
                    rows.append(row)
                    if row.timestamp_ms == latest_ts:
                        diag.confluence_fired += 1
                        if place_cb is not None:
                            try:
                                await place_cb(row, item)
                            except Exception as exc:  # noqa: BLE001
                                log.warning("kite-engine confluence auto-exec fail %s: %s",
                                            item.name, exc)
                _flush()

            await asyncio.gather(*[_confluence_one(i) for i in (confluence_universe or [])])

            us.diag = diag
            us.generated_ms = int(time.time() * 1000)
            model_rows = [r.model_dump() for r in us.rows]
            state.save_signal_cache(uid, model_rows, us.generated_ms)
        finally:
            us.scanning = False
            us.scanning_label = ""


scanner = KiteEngineScanner()


# ── setup chart (click-to-visualize) ────────────────────────────────────────
async def build_setup_chart(
    client, token: int, underlying: str, cfg: SterlingKiteEngineConfig,
) -> SetupChart:
    item = UniverseItem(name=underlying or str(token), tradingsymbol=underlying or str(token),
                        token=token, exchange="", option_exchange="NFO")
    candles = drop_forming(await client.get_candles(_inst(item), "1H", _LOOKBACK_BARS))
    o = np.array([c.open for c in candles], float)
    h = np.array([c.high for c in candles], float)
    l = np.array([c.low for c in candles], float)
    c = np.array([c.close for c in candles], float)
    ha_o, ha_h, ha_l, ha_c = compute_heikin_ashi(o, h, l, c)
    r = compute_regime(o, h, l, c, cfg)
    longs, shorts = entry_transitions(r)

    secs = [int(cd.timestamp_ms // 1000) for cd in candles]
    points = [SetupPoint(time=secs[i], open=float(ha_o[i]), high=float(ha_h[i]),
                         low=float(ha_l[i]), close=float(ha_c[i])) for i in range(len(candles))]

    def _line(arr) -> List[SetupLine]:
        out: List[SetupLine] = []
        for i in range(cfg.warmup, len(candles)):
            v = float(arr[i])
            if v > 0:
                out.append(SetupLine(time=secs[i], value=v))
        return out

    fresh = np.where(longs | shorts)[0]
    entry_index = int(fresh[-1]) if len(fresh) else None
    return SetupChart(
        underlying=underlying or str(token), candles=points,
        st_fast=_line(r.l_fast), st_mid=_line(r.l_mid), st_slow=_line(r.l_slow),
        entry_index=entry_index, trail_target=cfg.trail_target,
        exit_mode=cfg.exit_mode,
    )
