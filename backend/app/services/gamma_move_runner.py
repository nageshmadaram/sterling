"""Session runner for Gamma Move: scanning, arming, and watching positions out."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.core.logging import get_logger
from app.engines.gamma_move import (GammaMoveConfig, GammaMoveStrategy, GammaSignal,
                                    Intent, PositionState, SessionState, STRATEGY_ID,
                                    align_to_tick, exit_order_price, q2)
from app.services import gamma_move_positions as positions_store
from app.services.gamma_move import get_config, ist_today

log = get_logger(__name__)
_IST = timezone(timedelta(hours=5, minutes=30))
_OWNER = STRATEGY_ID
_sessions: dict[str, "Session"] = {}
_locks: dict[str, asyncio.Lock] = {}


def _lock_for(uid: str) -> asyncio.Lock:
    lock = _locks.get(uid)
    if lock is None:
        lock = _locks[uid] = asyncio.Lock()
    return lock


def _now_ms() -> int:
    return int(datetime.now(_IST).timestamp() * 1000)


def _today_str() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%d")


def _is_market_open(cfg: GammaMoveConfig) -> bool:
    now = datetime.now(_IST)
    if now.weekday() >= 5:
        return False
    return cfg.session_start <= now.strftime("%H:%M") <= cfg.session_end


async def _client(uid: str):
    from app.services.exchanges.kite import accounts
    acct = accounts.get_active(uid)
    if not acct:
        raise RuntimeError("No active Kite account")
    return await accounts.acquire_client(acct)


def is_paper(uid: str) -> bool:
    try:
        from app.services.exchanges.kite import accounts
        acct = accounts.get_active(uid)
        return bool(getattr(acct, "is_paper", True)) if acct else True
    except Exception:
        return True


def auto_execute(uid: str) -> bool:
    try:
        from app.services.kite_engine import state as engine_state
        return bool(getattr(engine_state.get_config(uid), "auto_execute", False))
    except Exception:
        return False


def spread_blocks_entry(quote: dict, cfg: GammaMoveConfig) -> Optional[str]:
    """Refuse a live quote whose bid/ask width exceeds ``max_spread_pct``.

    Missing depth is not a refusal — the scanner already skipped unquoted
    rows, and a tick with no book is not the same as a 3.1% book.
    """
    from app.services.gamma_move_scanner import _spread_pct
    spread = _spread_pct(quote or {})
    if spread is None:
        return None
    cap = float(cfg.max_spread_pct or 0.0)
    if cap > 0 and spread > cap:
        return f"spread {spread:.1f}% exceeds {cap:g}%"
    return None


def _safety(uid: str, idempotency_key: Optional[str]) -> tuple[bool, str]:
    try:
        from app.services.live_safety import assert_safe_to_trade
        decision = assert_safe_to_trade([], idempotency_key,
                                        check_daily_loss=True, uid=uid)
        return bool(decision.allowed), str(decision.reason or "")
    except Exception as exc:
        log.error("gamma_move: safety check failed closed for %s: %s", uid, exc)
        return False, f"safety check unavailable: {exc}"


async def _confirm_fill(client, order_id: str) -> tuple[str, float]:
    if str(order_id).startswith("PAPER-"):
        return "COMPLETE", 0.0
    try:
        history = await client.get_order_history(order_id)
    except Exception as exc:
        log.warning("gamma_move: order status unavailable for %s: %s", order_id, exc)
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


class Session:
    def __init__(self, uid: str, cfg: GammaMoveConfig):
        self.uid = uid
        self.cfg = cfg
        self.strategy = GammaMoveStrategy(cfg, SessionState(day=_today_str()))
        self.signals: dict[str, GammaSignal] = {}
        self.subscribed: set[int] = set()
        self.notes: list[dict] = []

    def note(self, kind: str, message: str) -> None:
        self.notes.append({"kind": kind, "message": message, "at_ms": _now_ms()})
        del self.notes[:-40]


def session_for(uid: str, cfg: Optional[GammaMoveConfig] = None) -> Session:
    """Reuse the in-memory session across midnight."""
    cfg = cfg or get_config(uid)
    s = _sessions.get(uid)
    if s is None:
        s = _sessions[uid] = Session(uid, cfg)
        s.strategy.state.record = positions_store.load_record(uid)
        from app.services import db
        try:
            raw = db.get_config(f"gamma_move_signals:{uid}")
            if raw:
                import json
                rows = json.loads(raw) if isinstance(raw, str) else raw
                for r in rows:
                    sig = GammaSignal.from_dict(r)
                    s.signals[sig.id] = sig
        except Exception as exc:
            log.debug("gamma_move: signal hydration failed for %s: %s", uid, exc)
    s.cfg = cfg
    s.strategy.cfg = cfg
    s.strategy.state.roll(_today_str())
    return s


def clear(uid: Optional[str] = None) -> None:
    if uid:
        _sessions.pop(uid, None)
    else:
        _sessions.clear()


def _replay_owns_the_board() -> bool:
    try:
        from app.services.simulation import simulation_runner
        return bool(simulation_runner.has_session_view)
    except Exception:
        return False


async def scan_once(uid: str) -> dict:
    if _replay_owns_the_board():
        return {"scanned": 0, "armed": 0, "signals": [],
                "message": "replay is driving this board — live scan is off"}
    cfg = get_config(uid)
    async with _lock_for(uid):
        session = session_for(uid, cfg)
        from app.services.gamma_move_scanner import scan_once as _scan
        signals = await _scan(uid, cfg, session.strategy)
        session.signals = {s.id: s for s in signals}
        from app.services import db
        try:
            import json
            db.set_config(f"gamma_move_signals:{uid}",
                          json.dumps([s.as_dict() for s in signals], separators=(",", ":")))
        except Exception as exc:
            log.warning("gamma_move: could not persist scan signals for %s: %s", uid, exc)
        await _subscribe_watched(uid, session)
        armed = [s for s in signals if s.state == "armed"]
        return {"scanned": len(signals), "armed": len(armed),
                "signals": [s.as_dict() for s in signals]}


async def _subscribe_watched(uid: str, session: Session) -> None:
    tokens = {int(s.candidate.instrument.instrument_id)
              for s in session.signals.values()
              if s.candidate.instrument.instrument_id.isdigit()}
    tokens |= {int(p.instrument.instrument_id)
               for p in session.strategy.state.positions.values()
               if p.instrument.instrument_id.isdigit()}
    new = tokens - session.subscribed
    stale = session.subscribed - tokens
    if new:
        try:
            from app.services.exchanges.kite import ticker_manager
            from app.services.exchanges.kite import constants as K
            await ticker_manager.subscribe(uid, sorted(new), K.MODE_FULL, owner=_OWNER)
            session.subscribed |= new
        except Exception as exc:
            log.warning("gamma_move subscribe failed for %s: %s", uid, exc)
    if stale:
        await _release(uid, session, stale)


async def _release(uid: str, session: Session, tokens: set) -> None:
    if not tokens:
        return
    try:
        from app.services.exchanges.kite import ticker_manager
        await ticker_manager.release(uid, sorted(tokens), owner=_OWNER)
    except Exception as exc:
        log.debug("gamma_move release failed for %s: %s", uid, exc)
    session.subscribed -= tokens


async def release_subscriptions(uid: str) -> None:
    session = _sessions.get(uid)
    if session:
        await _release(uid, session, set(session.subscribed))


async def arm(uid: str, signal_id: str) -> dict:
    if _replay_owns_the_board():
        return {"ok": False, "message": "replay is driving this board — live entry is off"}
    cfg = get_config(uid)
    async with _lock_for(uid):
        session = session_for(uid, cfg)
        signal = session.signals.get(signal_id)
        if signal is None:
            return {"ok": False, "message": f"no signal {signal_id} in this session"}
        inst = signal.candidate.instrument
        client = await _client(uid)
        try:
            quotes = await client.get_quote([f"{inst.exchange}:{inst.tradingsymbol}"]) or {}
        except Exception as exc:
            session.note("error", f"quote failed for {inst.tradingsymbol}: {exc}")
            return {"ok": False, "message": str(exc)}
        q = quotes.get(f"{inst.exchange}:{inst.tradingsymbol}") or quotes.get(inst.tradingsymbol) or {}
        why = spread_blocks_entry(q, cfg)
        if why:
            session.note("blocked", f"entry refused for {inst.tradingsymbol}: {why}")
            return {"ok": False, "message": why}
        blocker = session.strategy.admit(signal, _today_str())
        if blocker:
            return {"ok": False, "message": blocker}
        if signal.entry is None or signal.quantity is None or signal.quantity <= 0:
            return {"ok": False, "message": "signal has no priced entry or size"}
        if positions_store.get(uid, inst.tradingsymbol) and \
                positions_store.get(uid, inst.tradingsymbol).is_open:
            return {"ok": False, "message": f"already holding {inst.tradingsymbol}"}
        idem = f"{STRATEGY_ID}:{uid}:{signal.id}:{_today_str()}"
        ok, why = _safety(uid, idem)
        if not ok:
            session.note("blocked", f"entry refused for {inst.tradingsymbol}: {why}")
            return {"ok": False, "message": why}
        limit = align_to_tick(signal.entry, inst.tick_size, side="buy")
        try:
            res = await client.place_order(
                f"{inst.exchange}:{inst.tradingsymbol}", "buy", float(signal.quantity),
                order_type="limit_order", limit_price=float(limit), tag=STRATEGY_ID[:20])
        except Exception as exc:
            session.note("error", f"entry rejected for {inst.tradingsymbol}: {exc}")
            return {"ok": False, "message": str(exc)}
        order_id = str((res or {}).get("order_id")
                       or ((res or {}).get("data") or {}).get("order_id") or "")
        if not order_id:
            session.note("error", f"{inst.tradingsymbol}: broker returned no order id")
            return {"ok": False, "message": "broker returned no order id"}
        pos = session.strategy.on_entry(signal, limit, _now_ms(), _today_str())
        pos.order_id = order_id
        pos.stop_mode = cfg.stop_mode
        pos.idempotency_key = idem
        positions_store.put(uid, pos)
        status, avg = await _confirm_fill(client, order_id)
        if status in ("REJECTED", "CANCELLED"):
            positions_store.mark_rejected(uid, inst.tradingsymbol, status)
            session.strategy.state.positions.pop(inst.tradingsymbol, None)
            session.strategy.state.trades_today = max(
                0, session.strategy.state.trades_today - 1)
            session.note("error", f"{inst.tradingsymbol} {status.lower()} at the broker")
            return {"ok": False, "message": f"order {status.lower()}"}
        filled = positions_store.mark_filled(uid, inst.tradingsymbol, avg)
        if filled is not None:
            pos.stop, pos.fill_price, pos.status = filled.stop, filled.fill_price, filled.status
        gtt_id = await _place_protection(uid, client, pos, cfg)
        await _subscribe_watched(uid, session)
        session.note("entry",
                     f"bought {pos.quantity} {inst.tradingsymbol} @ {pos.effective_entry} "
                     f"(stop {pos.stop})")
        return {"ok": True, "order_id": order_id, "symbol": inst.tradingsymbol,
                "quantity": pos.quantity, "entry": pos.effective_entry,
                "stop": pos.stop, "gtt_id": gtt_id, "paper": is_paper(uid),
                "status": pos.status}


async def _place_protection(uid: str, client, pos, cfg) -> int:
    if cfg.stop_mode == "monitor" or pos.stop <= 0 or pos.quantity <= 0:
        return 0
    try:
        from app.services.kite_engine.protective_stop import place_stop
        gtt_id = await place_stop(
            client, tradingsymbol=pos.instrument.tradingsymbol,
            exchange=pos.instrument.exchange, qty=int(pos.quantity),
            trigger_premium=float(pos.stop), last_price=float(pos.effective_entry),
            direction="long", target_premium=float(pos.target or 0.0))
    except Exception as exc:
        gtt_id = None
        log.warning("gamma_move: protective GTT failed for %s: %s",
                    pos.instrument.tradingsymbol, exc)
    if gtt_id:
        pos.gtt_id = int(gtt_id)
        positions_store.put(uid, pos)
        return int(gtt_id)
    session = _sessions.get(uid)
    if session:
        session.note("warning", f"{pos.instrument.tradingsymbol}: no broker stop")
    return 0


async def adopt(uid: str, symbol: str, quantity: int, entry_price: float) -> dict:
    if _replay_owns_the_board():
        return {"ok": False, "message": "replay is driving this board — live adopt is off"}
    cfg = get_config(uid)
    async with _lock_for(uid):
        session = session_for(uid, cfg)
        match = next((s for s in session.signals.values()
                      if s.candidate.instrument.tradingsymbol == symbol), None)
        if match is None:
            return {"ok": False, "message": f"{symbol} is not a contract this scan knows"}
        inst = match.candidate.instrument
        stop = q2(float(entry_price) * (1 - cfg.stop_percent / 100.0))
        pos = PositionState(signal_id=match.id, instrument=inst, entry=q2(entry_price),
                            stop=stop, quantity=int(quantity),
                            lots=max(1, int(quantity) // max(1, inst.lot_size)),
                            entered_ms=_now_ms(), entry_day=_today_str(),
                            status=positions_store.OPEN,
                            fill_price=q2(entry_price), stop_mode=cfg.stop_mode)
        session.strategy.state.positions[symbol] = pos
        positions_store.put(uid, pos)
        gtt_id = await _place_protection(uid, await _client(uid), pos, cfg)
        session.note("adopt", f"adopted {quantity} {symbol} @ {entry_price}, stop {stop}")
        await _subscribe_watched(uid, session)
        return {"ok": True, "symbol": symbol, "stop": stop, "gtt_id": gtt_id}


async def orphan_positions(uid: str, cfg: GammaMoveConfig) -> list[dict]:
    try:
        client = await _client(uid)
        book = await client.get_positions()
    except Exception as exc:
        log.debug("gamma_move position fetch failed for %s: %s", uid, exc)
        return []
    raw_rows = book.get("net") or [] if isinstance(book, dict) else (book or [])
    known = {sym for sym, p in positions_store.load(uid).items() if p.is_open}
    out = []
    for r in raw_rows:
        if isinstance(r, dict):
            sym = str(r.get("tradingsymbol") or r.get("symbol") or "")
            qty = int(r.get("quantity") or r.get("size") or 0)
            exchange = str(r.get("exchange") or "")
            entry_price = float(r.get("average_price") or r.get("entry_price") or 0.0)
        else:
            sym = str(getattr(r, "symbol", "") or "")
            qty = int(getattr(r, "size", 0) or 0)
            underlying = str(getattr(r, "underlying", "") or "")
            exchange = underlying.split(":")[0] if ":" in underlying else ""
            entry_price = float(getattr(r, "entry_price", 0.0) or 0.0)
        if not exchange and (sym.endswith("CE") or sym.endswith("PE")):
            exchange = "NFO"
        if qty <= 0 or exchange not in ("NFO", "BFO") or sym in known:
            continue
        if not (sym.endswith("CE") or sym.endswith("PE")):
            continue
        out.append({"symbol": sym, "quantity": qty, "entry_price": q2(entry_price)})
    return out


async def reconcile(uid: str) -> dict:
    cfg = get_config(uid)
    session = session_for(uid, cfg)
    restored = 0
    for sym, pos in positions_store.load(uid).items():
        if pos.is_open:
            session.strategy.state.positions[sym] = pos
            restored += 1
    orphans = await orphan_positions(uid, cfg)
    vanished = []
    try:
        client = await _client(uid)
        book = await client.get_positions()
        raw_rows = book.get("net") or [] if isinstance(book, dict) else (book or [])
        live = {}
        for r in raw_rows:
            if isinstance(r, dict):
                sym = str(r.get("tradingsymbol") or r.get("symbol") or "")
                qty = int(r.get("quantity") or r.get("size") or 0)
            else:
                sym = str(getattr(r, "symbol", "") or "")
                qty = int(getattr(r, "size", 0) or 0)
            if sym:
                live[sym] = qty
        for sym in list(session.strategy.state.positions):
            if live.get(sym, 0) <= 0 and not is_paper(uid):
                vanished.append(sym)
                positions_store.close(uid, sym, "not at the broker on reconcile")
                session.strategy.state.positions.pop(sym, None)
    except Exception as exc:
        log.warning("gamma_move: reconcile could not read the broker for %s: %s", uid, exc)
    if restored or orphans or vanished:
        session.note("reconcile",
                     f"restored {restored}, {len(orphans)} unaccounted, "
                     f"{len(vanished)} gone at the broker")
    await _subscribe_watched(uid, session)
    return {"restored": restored, "orphans": orphans, "vanished": vanished}


async def on_ticks(uid: str, ticks: list) -> str:
    session = _sessions.get(uid)
    if not session or not session.strategy.state.positions:
        return "idle"
    cfg = session.cfg
    by_token = {int(t.get("instrument_token") or 0): t for t in ticks or []}
    today = _today_str()
    session_over = not _is_market_open(cfg)
    client = None
    acted = "watching"
    for pos in list(session.strategy.state.positions.values()):
        tid = int(pos.instrument.instrument_id) if pos.instrument.instrument_id.isdigit() else 0
        tick = by_token.get(tid)
        if not tick:
            continue
        ltp = float(tick.get("last_price") or 0)
        if ltp <= 0:
            continue
        decision = session.strategy.on_price(pos, ltp, _now_ms(), today,
                                             session_over=session_over)
        if decision.intent is not Intent.EXIT or not decision.exit_position:
            await _sync_trail(uid, pos, cfg)
            continue
        client = client or await _client(uid)
        await _cancel_protection(uid, client, pos)
        price = exit_order_price(ltp, pos.instrument.tick_size)
        try:
            res = await client.place_order(
                f"{pos.instrument.exchange}:{pos.instrument.tradingsymbol}", "sell",
                float(pos.quantity), order_type="limit_order", limit_price=float(price),
                tag=f"{STRATEGY_ID[:14]}-exit")
            oid = str((res or {}).get("order_id") or "")
        except Exception as exc:
            oid, err = "", str(exc)
            pos.exiting = False
            await _place_protection(uid, client, pos, cfg)
            session.note("error", f"exit rejected for {pos.instrument.tradingsymbol}: {err}")
            continue
        if not oid:
            pos.exiting = False
            await _place_protection(uid, client, pos, cfg)
            continue
        pnl = session.strategy.on_exit(pos, price, today)
        positions_store.close(uid, pos.instrument.tradingsymbol, decision.exit_reason)
        positions_store.save_record(uid, session.strategy.state.record)
        session.note("exit", f"sold {pos.quantity} {pos.instrument.tradingsymbol} @ {price} "
                             f"({decision.exit_reason}), Rs {pnl:,.0f}")
        acted = "exited"
    if not session.strategy.state.positions:
        await _subscribe_watched(uid, session)
    return acted


async def _sync_trail(uid: str, pos, cfg) -> None:
    if not pos.gtt_id or pos.stop <= 0:
        return
    if abs(pos.stop - getattr(pos, "_gtt_at", 0.0)) < 0.05:
        return
    try:
        from app.services.kite_engine.protective_stop import move_stop
        client = await _client(uid)
        await move_stop(client, trigger_id=int(pos.gtt_id),
                        tradingsymbol=pos.instrument.tradingsymbol,
                        exchange=pos.instrument.exchange, qty=int(pos.quantity),
                        trigger_premium=float(pos.stop),
                        last_price=float(pos.effective_entry), direction="long",
                        target_premium=float(pos.target or 0.0))
        object.__setattr__(pos, "_gtt_at", float(pos.stop))
        positions_store.put(uid, pos)
    except Exception as exc:
        log.warning("gamma_move: could not move the broker stop for %s: %s",
                    pos.instrument.tradingsymbol, exc)


async def _cancel_protection(uid: str, client, pos) -> None:
    if not pos.gtt_id:
        return
    try:
        from app.services.kite_engine.protective_stop import cancel_stop
        await cancel_stop(client, int(pos.gtt_id))
    except Exception as exc:
        log.warning("gamma_move: could not cancel the broker stop for %s: %s",
                    pos.instrument.tradingsymbol, exc)
    pos.gtt_id = 0
    positions_store.put(uid, pos)


def scan_state(uid: str) -> dict:
    from app.services.gamma_move_scanner import scan_stats
    return scan_stats(uid)


def session_status(uid: str) -> Optional[dict]:
    session = _sessions.get(uid)
    if not session:
        return None
    st = session.strategy.state
    positions = []
    for sym, p in st.positions.items():
        positions.append({
            "symbol": sym, "signal_id": p.signal_id, "entry": p.entry, "stop": p.stop,
            "trail": p.trail, "target": p.target, "quantity": p.quantity,
            "lots": p.lots, "entered_ms": p.entered_ms, "entry_day": p.entry_day,
            "sessions_held": p.sessions_held, "exiting": p.exiting,
            "high_water": p.high_water, "status": p.status, "order_id": p.order_id,
            "fill_price": p.fill_price, "effective_entry": p.effective_entry,
            "gtt_id": p.gtt_id, "stop_mode": p.stop_mode,
        })
    return {
        "day": st.day, "phase": st.phase.value, "halt_reason": st.halt_reason,
        "trades_today": st.trades_today,
        "candidates": [s.as_dict() for s in session.signals.values()],
        "positions": positions, "record": st.record.as_dict(),
        "subscribed": sorted(session.subscribed), "notes": session.notes[-10:],
    }


def _kite_user_ids() -> list[str]:
    try:
        from app.services.exchanges.kite import accounts
        accounts.bootstrap()
        return sorted({a.user_id for a in accounts.all_accounts() if a.is_active})
    except Exception:
        return []


async def scan_all_once() -> dict[str, str]:
    if _replay_owns_the_board():
        return {"*": "replay is driving this board — live scan is off"}
    uids = _kite_user_ids()
    if not uids:
        return {"*": "no active accounts"}
    out: dict[str, str] = {}
    for uid in uids:
        cfg = get_config(uid)
        if not cfg.enabled:
            out[uid] = "disabled"
            continue
        if not _is_market_open(cfg):
            out[uid] = "outside session"
            continue
        try:
            res = await scan_once(uid)
            note = f"{res['armed']} armed of {res['scanned']}"
            if res["armed"] and auto_execute(uid):
                taken = await _auto_enter(uid)
                note += f", auto-entered {taken}"
            out[uid] = note
        except Exception as exc:
            out[uid] = f"error: {exc}"
            log.warning("gamma_move scan failed for %s: %s", uid, exc)
    return out


async def _auto_enter(uid: str) -> int:
    session = _sessions.get(uid)
    if not session:
        return 0
    armed = sorted((s for s in session.signals.values() if s.state == "armed"),
                   key=lambda s: -s.candidate.oi)
    taken = 0
    for signal in armed:
        result = await arm(uid, signal.id)
        if result.get("ok"):
            taken += 1
        elif "limit" in str(result.get("message", "")) or "cap" in str(result.get("message", "")):
            break
    return taken


async def reconcile_all() -> dict[str, dict]:
    out = {}
    for uid in _kite_user_ids():
        try:
            out[uid] = await reconcile(uid)
        except Exception as exc:
            log.warning("gamma_move reconcile failed for %s: %s", uid, exc)
    return out


async def auto_scan_loop(interval: int = 300) -> None:
    log.info("gamma_move auto-scan loop started (every %ss)", interval)
    try:
        await reconcile_all()
    except Exception as exc:
        log.warning("gamma_move startup reconcile failed: %s", exc)
    while True:
        try:
            cfg = get_config()
            if cfg.enabled and _is_market_open(cfg):
                await scan_all_once()
            interval = max(60, int(cfg.scan_interval_seconds or interval))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("gamma_move auto-scan cycle failed: %s", exc)
        await asyncio.sleep(interval)
