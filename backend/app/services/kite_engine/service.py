"""Shared scan orchestration for the Kite engine.

One ``scan_user`` entrypoint used by BOTH the manual ``/scan`` endpoint and the
background auto-scan loop: builds the universe from the live instrument dumps,
runs the scanner, logs activity, updates status, and (when auto-execute is on)
places gated option BUYs through the Kite order path. No other-engine imports.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.core.logging import get_logger
from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
from app.engines.sterling_kite_engine.schemas import EngineConfigModel
from app.services import live_safety
from app.services.kite_engine import monitor, order_journal, positions, protection, protective_stop, sizing, state
from app.services.kite_engine import futures as futures_mod
from app.services.kite_engine.greeks import (
    black_scholes_greeks, implied_vol, premium_stop_from_move,
)
from app.services.kite_engine import market_hours
from app.services.kite_engine.market_hours import is_market_open
from app.services.kite_engine.scanner import option_order_args, scanner
from app.services.kite_engine.strikes import (chain_rows_for, expiry_window_of,
                                              pick_by_delta, pick_strikes)
from app.services.kite_engine.universe import build_universe, select_scan_universe

_IST = timezone(timedelta(hours=5, minutes=30))

log = get_logger(__name__)

SCAN_INTERVAL_S = 300  # background auto-scan cadence (5 min; 1H bars move slowly)

_auto_running = False
_first_scan_done = False
_entry_locks: dict[str, asyncio.Lock] = {}


def is_auto_running() -> bool:
    return _auto_running


def has_scanned() -> bool:
    return _first_scan_done


def _ts_cfg(c: EngineConfigModel) -> SterlingKiteEngineConfig:
    return SterlingKiteEngineConfig(
        trail_target=c.trail_target,
        exit_mode=c.exit_mode,
        exit_aligned_trail=getattr(c, 'exit_aligned_trail', False),
        price_stop_exit=getattr(c, 'price_stop_exit', True),
        max_contract_staleness_bars=getattr(c, 'max_contract_staleness_bars', 0),
    )


async def place_manual_order(uid: str, option_symbol: str, side: str,
                             quantity: int, exchange: str = "NFO") -> dict:
    """Shared manual BUY/SELL path used by BOTH the detail-panel REST endpoint and
    the Telegram bot, so they apply the identical live-safety gate + idempotency and
    place through the same warm client. Returns a status dict (never raises):
      {status: ok|duplicate|blocked|error, order_id?, message?, reason?, code?,
       protected?, protection?}
    Callers map this to HTTP / chat replies.

    A BUY here is a real position, so — when ``protect_manual_orders`` is on — it is
    registered and armed through the same `protection.arm_position` the auto-exec
    path uses, with the stop read off the board's own plan for that contract. Before
    this, a hand-placed order got no registry entry, no broker stop and no monitor,
    while the board kept showing it an SL, a TSL and a Target. `protected` says which
    of those two worlds the caller is in, so the UI can stop implying a stop that
    does not exist.

    A SELL is an exit, not an entry: if it matches a position we hold, it goes
    through the monitor's exit path so the manual sell and an automatic one cannot
    both fire.
    """
    from app.services.exchanges.kite import accounts as kite_accounts
    from app.services.exchanges.kite.errors import KiteError

    norm = "buy" if side.upper() == "BUY" else "sell"
    idem = live_safety.make_idempotency_key(uid, option_symbol, side.upper(), quantity)
    # Kite is INR; the USD daily-loss breaker is crypto-only (kill-switch + idempotency still apply).
    decision = live_safety.assert_safe_to_trade(
        positions=[], idempotency_key=idem, check_daily_loss=False)
    if not decision.allowed and decision.code != "duplicate_order":
        state.log(uid, "order_blocked", f"{side} {option_symbol} blocked: {decision.reason}")
        return {"status": "blocked", "reason": decision.reason, "code": decision.code}
    prior = live_safety.check_idempotency(idem)
    if prior:
        return {"status": "duplicate", "order_id": prior, "message": "Already submitted"}

    acct = kite_accounts.get_active(uid)
    if not acct:
        return {"status": "error", "message": "No active Kite account."}
    client = await kite_accounts.acquire_client(acct)

    # ── a manual SELL of something we hold is an EXIT ──────────────────────────
    # Routing it through the monitor takes the same `_exiting` claim an automatic
    # exit takes, so the user's sell and a trail/GTT exit cannot both place a SELL
    # for one position — and the registry, realized PnL and guard release all happen
    # exactly once, instead of the position lingering "open" after a manual close.
    if norm == "sell":
        held = positions.get(uid, option_symbol)
        if held is not None and held.status in (positions.OPEN, positions.PENDING):
            # The exit price is only used for the activity line and the realized-PnL
            # figure that feeds the INR daily-loss breaker. Passing the STOP there books
            # every discretionary exit as if it had been stopped out, which is a
            # fabricated number in both directions — so ask for the real one, and fall
            # back to the stop only if the quote fails.
            exit_px = held.stop_premium
            try:
                key = f"{held.exchange}:{held.symbol}"
                q = await client.get_ltp([key])
                exit_px = float((q or {}).get(key, {}).get("last_price") or held.stop_premium)
            except Exception:  # noqa: BLE001
                pass
            exited = await monitor._exit_position(
                client, uid, held, exit_px, reason="manual exit from the board",
                price_stop_exit=False)
            if not exited:
                # `_exit_position` bails without selling when another exit path already
                # holds this position, or when a broker stop it could not cancel may
                # already be selling it. Reporting "closed" here would be a lie the user
                # acts on — and the idempotency key must stay unrecorded, or the retry
                # would come back "Already submitted" for the next 60 seconds.
                state.log(uid, "order_failed",
                          f"⚠ {option_symbol}: manual exit did NOT go through — the position "
                          f"is STILL OPEN. Check Zerodha for a resting or triggered stop.")
                return {"status": "error", "order_id": held.order_id or "",
                        "message": ("Could not close the position — it is still open. A broker "
                                    "stop may already be selling it, or another exit is in "
                                    "flight. Check Zerodha, then retry."),
                        "protected": False, "protection": "position still open"}
            live_safety.record_idempotency(idem, held.order_id or "manual-exit")
            # `_exit_position` always exits the WHOLE tracked position — a GTT-protected
            # holding cannot be part-sold without re-arming the trigger for the remainder,
            # which it does not do. Say so rather than let a smaller requested quantity
            # imply a partial close happened.
            note = ("" if int(quantity) >= int(held.qty or 0) else
                    f" — the whole tracked position ({held.qty} qty) was closed, not the "
                    f"{quantity} requested")
            return {"status": "ok", "order_id": held.order_id or "",
                    "message": "Exit submitted; awaiting broker fill confirmation",
                    "protected": False, "protection": "exit pending broker confirmation"}

    cfg = state.get_config(uid)
    plan = None
    if norm == "buy" and getattr(cfg, "protect_manual_orders", True):
        plan = protection.plan_for_symbol(uid, option_symbol)

    try:
        result = await client.place_order_option(
            option_symbol, norm, quantity, exchange=exchange, tag=idem)
    except KiteError as exc:
        state.log(uid, "order_failed", f"{side} {option_symbol}: {exc}")
        return {"status": "error", "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        state.log(uid, "order_failed", f"{side} {option_symbol}: {exc}")
        return {"status": "error", "message": str(exc)}

    oid = (result or {}).get("order_id", "")
    if oid:
        live_safety.record_idempotency(idem, oid)
    state.log(uid, "order_placed", f"{side} {quantity} {option_symbol} (#{oid})")

    if norm != "buy":
        return {"status": "ok", "order_id": oid, "message": "Order submitted"}

    armed = await arm_manual_option_buy(
        client, uid, option_symbol=option_symbol, exchange=exchange,
        quantity=quantity, order_id=oid, plan=plan)
    return {"status": "ok", "order_id": oid, "message": "Order submitted", **armed}


async def arm_manual_option_buy(client, uid: str, *, option_symbol: str, exchange: str,
                                quantity: int, order_id: str, plan=None) -> dict:
    """Turn a just-placed hand-made option BUY into a protected position.

    Returns ``{"protected": bool, "protection": str}`` — never raises and never unwinds:
    the order is already live at the exchange, so a failure here is reported, not
    thrown at a caller who can no longer undo the trade. An entry we could not protect
    still succeeded as an ORDER; what must not happen is the board rendering an SL, a
    TSL and a Target beside a position that has none.

    Shared by BOTH order paths on purpose. ``/kite/engine/order`` (detail panel,
    Telegram) is not the one the signal board's Buy button reaches — that goes Buy →
    OrderWindow → ``POST /kite/orders`` — so arming only the first left every entry
    clicked from the board unguarded.
    """
    cfg = state.get_config(uid)
    if plan is None and getattr(cfg, "protect_manual_orders", True):
        plan = protection.plan_for_symbol(uid, option_symbol)
    if plan is None:
        reason = ("this contract is not on the current board, so there is no stop to arm"
                  if getattr(cfg, "protect_manual_orders", True)
                  else "automatic protection for manual orders is switched off")
        state.log(uid, "info", f"⚠ {option_symbol} is UNPROTECTED — {reason}")
        return {"protected": False, "protection": reason}
    if not plan.protectable:
        reason = "the signal has no premium stop for this contract, so nothing was armed"
        state.log(uid, "info", f"⚠ {option_symbol} is UNPROTECTED — {reason}")
        return {"protected": False, "protection": reason}

    # A stop that is not below the live premium is not a stop. Arm ZERO rather than
    # skip arming altogether: the registry row, the tick subscription and — the
    # expensive one — the expiry square-off all still apply, so a physically-settled
    # stock option cannot go to delivery just because its stop was unusable.
    stale = await protection.stale_stop_reason(client, plan)
    stop_to_arm = 0.0 if stale else plan.stop_premium

    try:
        armed = await protection.arm_position(
            client, uid, symbol=plan.symbol, exchange=plan.exchange or exchange,
            token=plan.token, qty=quantity, lot_size=plan.lot_size,
            entry_premium=plan.entry_premium, stop_premium=stop_to_arm,
            order_id=order_id, stop_mode=cfg.stop_mode, direction=plan.direction,
            # Hand-placed is the path essentially every real position takes, so leaving
            # this off meant the C5 fix reached almost nothing.
            signal_direction=plan.signal_direction,
            vehicle="otm_options", underlying=plan.underlying, exit_mode=cfg.exit_mode,
            entry_spot=plan.entry_spot, entry_delta=plan.entry_delta,
            strike=plan.strike, expiry=plan.expiry, target_premium=plan.target_premium)
    except Exception as exc:  # noqa: BLE001
        log.warning("manual order arming failed for %s/%s: %s", uid, option_symbol, exc)
        state.log(uid, "order_failed",
                  f"⚠ {option_symbol} filled but could NOT be protected: {exc}")
        return {"protected": False, "protection": f"arming failed: {exc}"}

    if stale:
        state.log(uid, "order_failed",
                  f"⚠ {option_symbol} has NO STOP — {stale}. It is tracked (tick monitor + "
                  f"expiry square-off) but you must set a stop yourself.")
        return {"protected": False, "protection": f"no stop armed — {stale}"}
    state.log(uid, "order_placed", f"{option_symbol} protected — {armed.describe()}")
    return {"protected": armed.protected, "protection": armed.describe()}


async def disarm_for_manual_exit(client, uid: str, option_symbol: str) -> str:
    """Take the broker stop off a position the user is selling by hand. Returns a note
    for the caller, or "".

    A resting GTT plus a hand-placed SELL is the orphan case: once the user's sell
    fills, the trigger has nothing behind it, and if it later fires the account goes
    NAKED SHORT an option. Cancelling first is strictly safer than cancelling after —
    the worst case is a few seconds with only the tick monitor guarding.
    """
    held = positions.get(uid, option_symbol)
    if held is None or held.status not in (positions.OPEN, positions.PENDING) or not held.gtt_id:
        return ""
    outcome = await protective_stop.cancel_stop_result(client, held.gtt_id)
    positions.update_stop(uid, option_symbol, held.stop_premium, gtt_id=0)
    if outcome == protective_stop.CANCELLED:
        state.log(uid, "info",
                  f"{option_symbol}: broker GTT #{held.gtt_id} cancelled for a hand-placed "
                  f"SELL — it would have been a resting order with no position behind it")
        return ""
    state.log(uid, "order_failed",
              f"⚠ {option_symbol}: broker GTT #{held.gtt_id} could NOT be cancelled "
              f"({outcome}) before your SELL — if it fires after you are flat you will be "
              f"SHORT an option. Check Zerodha now.")
    return f"broker stop #{held.gtt_id} could not be cancelled ({outcome}) — check Zerodha"


async def available_fo_capital(client) -> float:
    """Available F&O-segment capital (INR) for risk sizing. Falls back to 0 on
    error — sizing then floors to 1 lot."""
    try:
        margins = await client.get_margins("equity")
        # Kite nests available cash under available.live_balance / available.cash.
        avail = (margins or {}).get("available", {}) if isinstance(margins, dict) else {}
        return float(avail.get("live_balance") or avail.get("cash") or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


_IV_ASSUMPTION = 0.18  # fixed BS IV for delta translation (matches the study harness)


def _dte_from_expiry(expiry: str, today: Optional[datetime] = None) -> float:
    """Calendar days to an option ``expiry`` ("YYYY-MM-DD…"). Floored at 1 so a
    same-/next-day weekly still prices with non-degenerate greeks. Returns a safe
    default (7) if the string can't be parsed."""
    try:
        d = datetime.strptime(str(expiry)[:10], "%Y-%m-%d").date()
        ref = (today or datetime.now(_IST)).date()
        return float(max(1, (d - ref).days))
    except Exception:  # noqa: BLE001
        return 7.0


def _passes_liquidity(quote, max_spread_pct, min_oi) -> tuple:
    """An enabled gate requires finite, usable broker evidence."""
    from math import isfinite
    if max_spread_pct is None and min_oi is None:
        return True, ""
    if not isinstance(quote, dict):
        return False, "liquidity_quote_missing"
    try:
        if min_oi is not None:
            value = quote.get("oi", quote.get("open_interest"))
            if value is None or not isfinite(float(value)) or float(value) < float(min_oi):
                return False, "OI missing or below minimum"
        if max_spread_pct is not None:
            depth = quote.get("depth") or {}
            bid = float((depth.get("buy") or [{}])[0].get("price") or 0)
            ask = float((depth.get("sell") or [{}])[0].get("price") or 0)
            if not all(isfinite(v) and v > 0 for v in (bid, ask)) or ask < bid:
                return False, "invalid or missing two-sided depth"
            spread = (ask - bid) / ((ask + bid) / 2) * 100
            if spread > float(max_spread_pct):
                return False, f"spread {spread:.1f}% > {max_spread_pct}%"
    except (ValueError, TypeError, IndexError, AttributeError):
        return False, "malformed liquidity evidence"
    return True, ""


#: Widest solved IV still treated as a real market quote (1% … 300%). ``implied_vol``
#: clamps into [1e-3, 5.0], and a solve pinned near either bound means the price it was
#: given is not a tradable one — a stale print, a crossed book, or a premium below
#: intrinsic. Those fall back to the assumption rather than poisoning the delta.
_IV_SOLVE_BOUNDS = (0.01, 3.0)


def _effective_iv(*, price: float, spot: float, strike: float, dte_days: float,
                  option_type: str, fallback: float = _IV_ASSUMPTION) -> float:
    """The volatility to translate an underlying move into a premium move with.

    Prefers the IV backed out of the option's OWN last price. The delta that carries
    the underlying's SuperTrend level into a premium stop is sensitive to vol, and a
    flat 18% assumption is wrong by a wide margin on most real chains — an index
    weekly frequently prints north of 25%, a single stock far more around results. The
    consequence is not academic: the broker's GTT trigger ends up at a different
    premium from the stop the board is showing for the same position, so the two
    disagree about where the trade is protected.

    Solving from the same quote the entry premium came from makes them agree. When the
    quote is missing or the solve is degenerate, the caller's assumption stands.
    """
    if price <= 0 or spot <= 0 or strike <= 0 or dte_days <= 0:
        return fallback
    try:
        solved = implied_vol(price=float(price), spot=float(spot), strike=float(strike),
                             dte_days=float(dte_days), option_type=option_type)
    except Exception as _exc:  # noqa: BLE001
        log.debug("suppressed: %s", _exc)
        return fallback
    lo, hi = _IV_SOLVE_BOUNDS
    return solved if lo <= solved <= hi else fallback


async def _resolve_premium_stop(
    client, *, exch: str, symbol: str, strike: float, expiry: str,
    option_type: str, spot: float, trail_level: float, iv: float = _IV_ASSUMPTION,
) -> tuple:
    """Fetch an option's LTP and derive a delta-implied premium stop from the
    underlying ST ``trail_level``. Returns ``(entry_premium, stop_premium, delta)``.

    Shared by the OTM (spot-signal) and deep-ITM auto-exec paths so the spot→premium
    stop translation lives in exactly one place. ``entry_premium`` is 0 when the quote
    is unavailable (caller then degrades to single-lot sizing + the tick monitor).

    ``iv`` is only the FALLBACK. When the quote answers, the vol is solved out of that
    same premium instead — see ``_effective_iv``.
    """
    entry_premium = 0.0
    qkey = f"{exch}:{symbol}"
    try:
        q = await client.get_ltp([qkey])
        if q and qkey in q:
            entry_premium = float(q[qkey].get("last_price") or 0.0)
    except Exception as _exc:  # noqa: BLE001
        log.debug("suppressed: %s", _exc)
    dte = _dte_from_expiry(expiry)
    eff_iv = _effective_iv(price=entry_premium, spot=float(spot), strike=float(strike),
                           dte_days=dte, option_type=option_type, fallback=iv)
    g = black_scholes_greeks(spot=float(spot), strike=float(strike), dte_days=dte,
                             iv=eff_iv, option_type=option_type)
    # SIGNED delta: + for a CE, − for a PE. Falls back to a signed ±0.5 if BS returns
    # a degenerate 0 so the translation still produces a stop on either side.
    delta = g.delta if g.delta != 0.0 else (0.5 if str(option_type).upper().startswith("C") else -0.5)
    stop_premium = premium_stop_from_move(
        entry_premium=entry_premium, delta=delta, spot=float(spot), trail_level=float(trail_level))
    return entry_premium, stop_premium, delta


@dataclass
class _ResolvedTrade:
    """The instrument the auto-exec should actually trade for the chosen vehicle."""
    symbol: str
    exchange: str
    token: int
    lot_size: int
    entry_px: float            # premium (options) or index price (futures)
    stop_px: float             # premium stop (options) or index-point stop (futures)
    delta: float = 0.0         # |BS delta| for option vehicles (0 for futures)
    strike: float = 0.0
    expiry: str = ""


async def _resolve_future(client, item, expiry_pref: str) -> Optional[futures_mod.FuturesPick]:
    """Resolve the near/next-month index future for an underlying from the warm
    instrument dump (no extra network round-trip on a warm cache)."""
    exch = item.option_exchange  # NFO / BFO
    try:
        dump = await client.search_instruments("", exch, limit=1_000_000)
    except Exception:  # noqa: BLE001
        return None
    return futures_mod.pick_futures_contract(
        dump, name=item.tradingsymbol, exchange=exch,
        expiry_preference="next" if expiry_pref == "next" else "near",
        today=datetime.now(_IST).date())


async def _futures_entry_and_stop(
    client, *, exchange: str, symbol: str, spot: float, trail_level: float,
) -> tuple:
    """Translate an UNDERLYING-domain entry and trail into the futures contract's own
    price domain. Returns ``(entry, stop)``, or ``(0.0, 0.0)`` when it cannot.

    A future trades at spot plus basis (cost of carry less dividends). On an index
    that is tens of points; on a single-stock future it is larger, and it can be
    NEGATIVE (a discount). Using the index level as the futures entry mis-states the
    entry that every realized-PnL figure is derived from, and using the underlying's
    SuperTrend level as the GTT trigger puts the broker's stop a whole basis from
    where it was meant to sit — in a discount, on the wrong side of the last traded
    price, where the exchange either rejects the trigger or fires it at once.

    Futures track spot ~1:1, so the stop DISTANCE is already correct and only the
    level needs shifting: ``stop = trail + (futures_ltp − spot)``. That holds for both
    directions — a long's stop sits below the entry and a short's above it, by the
    same number of points as on the underlying chart.

    Returning zeros on an unavailable quote lets the caller's existing "no stop, no
    trade" guard refuse the entry, rather than opening a position whose protective
    trigger is in the wrong units.
    """
    if spot <= 0 or trail_level <= 0:
        return 0.0, 0.0
    key = f"{exchange}:{symbol}"
    try:
        quote = await client.get_ltp([key])
        last = float((quote or {}).get(key, {}).get("last_price") or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0, 0.0
    if last <= 0:
        return 0.0, 0.0
    return last, max(0.0, trail_level + (last - spot))


async def _resolve_deep_itm(client, item, row, cfg) -> Optional[_ResolvedTrade]:
    """Resolve a deep-ITM (≈delta-0.9) CE/PE for the signal direction, with an
    LTP-based entry premium and a delta-implied premium stop derived from the
    underlying ST trail. Returns None if the strike can't be resolved."""
    exch = item.option_exchange
    try:
        dump = await client.search_instruments("", exch, limit=1_000_000)
    except Exception:  # noqa: BLE001
        return None
    today = datetime.now(_IST).date()
    chain = chain_rows_for(dump, item.tradingsymbol, today)
    if not chain:
        return None
    direction = "long" if getattr(row, "direction", "long") in ("long", "bull", 1) else "short"
    # Strike SELECTION keeps the flat assumption: picking a ~0.9-delta strike compares
    # candidates across a whole chain, and solving a real IV for each would need a
    # quote per strike. The chosen leg's protective stop does not — it is derived from
    # that one contract's own price, and `_resolve_premium_stop` solves the vol there.
    iv = _IV_ASSUMPTION
    expiry_types = tuple(cfg.scan_expiries or ())
    if cfg.target_delta:
        pick = pick_by_delta(chain, spot=row.spot, direction=direction,
                             target_delta=float(cfg.target_delta), iv=iv,
                             expiry_types=expiry_types, today=today)
    else:
        picks = pick_strikes(chain, spot=row.spot, direction=direction,
                             moneynesses=[cfg.itm_depth or "ITM10"],
                             expiry_types=expiry_types, today=today,
                             **expiry_window_of(cfg))
        pick = picks[0][1] if picks else None
    if pick is None or not pick.option_symbol:
        return None

    # entry premium (single LTP quote) + delta-implied premium stop from the
    # underlying ST trail — the SAME translation used for spot-mode OTM legs.
    entry_premium, stop_premium, delta = await _resolve_premium_stop(
        client, exch=exch, symbol=pick.option_symbol, strike=float(pick.strike),
        expiry=pick.expiry, option_type=pick.option_type,
        spot=float(row.spot), trail_level=float(row.stop_loss or row.spot), iv=iv)
    return _ResolvedTrade(
        symbol=pick.option_symbol, exchange=exch, token=int(pick.token or 0),
        lot_size=int(pick.lot_size or 0), entry_px=entry_premium, stop_px=stop_premium,
        delta=delta, strike=float(pick.strike), expiry=str(pick.expiry))


def entry_data_block_reason(row, *, exchange: str, buffer_minutes: int) -> str:
    reason = market_hours.entry_block_reason(
        exchange=exchange, cash_signal=row.source != "derivatives", buffer_minutes=buffer_minutes)
    if reason:
        return reason
    signal_at = datetime.fromtimestamp(row.timestamp_ms / 1000, _IST)
    now = datetime.now(_IST)
    if signal_at.date() != now.date() or not (0 <= (now - signal_at).total_seconds() - 3600 <= 600):
        return "stale_or_unclosed_signal"
    return ""


def _make_place_cb(client, uid: str):
    """Gated auto-exec: risk-sized option BUY (nearest-spot leg) under the same
    live-safety + idempotency checks as manual Kite orders, with a broker-side
    protective stop and tick-monitor registration. Logs every outcome.

    When directional_mode is ON and vehicle is 'futures', dispatches to the
    futures order path instead.  All existing behavior is preserved when
    directional_mode is OFF (default)."""
    async def _cb(row, item) -> None:
        args = option_order_args(row)  # primary (first) leg — existing options behavior
        if not args or not args["option_symbol"] or args["size"] <= 0:
            return
        cfg = state.get_config(uid)
        if any(p.exit_order_id or p.pnl_reconciliation_required for p in positions._load(uid).values()
               if p.status in (positions.OPEN, positions.PENDING) or p.pnl_reconciliation_required):
            state.log(uid, "order_blocked", "unreconciled_exit_or_pnl")
            return
        health_reasons = autoexec_preflight(uid)
        if health_reasons:
            state.log(uid, "order_blocked", "; ".join(health_reasons))
            return

        # ── Sterling Value-Flow Navigator gate (additive; a pass-through
        # unless the user explicitly enabled Navigator in `gate` mode).
        # NOTE: gate mode is reachable in production — promoting a calibration
        # report unlocks it, and a user can then select it. Do not reason about
        # this block as dead code.
        # Reading the config itself is allowed to fail OPEN — a Navigator-
        # side hiccup (its tables not migrated, a transient read error)
        # must never halt the entire unrelated Kite auto-exec engine for
        # users who never touched Navigator. But once the config confirms
        # gate mode is actually active, a subsequent eligibility-check
        # failure fails CLOSED — at that point we cannot prove the user's
        # own explicitly-selected gate isn't being bypassed.
        nav_gate_active = False
        try:
            from app.services.navigator import config_store as navigator_config_store
            nav_record = navigator_config_store.get(uid, default_underlyings=cfg.scan_indices)
            nav_gate_active = nav_record.config.enabled and nav_record.config.operating_mode == "gate"
        except Exception as exc:  # noqa: BLE001
            log.debug("Navigator config unavailable, treating as not-gating for %s: %s", uid, exc)

        if nav_gate_active:
            try:
                from app.services.navigator import service as navigator_service
                nav_eligible, nav_reason = navigator_service.check_execution_eligible(
                    uid, row, default_underlyings=cfg.scan_indices,
                )
            except Exception as exc:  # noqa: BLE001
                log.error("Navigator gate check failed while gate mode is active — failing closed for %s: %s", uid, exc)
                nav_eligible, nav_reason = False, "navigator_check_failed"
            if not nav_eligible:
                state.log(uid, "order_blocked", f"{row.underlying}: entry skipped — Navigator gate ({nav_reason})")
                return

        # One open auto-position per "slot": per underlying for spot signals, per
        # contract for derivatives (so each fired CE/PE strike is independent).
        guard_key = args["option_symbol"] if row.source == "derivatives" else row.underlying
        if state.is_auto_open(uid, guard_key):
            return

        # ── "both"-mode cross guard ────────────────────────────────────────────
        # In scan_source="both", a spot signal and a derivatives signal on the SAME
        # underlying can both fire in one scan (their guard keys differ: underlying
        # vs option symbol). Block the second so one move isn't traded twice.
        if getattr(cfg, "scan_source", "spot") == "both" and any(
                op.underlying == row.underlying for op in positions.open_positions(uid)):
            state.log(uid, "info",
                      f"{row.underlying}: entry skipped — already holding a position on this "
                      f"underlying (both-mode cross guard)")
            return

        # Rechecked immediately before submission: scans cross session boundaries.
        blk = int(getattr(cfg, "block_entry_minutes_before_close", 0) or 0)
        session_reason = entry_data_block_reason(row, exchange=row.exchange, buffer_minutes=blk)
        if session_reason:
            state.log(uid, "order_blocked", f"{row.underlying}: {session_reason}")
            return

        # ── INR daily-loss breaker (opt-in): halt new entries after a bad day ───
        if getattr(cfg, "max_daily_loss_pct", None) is not None:
            cap0 = await available_fo_capital(client)
            day_pnl = state.daily_realized_pnl(uid)
            if cap0 > 0 and day_pnl < 0 and (-day_pnl) >= (float(cfg.max_daily_loss_pct) / 100.0) * cap0:
                state.log(uid, "order_blocked",
                          f"{row.underlying}: daily loss ₹{-day_pnl:.0f} ≥ {cfg.max_daily_loss_pct}% of "
                          f"₹{cap0:.0f} — new entries halted for today")
                return

        # ── vehicle selection (directional mode; OFF ⇒ existing behavior) ──────
        use_futures = (cfg.directional_mode and cfg.vehicle == "futures"
                       and "futures" in cfg.enabled_vehicles)
        use_deep_itm = (cfg.directional_mode and cfg.vehicle == "deep_itm_options"
                        and "deep_itm_options" in cfg.enabled_vehicles)
        # Label reflects what is ACTUALLY traded — selecting a disabled vehicle
        # falls back to options, so it must not be labelled as that vehicle.
        vehicle_label = "futures" if use_futures else ("deep_itm_options" if use_deep_itm else "otm_options")
        # Signal direction (bull→long / bear→short). OPTIONS are ALWAYS long-premium
        # (we BUY a call for bull, a put for bear), so their stop stays a downside
        # premium stop regardless of bull/bear — only FUTURES carry the signal's
        # direction into a two-sided stop. (This is the P0 fix: previously a bear
        # PE-buy was mislabelled "short", inverting its GTT side + monitor exit.)
        signal_dir = "long" if getattr(row, "direction", "long") in ("long", "bull", 1) else "short"
        pos_direction = signal_dir if use_futures else "long"

        # ── optional entry-quality filters (None ⇒ off; never gate by default) ─
        if cfg.adx_min is not None and row.adx is not None and row.adx < float(cfg.adx_min):
            state.log(uid, "info", f"{row.underlying} entry skipped — ADX {row.adx:.1f} < {cfg.adx_min}")
            return
        if cfg.atr_pct_min is not None and row.atr_pct is not None and row.atr_pct < float(cfg.atr_pct_min):
            state.log(uid, "info", f"{row.underlying} entry skipped — ATR %ile {row.atr_pct:.0f} < {cfg.atr_pct_min}")
            return

        # ── resolve the tradable instrument for the chosen vehicle ─────────────
        # Defaults = the existing options leg (unchanged when directional_mode OFF).
        trade_symbol = args["option_symbol"]
        trade_exchange = args["exchange"]
        trade_token = int(args.get("token") or row.token or 0)
        trade_lot = int(args["lot_size"] or 0)
        entry_px = float(args.get("entry_premium") or 0.0)
        stop_px = float(args.get("stop_premium") or 0.0)
        # Only Navigator originations carry a target (from the AVWAP proposal). A
        # SuperTrend row has none by design — it exits on the trail or the red
        # counter — so this stays 0 and the GTT is a plain stop.
        target_px = 0.0
        # Delta-translation context stored on the position so every trailing update can
        # re-price the underlying ST level into a premium stop. Futures leave delta 0
        # (they trail in index points directly).
        pos_entry_spot = float(row.spot or 0.0)
        pos_delta = 0.0
        pos_strike = 0.0
        pos_expiry = ""

        if use_futures:
            fp = await _resolve_future(client, item, cfg.futures_expiry)
            if fp is None or not fp.tradingsymbol:
                state.log(uid, "order_blocked", f"{row.underlying}: futures contract unresolved — skipped")
                return
            trade_symbol, trade_exchange = fp.tradingsymbol, fp.exchange
            trade_token, trade_lot = int(fp.token or 0), int(fp.lot_size or 0)
            # The contract's OWN expiry. Left blank, `_square_off_expiring` skipped the
            # position entirely — and a single-stock future settles physically, so
            # riding one into expiry means taking delivery.
            pos_expiry = str(fp.expiry or "")
            # Futures risk is in INDEX POINTS, but the LEVELS belong to the futures
            # contract, not the underlying: it trades at spot plus basis.
            entry_px, stop_px = await _futures_entry_and_stop(
                client, exchange=trade_exchange, symbol=trade_symbol,
                spot=float(row.spot or 0.0), trail_level=float(row.stop_loss or 0.0))
            if entry_px <= 0:
                state.log(uid, "order_blocked",
                          f"{row.underlying} {trade_symbol}: no futures quote — cannot "
                          f"place the stop in the contract's own price domain, "
                          f"auto-entry skipped")
                return
        elif use_deep_itm:
            rt = await _resolve_deep_itm(client, item, row, cfg)
            if rt is None or not rt.symbol:
                state.log(uid, "order_blocked", f"{row.underlying}: deep-ITM strike unresolved — skipped")
                return
            trade_symbol, trade_exchange = rt.symbol, rt.exchange
            trade_token, trade_lot = rt.token, rt.lot_size
            entry_px, stop_px = rt.entry_px, rt.stop_px
            pos_delta, pos_strike, pos_expiry = rt.delta, rt.strike, rt.expiry
        else:
            # ── default OTM path ──────────────────────────────────────────────
            # Derivatives rows carry premium_spot/premium_sl → entry_px/stop_px are
            # already set from option_order_args. SPOT-source rows carry NO premium,
            # so their stop_px was 0 → the position was UNPROTECTED (no GTT, monitor
            # exit inert, sizing floored to 1 lot). D1 fix: fetch the leg LTP and derive
            # a delta-implied premium stop from the underlying ST trail (row.stop_loss).
            # Reuse the exact leg selected by option_order_args. Independently
            # resolving against grouped row.spot (zero) could select another strike.
            leg = next((l for l in row.legs if l.option_symbol == trade_symbol), None)
            if leg is None and row.legs:
                reference_spot = float(row.underlying_spot or row.spot or 0.0)
                leg = min(row.legs, key=lambda l: abs(l.strike - reference_spot))
            if leg is not None:
                pos_strike = float(leg.strike or 0.0)
                pos_expiry = str(leg.expiry or "")
                target_px = float(getattr(leg, "premium_target", 0.0) or 0.0)
                if entry_px <= 0 or stop_px <= 0:
                    # The translation is UNDERLYING → premium: it carries a SuperTrend
                    # level on the underlying's chart into a premium via delta. Two
                    # inputs therefore have to be in the underlying's domain, and on a
                    # derivatives-source row neither one is. There, `spot` holds the
                    # CONTRACT's premium (the ST ran on its own premium series, and
                    # place_cb sees the raw row before grouping zeroes it) and
                    # `stop_loss` is a premium level too. Feeding those in prices a
                    # ₹90 premium against a ₹3000 strike: the vol solve fails, delta
                    # collapses to the ±0.5 fallback, and the "stop" is an invented
                    # number that then becomes the broker's trigger.
                    #
                    # A derivatives row needs no translation — its leg already carries
                    # the premium stop. If that is missing (a legacy cached leg), there
                    # is nothing here to derive it from, and leaving stop_px at 0 lets
                    # the no-stop-no-trade guard refuse the entry rather than arm a
                    # fabricated one. `underlying_spot` matches what the board feeds
                    # `_stamp_leg_premium_stops`, so the two stay in step.
                    under_spot = float(getattr(row, "underlying_spot", 0.0) or 0.0)
                    if row.source == "derivatives":
                        state.log(uid, "order_blocked",
                                  f"{trade_symbol}: derivatives signal carries no premium "
                                  f"stop on its leg, and its levels are not in the "
                                  f"underlying's domain to derive one — auto-entry skipped")
                        return
                    if under_spot <= 0:
                        under_spot = float(row.spot or 0.0)
                    entry_px, stop_px, pos_delta = await _resolve_premium_stop(
                        client, exch=trade_exchange, symbol=trade_symbol,
                        strike=pos_strike, expiry=pos_expiry, option_type=row.option_type,
                        spot=under_spot, trail_level=float(row.stop_loss or under_spot))

        # ── risk sizing ───────────────────────────────────────────────────────
        # No longer byte-identical to the pre-risk-cap behaviour, in one direction:
        # a size that breaks the budget is now refused rather than floored to one lot.
        qty = int(args["size"])
        lots = 1
        capital = None
        # A blocked result means no tradable size honours ``risk_pct``. It must abort
        # the entry rather than fall through: ``qty`` still holds the un-risk-sized
        # default from ``args``, so treating "no size" as "keep the default" would
        # place the very order the cap just refused.
        allow_min_lot = bool(getattr(cfg, "allow_min_lot_over_risk", False))

        def _blocked(sized) -> bool:
            if not getattr(sized, "blocked", False):
                return False
            state.log(uid, "order_blocked",
                      f"{trade_symbol} entry skipped — {sized.reason}. Check broker inputs and risk budget.")
            return True

        if use_futures:
            if cfg.risk_sizing and entry_px > 0 and stop_px > 0 and trade_lot > 0:
                capital = await available_fo_capital(client)
                sized = sizing.size_future_position(
                    entry_price=entry_px, stop_price=stop_px, lot_size=trade_lot,
                    available_capital=capital, risk_pct=cfg.risk_pct, max_lots=cfg.max_lots,
                    allow_min_lot_over_risk=allow_min_lot)
                if _blocked(sized):
                    return
                if sized.qty > 0:
                    qty, lots = sized.qty, sized.lots
                    state.log(uid, "info", f"futures sizing → {sized.reason}")
            else:
                qty = trade_lot or qty
        elif use_deep_itm:
            qty = trade_lot or qty
            if cfg.risk_sizing and entry_px > 0 and stop_px > 0 and trade_lot > 0:
                capital = await available_fo_capital(client)
                sized = sizing.size_position(
                    entry_premium=entry_px, stop_premium=stop_px, lot_size=trade_lot,
                    available_capital=capital, risk_pct=cfg.risk_pct, max_lots=cfg.max_lots,
                    allow_min_lot_over_risk=allow_min_lot)
                if _blocked(sized):
                    return
                if sized.qty > 0:
                    qty, lots = sized.qty, sized.lots
                    state.log(uid, "info", f"{trade_symbol} sizing → {sized.reason}")
        elif cfg.risk_sizing and entry_px > 0 and stop_px > 0 and trade_lot > 0:
            # Default OTM sizing. Now keyed off the RESOLVED premium (entry_px/stop_px)
            # rather than args, so spot-source signals (whose args carry no premium) are
            # risk-sized too instead of always defaulting to a single lot.
            capital = await available_fo_capital(client)
            sized = sizing.size_position(
                entry_premium=entry_px,
                stop_premium=stop_px,
                lot_size=trade_lot,
                available_capital=capital,
                risk_pct=cfg.risk_pct,
                max_lots=cfg.max_lots,
                allow_min_lot_over_risk=allow_min_lot,
            )
            if _blocked(sized):
                return
            if sized.qty > 0:
                qty, lots = sized.qty, sized.lots
                state.log(uid, "info", f"{trade_symbol} sizing → {sized.reason}")

        # ── option-leg liquidity gate (opt-in): skip thin / wide-spread strikes ─
        if (not use_futures) and (cfg.max_spread_pct is not None or cfg.min_oi is not None):
            try:
                qkey = f"{trade_exchange}:{trade_symbol}"
                q = await client.get_quote([qkey])
                ok, why = _passes_liquidity((q or {}).get(qkey), cfg.max_spread_pct, cfg.min_oi)
            except Exception as _exc:  # noqa: BLE001
                log.debug("suppressed: %s", _exc)
                ok, why = False, "liquidity_quote_unavailable"
            if not ok:
                state.log(uid, "info", f"{row.underlying} {trade_symbol} entry skipped — {why}")
                return

        # ── portfolio drawdown breaker (opt-in; only ever downsizes or halts) ──
        if cfg.wire_risk_infra:
            cap = capital if capital is not None else await available_fo_capital(client)
            mult, brk = state.drawdown_multiplier(uid, cap)
            if mult <= 0.0:
                state.log(uid, "order_blocked",
                          f"{row.underlying}: circuit breaker [{brk}] — new entries halted")
                return
            if mult < 1.0:
                lots = max(1, int(lots * mult))
                qty = lots * trade_lot if trade_lot > 0 else qty
                state.log(uid, "info", f"{row.underlying}: breaker [{brk}] → size ×{mult:.1f} ({lots} lot)")

            # correlation penalty: downsize an entry correlated with an open position
            open_assets = [op.underlying for op in positions.open_positions(uid)
                           if op.underlying and op.underlying != row.underlying]
            cmult = state.correlation_penalty(uid, row.underlying, open_assets)
            if cmult < 1.0:
                lots = max(1, int(lots * cmult))
                qty = lots * trade_lot if trade_lot > 0 else qty
                state.log(uid, "info",
                          f"{row.underlying}: correlation ×{cmult:.1f} vs open {open_assets} ({lots} lot)")

        # ── live safety (Kite is INR; USD daily-loss breaker is crypto-only) ───
        trade_side = "BUY" if (not use_futures or signal_dir == "long") else "SELL"
        # ── no stop, no trade ─────────────────────────────────────────────────
        # An unresolved premium (strike has not traded yet today, quote rate-limited,
        # `_resolve_premium_stop` swallowed an error) leaves stop_px at 0. Everything
        # downstream then degrades silently: place_stop refuses a trigger of 0 so no GTT
        # is armed, positions.should_exit(0, ltp) is False on every tick so the monitor
        # is inert, and _retranslated_stop cannot re-derive a level from an entry premium
        # of 0 — so the stop stays 0 for the life of the position. The result is a real,
        # automatic, unattended BUY with no exit of any kind until the expiry square-off.
        # AUTO-EXEC IS UNATTENDED: a position we cannot protect must not be opened at all.
        # (The manual path deliberately does the opposite — the user asked for the fill,
        # and gets an explicit "UNPROTECTED" warning instead of a refusal.)
        from math import isfinite
        inputs_valid = all(isfinite(float(v)) and float(v) > 0
                           for v in (entry_px, stop_px, trade_lot, qty))
        stop_valid = stop_px < entry_px if trade_side == "BUY" else stop_px > entry_px
        if not inputs_valid or not stop_valid or qty % trade_lot != 0:
            state.log(uid, "order_blocked",
                      f"{row.underlying} {trade_symbol}: invalid execution inputs "
                      f"(price, protective stop or lot quantity) — auto-entry skipped")
            return

        idem = live_safety.make_idempotency_key(uid, trade_symbol, trade_side, qty, row.timestamp_ms)
        decision = live_safety.assert_safe_to_trade(
            positions=[], idempotency_key=idem, check_daily_loss=False)
        if not decision.allowed and decision.code != "duplicate_order":
            state.log(uid, "order_blocked", f"{row.underlying} {trade_symbol} blocked: {decision.reason}")
            return
        if live_safety.check_idempotency(idem):
            return  # this signal already executed

        # ── place order ───────────────────────────────────────────────────────
        if use_futures:
            try:
                from math import isfinite
                margin_rows = await client.order_margins([{
                    "exchange": trade_exchange, "tradingsymbol": trade_symbol,
                    "transaction_type": "BUY" if pos_direction == "long" else "SELL",
                    "variety": "regular", "product": "NRML", "order_type": "MARKET",
                    "quantity": qty, "price": 0, "trigger_price": 0,
                }])
                required_margin = float(margin_rows[0]["total"])
                available = await available_fo_capital(client)
                margin_ok = isfinite(required_margin) and required_margin > 0 and required_margin <= available
            except Exception:
                margin_ok = False
            if not margin_ok:
                state.log(uid, "order_blocked", f"{trade_symbol}: broker_margin_unavailable_or_insufficient")
                return
        else:
            # Fixed-lot sizing still requires actual funding. Unknown capital must
            # never become an implicit permission to buy one lot.
            available = await available_fo_capital(client)
            limit_buffer = 1.003  # highest price used by the stock-option order below
            if not isfinite(available) or available <= 0 or entry_px * qty * limit_buffer > available:
                state.log(uid, "order_blocked", f"{trade_symbol}: broker_capital_unavailable_or_insufficient")
                return
        session_reason = entry_data_block_reason(row, exchange=trade_exchange, buffer_minutes=blk)
        if session_reason:
            state.log(uid, "order_blocked", f"{trade_symbol}: {session_reason}")
            return
        intent = None
        if getattr(client, "_is_paper", True) is False:
            try:
                import hashlib, json
                generation = hashlib.sha256(
                    json.dumps(cfg.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()[:16]
                intent = order_journal.reserve(
                    uid=uid, account_id=str(getattr(client, "_account_id", "") or uid),
                    strategy_id="sterling-kite", generation_id=generation,
                    signal_id=f"{row.source}:{row.timestamp_ms}:{row.direction}",
                    exchange=trade_exchange, symbol=trade_symbol, side=trade_side, quantity=qty,
                    payload=dict(symbol=trade_symbol, exchange=trade_exchange, quantity=qty,
                        lot_size=trade_lot, entry_premium=entry_px, stop_premium=stop_px,
                        direction=pos_direction, signal_direction=signal_dir, vehicle=vehicle_label,
                        underlying=row.underlying, token=trade_token, guard_key=guard_key,
                        entry_spot=pos_entry_spot, entry_delta=pos_delta, strike=pos_strike,
                        expiry=pos_expiry, target_premium=target_px, exit_mode=cfg.exit_mode,
                        stop_mode=cfg.stop_mode))
                if intent.state != "RESERVED":
                    state.log(uid, "order_blocked", f"{trade_symbol}: durable intent already {intent.state}")
                    return
                intent = order_journal.transition(intent.intent_key, "SUBMITTING")
            except Exception as exc:
                state.log(uid, "order_blocked", f"{trade_symbol}: durable order reservation failed: {exc}")
                return
        try:
            if use_futures:
                side = "buy" if signal_dir == "long" else "sell"
                result = await client.place_order_future(
                    trade_symbol, side, qty, exchange=trade_exchange,
                    tag=(intent.tag if intent else idem))
            else:
                # `stop_px` — not args["stop_loss"] — is the authoritative premium stop:
                # it is what the protective GTT and the tick monitor below use, and for
                # a spot/navigator row it is the only one resolved into the premium
                # domain at all.
                is_stock = str(row.underlying).upper() not in {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "BANKEX", "MIDCPNIFTY"}
                if is_stock and entry_px > 0:
                    limit_px = round(entry_px * 1.003, 2)
                    order_type = "limit_order"
                else:
                    limit_px = None
                    order_type = "market_order"
                result = await client.place_order_option(
                    trade_symbol, "buy", qty, order_type=order_type, limit_price=limit_px,
                    exchange=trade_exchange,
                    stop_loss=(stop_px if stop_px > 0 else None),
                    tag=(intent.tag if intent else idem))
        except Exception as exc:  # noqa: BLE001
            if intent is not None:
                order_journal.transition(intent.intent_key, "UNKNOWN", error=type(exc).__name__)
            state.log(uid, "order_failed", f"{row.underlying} {trade_symbol}: {exc}")
            return
        oid = (result or {}).get("order_id", "")
        if not oid:
            if intent is not None:
                order_journal.transition(intent.intent_key, "UNKNOWN", error="missing_order_id")
            return
        if intent is not None:
            order_journal.transition(intent.intent_key, "SUBMITTED", order_id=str(oid))
        live_safety.record_idempotency(idem, oid)
        state.mark_auto_open(uid, guard_key)  # one-position guard (per slot)

        # ── register + arm (registry, tick subscription, broker stop/target) ───
        # Shared with the manual order path so a hand-placed entry is protected the
        # same way by the same code — this block used to be inline here, which is
        # how manual orders ended up with no stop at all.
        armed = await protection.arm_position(
            client, uid, symbol=trade_symbol, exchange=trade_exchange,
            token=trade_token, qty=qty, lot_size=trade_lot,
            entry_premium=entry_px, stop_premium=stop_px, order_id=oid,
            stop_mode=cfg.stop_mode, direction=pos_direction,
            # The red counter is defined against the SIGNAL, not the premium side.
            signal_direction=signal_dir, vehicle=vehicle_label,
            underlying=row.underlying, exit_mode=cfg.exit_mode, guard_key=guard_key,
            entry_spot=pos_entry_spot, entry_delta=pos_delta,
            strike=pos_strike, expiry=pos_expiry,
            target_premium=target_px)

        veh_note = f"{vehicle_label} " if cfg.directional_mode else ""
        dir_note = f" [{pos_direction}]" if cfg.directional_mode else ""
        # `armed.describe()`, not cfg.stop_mode: the config is what was ASKED for, and
        # printing it made the terminal claim "[both stop+monitor]" over a position that
        # got neither. This reports what is actually holding the position.
        state.log(uid, "order_placed",
                  f"{trade_side} {qty} ({lots} lot) {trade_symbol} @ market (#{oid}) "
                  f"[{veh_note}{armed.describe()}]{dir_note}")
        if not armed.protected:
            state.log(uid, "order_failed",
                      f"⚠ {trade_symbol} is OPEN and UNPROTECTED — {armed.describe()}. "
                      f"Set a stop in Zerodha now.")
    async def _serialized(row, item):
        async with _entry_locks.setdefault(uid, asyncio.Lock()):
            await _cb(row, item)
    return _serialized


def _is_expiring(expiry: str, today, within_days: int = 1) -> bool:
    """True if an option ``expiry`` ("YYYY-MM-DD…") is within ``within_days`` calendar
    days of ``today`` (or already past). ``within_days <= 0`` disables (always False);
    an empty/unparseable expiry is treated as not-expiring (futures carry none)."""
    if within_days <= 0 or not expiry:
        return False
    try:
        d = datetime.strptime(str(expiry)[:10], "%Y-%m-%d").date()
    except Exception:  # noqa: BLE001
        return False
    return (d - today).days <= within_days


#: A live market order fills in seconds. Past this, a position still PENDING means the
#: postback was lost, not that the order is slow.
_PENDING_GRACE_MS = 90_000


async def _reconcile_pending_positions(client, uid: str) -> None:
    """Resolve positions still PENDING long after their entry order went in.

    Fill confirmation arrives on the WS order postback. When that message is missed — a
    dropped socket, a server restart, a postback that landed while we were down — the
    position stays PENDING forever, and PENDING is invisible to everything that guards
    it: ``on_tick`` returns early, the trail updater skips it, and so do the time stop
    and the expiry square-off. The broker GTT is then the only protection, and under
    ``stop_mode="monitor"`` there is none at all.

    So stop waiting for the message and ask the order book. The reply is fed through the
    same ``on_order_update`` the postback would have hit, so fill / rejection / partial
    all take their normal path, including cancelling a GTT armed against an entry that
    never filled.
    """
    now_ms = int(datetime.now(_IST).timestamp() * 1000)
    for p in positions.open_positions(uid):
        exit_id = p.exit_order_id
        order_id = exit_id if exit_id and exit_id not in {"submitting", "unknown"} else p.order_id
        if exit_id in {"submitting", "unknown"}:
            # A unique persisted client tag identifies an accepted-but-timed-out
            # request without guessing from symbol/side or resubmitting it.
            if not p.exit_tag:
                continue  # legacy ambiguous request needs operator reconciliation
            try:
                book = await client.get_orders()
                matches = [o for o in book if isinstance(o, dict)
                           and o.get("tag") == p.exit_tag
                           and o.get("tradingsymbol") == p.symbol
                           and o.get("exchange", p.exchange) == p.exchange
                           and o.get("transaction_type") == ("SELL" if p.direction == "long" else "BUY")
                           and o.get("order_id")]
            except Exception:
                continue
            if len(matches) != 1:
                continue  # absence/ambiguity does not authorize a retry
            p.exit_order_id = str(matches[0]["order_id"])
            positions._persist(uid)
            await monitor.on_order_update(uid, matches[0], client=client)
            continue
        if not exit_id and (p.status != positions.PENDING or not order_id):
            continue
        if not exit_id and now_ms - int(p.opened_ms or 0) < _PENDING_GRACE_MS:
            continue
        try:
            hist = await client.get_order_history(order_id)
        except Exception as exc:  # noqa: BLE001
            log.debug("kite pending reconcile failed for %s/%s: %s", uid, p.symbol, exc)
            continue
        last = (hist or [])[-1] if hist else None
        if not isinstance(last, dict) or not str(last.get("status") or "").strip():
            continue
        # The order book omits the symbol on some venues; on_order_update matches on it.
        last = {**last, "tradingsymbol": last.get("tradingsymbol") or p.symbol,
                "order_id": last.get("order_id") or order_id}
        state.log(uid, "info",
                  f"{p.symbol}: fill postback never arrived — reconciling from the order "
                  f"book ({str(last.get('status')).lower()})")
        await monitor.on_order_update(uid, last, client=client)


def _broker_net_row(raw: dict, symbol: str) -> Optional[dict]:
    """The broker's net-positions row for ``symbol``, or None if absent.

    Kite keeps a squared-off position in ``net`` with ``quantity: 0`` for the rest of
    the day, so "row present, quantity 0" is the signal that it CLOSED — distinct
    from "row absent", which means it was never opened today (a carry-over, or a
    symbol we are simply wrong about).
    """
    for row in ((raw or {}).get("net") or []):
        if not isinstance(row, dict):
            continue
        if str(row.get("tradingsymbol", "")).strip().upper() == symbol.strip().upper():
            return row
    return None


async def _reconcile_closed_positions(client, uid: str) -> None:
    """Close registry positions the broker no longer holds.

    The mirror of ``_reconcile_pending_positions``, at the other end of the trade. An
    exit that fills at Zerodha — a GTT firing, a square-off in the Kite app, or the
    exit the monitor deliberately stood down for when it could not confirm a GTT
    cancel — reaches us only as an order postback. Miss that message and the registry
    believes we still hold a position that is gone, which is worse than cosmetic:

      * the board shows an open trade that does not exist;
      * the auto-open guard stays held, so that slot can never re-enter;
      * and every exit path still sees a live position — the expiry square-off, the
        time stop and the tick monitor will each place a SELL for something the
        account no longer owns, which is a NAKED SHORT.

    So ask the broker instead of waiting. This function only ever repairs bookkeeping;
    it never places an order. A quantity that shrank (a partial exit outside the
    engine) is corrected in place rather than closed, so what protection remains is
    sized to what is actually held.
    """
    open_now = [p for p in positions.open_positions(uid) if p.status == positions.OPEN]
    if not open_now:
        return
    try:
        raw = await client.get_positions_raw()
    except Exception as exc:  # noqa: BLE001
        log.debug("kite closed-position reconcile skipped for %s: %s", uid, exc)
        return
    if not isinstance(raw, dict) or not isinstance(raw.get("net"), list):
        return  # a malformed reply is not evidence that anything closed

    for p in open_now:
        row = _broker_net_row(raw, p.symbol)
        if row is None:
            continue  # never opened today at the broker — not evidence of a close
        try:
            held_qty = abs(int(row.get("quantity", 0) or 0))
        except (TypeError, ValueError):
            continue

        if held_qty == 0:
            # The broker's average sell price for the day is the honest exit price.
            # Without it we still close the position — the bookkeeping error is the
            # dangerous part — but we do NOT invent a realized PnL, because a wrong
            # number here feeds the daily-loss breaker.
            exit_px = 0.0
            try:
                exit_px = float(row.get("sell_price") or 0.0) if p.direction == "long" \
                    else float(row.get("buy_price") or 0.0)
            except (TypeError, ValueError):
                exit_px = 0.0
            if p.gtt_id:
                # Whatever did the selling, our trigger is now resting over nothing.
                outcome = await protective_stop.cancel_stop_result(client, p.gtt_id)
                state.log(uid, "info",
                          f"{p.symbol}: protective GTT #{p.gtt_id} {outcome} (position "
                          f"already closed at the broker)")
                positions.update_stop(uid, p.symbol, p.stop_premium, gtt_id=0)
            positions.close(
                uid, p.symbol,
                reason=(f"reconciled closed at broker @ ₹{exit_px:.2f}" if exit_px
                        else "reconciled closed at broker (exit price unknown)"))
            p.pnl_reconciliation_required = True
            positions._persist(uid)
            if p.guard_key:
                state.clear_auto_open(uid, p.guard_key)
            if p.token:
                try:
                    from app.services.exchanges.kite import ticker_manager
                    await ticker_manager.unsubscribe(uid, [p.token])
                except Exception as exc:  # noqa: BLE001
                    log.debug("suppressed: %s", exc)
            state.log(uid, "order_placed",
                      f"{p.symbol}: exit postback never arrived — the broker holds none, "
                      f"so the position is reconciled CLOSED"
                      + (f" @ ₹{exit_px:.2f}" if exit_px else
                         " (no exit price available, realized PnL not recorded)"))
        elif held_qty < p.qty:
            # Partially exited elsewhere. Shrink to what is held so the trail, the GTT
            # and any later exit are sized to the real position instead of overselling.
            was = p.qty
            positions.mark_filled(uid, p.symbol, p.fill_price, filled_qty=held_qty)
            state.log(uid, "info",
                      f"{p.symbol}: broker holds {held_qty} of {was} — position resized "
                      f"to match (partial exit outside the engine)")


async def _square_off_expiring(client, uid: str) -> None:
    """Market-exit auto-exec option positions inside the configured expiry window.

    Without this, a held weekly can settle at/after expiry with no managed exit (the
    ST signal exit + premium stop both assume a live, tradable contract). Runs only
    during market hours (an exit order off-hours is pointless) and reuses the tick
    monitor's exit path (correct side, GTT cancel, guard release, unsubscribe)."""
    cfg = state.get_config(uid)
    days = int(getattr(cfg, "expiry_square_off_days", 1) or 0)
    if days <= 0 or not is_market_open():
        return
    today = datetime.now(_IST).date()
    for p in positions.open_positions(uid):
        if p.status != positions.OPEN or not _is_expiring(p.expiry, today, days):
            continue
        try:
            key = f"{p.exchange}:{p.symbol}"
            q = await client.get_ltp([key])
            ltp = float((q or {}).get(key, {}).get("last_price") or p.stop_premium)
        except Exception:  # noqa: BLE001
            ltp = p.stop_premium
        # NOT a price-stop exit: no broker GTT will ever square off a position for
        # expiry, so this must place its own SELL even when the GTT cancel could not be
        # confirmed. On a physically-settled stock option, skipping it means taking
        # delivery — lakhs per lot.
        await monitor._exit_position(
            client, uid, p, ltp, price_stop_exit=False,
            reason=f"expiry square-off (T-{days}, exp {str(p.expiry)[:10]})")


async def _time_stop_positions(client, uid: str) -> None:
    """Square off auto-exec positions held beyond ``time_stop_bars`` 1H bars (opt-in).

    The exit-mechanics sweep's one robust, cross-lens finding: a hold cap curbs theta
    bleed on long-option positions. Off unless configured; market-hours gated; reuses
    the tick-monitor exit path (correct side, GTT cancel, guard release, unsubscribe)."""
    cfg = state.get_config(uid)
    bars = int(getattr(cfg, "time_stop_bars", 0) or 0)
    if bars <= 0 or not is_market_open():
        return
    now_ms = int(datetime.now(_IST).timestamp() * 1000)
    for p in positions.open_positions(uid):
        if p.status != positions.OPEN or not p.opened_ms:
            continue
        held = (now_ms - int(p.opened_ms)) // 3_600_000
        if held < bars:
            continue
        try:
            key = f"{p.exchange}:{p.symbol}"
            q = await client.get_ltp([key])
            ltp = float((q or {}).get(key, {}).get("last_price") or p.stop_premium)
        except Exception:  # noqa: BLE001
            ltp = p.stop_premium
        await monitor._exit_position(
            client, uid, p, ltp, price_stop_exit=False,
            reason=f"time stop ({held}≥{bars} bars held)")


def _new_trail_for_open(p, rows) -> Optional[float]:
    """Given an open position ``p``, find the matching fresh signal row and return
    the updated trail stop price.  Returns None if no match or no improvement.

    - Futures: trail is the underlying ST index level (``row.stop_loss``), and it
      tightens monotonically only for the correct direction:
        long  → trail moves UP   → take max(p.stop_premium, new)
        short → trail moves DOWN → take min(p.stop_premium, new)
    - OTM options (long premium): if the matching leg carries ``premium_sl`` (a
      derivatives-mode row), trail to it. Otherwise (a spot-mode leg — whose
      option_symbol drifts as the ATM re-picks) re-derive the premium stop from the
      fresh underlying ST level via the stored entry delta.
    - Deep-ITM: always re-translate the fresh underlying ST level via the stored
      delta (D3 — previously deep-ITM never trailed after entry).

    Re-translation trails INTO profit as the ST ratchets past the entry spot, and
    ratchets monotonically (only returns a value that tightens the current stop).
    """
    for row in rows:
        if row.underlying != p.underlying:
            continue
        if p.vehicle == "futures":
            new_sl = float(row.stop_loss or 0.0)
            if new_sl <= 0:
                return None
            # ``row.stop_loss`` is an UNDERLYING level; ``p.stop_premium`` lives in the
            # futures contract's price domain. Comparing them directly would compare
            # index points with futures points and, on a premium basis, freeze the
            # trail — the underlying level would never clear the futures stop.
            # The basis recorded at entry carries it across. It narrows toward expiry,
            # so holding the entry basis leaves a long's stop slightly tighter over
            # time, which is the safe direction to be wrong in.
            #
            # BOTH ends must be present. A row written before the futures entry was
            # priced in its own domain has ``entry_spot`` unset, and subtracting a zero
            # spot from a real entry price would add the WHOLE contract price as
            # "basis" — a stop tens of thousands of points away, i.e. no stop at all.
            # Missing either end means the position's stop is already in the same
            # domain as the row, so leave it alone.
            entry_px_rec = float(getattr(p, "entry_premium", 0.0) or 0.0)
            entry_spot_rec = float(getattr(p, "entry_spot", 0.0) or 0.0)
            if entry_px_rec > 0 and entry_spot_rec > 0:
                new_sl += entry_px_rec - entry_spot_rec
            if new_sl <= 0:
                return None
            if p.direction == "long":
                return new_sl if new_sl > p.stop_premium else None
            else:
                return new_sl if new_sl < p.stop_premium else None
        elif p.vehicle == "otm_options":
            for leg in row.legs:
                if leg.option_symbol == p.symbol and (leg.premium_sl or 0) > 0:
                    new_sl = float(leg.premium_sl)
                    # OTM option long: stop is a floor price — tighter = higher floor
                    return new_sl if new_sl > p.stop_premium else None
            # spot-mode leg (no premium_sl / symbol drifted): delta re-translation
            new_sl = _retranslated_stop(p, row)
            if new_sl is not None:
                return new_sl
        elif p.vehicle == "deep_itm_options":
            new_sl = _retranslated_stop(p, row)
            if new_sl is not None:
                return new_sl
    return None


def _retranslated_stop(p, row) -> Optional[float]:
    """Re-derive a long-option premium stop from the fresh underlying ST level using
    the stored entry delta, returning it only if it TIGHTENS the current stop (higher
    for a long premium). Needs the delta-translation context captured at entry."""
    if not (p.entry_delta and p.entry_spot and (row.stop_loss or 0) > 0):
        return None
    new_sl = premium_stop_from_move(
        entry_premium=p.entry_premium, delta=p.entry_delta,
        spot=p.entry_spot, trail_level=float(row.stop_loss))
    return new_sl if (new_sl > p.stop_premium and new_sl > 0) else None


def autoexec_preflight(uid: str) -> List[str]:
    """Reasons it is not safe to start opening positions automatically right now.

    Empty means clear. This is deliberately about the positions ALREADY held rather
    than about the strategy: turning auto-exec on tells an unattended process to add
    new real-money positions, and doing that while the engine cannot account for the
    ones it is already carrying is how a small problem becomes several.

    Registry-only — no broker calls — so it can run inline on the config write.
    """
    reasons: List[str] = []
    try:
        pending_intents = order_journal.unresolved(uid)
    except Exception:
        pending_intents = []
    if pending_intents:
        reasons.append("Unresolved durable order intents: " +
                       ", ".join(i.symbol for i in pending_intents[:5]))
    now = int(time.time() * 1000)
    live = [p for p in positions.open_positions(uid)
            if p.status in (positions.PENDING, positions.OPEN)]

    unresolved_pnl = [p.symbol for p in positions._load(uid).values() if p.pnl_reconciliation_required]
    if unresolved_pnl:
        reasons.append("Fill-level PnL reconciliation required: " + ", ".join(unresolved_pnl))
    uncertain_exits = [p.symbol for p in live if p.exit_order_id]
    if uncertain_exits:
        reasons.append("Exit confirmation pending: " + ", ".join(uncertain_exits))

    unprotected = [p.symbol for p in live
                   if p.status == positions.OPEN and float(p.stop_premium or 0.0) <= 0]
    if unprotected:
        reasons.append(
            f"{len(unprotected)} open position(s) have NO stop: "
            f"{', '.join(sorted(unprotected)[:5])}. Nothing will exit them on price.")

    stuck = [p.symbol for p in live
             if p.status == positions.PENDING
             and (now - int(p.opened_ms or 0)) > _PENDING_GRACE_MS]
    if stuck:
        reasons.append(
            f"{len(stuck)} position(s) stuck PENDING past the fill grace window: "
            f"{', '.join(sorted(stuck)[:5])}. We do not know whether they filled.")

    stale = [p.symbol for p in live
             if p.status == positions.OPEN
             and (now - int(getattr(p, "red_count_ms", 0) or p.opened_ms or 0)) > _RED_STALE_MS]
    if stale:
        reasons.append(
            f"{len(stale)} position(s) have a red counter that stopped updating: "
            f"{', '.join(sorted(stale)[:5])}. Their red-count exit is not being maintained.")

    return reasons


#: How long a red count may go unrefreshed before the user is told it has stopped
#: counting. Three scan intervals — one missed scan is noise, three is a pattern.
_RED_STALE_MS = int(SCAN_INTERVAL_S * 3 * 1000)

#: (uid, symbol) already warned about a frozen counter, cleared when it refreshes.
_red_stale_warned: set = set()


def _warn_if_red_count_stale(uid: str, p) -> None:
    """Say so when a position's red counter has stopped being refreshed.

    This is now the narrow case: the scan did not EVALUATE this position's instrument
    at all, so there is no reading to take — its underlying dropped out of the scan
    universe, or its candles came back empty. The signal merely ending no longer gets
    here; `_live_red_count` falls back to the scan's own regime reading for that.

    Holding the last value is still the safe choice (inventing a 0 would disarm the
    exit outright), but a counter that has silently stopped counting looks exactly like
    a working one on the board, and the user would go on believing the red-count exit
    is watching this position. It is not; only the price trail and the expiry
    square-off are.
    """
    key = (uid, p.symbol)
    stamped = int(getattr(p, "red_count_ms", 0) or 0)
    age = int(time.time() * 1000) - (stamped or int(getattr(p, "opened_ms", 0) or 0))
    if age < _RED_STALE_MS:
        _red_stale_warned.discard(key)
        return
    if key in _red_stale_warned:
        return
    _red_stale_warned.add(key)
    state.log(uid, "order_failed",
              f"⚠ {p.symbol}: no signal row for its direction in "
              f"{int(age / 60000)} min — the red-count exit is NOT being maintained "
              f"(showing a stale {int(getattr(p, 'current_red_count', 0) or 0)}). "
              f"The price trail and the expiry square-off still apply.")


def _live_red_count(p, rows, snap=None) -> Optional[int]:
    """This position's live red count from the fresh scan, or None when the scan cannot
    say — in which case the caller must leave the last known count alone.

    Two match steps, and the order matters:

    1. **The exact contract.** A derivatives-source row runs the SuperTrend on ONE
       contract's own premium series, and ``_compile_rows`` then groups every strike of
       an underlying under a single parent whose count belongs to whichever leg arrived
       first. Only the leg's own count describes this position.
    2. **The underlying**, matched on the SIGNAL direction — and never against a
       derivatives row. Every derivatives row carries ``direction="long"`` (long premium,
       whatever the strike) on the same underlying string as the spot rows, so matching
       one by underlying would hand a PE the bull row's count and market-sell a position
       whose own trend is intact. That is C5 again through a different door.

    None is returned for "no matching row" and for a row whose ``current_reds`` is None —
    a row hydrated from a cache written before the field existed. Reading that as 0 would
    say "nothing against us" and overwrite a real count of 2 or 3, disarming the red exit
    one tick before it fired.

    3. **The scan's own reading**, when no row matches at all. A row exists only where
       there was an entry TRANSITION, so the moment the signal that opened a position
       ends there is nothing left to match — and the count froze at its last value for
       the life of the position, which is exactly when the exit stopped working. The
       scan evaluates the regime for every instrument regardless, so ``snap`` carries
       that reading; steps 1 and 2 still come first because a row is the more specific
       answer (it is scoped to the exact signal, not just the instrument).
    """
    sym = str(getattr(p, "symbol", "") or "").upper()
    for r in rows:
        if getattr(r, "source", "") != "derivatives":
            continue
        for leg in (getattr(r, "legs", None) or []):
            if str(getattr(leg, "option_symbol", "") or "").upper() != sym:
                continue
            reds = getattr(leg, "current_reds", None)
            return None if reds is None else int(reds)
    want_dir = positions.signal_direction_of(p)
    for r in rows:
        if getattr(r, "source", "") == "derivatives":
            continue
        if r.underlying != getattr(p, "underlying", "") or r.direction != want_dir:
            continue
        reds = getattr(r, "current_reds", None)
        return None if reds is None else int(reds)
    if snap is not None:
        # The contract's own premium series first — it is what a derivatives-source
        # position's counter is defined against, and it is keyed by the exact symbol
        # we hold, so it cannot be confused with another leg on the same underlying.
        contract_reds = getattr(snap, "contract_reds", None) or {}
        held = contract_reds.get(str(getattr(p, "symbol", "") or ""))
        if held is not None:
            return int(held)
        by_dir = (getattr(snap, "underlying_reds", None) or {}).get(
            str(getattr(p, "underlying", "") or ""))
        if by_dir:
            # Same direction rule as step 2: a bear position's reds are the SHORT
            # count. Reading the long count here is C5 through a third door.
            reds = by_dir.get(want_dir)
            if reds is not None:
                return int(reds)
    return None


async def _update_open_position_trails(client, uid: str) -> None:
    """After each scan, push tightened trail stops to open positions.

    Covers the in-scan trail-update gap: the scanner computes fresh ST levels
    every 5 min but the original entry stop was never updated.  This pass:
      1. Reads the freshly-computed signal rows from the scanner snapshot.
      2. For each open position, computes whether the trail has tightened.
      3. Updates the in-memory stop (positions.update_stop).
      4. Moves the broker GTT if one is registered (protective_stop.move_stop).
      5. Re-subscribes the tick subscription (no-op if already subscribed).
    """
    open_pos = positions.open_positions(uid)
    if not open_pos:
        return
    snap = scanner.snapshot(uid)
    rows = snap.rows
    cfg = state.get_config(uid)
    for p in open_pos:
        if p.status != positions.OPEN:
            continue
        new_sl = _new_trail_for_open(p, rows)
        if new_sl is not None:
            old_sl = p.stop_premium
            positions.update_stop(uid, p.symbol, new_sl)
            state.log(uid, "info",
                      f"Trail updated {p.symbol} ({p.vehicle}): "
                      f"₹{old_sl:.2f} → ₹{new_sl:.2f}")
            if p.gtt_id and cfg.stop_mode in ("broker", "both"):
                try:
                    ltp_key = f"{p.exchange}:{p.symbol}"
                    ltp_data = await client.get_ltp([ltp_key])
                    ltp = float((ltp_data or {}).get(ltp_key, {}).get("last_price") or new_sl)
                except Exception:  # noqa: BLE001
                    ltp = new_sl
                moved = await protective_stop.move_stop(
                    client, trigger_id=p.gtt_id,
                    tradingsymbol=p.symbol, exchange=p.exchange,
                    qty=p.qty, trigger_premium=new_sl, last_price=ltp,
                    direction=p.direction,
                    # Carried on every move: a GTT modify rewrites the whole trigger,
                    # so omitting the target would quietly turn the OCO into a bare
                    # stop the first time the trail ratcheted.
                    target_premium=float(getattr(p, "target_premium", 0.0) or 0.0))
                if moved:
                    state.log(uid, "info",
                              f"Broker GTT #{p.gtt_id} trailed to ₹{new_sl:.2f} for {p.symbol}")
                else:
                    # The registry now says ₹new_sl while the broker still holds ₹old_sl.
                    # The tick monitor enforces the tighter one, so the position is not
                    # unguarded — but a silent failure here is how the two drift apart.
                    state.log(uid, "order_failed",
                              f"⚠ {p.symbol}: broker GTT #{p.gtt_id} could NOT be trailed to "
                              f"₹{new_sl:.2f} — the stop at Zerodha is still ₹{old_sl:.2f}; "
                              f"only the tick monitor is enforcing the tighter level")

        # ── red-count awareness (scan driven) ─────────────────────────────────
        # The monitor exits on current_red_count >= the exit_mode threshold, so what is
        # written here is a market SELL waiting to happen. Two things it must get right:
        #
        #   1. WHICH ROW. Match on the SIGNAL direction, not just the underlying. With
        #      scan_source="both" one underlying yields a bull row AND a bear row, and
        #      taking whichever came first (the old `break`) read the counter of the
        #      opposite trade.
        #   2. WHICH BAR. `current_reds` is computed at the LATEST bar. The old code read
        #      `row.alignment`, which is frozen at the ENTRY bar, and counted it against
        #      `p.direction` — "long" for every option, CE and PE alike. So a PE opened on
        #      an all-red bear signal scored 3-of-3 against itself immediately and was
        #      market-sold on the very next tick, with the SuperTrend still perfectly
        #      aligned in its favour.
        #
        # See `_live_red_count` for how the row is chosen. None → leave the last known
        # count alone: writing 0 would disarm the red exit for a position whose underlying
        # dropped out of this scan's universe entirely.
        #
        # The signal ending no longer freezes it. A row exists only where there was an
        # entry transition, so a position routinely outlives the row that opened it —
        # and the counter used to stop there, which is precisely when its exit stopped
        # working. The scan computes the regime for every instrument it evaluates
        # whether or not a row comes out, and `snap` carries that reading. What is left
        # is the genuinely unknowable case: an instrument this scan never looked at.
        current_reds = _live_red_count(p, rows, snap)
        if current_reds is None:
            _warn_if_red_count_stale(uid, p)
        if current_reds is not None:
            _red_stale_warned.discard((uid, p.symbol))
            positions.update_health(uid, p.symbol, current_reds, getattr(p, 'exit_mode', None))
            if current_reds > 0:
                mode = getattr(p, 'exit_mode', 'one_red')
                from app.engines.common.exit_counter import get_exit_threshold
                thresh = get_exit_threshold(mode)
                if current_reds >= thresh:
                    state.log(uid, "info", f"Red count hit {current_reds}/{thresh} for open {p.symbol} under {mode} — monitor will consider for exit")


async def scan_user(client, uid: str, *, interval_s: float = SCAN_INTERVAL_S) -> int:
    """Run one full scan for ``uid`` with ``client``. Returns the signal count."""
    cfg_model = state.get_config(uid)
    if not cfg_model.engine_enabled:
        return 0  # engine is OFF — preserve existing Kite behaviour, no scanning
    if state.status(uid).scanning:
        state.log(uid, "info", "Scan skipped — another scan is already in progress for this account.")
        return 0
    if state.clear_cooldown(uid):
        state.log(uid, "info", "Scan skipped — cancelled recently (60s cooldown).")
        return 0
    state.set_scanning(uid, True)
    state.log(uid, "scan_start", "Initiating 1H Sterling Kite Engine scan…")
    try:
        # Fetch the four exchange dumps concurrently (warm cache → instant; cold →
        # parallel downloads instead of ~1s of sequential round-trips).
        nfo, bfo, nse, bse = await asyncio.gather(
            client.search_instruments("", "NFO", limit=1_000_000),
            client.search_instruments("", "BFO", limit=1_000_000),
            client.search_instruments("", "NSE", limit=1_000_000),
            client.search_instruments("", "BSE", limit=1_000_000),
        )
        full_universe = build_universe(nfo_instruments=nfo, bfo_instruments=bfo, equities=nse + bse)
        source = cfg_model.scan_source
        # Granular selection applies to BOTH scans.
        selected = select_scan_universe(
            full_universe, indices=cfg_model.scan_indices,
            stocks=cfg_model.scan_stocks, all_stocks=cfg_model.scan_all_stocks,
            stock_contracts=getattr(cfg_model, "scan_stock_contracts", True))
        spot_universe = selected if source in ("spot", "both") else []
        deriv_universe = selected if source in ("derivatives", "both") else None
        # Confluence runs its own pass (underlying regime + per-leg premium confirmation),
        # so it uses neither the plain spot nor the plain deriv universe.
        confluence_universe = selected if source == "confluence" else None
        state.log(uid, "info", f"Scan plan: Scanning {len(selected)} instruments using '{source}' source.")

        # Auto-exec is universal — it fires on spot, derivatives, and confluence signals.
        place_cb = _make_place_cb(client, uid) if cfg_model.auto_execute else None
        await scanner.scan(
            uid=uid, client=client, universe=spot_universe, nfo_rows=nfo, bfo_rows=bfo,
            cfg=_ts_cfg(cfg_model), moneyness=cfg_model.strike_moneyness,
            expiry_types=cfg_model.scan_expiries,
            expiry_types_indices=cfg_model.scan_expiries_indices,
            expiry_types_stocks=cfg_model.scan_expiries_stocks,
            place_cb=place_cb,
            deriv_universe=deriv_universe, confluence_universe=confluence_universe,
            log_cb=lambda msg: state.log(uid, "info", msg),
            close_feed=((lambda name, close: state.feed_correlation(uid, name, close))
                        if cfg_model.wire_risk_infra else None))
        snap = scanner.snapshot(uid)
        # Sterling Value-Flow Navigator: CONFIRMATION ONLY here. Navigator
        # evaluates each of this engine's rows against its own fresh candle
        # fetch, then joins the resulting evidence back on synchronously from
        # cache. Both steps are a complete no-op — zero extra broker calls —
        # unless the user has explicitly enabled Navigator (disabled by
        # default for everyone).
        #
        # Structure Radar and Signal Origination are deliberately NOT run from
        # here (`include_origination=False`). Navigator is a peer engine with
        # its own scan loop (`services/navigator/runtime.py`), and that loop
        # owns origination: it fetches the candles, writes the shared decision
        # cache, and is the single place a Navigator-originated order can be
        # submitted. Running origination from both loops would double the
        # broker calls and, once calibration is promoted, could place the same
        # originated order twice.
        #
        # Navigator still either shares this engine's universe or resolves its
        # own. Re-selecting from the SAME already-built `full_universe` is a
        # pure in-memory filter — the four exchange dumps behind it are cached
        # for an hour, so a custom scope costs no extra broker round-trips.
        nav_universe = selected
        try:
            from app.services.navigator import service as navigator_service
            from app.services.navigator import config_store as navigator_config_store

            try:
                nav_cfg = navigator_config_store.get(
                    uid, default_underlyings=cfg_model.scan_indices).config
                if nav_cfg.scan_scope_mode == "custom":
                    nav_universe = select_scan_universe(
                        full_universe, indices=nav_cfg.scan_indices,
                        stocks=nav_cfg.scan_stocks, all_stocks=nav_cfg.scan_all_stocks,
                        stock_contracts=getattr(nav_cfg, "scan_stock_contracts", True))
                    if nav_cfg.enabled:
                        state.log(uid, "info",
                                  f"Navigator scan plan: {len(nav_universe)} instruments "
                                  f"using its own '{nav_cfg.scan_source}' source.")
            except Exception as exc:  # noqa: BLE001
                # A Navigator-side config hiccup must never change what the
                # base engine already decided to scan — fall back to shared.
                log.warning("Navigator scope resolve failed, using shared universe for %s: %s", uid, exc)

            snap.rows = await navigator_service.run_navigator_pass(
                client, uid, snap.rows, engine_config_payload=cfg_model.model_dump(mode="json"),
                default_underlyings=cfg_model.scan_indices,
                underlying_tokens={u.name: u.token for u in nav_universe},
                universe=nav_universe, nfo_rows=nfo, bfo_rows=bfo, moneyness=cfg_model.strike_moneyness,
                expiry_types=cfg_model.scan_expiries, expiry_types_indices=cfg_model.scan_expiries_indices,
                expiry_types_stocks=cfg_model.scan_expiries_stocks,
                include_origination=False,
            )
            snap.rows = navigator_service.attach_to_rows(uid, snap.rows, default_underlyings=cfg_model.scan_indices)
        except Exception as exc:  # noqa: BLE001
            log.warning("Navigator row-attach failed (non-fatal) for %s: %s", uid, exc)

        # No Navigator-originated auto-exec here. Originated rows are produced
        # and submitted by Navigator's own runtime loop, which additionally
        # re-checks `check_originated_execution_eligible` and the entry delay
        # per row. Keeping the single submission path there is what makes
        # "the same originated setup can only be ordered once" true by
        # construction rather than by timing luck.

        # The board now retains recently-ended setups too, but the "ready" count, badge
        # and return value should reflect only live (running/just-fired) signals.
        live = sum(1 for r in snap.rows if r.is_active or r.is_fresh)
        ended = len(snap.rows) - live
        d = snap.diag
        mode = "auto-exec ON" if place_cb else "advisory"
        parts = []
        if source in ("spot", "both"):
            # Index breakdown makes the 'no index signals' case visible: if indices
            # have 0 with data, the live index candle fetch is coming back empty.
            ix = f"indices {d.index_fired} fired, {d.index_evaluated}/{d.indices} with data"
            if d.indices and d.index_evaluated == 0:
                ix += " — index candle fetch returned nothing"
            parts.append(ix)
        if source in ("derivatives", "both"):
            # Per-stage so a zero-signal scan is self-explaining:
            #   resolved 0           → chains/strikes didn't resolve (config/expiry)
            #   resolved N, charts 0 → premium fetch returned nothing
            #   charts N, fired 0    → scanning fine; no fresh BUY this bar (base rate)
            dv = (f"deriv {d.deriv_fired} fired / {d.deriv_charts} charts "
                  f"(resolved {d.deriv_resolved}, bars {d.deriv_min_bars}–{d.deriv_max_bars})")
            if d.deriv_no_data:
                dv += f", {d.deriv_no_data} no-data"
            if d.deriv_no_spot:
                dv += f", {d.deriv_no_spot} no-spot (underlying price unavailable)"
            if d.deriv_resolved == 0 and d.deriv_no_spot == 0:
                dv += " — no option contracts resolved from chains"
            parts.append(dv)
        if source == "confluence":
            # Merged rows where the underlying AND a leg's own premium both fired.
            # deriv_charts here counts the candidate premiums checked for confirmation.
            cf = (f"confluence {d.confluence_fired} fired (spot+premium), "
                  f"{d.deriv_charts} premiums checked (bars {d.deriv_min_bars}–{d.deriv_max_bars})")
            parts.append(cf)
        if d.premium_missing:
            # Blank Entry/SL/TSL cells were previously indistinguishable from "no
            # signal". Name the count so a rate-limited option-history fetch is
            # visible instead of being read as a data-less contract.
            parts.append(
                f"⚠ {d.premium_missing}/{d.premium_ok + d.premium_missing} candidate legs "
                f"have no entry premium (option history empty and the signal is too old "
                f"to use today's LTP) — their Entry/SL/TSL show “—”"
            )
        # Maintenance of ALREADY-OPEN positions: ratchet their stops to the freshly
        # computed trail, square off anything near expiry, apply time stops.
        #
        # This used to be gated on `auto_execute`, which is the switch for OPENING new
        # positions. Turning AUTO off left every open position frozen on the stop it was
        # entered with — the trail stopped ratcheting, the broker GTT stopped moving, and
        # expiry/time stops stopped running — on exactly the positions the engine had
        # already committed real money to. Maintaining a position you hold is not the
        # same decision as opening a new one.
        # Reconcile FIRST: everything below skips a position that is still PENDING, so a
        # lost fill postback would otherwise hide it from all of them.
        await _reconcile_pending_positions(client, uid)
        # ...and at the other end of the trade: a position the broker no longer holds
        # must leave the registry before the trail, the expiry square-off or the time
        # stop below can act on it and SELL something we do not own.
        await _reconcile_closed_positions(client, uid)
        await _reconcile_orphan_stops(client, uid)
        await _update_open_position_trails(client, uid)
        await _square_off_expiring(client, uid)
        await _time_stop_positions(client, uid)
        board = f"{live} live signal(s)" + (f" + {ended} ended" if ended else "")
        state.log(uid, "scan_done",
                  f"Scan complete — {board} / {len(selected)} instruments "
                  f"[{source}] ({mode}) · {' · '.join(parts)}")
        state.mark_scan_done(uid, signal_count=live, next_in_s=interval_s)
        return live
    except Exception as exc:  # noqa: BLE001
        state.set_scanning(uid, False)
        state.log(uid, "error", f"Scan failed: {exc}")
        log.warning("kite-engine scan_user failed for %s: %s", uid, exc)
        raise


#: Orphan-stop warnings already issued, per (uid, trigger_id). The sweep runs every
#: scan; without this it would repeat the same alarm every few minutes until the user
#: acts, and an alert that cries every scan stops being read.
_orphan_warned: set = set()


async def _reconcile_orphan_stops(client, uid: str) -> None:
    """Find protective triggers resting at Zerodha with NO position behind them.

    Every live path that can orphan a trigger is now closed at the point it happens — a
    rejected entry cancels its GTT, an exit filled outside the engine cancels it, an
    unconfirmed cancel is chased after the sell. What none of them covers is a trigger
    that was ALREADY orphaned: the process restarted while one was armed, or a cancel
    failed and only logged "check Zerodha for a resting SELL" before the position left
    the registry. Nothing ever looked again. A resting SELL with nothing behind it opens
    a naked short the moment it fires.

    Reports, never cancels. We cannot tell our own abandoned trigger from a stop the
    user placed by hand in the Kite app, and silently deleting the second would remove
    the protection they were relying on — the same class of harm, pointed the other way.
    The broker's own net quantity is the discriminator that makes the warning safe: a
    trigger over a holding they actually have is legitimate whoever placed it, so only a
    trigger with NO underlying holding is reported.
    """
    try:
        triggers = await client.get_gtts()
        raw = await client.get_positions_raw()
    except Exception as exc:  # noqa: BLE001
        log.debug("kite orphan-stop sweep skipped for %s: %s", uid, exc)
        return
    if not isinstance(triggers, list) or not triggers:
        return
    held = {str(p.get("tradingsymbol", "")).strip().upper()
            for p in ((raw or {}).get("net") or [])
            if int(p.get("quantity", 0) or 0) != 0}
    live = {p.symbol.upper() for p in positions.open_positions(uid)
            if p.status in (positions.PENDING, positions.OPEN)}
    for t in triggers:
        if not isinstance(t, dict):
            continue
        if str(t.get("status") or "").strip().lower() != "active":
            continue  # only a RESTING trigger can still fire
        tid = int(t.get("id") or t.get("trigger_id") or 0)
        if not tid or (uid, tid) in _orphan_warned:
            continue
        cond = t.get("condition") if isinstance(t.get("condition"), dict) else {}
        sym = str(cond.get("tradingsymbol") or "").strip().upper()
        if not sym or sym in held or sym in live:
            continue
        _orphan_warned.add((uid, tid))
        state.log(uid, "order_failed",
                  f"⚠ ORPHANED STOP: GTT #{tid} is resting at Zerodha for {sym}, but you "
                  f"hold no {sym} and the engine has no open position in it. If it "
                  f"triggers it will SELL something you do not own — cancel it in Kite.")


def _broker_open_slots(positions_net: list) -> set:
    """Build the set of auto-open guard slots from raw broker positions.

    The guard key is the option tradingsymbol for derivatives slots and the
    underlying name for spot slots, so we emit BOTH for every position with a
    non-zero net quantity: the full tradingsymbol and its leading alpha prefix
    (e.g. ``NIFTY24JUN24000CE`` → ``NIFTY``). Reconcile intersects the persisted
    guard with this set, so an unmatched key is *cleared* (re-entry allowed) —
    we err toward emitting more candidate keys to avoid clearing a live slot.
    """
    slots: set = set()
    for p in positions_net or []:
        try:
            if int(p.get("quantity", 0) or 0) == 0:
                continue
            ts = str(p.get("tradingsymbol", "")).strip()
            if not ts:
                continue
            slots.add(ts)
            prefix = re.match(r"^[A-Z&-]+", ts.upper())
            if prefix:
                slots.add(prefix.group(0))
        except Exception:
            continue
    return slots


async def reconcile_user_auto_open(client, uid: str) -> None:
    """Sync ``uid``'s auto-open guard to the broker's real open positions.

    Called on startup before the scan loop: a server restart loses nothing
    (the guard is DB-persisted) but the persisted guard may be stale (positions
    closed / expired while we were down). Fetching ``GET /positions`` and
    intersecting prevents both a forever-blocked slot and a double-entry.
    """
    try:
        raw = await client.get_positions_raw()
        broker_slots = _broker_open_slots((raw or {}).get("net", []))
        before = state.auto_open_underlyings(uid)
        after = state.reconcile_auto_open(uid, broker_slots)
        dropped = before - after
        if dropped:
            state.log(uid, "info",
                      f"Auto-open guard reconciled against broker: cleared {len(dropped)} "
                      f"stale slot(s) ({', '.join(sorted(dropped))}); {len(after)} still open.")
    except Exception as exc:  # noqa: BLE001
        log.warning("kite-engine auto-open reconcile failed for %s: %s", uid, exc)
    # A restart is the likeliest way to miss an exit postback entirely, so repair the
    # POSITIONS too — not just the guard slots — before the first scan can act on them.
    await _reconcile_closed_positions(client, uid)


async def reconcile_all_auto_open() -> None:
    """Reconcile every connected account's auto-open guard on startup."""
    from app.services.exchanges.kite import accounts as kite_accounts
    from app.services.exchanges.kite.errors import KiteTokenError
    try:
        accts = [a for a in kite_accounts._load_from_db() if a.connected]
    except Exception as exc:  # noqa: BLE001
        log.warning("kite-engine auto-open reconcile: account load failed: %s", exc)
        return
    for acct in accts:
        try:
            client = await kite_accounts.acquire_client(acct)
            await reconcile_user_auto_open(client, acct.user_id)
        except KiteTokenError:
            continue
        except Exception:  # noqa: BLE001
            continue


async def _scan_all_connected_once() -> None:
    """One pass over every connected Kite account."""
    global _first_scan_done
    from app.services.exchanges.kite import accounts as kite_accounts
    from app.services.exchanges.kite.errors import KiteTokenError
    try:
        accts = [a for a in kite_accounts._load_from_db() if a.connected]
    except Exception as exc:  # noqa: BLE001
        log.warning("kite-engine auto-scan: account load failed: %s", exc)
        return
    scanned = False
    for acct in accts:
        # Warm cached client — keeps the instrument dump (1h TTL) hot across scan
        # cycles instead of re-downloading all four exchange dumps every interval.
        client = await kite_accounts.acquire_client(acct)
        try:
            try:
                await client.get_profile()
            except KiteTokenError:
                kite_accounts.clear_session(acct.user_id, acct.id)
                await kite_accounts.release_client(acct.id)
                state.log(acct.user_id, "info",
                          "Kite session expired — auto-disconnected; re-login required.")
                continue
            await scan_user(client, acct.user_id)
            scanned = True
        except Exception as _exc:
            log.debug("suppressed: %s", _exc)
    if scanned:
        _first_scan_done = True


async def auto_scan_loop(interval_s: float = SCAN_INTERVAL_S) -> None:
    """Background task: scan all connected accounts every ``interval_s`` seconds.
    Runs one initial scan regardless of market hours (to seed the DB cache), then
    gates subsequent iterations on market-open only."""
    global _auto_running
    _auto_running = True
    log.info("kite-engine auto-scan loop started (every %ss)", interval_s)
    try:
        while True:
            try:
                if _first_scan_done and not is_market_open():
                    await asyncio.sleep(30)  # check market hours every 30s when closed
                    continue
                await _scan_all_connected_once()
            except Exception as exc:  # noqa: BLE001
                log.warning("kite-engine auto-scan iteration error: %s", exc)
            await asyncio.sleep(interval_s)
    except asyncio.CancelledError:
        log.info("kite-engine auto-scan loop stopped")
        raise
    finally:
        _auto_running = False
