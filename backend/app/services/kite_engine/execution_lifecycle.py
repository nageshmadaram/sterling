"""Recover durable live entries and protect only broker-confirmed holdings.

Broker observations are journaled before projection. Recovery repeats projection,
never submission. An uncertain initial GTT request requires reconciliation, not a
second trigger. A signed ledger settles fills and costs. Live scale-ins stay blocked:
that is a protection question, not an accounting one.
"""
from __future__ import annotations

import asyncio
from dataclasses import fields

from app.core.logging import get_logger
from app.services.kite_engine import execution_lease, fill_ledger
from app.services.kite_engine import order_journal as journal, positions, state

log = get_logger(__name__)

_locks: dict[tuple[str, str], asyncio.Lock] = {}


def account_id(client) -> str:
    value = str(getattr(client, "_account_id", "") or "")
    if not value:
        raise ValueError("live_account_identity_missing")
    return value


def register_pending(intent, order_id: str) -> positions.OpenPosition:
    """Idempotent projection of an accepted entry; ACK carries zero filled qty."""
    prior = positions.get(intent.uid, intent.symbol)
    if prior and prior.order_id == order_id and prior.account_id == intent.account_id:
        return prior
    if prior and prior.status in (positions.OPEN, positions.PENDING):
        raise ValueError("live_scale_in_or_account_collision")
    allowed = {f.name for f in fields(positions.OpenPosition)}
    payload = {k: v for k, v in intent.payload.items() if k in allowed}
    payload.update(uid=intent.uid, account_id=intent.account_id, product="NRML",
                   symbol=intent.symbol, exchange=intent.exchange, qty=0,
                   order_id=order_id, entry_requested_qty=intent.quantity,
                   entry_pending=True, status=positions.PENDING, qty_by_order={})
    p = positions.register(positions.OpenPosition(**payload))
    positions.persist_strict(intent.uid)
    if p.guard_key:
        state.mark_auto_open(intent.uid, p.guard_key)
    return p


async def _protect(client, p) -> None:
    from app.services.exchanges.kite import constants as K, ticker_manager
    from app.services.kite_engine import protective_stop as stops

    if p.qty <= 0 or p.status != positions.OPEN:
        return
    if p.token:
        try:
            await ticker_manager.subscribe(p.uid, [p.token], mode=K.MODE_LTP)
        except Exception:
            state.log(p.uid, "order_failed", f"{p.symbol}: live tick subscription unavailable")
    if p.stop_mode not in {"broker", "both"} or p.stop_premium <= 0:
        return
    if p.protection_pending:
        return  # prior placement could have succeeded; do not create a rival stop
    account_id = str(getattr(p, "account_id", "") or "")
    if not account_id:
        await _place_protection(client, p)   # paper: no broker to race for
        return
    # Two processes placing a stop for one position leaves TWO live triggers, and
    # the second one sells an option we no longer own the moment it fires.
    try:
        with execution_lease.guard(execution_lease.PROTECTION, account_id=account_id,
                                   uid=p.uid, symbol=p.symbol) as token:
            if token is None:
                state.log(p.uid, "info",
                          f"{p.symbol}: another engine process is arming protection; "
                          f"not placing a second trigger")
                return
            await _place_protection(client, p)
    except Exception as exc:  # noqa: BLE001
        state.log(p.uid, "order_failed",
                  f"{p.symbol}: protection ownership cannot be established ({exc}); "
                  f"no trigger sent")


async def _place_protection(client, p) -> None:
    """Arm or resize the broker trigger. Caller owns the protection lease."""
    from app.services.kite_engine import protective_stop as stops

    ref_last_price = float(p.fill_price or 0.0)
    if p.direction == "long" and p.stop_premium > 0:
        # Sell stop: Kite requires trigger_premium < last_price
        ref_last_price = max(ref_last_price, p.stop_premium + 0.05)
    elif p.direction == "short" and p.stop_premium > 0:
        # Buy stop: Kite requires trigger_premium > last_price
        ref_last_price = min(ref_last_price, max(0.05, p.stop_premium - 0.05)) if ref_last_price > 0 else max(0.05, p.stop_premium - 0.05)

    kwargs = dict(tradingsymbol=p.symbol, exchange=p.exchange, qty=p.qty,
                  trigger_premium=p.stop_premium, last_price=ref_last_price,
                  direction=p.direction, target_premium=p.target_premium)
    if p.gtt_id:
        if not await stops.move_stop(client, trigger_id=p.gtt_id, **kwargs):
            p.protection_pending = True
            positions.persist_strict(p.uid)
        return
    p.protection_pending = True
    positions.persist_strict(p.uid)  # durable before network: crash/timeout is uncertain
    gid = await stops.place_stop(client, **kwargs)
    if gid:
        p.gtt_id = int(gid)
        p.protection_pending = False
        positions.persist_strict(p.uid)
    else:
        state.log(p.uid, "order_failed", f"{p.symbol}: GTT outcome unresolved; reconcile before retry")


def exchange_ms(order: dict) -> int:
    """Epoch ms the EXCHANGE stamped on this event, or 0 when it did not.

    The ledger replays increments in exchange order, so a real timestamp is what
    lets a late event land in its true place. 0 means "unknown" and must stay 0 —
    substituting arrival time here would look like ordering information and
    silently defeat the replay.
    """
    from app.services.exchanges.kite.client import _parse_kite_ts
    raw = order.get("exchange_timestamp") or order.get("exchange_update_timestamp")
    if not raw:
        return 0
    try:
        return int(_parse_kite_ts(raw))
    except Exception as exc:  # noqa: BLE001
        log.debug("suppressed exchange timestamp parse: %s", exc)
        return 0


async def apply_broker_charges(client, uid: str, p,
                                order_id: str, order: dict) -> None:
    """Ask the broker what this order actually cost and record it against the fills.

    Realized PnL is gross of costs until this lands, so it reads better than the
    truth — the direction that delays the daily-loss breaker rather than tripping
    it early. Best effort: charges are computed after the fact and a failure here
    must not disturb an exit that has already happened. ``Inventory.fees_complete``
    is what says whether the figure is net.
    """
    if client is None or getattr(client, "_is_paper", True) is not False:
        return
    charges = getattr(client, "order_charges", None)
    if charges is None:
        return
    try:
        rows = await charges([{
            "order_id": order_id, "exchange": p.exchange, "tradingsymbol": p.symbol,
            "transaction_type": str(order.get("transaction_type") or "").upper(),
            "variety": str(order.get("variety") or "regular"),
            "product": str(order.get("product") or "NRML"),
            "order_type": str(order.get("order_type") or "MARKET"),
            "quantity": int(order.get("filled_quantity") or 0),
            "average_price": float(order.get("average_price") or 0.0)}])
        total = float((rows or [{}])[0].get("charges", {}).get("total") or 0.0)
        if total <= 0:
            return
        fill_ledger.apply_fees(account_id=p.account_id, uid=uid, symbol=p.symbol,
                               order_id=order_id, fees=total, source="broker")
    except Exception as exc:  # noqa: BLE001
        state.log(uid, "info",
                  f"{p.symbol}: broker charges for #{order_id} unavailable ({exc}); "
                  f"realized PnL stays gross of costs")


def _book_entry(uid: str, account_id: str, intent, order: dict) -> bool:
    """Record the journal's confirmed entry quantity/value in the signed ledger.

    The journal already holds the broker's cumulative figures and rejects older
    or contradictory evidence, so it is the right thing to forward: the ledger
    derives the increment itself and is safe to call again on every replay.

    False means the cost basis did NOT reach the ledger. That is not cosmetic:
    every later exit for this holding then settles on the legacy accumulator, so
    the caller flags the position rather than letting it look accounted for.
    """
    if intent.filled_quantity <= 0 or intent.filled_value <= 0 or not intent.order_id:
        return True
    try:
        applied = fill_ledger.record(
            account_id=account_id, uid=uid, symbol=intent.symbol,
            exchange=intent.exchange, side=intent.side.upper(),
            order_id=intent.order_id, cumulative_quantity=intent.filled_quantity,
            cumulative_value=intent.filled_value, source="entry",
            lot_size=int(intent.payload.get("lot_size") or 0),
            exchange_ts_ms=exchange_ms(order))
    except Exception as exc:  # noqa: BLE001
        state.log(uid, "order_failed",
                  f"{intent.symbol}: entry fill could not be booked to the ledger "
                  f"({exc}); cost basis needs reconciliation")
        return False
    if applied.reconciliation_required:
        state.log(uid, "order_failed",
                  f"{intent.symbol}: contradictory entry fill evidence quarantined "
                  f"({applied.inventory.reconciliation_reason})")
        return False
    return True


async def consume_order(client, uid: str, order: dict) -> bool:
    """True means a journal-owned entry was handled (including blocked evidence)."""
    aid = account_id(client)
    oid = str(order.get("order_id") or "")
    symbol = str(order.get("tradingsymbol") or "")
    intent = journal.find(uid=uid, account_id=aid, order_id=oid,
                          tag=str(order.get("tag") or ""))
    if intent is None:
        return False
    # The active client's authenticated order book/postback must agree with the
    # persisted contract. Missing or contradictory identity is never inferred.
    if (symbol != intent.symbol or order.get("exchange") != intent.exchange
            or str(order.get("transaction_type") or "").upper() != intent.side.upper()
            or order.get("product") != "NRML"
            or int(order.get("quantity") or 0) != intent.quantity):
        state.log(uid, "order_failed", f"{intent.symbol}: journal/broker identity mismatch")
        return True
    async with _locks.setdefault((uid, aid), asyncio.Lock()):
        observed = journal.observe_order(
            intent.intent_key, status=str(order.get("status") or ""), order_id=oid,
            filled_quantity=order.get("filled_quantity", 0),
            average_price=order.get("average_price", 0), raw=order)
        if observed.reconciliation_required:
            state.log(uid, "order_failed", f"{symbol}: conflicting broker fill evidence")
            return True
        if not observed.accepted and not observed.intent.projection_pending:
            p = positions.get(uid, symbol)
            if p and p.order_id == oid and p.account_id == aid and not p.gtt_id:
                await _protect(client, p)
            return True
        # Replaying a terminal event for an already closed position must not reopen it.
        p = register_pending(observed.intent, oid)
        if p.status in (positions.CLOSED, positions.REJECTED):
            journal.mark_projected(intent.intent_key, observed.intent.projection_version)
            return True
        latest = observed.intent
        filled = latest.filled_quantity
        p.qty = filled
        p.qty_by_order = {oid: filled} if filled else {}
        p.fill_price = latest.filled_value / filled if filled else 0.0
        p.entry_pending = latest.state not in journal.TERMINAL
        p.status = (positions.OPEN if filled else
                    positions.PENDING if p.entry_pending else positions.REJECTED)
        positions.persist_strict(uid)
        # Settle the money before the projection is acknowledged. A crash in
        # between leaves the intent projection_pending, so recovery replays this
        # same cumulative evidence and the ledger books it exactly once.
        if not _book_entry(uid, aid, latest, order):
            p.pnl_reconciliation_required = True
            positions.persist_strict(uid)
        elif latest.state in journal.TERMINAL:
            await apply_broker_charges(client, uid, p, oid, order)
        journal.mark_projected(intent.intent_key, latest.projection_version)
        if p.status == positions.REJECTED and p.guard_key:
            state.clear_auto_open(uid, p.guard_key)
        await _protect(client, p)
    return True


async def recover(client, uid: str) -> None:
    """Find accepted/unknown entries even when the registry was never written."""
    aid = account_id(client)
    candidates = {i.intent_key: i for i in
                  journal.unresolved(uid, account_id=aid) + journal.pending_projection(uid, aid)}
    # Repair a known missing trigger after restart/cancel, never an unknown submit.
    for p in positions.open_positions(uid):
        if p.account_id == aid and not p.gtt_id and not p.protection_pending:
            await _protect(client, p)
    if not candidates:
        return
    book = await client.get_orders()
    if not isinstance(book, list):
        raise ValueError("malformed_broker_order_book")
    for intent in candidates.values():
        if intent.state == "RESERVED":
            continue  # no send claim; only the originating validated request may claim
        matches = [o for o in book if isinstance(o, dict) and
                   ((intent.order_id and o.get("order_id") == intent.order_id)
                    or o.get("tag") == intent.tag)]
        if not matches and intent.order_id:
            history = await client.get_order_history(intent.order_id)
            matches = [history[-1]] if history and isinstance(history[-1], dict) else []
        if len(matches) == 1:
            await consume_order(client, uid, matches[0])
        else:
            state.log(uid, "order_failed", f"{intent.symbol}: broker entry unresolved; no resubmission")
