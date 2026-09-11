"""The live path for the intraday pack: entry, protection, trailing and exit.

Nothing here decides whether a setup is valid — that is
``app.engines.intraday`` and it runs identically in a replay. This module is
everything that only exists because real money is involved: sizing against
capital, a stop resting at the broker, a tick loop that can exit intrabar, and
a reconcile that assumes the broker is right and we are wrong.

Two rules shape the whole file:

* **The broker is the truth.** A position we think we hold and Zerodha does not
  is closed, not re-protected.
* **An exit claims the position first.** ``pos.exiting`` is taken before any
  order goes out, so the tick loop, the session-end sweep and a manual
  square-off cannot each sell the same lots.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.core.logging import get_logger
from app.engines.intraday import IntradayConfig, STRATEGY_ID
from app.engines.intraday.position import (ContractRef, IntradayPosition, OPEN,
                                           PENDING, align_to_tick, premium_stop_for,
                                           q2, should_exit, should_scale_out,
                                           spot_trail, update_trail)
from app.services import intraday_positions as store
from app.services.intraday import get_config, ist_today, status as scan_status

log = get_logger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))
_OWNER = STRATEGY_ID
_locks: dict[str, asyncio.Lock] = {}
_notes: dict[str, list[dict]] = {}
_subscribed: dict[str, set[int]] = {}

__all__ = ["arm", "adopt", "on_ticks", "square_off_all", "exit_one", "reconcile",
           "scan_all_once", "auto_scan_loop", "notes", "realised_pnl_today",
           "positions_view", "is_paper", "auto_execute"]


def _lock_for(uid: str) -> asyncio.Lock:
    lock = _locks.get(uid)
    if lock is None:
        lock = _locks[uid] = asyncio.Lock()
    return lock


def _now_ms() -> int:
    return int(datetime.now(_IST).timestamp() * 1000)


def _today() -> str:
    return ist_today().isoformat()


def note(uid: str, kind: str, message: str) -> None:
    rows = _notes.setdefault(uid, [])
    rows.append({"kind": kind, "message": message, "at_ms": _now_ms()})
    del rows[:-40]


def notes(uid: str) -> list[dict]:
    return list(_notes.get(uid) or [])[-20:]


def _is_market_open(cfg: IntradayConfig) -> bool:
    now = datetime.now(_IST)
    if now.weekday() >= 5:
        return False
    return cfg.session_start <= now.strftime("%H:%M") <= cfg.session_end


def _entries_allowed(cfg: IntradayConfig) -> bool:
    """Entries stop before the session does. Exits never do."""
    now = datetime.now(_IST).strftime("%H:%M")
    return _is_market_open(cfg) and now < cfg.no_entry_after


async def _client(uid: str):
    from app.services.exchanges.kite import accounts
    acct = accounts.get_active(uid)
    if not acct:
        raise RuntimeError("No active Kite account")
    return await accounts.acquire_client(acct)


def is_paper(uid: str) -> bool:
    """Paper or live is the ACCOUNT's setting, never this engine's own copy.

    Two switches for one setting is how an engine ends up believing it is
    papering while the broker is not.
    """
    try:
        from app.services.exchanges.kite import accounts
        acct = accounts.get_active(uid)
        return bool(getattr(acct, "is_paper", True)) if acct else True
    except Exception:
        return True


def auto_execute(uid: str) -> bool:
    """Auto entry needs BOTH the account-wide switch and this engine's own.

    ANDed, never ORed: the shared switch is the operator's master control and
    this engine's is a statement that these three strategies specifically have
    earned it. Neither alone is enough.
    """
    try:
        from app.services.kite_engine import state as engine_state
        shared = bool(getattr(engine_state.get_config(uid), "auto_execute", False))
    except Exception:
        shared = False
    return shared and bool(get_config(uid).auto_execute)


def _replay_owns_the_board() -> bool:
    try:
        from app.services.simulation import simulation_runner
        return bool(simulation_runner.has_session_view)
    except Exception:
        return False


def _safety(uid: str, idempotency_key: Optional[str]) -> tuple[bool, str]:
    """The account-wide gate: kill switch, daily loss, duplicate order.

    Fails CLOSED. A safety check that cannot run is not a pass — and ``uid`` is
    passed explicitly because the daily-loss breaker reads per-account P&L and
    silently returns zero without it, which is a breaker that never trips.
    """
    try:
        from app.services.live_safety import assert_safe_to_trade
        decision = assert_safe_to_trade([], idempotency_key,
                                        check_daily_loss=True, uid=uid)
        return bool(decision.allowed), str(decision.reason or "")
    except Exception as exc:
        log.error("intraday: safety check failed closed for %s: %s", uid, exc)
        return False, f"safety check unavailable: {exc}"


def realised_pnl_today(uid: str) -> float:
    return round(store.load_record(uid).roll(_today()).realised_inr, 2)


# ------------------------------------------------------------------- gating

def entry_blocker(uid: str, cfg: IntradayConfig, symbol: str) -> Optional[str]:
    """Every reason this engine will not open another position, in one place.

    Collected here rather than scattered through ``arm`` so the board can show
    the same answer BEFORE a click as the click itself would give.
    """
    if not cfg.enabled:
        return "the intraday engine is switched off"
    if _replay_owns_the_board():
        return "replay is driving this board — live entry is off"
    if not _entries_allowed(cfg):
        return f"outside the entry window ({cfg.session_start}–{cfg.no_entry_after})"
    open_now = store.open_positions(uid)
    if len(open_now) >= cfg.max_concurrent_positions:
        return (f"{len(open_now)} positions open, cap is "
                f"{cfg.max_concurrent_positions}")
    if any(p.contract.tradingsymbol == symbol for p in open_now):
        return f"already holding {symbol}"
    rec = store.load_record(uid).roll(_today())
    if rec.trades >= cfg.max_new_trades_per_day:
        return f"{rec.trades} trades today, cap is {cfg.max_new_trades_per_day}"
    if cfg.daily_loss_limit_inr > 0 and rec.realised_inr <= -abs(cfg.daily_loss_limit_inr):
        return (f"this engine is down Rs {abs(rec.realised_inr):,.0f} today, "
                f"limit Rs {cfg.daily_loss_limit_inr:,.0f}")
    return None


def _size(cfg: IntradayConfig, *, premium: float, stop: float,
          lot_size: int, uid: str) -> tuple[int, int, str]:
    """Lots and quantity for one option BUY. Returns ``(lots, qty, why)``.

    ``lots`` of 0 means do not trade, and ``why`` says which cap refused —
    never a silent floor to one lot, which turns ``risk_per_trade_pct`` into a
    suggestion.
    """
    rec = store.load_record(uid).roll(_today())
    cap = cfg.max_lots
    if cfg.descale_after_losses and rec.consecutive_losses >= cfg.descale_after_losses:
        cap = max(1, cap // 2)
    if cfg.sizing_mode == "LOTS":
        lots = min(cfg.lots, cap)
        return lots, lots * max(1, lot_size), (
            f"{lots} lot(s) fixed" + (" (de-scaled after losses)" if cap < cfg.max_lots else ""))
    from app.services.kite_engine.sizing import size_position
    res = size_position(entry_premium=premium, stop_premium=stop, lot_size=lot_size,
                        available_capital=cfg.capital_inr,
                        risk_pct=cfg.risk_per_trade_pct, max_lots=cap,
                        allow_min_lot_over_risk=cfg.allow_min_lot_over_risk)
    if getattr(res, "blocked", False):
        return 0, 0, str(res.reason)
    # ``lots`` is exchange lots; ``qty`` is units. Every value, risk and
    # P&L figure in this engine uses qty — confusing the two is a known
    # lot-size-multiple error in this repo.
    return int(res.lots), int(res.qty), str(res.reason)


# --------------------------------------------------------------- protection

async def _place_protection(uid: str, client, pos: IntradayPosition,
                            cfg: IntradayConfig) -> int:
    """Rest the stop (and target) at the broker.

    ``monitor`` mode deliberately places nothing: an operator who chose it has
    accepted that protection lives only while this process does, and the
    settings page says so. Everything else gets a GTT, and a GTT that fails to
    place is a warning on the board, not a silent monitor-only position.
    """
    if cfg.stop_mode == "monitor" or pos.stop <= 0 or pos.quantity <= 0:
        return 0
    try:
        from app.services.kite_engine.protective_stop import place_stop
        gtt_id = await place_stop(
            client, tradingsymbol=pos.contract.tradingsymbol,
            exchange=pos.contract.exchange, qty=int(pos.quantity),
            trigger_premium=float(pos.stop), last_price=float(pos.effective_entry),
            # A BOUGHT option is long premium whichever way the thesis points.
            # Reading the thesis here is the bug that sold every PE at entry.
            direction="long", target_premium=float(pos.target or 0.0))
    except Exception as exc:                                       # noqa: BLE001
        gtt_id = None
        log.warning("intraday: protective GTT failed for %s: %s",
                    pos.contract.tradingsymbol, exc)
    if gtt_id:
        pos.gtt_id = int(gtt_id)
        pos.gtt_at = float(pos.stop)
        store.put(uid, pos)
        return int(gtt_id)
    note(uid, "warning", f"{pos.contract.tradingsymbol}: no broker stop — "
                         "protection is this process only")
    return 0


async def _sync_protection(uid: str, pos: IntradayPosition, cfg: IntradayConfig) -> None:
    """Move the resting stop to where the trail has got to.

    Skipped when it has not actually moved: a GTT modify per tick is both a rate
    limit and a way to lose the resting order to a rejected amend.
    """
    if not pos.gtt_id or pos.stop <= 0:
        return
    if abs(pos.stop - float(pos.gtt_at or 0.0)) < 0.05:
        return
    try:
        from app.services.kite_engine.protective_stop import move_stop
        client = await _client(uid)
        await move_stop(client, trigger_id=int(pos.gtt_id),
                        tradingsymbol=pos.contract.tradingsymbol,
                        exchange=pos.contract.exchange, qty=int(pos.quantity),
                        trigger_premium=float(pos.stop),
                        last_price=float(pos.effective_entry), direction="long",
                        target_premium=float(pos.target or 0.0))
        pos.gtt_at = float(pos.stop)
        store.put(uid, pos)
    except Exception as exc:                                       # noqa: BLE001
        log.warning("intraday: could not move the broker stop for %s: %s",
                    pos.contract.tradingsymbol, exc)


async def _cancel_protection(uid: str, client, pos: IntradayPosition) -> None:
    if not pos.gtt_id:
        return
    try:
        from app.services.kite_engine.protective_stop import cancel_stop
        await cancel_stop(client, int(pos.gtt_id))
    except Exception as exc:                                       # noqa: BLE001
        log.warning("intraday: could not cancel the broker stop for %s: %s",
                    pos.contract.tradingsymbol, exc)
    pos.gtt_id = 0
    pos.gtt_at = 0.0
    store.put(uid, pos)


# ------------------------------------------------------------------ ticker

async def _subscribe(uid: str) -> None:
    """Hold ticks for everything this engine is watching or holding.

    The subscription set is shared per account and refcounted by owner, so
    releasing ours never takes another engine's ticks away.
    """
    st = scan_status(uid)
    tokens = {int((r.get("contract") or {}).get("token") or 0)
              for r in st.signals.values()}
    for p in store.open_positions(uid):
        tokens.add(int(p.contract.token or 0))
        # The UNDERLYING as well. Every one of these three states its stop in
        # the underlying's points, and without its ticks that stop can never
        # fire — the position would be protected only by its premium stop,
        # which is a different rule than the one on the board.
        tokens.add(int(p.underlying_token or 0))
    tokens.discard(0)
    held = _subscribed.setdefault(uid, set())
    new, stale = tokens - held, held - tokens
    if new:
        try:
            from app.services.exchanges.kite import constants as K
            from app.services.exchanges.kite import ticker_manager
            await ticker_manager.subscribe(uid, sorted(new), K.MODE_FULL, owner=_OWNER)
            held |= new
        except Exception as exc:                                   # noqa: BLE001
            log.warning("intraday subscribe failed for %s: %s", uid, exc)
    if stale:
        try:
            from app.services.exchanges.kite import ticker_manager
            await ticker_manager.release(uid, sorted(stale), owner=_OWNER)
        except Exception as exc:                                   # noqa: BLE001
            log.debug("intraday release failed for %s: %s", uid, exc)
        held -= stale


async def release_subscriptions(uid: str) -> None:
    held = _subscribed.get(uid) or set()
    if not held:
        return
    try:
        from app.services.exchanges.kite import ticker_manager
        await ticker_manager.release(uid, sorted(held), owner=_OWNER)
    except Exception as exc:                                       # noqa: BLE001
        log.debug("intraday release failed for %s: %s", uid, exc)
    held.clear()


# --------------------------------------------------------------------- entry

async def arm(uid: str, signal_id: str) -> dict:
    """Enter one armed signal by id."""
    cfg = get_config(uid)
    async with _lock_for(uid):
        st = scan_status(uid)
        row = st.signals.get(signal_id)
        if row is None:
            return {"ok": False, "message": f"no armed signal {signal_id} in this scan"}
        contract = row.get("contract") or {}
        sig = row.get("signal") or {}
        symbol = str(contract.get("symbol") or "")
        if not symbol:
            return {"ok": False, "message": "this signal has no resolved contract"}
        blocker = entry_blocker(uid, cfg, symbol)
        if blocker:
            note(uid, "blocked", f"entry refused for {symbol}: {blocker}")
            return {"ok": False, "message": blocker}

        client = await _client(uid)
        quote = await _fresh_quote(client, contract, cfg,
                                   spot=float(sig.get("entry") or 0.0))
        if quote.get("blockers"):
            why = quote["blockers"][0]
            note(uid, "blocked", f"entry refused for {symbol}: {why}")
            return {"ok": False, "message": why}
        premium = float(quote["premium"])

        stop = premium_stop_for(premium, float(sig.get("entry") or 0.0),
                                float(sig.get("stop") or 0.0), cfg,
                                delta=quote.get("delta"))
        if stop >= premium:
            return {"ok": False, "message": "the premium stop is not below the premium"}
        lot_size = int(contract.get("lot_size") or 0) or 1
        lots, qty, why = _size(cfg, premium=premium, stop=stop,
                               lot_size=lot_size, uid=uid)
        if lots <= 0 or qty <= 0:
            note(uid, "blocked", f"entry refused for {symbol}: {why}")
            return {"ok": False, "message": why}

        idem = f"{STRATEGY_ID}:{uid}:{signal_id}"
        ok, safety_why = _safety(uid, idem)
        if not ok:
            note(uid, "blocked", f"entry refused for {symbol}: {safety_why}")
            return {"ok": False, "message": safety_why}

        tick = float(contract.get("tick_size") or 0.05) or 0.05
        limit = align_to_tick(premium, tick, side="buy")
        target, target2 = _premium_targets(premium, stop, sig, cfg)
        pos = IntradayPosition(
            strategy=str(row.get("strategy") or ""), signal_id=signal_id,
            underlying=str(row.get("symbol") or ""),
            contract=ContractRef(
                tradingsymbol=symbol, exchange=str(contract.get("exchange") or "NFO"),
                token=int(contract.get("token") or 0),
                option_type=str(contract.get("option_type") or "CE"),
                strike=float(contract.get("strike") or 0.0),
                expiry=str(contract.get("expiry") or ""),
                lot_size=lot_size, tick_size=tick),
            thesis="BEARISH" if str(sig.get("direction")) == "BEARISH" else "BULLISH",
            spot_entry=float(sig.get("entry") or 0.0),
            spot_stop=float(sig.get("stop") or 0.0),
            spot_target=float(sig.get("target") or 0.0),
            spot_target2=sig.get("target2"),
            spot_risk=float(sig.get("risk") or 0.0),
            underlying_token=int(row.get("underlying_token") or 0),
            entry=limit, stop=stop, initial_stop=stop,
            target=target, target2=target2,
            quantity=qty, lots=lots, peak=limit,
            stop_mode=cfg.stop_mode, idempotency_key=idem,
            entered_ms=_now_ms(), entry_day=_today(), status=PENDING)

        try:
            res = await client.place_order(
                f"{pos.contract.exchange}:{symbol}", "buy", float(qty),
                order_type="limit_order", limit_price=float(limit),
                tag=STRATEGY_ID[:20])
        except Exception as exc:                                   # noqa: BLE001
            note(uid, "error", f"entry rejected for {symbol}: {exc}")
            return {"ok": False, "message": str(exc)}
        order_id = str((res or {}).get("order_id")
                       or ((res or {}).get("data") or {}).get("order_id") or "")
        if not order_id:
            note(uid, "error", f"{symbol}: broker returned no order id")
            return {"ok": False, "message": "broker returned no order id"}
        pos.order_id = order_id
        store.put(uid, pos)

        status, avg = await _confirm_fill(client, order_id)
        if status in ("REJECTED", "CANCELLED"):
            store.mark_rejected(uid, symbol, status)
            note(uid, "error", f"{symbol} {status.lower()} at the broker")
            return {"ok": False, "message": f"order {status.lower()}"}
        filled = store.mark_filled(uid, symbol, avg) or pos
        gtt_id = await _place_protection(uid, client, filled, cfg)
        st.signals.pop(signal_id, None)
        await _subscribe(uid)
        note(uid, "entry", f"bought {qty} {symbol} @ {filled.effective_entry} "
                           f"(stop {filled.stop}, target {filled.target}) — {why}")
        return {"ok": True, "order_id": order_id, "symbol": symbol,
                "quantity": qty, "lots": lots, "entry": filled.effective_entry,
                "stop": filled.stop, "target": filled.target, "gtt_id": gtt_id,
                "paper": is_paper(uid), "sizing": why}


def _premium_targets(premium: float, stop: float, sig: dict,
                     cfg: IntradayConfig) -> tuple[float, float]:
    """The premium objectives that match the strategy's own reward-to-risk.

    Derived from R rather than from the spot target scaled by a delta: delta
    changes as the trade works, so a delta-scaled target drifts away from the
    ratio the strategy was tested at. Holding the RATIO fixed is the honest
    translation.

    The second value is the runner's, and is 0 for the two strategies that have
    one objective. A row is only allowed to advertise a runner when one exists.
    """
    rr = float(sig.get("rr") or 0.0) or 2.0
    risk = max(0.0, premium - stop)
    if risk <= 0:
        return 0.0, 0.0
    first = q2(premium + rr * risk)
    spot_entry = float(sig.get("entry") or 0.0)
    spot_risk = float(sig.get("risk") or 0.0)
    t2 = sig.get("target2")
    if t2 is None or spot_risk <= 0 or spot_entry <= 0:
        return first, 0.0
    rr2 = abs(float(t2) - spot_entry) / spot_risk
    return first, q2(premium + rr2 * risk)


async def _fresh_quote(client, contract: dict, cfg: IntradayConfig,
                       spot: float = 0.0) -> dict:
    from app.services.intraday import _quote_for
    q = await _quote_for(client, contract, cfg, spot=spot)
    if q is None:
        return {"premium": 0.0, "blockers": ["no quote for this contract"]}
    return q


async def _confirm_fill(client, order_id: str) -> tuple[str, float]:
    if str(order_id).startswith("PAPER-"):
        return "COMPLETE", 0.0
    try:
        history = await client.get_order_history(order_id)
    except Exception as exc:                                       # noqa: BLE001
        log.warning("intraday: order status unavailable for %s: %s", order_id, exc)
        return "UNKNOWN", 0.0
    if not history:
        return "UNKNOWN", 0.0
    last = history[-1] if isinstance(history, list) else history
    status = str((last or {}).get("status") or "").upper()
    try:
        avg = float((last or {}).get("average_price") or 0.0)
    except (TypeError, ValueError):
        avg = 0.0
    return status, avg


async def adopt(uid: str, symbol: str, quantity: int, entry_price: float) -> dict:
    """Take responsibility for a position this engine did not open.

    Protection is placed as part of adopting. A hand-placed position the engine
    is managing but has not protected is the worst of both worlds.
    """
    if _replay_owns_the_board():
        return {"ok": False, "message": "replay is driving this board — adopt is off"}
    cfg = get_config(uid)
    async with _lock_for(uid):
        existing = store.get(uid, symbol)
        if existing and existing.is_open:
            return {"ok": False, "message": f"already managing {symbol}"}
        client = await _client(uid)
        meta = await _contract_meta(client, symbol)
        if meta is None:
            return {"ok": False, "message": f"{symbol} is not a listed NFO/BFO contract"}
        stop = q2(float(entry_price) * (1.0 - cfg.premium_stop_pct / 100.0))
        pos = IntradayPosition(
            strategy="adopted", signal_id=f"adopt:{symbol}:{_today()}",
            underlying=meta["underlying"], contract=ContractRef(**meta["contract"]),
            thesis="BULLISH" if meta["contract"]["option_type"] == "CE" else "BEARISH",
            entry=q2(entry_price), fill_price=q2(entry_price),
            stop=stop, initial_stop=stop, target=0.0,
            quantity=int(quantity),
            lots=max(1, int(quantity) // max(1, meta["contract"]["lot_size"])),
            peak=q2(entry_price), stop_mode=cfg.stop_mode,
            entered_ms=_now_ms(), entry_day=_today(), status=OPEN)
        store.put(uid, pos)
        gtt_id = await _place_protection(uid, client, pos, cfg)
        await _subscribe(uid)
        note(uid, "adopt", f"adopted {quantity} {symbol} @ {entry_price}, stop {stop}")
        return {"ok": True, "symbol": symbol, "stop": stop, "gtt_id": gtt_id}


async def _contract_meta(client, symbol: str) -> Optional[dict]:
    for exchange in ("NFO", "BFO"):
        try:
            rows = await client.search_instruments(symbol, exchange, limit=5) or []
        except Exception:
            rows = []
        for r in rows:
            if str(r.get("tradingsymbol") or "") != symbol:
                continue
            return {
                "underlying": str(r.get("name") or symbol),
                "contract": {
                    "tradingsymbol": symbol, "exchange": exchange,
                    "token": int(r.get("instrument_token") or 0),
                    "option_type": str(r.get("instrument_type") or "CE"),
                    "strike": float(r.get("strike") or 0.0),
                    "expiry": str(r.get("expiry") or "")[:10],
                    "lot_size": int(r.get("lot_size") or 1) or 1,
                    "tick_size": float(r.get("tick_size") or 0.05) or 0.05,
                },
            }
    return None


# ---------------------------------------------------------------------- exit

async def _exit_position(uid: str, client, pos: IntradayPosition, reason: str,
                         premium: float, cfg: IntradayConfig) -> bool:
    """Sell one position. The claim is taken BEFORE anything is sent.

    Every exit path in this engine goes through here for that reason: the tick
    loop, the session-end sweep and a manual square-off can all decide at the
    same moment, and two sells for one position is a naked short.
    """
    if pos.exiting or not pos.is_open:
        return False
    pos.exiting = True
    store.put(uid, pos)
    await _cancel_protection(uid, client, pos)
    tick = pos.contract.tick_size
    price = align_to_tick(max(0.05, float(premium or pos.stop)), tick, side="sell")
    try:
        res = await client.place_order(
            f"{pos.contract.exchange}:{pos.contract.tradingsymbol}", "sell",
            float(pos.quantity), order_type="limit_order", limit_price=float(price),
            tag=f"{STRATEGY_ID[:14]}-exit")
        oid = str((res or {}).get("order_id") or "")
    except Exception as exc:                                       # noqa: BLE001
        pos.exiting = False
        store.put(uid, pos)
        # The stop went away to make room for this order. Put it back.
        await _place_protection(uid, client, pos, cfg)
        note(uid, "error", f"exit rejected for {pos.contract.tradingsymbol}: {exc}")
        return False
    if not oid:
        pos.exiting = False
        store.put(uid, pos)
        await _place_protection(uid, client, pos, cfg)
        return False
    closed = store.close(uid, pos.contract.tradingsymbol, reason, exit_price=price)
    # Only the leg still open — what was banked at the first target was recorded
    # when it was sold, and counting it twice would inflate the day's result.
    pnl = closed.realised_inr if closed else 0.0
    rec = store.load_record(uid).roll(_today())
    store.save_record(uid, rec.record(pnl))
    note(uid, "exit", f"sold {pos.quantity} {pos.contract.tradingsymbol} @ {price} "
                      f"({reason}), Rs {pnl:,.0f}")
    return True


async def _scale_out(uid: str, client, pos: IntradayPosition, premium: float,
                     cfg: IntradayConfig) -> bool:
    """Bank half at the first target and let the rest run behind a breakeven stop.

    This is the 1:2-then-1:3 the board advertises. Without it the first target
    closed the whole position and the runner never existed — a UI claiming
    backend behaviour the backend does not honour, which is the failure this
    codebase keeps having to fix.

    A single-lot position cannot be halved, so it keeps its runner target and
    simply rides the trail. Attempting to sell half a lot gets a rejection at
    the exact moment it was trying to bank a win.
    """
    qty = pos.scale_out_qty(pos.contract.lot_size)
    if qty <= 0 or qty >= pos.quantity:
        # Nothing to bank. Mark it done so the stop still goes to breakeven and
        # the runner target becomes the live one.
        pos.target1_done = True
        pos.breakeven_done = True
        pos.stop = max(pos.stop, pos.effective_entry)
        store.put(uid, pos)
        await _sync_protection(uid, pos, cfg)
        return False
    price = align_to_tick(max(0.05, float(premium)), pos.contract.tick_size,
                          side="sell")
    await _cancel_protection(uid, client, pos)
    try:
        res = await client.place_order(
            f"{pos.contract.exchange}:{pos.contract.tradingsymbol}", "sell",
            float(qty), order_type="limit_order", limit_price=float(price),
            tag=f"{STRATEGY_ID[:12]}-scale")
        oid = str((res or {}).get("order_id") or "")
    except Exception as exc:                                       # noqa: BLE001
        await _place_protection(uid, client, pos, cfg)
        note(uid, "error", f"scale-out rejected for {pos.contract.tradingsymbol}: {exc}")
        return False
    if not oid:
        await _place_protection(uid, client, pos, cfg)
        return False
    pos.scaled_qty = qty
    pos.scaled_price = price
    pos.quantity -= qty
    pos.lots = max(1, pos.quantity // max(1, pos.contract.lot_size))
    pos.target1_done = True
    pos.breakeven_done = True
    pos.stop = max(pos.stop, pos.effective_entry)
    store.put(uid, pos)
    # Re-rest protection for what is LEFT. The old GTT was for the full size and
    # would have sold lots that are no longer there.
    await _place_protection(uid, client, pos, cfg)
    rec = store.load_record(uid).roll(_today())
    store.save_record(uid, rec.record(pos.banked_inr))
    note(uid, "scale",
         f"banked {qty} {pos.contract.tradingsymbol} @ {price} "
         f"(Rs {pos.banked_inr:,.0f}); {pos.quantity} runs to {pos.target2}")
    return True


async def on_ticks(uid: str, ticks: list) -> str:
    """Trail and exit on live prices.

    This is where the trailing stop actually happens. A trail that only runs
    when a UI is open is not a trail, so this is driven by the ticker manager's
    broadcast rather than by anything the browser does.
    """
    open_now = store.open_positions(uid)
    if not open_now:
        return "idle"
    cfg = get_config(uid)
    by_token = {int(t.get("instrument_token") or 0): t for t in ticks or []}
    session_over = cfg.close_at_session_end and not _is_market_open(cfg)
    client = None
    acted = "watching"
    for pos in open_now:
        tick = by_token.get(int(pos.contract.token or 0))
        spot_tick = by_token.get(int(pos.underlying_token or 0))
        spot = float((spot_tick or {}).get("last_price") or 0.0) or None
        if not tick and not spot_tick and not session_over:
            continue
        ltp = float((tick or {}).get("last_price") or 0.0)
        if ltp > 0:
            if ltp > pos.peak:
                pos.peak = ltp
                store.put(uid, pos)
            new_stop, why = update_trail(pos, ltp, cfg)
            if new_stop > pos.stop:
                pos.stop = new_stop
                pos.breakeven_done = pos.breakeven_done or new_stop >= pos.effective_entry
                store.put(uid, pos)
                note(uid, "trail",
                     f"{pos.contract.tradingsymbol} stop to {new_stop} ({why})")
                await _sync_protection(uid, pos, cfg)
        if should_scale_out(pos, ltp):
            client = client or await _client(uid)
            if await _scale_out(uid, client, pos, ltp, cfg):
                acted = "scaled"
            continue
        done, reason = should_exit(pos, ltp, spot=spot, session_over=session_over)
        if not done:
            continue
        client = client or await _client(uid)
        # A session-end sweep can arrive with no tick for this contract. Pricing
        # the exit at the stop would send a limit nowhere near the market, so
        # the price is fetched rather than assumed.
        price = ltp or await _last_price(client, pos) or pos.stop
        if await _exit_position(uid, client, pos, reason, price, cfg):
            acted = "exited"
    if not store.open_positions(uid):
        await _subscribe(uid)
    return acted


async def check_rules(uid: str) -> int:
    """Close positions whose REASON for being open has gone.

    A stop answers "how much am I willing to lose"; this answers "is the idea
    still true". For two of these three the stated exit IS the idea failing —
    the ribbon strategy is held until the opposite full cross, and nothing but
    this evaluates that — so without this the trail was the only exit and a
    strategy was being traded that nobody wrote.

    Bar-close work, so it runs on the scan cadence rather than on ticks.
    """
    open_now = store.open_positions(uid)
    if not open_now:
        return 0
    cfg = get_config(uid)
    from app.engines.intraday import thesis_broken, to_bars
    from app.services.intraday import (LOOKBACK_BARS, _drop_forming, _inst,
                                       resample_for)
    client = None
    closed = 0
    for pos in open_now:
        if pos.strategy == "adopted":
            continue          # no rule to test — it was not this engine's setup
        try:
            client = client or await _client(uid)
            token = int(pos.underlying_token or 0)
            if token <= 0:
                continue       # no series to test it on; the price stop still holds
            raw = _drop_forming(
                await client.get_candles(_inst(pos.underlying, token),
                                         cfg.timeframe, LOOKBACK_BARS), cfg)
            if not raw:
                continue
            bars = to_bars(resample_for(raw, cfg))
            done, why = thesis_broken(bars, cfg, pos.strategy, pos.thesis)
        except Exception as exc:                                   # noqa: BLE001
            log.debug("intraday: rule check failed for %s: %s",
                      pos.contract.tradingsymbol, exc)
            continue
        if not done:
            # The thesis holds. Ratchet the SPOT stop, which is the trail these
            # strategies actually state — the premium trail protects the money,
            # this protects the idea, and neither replaces the other.
            moved, trail_why = spot_trail(pos, cfg, spot=float(bars.close[-1]),
                                          **_trail_inputs(bars, cfg))
            if moved != pos.spot_stop:
                pos.spot_stop = moved
                store.put(uid, pos)
                note(uid, "trail",
                     f"{pos.contract.tradingsymbol} spot stop to {moved} ({trail_why})")
            continue
        premium = await _last_price(client, pos)
        if await _exit_position(uid, client, pos, why, premium, cfg):
            closed += 1
    return closed


def _trail_inputs(bars, cfg: IntradayConfig) -> dict:
    """ATR, the last swing, and VWAP — whichever the trail mode needs.

    Computed once per position per bar rather than inside the trail, so the
    trail itself stays a pure function of numbers and can be tested without a
    bar series.
    """
    import numpy as np
    from app.engines.indicators import compute_atr
    from app.engines.intraday.indicators import session_vwap
    out: dict = {}
    try:
        atr = compute_atr(bars.high, bars.low, bars.close, cfg.pb_atr_length)
        out["atr"] = float(atr[-1]) if np.isfinite(atr[-1]) else 0.0
    except Exception:                                              # noqa: BLE001
        out["atr"] = 0.0
    try:
        look = max(3, int(cfg.pb_atr_length))
        out["swing"] = float(np.min(bars.low[-look:]))
    except Exception:                                              # noqa: BLE001
        out["swing"] = None
    try:
        vwap = session_vwap(bars.high, bars.low, bars.close, bars.volume,
                            bars.session_starts)
        out["vwap"] = float(vwap[-1])
    except Exception:                                              # noqa: BLE001
        out["vwap"] = None
    return out


async def exit_one(uid: str, symbol: str, reason: str = "manual") -> dict:
    """Close one position now. Available whatever the manual/auto setting says.

    Auto gates OPENING. An operator must always be able to close.
    """
    async with _lock_for(uid):
        pos = store.get(uid, symbol)
        if pos is None or not pos.is_open:
            return {"ok": False, "message": f"not holding {symbol}"}
        cfg = get_config(uid)
        client = await _client(uid)
        premium = await _last_price(client, pos)
        ok = await _exit_position(uid, client, pos, reason, premium, cfg)
        await _subscribe(uid)
        return {"ok": ok, "symbol": symbol}


async def square_off_all(uid: str, reason: str = "square off") -> dict:
    async with _lock_for(uid):
        cfg = get_config(uid)
        client = await _client(uid)
        closed = []
        for pos in store.open_positions(uid):
            premium = await _last_price(client, pos)
            if await _exit_position(uid, client, pos, reason, premium, cfg):
                closed.append(pos.contract.tradingsymbol)
        await _subscribe(uid)
        return {"ok": True, "closed": closed}


async def _last_price(client, pos: IntradayPosition) -> float:
    key = f"{pos.contract.exchange}:{pos.contract.tradingsymbol}"
    try:
        quotes = await client.get_quote([key]) or {}
        q = quotes.get(key) or quotes.get(pos.contract.tradingsymbol) or {}
        return float(q.get("last_price") or 0.0)
    except Exception:
        return 0.0


# ----------------------------------------------------------------- reconcile

async def reconcile(uid: str) -> dict:
    """Re-sync against the broker. The broker is right; we are wrong.

    A position we think we hold and Zerodha does not is CLOSED, not
    re-protected — re-protecting it would rest a sell order against lots that
    are not there, which is a naked short waiting for a trigger.
    """
    cfg = get_config(uid)
    restored = vanished = reprotected = 0
    gone: list[str] = []
    try:
        client = await _client(uid)
    except Exception as exc:                                       # noqa: BLE001
        return {"error": str(exc), "restored": 0, "vanished": 0, "reprotected": 0}
    live: dict[str, int] = {}
    try:
        book = await client.get_positions()
        rows = book.get("net") or [] if isinstance(book, dict) else (book or [])
        for r in rows:
            if isinstance(r, dict):
                sym = str(r.get("tradingsymbol") or r.get("symbol") or "")
                qty = int(r.get("quantity") or r.get("size") or 0)
            else:
                sym = str(getattr(r, "symbol", "") or "")
                qty = int(getattr(r, "size", 0) or 0)
            if sym:
                live[sym] = qty
    except Exception as exc:                                       # noqa: BLE001
        log.warning("intraday: reconcile could not read the broker for %s: %s", uid, exc)
        return {"error": str(exc), "restored": 0, "vanished": 0, "reprotected": 0}

    for pos in store.open_positions(uid):
        sym = pos.contract.tradingsymbol
        if not is_paper(uid) and live.get(sym, 0) <= 0:
            store.close(uid, sym, "not at the broker on reconcile")
            gone.append(sym)
            vanished += 1
            continue
        restored += 1
        if cfg.stop_mode != "monitor" and not pos.gtt_id:
            if await _place_protection(uid, client, pos, cfg):
                reprotected += 1
    await _subscribe(uid)
    if restored or vanished or reprotected:
        note(uid, "reconcile",
             f"restored {restored}, {vanished} gone at the broker, "
             f"{reprotected} re-protected")
    return {"restored": restored, "vanished": vanished, "gone": gone,
            "reprotected": reprotected}


def positions_view(uid: str) -> list[dict]:
    rows = []
    for pos in store.load(uid).values():
        d = pos.as_dict()
        d["broker_stop"] = bool(pos.gtt_id)
        rows.append(d)
    rows.sort(key=lambda r: (not r["is_open"], -int(r.get("entered_ms") or 0)))
    return rows


# ------------------------------------------------------------- the auto loop

def _kite_user_ids() -> list[str]:
    try:
        from app.services.exchanges.kite import accounts
        accounts.bootstrap()
        return sorted({a.user_id for a in accounts.all_accounts() if a.is_active})
    except Exception:
        return []


async def _auto_enter(uid: str) -> int:
    """Take the armed rows, best reward-to-risk first, until a cap refuses.

    Stopping on the first cap rather than trying every row: the caps are about
    the account, not the row, so the next one would be refused for the same
    reason and each attempt costs a quote.
    """
    st = scan_status(uid)
    cfg = get_config(uid)
    armed = sorted(st.signals.values(),
                   key=lambda r: -float((r.get("signal") or {}).get("rr") or 0.0))
    taken = 0
    for row in armed:
        sid = row.get("signal_id")
        if not sid:
            continue
        # Ask the account-level gates BEFORE the row's own. They are about the
        # account, not the row, so once one refuses, the next row is refused for
        # the same reason and each attempt costs a quote. Matching on words in
        # an error message was the earlier version of this, and a reworded
        # blocker would have silently turned the cap off.
        if entry_blocker(uid, cfg, ""):
            break
        if (await arm(uid, sid)).get("ok"):
            taken += 1
    return taken


async def scan_all_once() -> dict[str, str]:
    if _replay_owns_the_board():
        return {"*": "replay is driving this board — live scan is off"}
    uids = _kite_user_ids()
    if not uids:
        return {"*": "no active accounts"}
    from app.services.intraday import scan_once
    out: dict[str, str] = {}
    for uid in uids:
        cfg = get_config(uid)
        if not cfg.enabled:
            out[uid] = "disabled"
            continue
        if not _is_market_open(cfg):
            out[uid] = "outside session"
            # The session is over and the engine holds intraday positions. This
            # is the sweep that makes `close_at_session_end` real rather than a
            # setting nothing reads.
            if cfg.close_at_session_end and store.open_positions(uid):
                try:
                    await square_off_all(uid, "session end")
                except Exception as exc:                           # noqa: BLE001
                    log.warning("intraday: session-end square-off failed for %s: %s",
                                uid, exc)
            continue
        try:
            res = await scan_once(uid)
            note_text = f"{res.get('armed', 0)} armed of {res.get('scanned', 0)}"
            # Before opening anything new, close what the tape has invalidated.
            rule_closed = await check_rules(uid)
            if rule_closed:
                note_text += f", {rule_closed} closed on rule"
            await _subscribe(uid)
            if res.get("armed") and auto_execute(uid):
                note_text += f", auto-entered {await _auto_enter(uid)}"
            out[uid] = note_text
        except Exception as exc:                                   # noqa: BLE001
            out[uid] = f"error: {exc}"
            log.warning("intraday scan failed for %s: %s", uid, exc)
    return out


async def reconcile_all() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for uid in _kite_user_ids():
        try:
            out[uid] = await reconcile(uid)
        except Exception as exc:                                   # noqa: BLE001
            log.warning("intraday reconcile failed for %s: %s", uid, exc)
    return out


async def auto_scan_loop(interval: int = 60) -> None:
    """Scan on the strategy's own cadence. A no-op while it is disabled.

    Reconciles against the broker BEFORE the first scan, so a restart cannot
    open a second position in a contract it already holds.
    """
    log.info("intraday auto-scan loop started (every %ss)", interval)
    try:
        await reconcile_all()
    except Exception as exc:                                       # noqa: BLE001
        log.warning("intraday startup reconcile failed: %s", exc)
    while True:
        try:
            cfg = get_config()
            if cfg.enabled:
                await scan_all_once()
            interval = max(30, int(cfg.scan_interval_seconds or interval))
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                   # noqa: BLE001
            log.warning("intraday auto-scan cycle failed: %s", exc)
        await asyncio.sleep(interval)
