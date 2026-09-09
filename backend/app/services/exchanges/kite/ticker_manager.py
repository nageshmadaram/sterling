"""
Per-user KiteTicker lifecycle + fan-out to the app WebSocket.

Each user gets at most one live :class:`KiteTicker` (built from their active,
connected Kite account). Decoded ticks are broadcast to the StreamManager channel
``kite_ticks:{user_id}`` so the frontend consumes them over the existing
``/api/v1/stream/ws`` socket — no second public socket to operate.
"""
from __future__ import annotations

import asyncio

from typing import Dict, List, Optional, Set, Tuple

from app.core.logging import get_logger

from . import accounts as _accounts
from . import constants as K
from .ticker import KiteTicker

log = get_logger(__name__)

_tickers: Dict[str, KiteTicker] = {}

# Which components asked for a token. There is exactly one subscription set per
# account, so a component that has finished with a token cannot just unsubscribe
# it -- the operator may have its chart open, or the protection monitor may be
# watching it for a stop. This registry lets release() tell "nobody wants this
# any more" from "someone else still does".
#
# _ANY records a subscription that arrived without an owner tag: the operator's
# UI, the protection monitor. It is never removed by release(), because a caller
# that did not claim ownership cannot be assumed to be finished, and starving the
# protection monitor of ticks would leave a real stop unguarded.
_ANY = "*"
_owners: Dict[Tuple[str, int], Set[str]] = {}


async def _warm_client(user_id: str):
    """Best-effort warm client for the user's active account (for monitor exits)."""
    try:
        acct = _accounts.get_active(user_id)
        if acct and acct.connected:
            return await _accounts.acquire_client(acct)
    except Exception as exc:  # noqa: BLE001
        log.debug("kite monitor client acquire failed for %s: %s", user_id, exc)
    return None


def _make_broadcaster(user_id: str):
    async def _broadcast(ticks: List[dict]) -> None:
        # Feed the tick-driven exit monitor FIRST (C/D) — a trail breach must
        # trigger a server-side exit regardless of whether any UI is listening.
        try:
            from app.services.kite_engine import monitor, positions
            if positions.open_positions(user_id):
                client = await _warm_client(user_id)
                if client is not None:
                    await monitor.on_ticks(user_id, ticks, client=client)
        except Exception as exc:  # never let the monitor kill the tick loop
            log.debug("kite monitor on_ticks failed for %s: %s", user_id, exc)
        # Then the ATM Premium Imbalance session, for the same reason: it enters
        # and exits on ticks, and must not depend on a UI being connected.
        try:
            from app.services import atm_premium_imbalance_runner as api_runner
            session = api_runner.active_session(user_id)
            if session is not None and not session.finished:
                client = await _warm_client(user_id)
                if client is not None:
                    await api_runner.on_ticks(
                        user_id, ticks, api_runner.KiteBrokerPort(client, session.pair)
                    )
        except Exception as exc:  # never let this kill the tick loop
            log.debug("ATM PI on_ticks failed for %s: %s", user_id, exc)
        # Then Adaptive Edge, for the same reason: its stop and trail are
        # enforced on ticks, and a protective stop that only runs while a UI is
        # open is not a protective stop.
        try:
            from app.services import adaptive_edge_positions as ae_positions
            from app.services import adaptive_edge_runner as ae_runner
            if ae_positions.open_positions(user_id):
                await ae_runner.on_ticks(user_id, ticks)
        except Exception as exc:  # never let this kill the tick loop
            log.debug("Adaptive Edge on_ticks failed for %s: %s", user_id, exc)
        # Then OI Wall Flow: premium stop and opposing-wall invalidation both
        # fire on ticks, and a protective stop that only runs while a UI is
        # open is not a protective stop.
        try:
            from app.services import oi_wall_flow_positions as owf_positions
            from app.services import oi_wall_flow_runner as owf_runner
            if owf_positions.open_positions(user_id):
                await owf_runner.on_ticks(user_id, ticks)
        except Exception as exc:  # never let this kill the tick loop
            log.debug("OI Wall Flow on_ticks failed for %s: %s", user_id, exc)
        # Then Gamma Move: trailing stop and market-exit triggers both fire on
        # ticks, and must not depend on a UI being connected.
        try:
            from app.services import gamma_move_positions as gm_positions
            from app.services import gamma_move_runner as gm_runner
            if gm_positions.open_positions(user_id):
                await gm_runner.on_ticks(user_id, ticks)
        except Exception as exc:  # never let this kill the tick loop
            log.debug("Gamma Move on_ticks failed for %s: %s", user_id, exc)
        try:
            from app.api.v1.endpoints.stream import stream_manager
            await stream_manager.broadcast_to_channel(
                f"kite_ticks:{user_id}",
                {"type": "kite_ticks", "ticks": ticks},
            )
        except Exception as exc:  # never let a broadcast error kill the tick loop
            log.debug("kite tick broadcast failed for %s: %s", user_id, exc)
    return _broadcast


def _make_order_broadcaster(user_id: str, *, client=None):
    async def _broadcast(order: dict) -> None:
        # Confirm fills / rejections against our position registry FIRST (E).
        # The client is passed because a REJECTED entry has to cancel the protective
        # GTT that was already armed for it — without a client the handler can only
        # log that an orphaned SELL is resting at Zerodha.
        try:
            from app.services.kite_engine import monitor
            await monitor.on_order_update(user_id, order, client=client or await _warm_client(user_id))
        except Exception as exc:  # never let the monitor kill the WS loop
            log.debug("kite monitor on_order_update failed for %s: %s", user_id, exc)
        try:
            from app.api.v1.endpoints.stream import stream_manager
            await stream_manager.broadcast_to_channel(
                f"kite_orders:{user_id}",
                {"type": "kite_order_update", "order": order},
            )
        except Exception as exc:  # never let a broadcast error kill the WS loop
            log.debug("kite order broadcast failed for %s: %s", user_id, exc)
    return _broadcast


async def broadcast_order_update(user_id: str, order: dict, *, client=None) -> None:
    """Push a single order update to the user's ``kite_orders`` channel.

    Used by both the live WS postback (text frames) and the HTTP postback webhook.
    """
    await _make_order_broadcaster(user_id, client=client)(order)


async def ensure(user_id: str) -> Optional[KiteTicker]:
    """Return a started ticker for the user's active connected account, or None."""
    existing = _tickers.get(user_id)
    if existing and existing.is_active:
        return existing

    acct = _accounts.get_active(user_id)
    if not acct or not acct.connected or acct.is_paper:
        return None

    if existing is not None:
        # The ticker exists but is not running -- its `_run()` task finished
        # without `stop()` being called.
        #
        # Restart THIS one rather than building a fresh ticker. `start()` is
        # already safe to call on a finished task (it guards on the task, not on
        # the flag), and the existing object still holds `_subscribed`, which
        # `_resubscribe_all()` replays the moment it reconnects. A new ticker
        # would come up subscribed to nothing and every caller that had already
        # registered its tokens would sit there waiting for ticks that were never
        # coming.
        #
        # Before the liveness fix in ticker.py this branch was unreachable: a
        # dead task left `is_active` True, so this function returned the corpse
        # and the whole app silently ran on the 30-second REST heartbeat.
        log.warning(
            "KiteTicker for %s was not running (last error: %s) — restarting with %d instruments",
            user_id, existing.last_error or "none recorded", len(existing.status().get("subscribed") or []),
        )
        await existing.start()
        return existing
    ticker = KiteTicker(
        api_key=acct.api_key, access_token=acct.access_token,
        on_ticks=_make_broadcaster(user_id),
        on_order_update=_make_order_broadcaster(user_id, client=await _accounts.acquire_client(acct)),
    )
    _tickers[user_id] = ticker
    await ticker.start()
    return ticker


async def subscribe(user_id: str, tokens: List[int], mode: str = K.MODE_QUOTE,
                    owner: Optional[str] = None) -> dict:
    """Subscribe tokens, optionally claiming them for ``owner``.

    Passing ``owner`` opts into refcounted cleanup via :func:`release`. Callers
    that omit it keep the historical behaviour: the subscription lives until
    something unsubscribes it explicitly or the ticker restarts.
    """
    ticker = await ensure(user_id)
    if not ticker:
        return {"ok": False, "message": "No connected (live) Kite account — log in first."}
    await ticker.subscribe(tokens, mode)
    tag = owner or _ANY
    for t in tokens:
        _owners.setdefault((user_id, int(t)), set()).add(tag)
    return {"ok": True, **ticker.status()}


async def unsubscribe(user_id: str, tokens: List[int], *, force: bool = False) -> dict:
    """Stop streaming these tokens.

    The caller is normally a client tidying up its own view -- the frontend
    reconciler dropping a chart nobody is looking at. That must not blind a
    running strategy that is watching the same token for an exit, so a token a
    named owner still claims is kept, and only the caller's own untagged claim
    goes.

    ``force`` is the operator's override: drop it regardless of who wants it.
    """
    kept: List[int] = []
    drop: List[int] = []
    for t in tokens:
        key = (user_id, int(t))
        claimed = {o for o in _owners.get(key, set()) if o != _ANY}
        if claimed and not force:
            _owners[key].discard(_ANY)          # the caller is done, the owner is not
            kept.append(int(t))
            continue
        _owners.pop(key, None)
        drop.append(int(t))

    ticker = _tickers.get(user_id)
    if not ticker:
        return {"ok": True, "message": "No active ticker"}
    if drop:
        await ticker.unsubscribe(drop)
    out = {"ok": True, **ticker.status()}
    if kept:
        # Say so rather than reporting a clean success: a caller that asked for a
        # token to go and was refused should be able to see that in the reply.
        out["kept_for_owners"] = {str(t): sorted(_owners.get((user_id, t), set()))
                                  for t in kept}
    return out


async def release(user_id: str, tokens: List[int], owner: str) -> dict:
    """Drop ``owner``'s claim, unsubscribing only what nobody else wants.

    A token is unsubscribed only when ``owner`` was its last remaining claimant.
    A token that any untagged caller subscribed is never unsubscribed here, and a
    token this registry has never seen is left alone: not knowing who wants a
    token is a reason to keep it, not to drop it.
    """
    drop: List[int] = []
    for t in tokens:
        key = (user_id, int(t))
        owners = _owners.get(key)
        if owners is None:
            continue
        owners.discard(owner)
        if not owners:
            drop.append(int(t))
    if not drop:
        return {"ok": True, "unsubscribed": []}
    # The claims for these tokens are already gone, so force is not overriding
    # anybody -- it just stops unsubscribe() from re-deciding what we decided.
    result = await unsubscribe(user_id, drop, force=True)
    return {**result, "unsubscribed": drop}


def owners_of(user_id: str, token: int) -> Set[str]:
    """Test/diagnostic hook: who currently claims this token."""
    return set(_owners.get((user_id, int(token)), set()))


async def stop(user_id: str) -> dict:
    ticker = _tickers.pop(user_id, None)
    # The ticker's own subscription set dies with it, so the claims must go too.
    for key in [k for k in _owners if k[0] == user_id]:
        _owners.pop(key, None)
    if ticker:
        await ticker.stop()
    return {"ok": True}


def status(user_id: str) -> dict:
    ticker = _tickers.get(user_id)
    if not ticker:
        return {"active": False, "connected": False, "subscribed": [], "tick_count": 0}
    return ticker.status()


async def supervise(interval: float = 30.0) -> None:
    """Restart any ticker whose stream has died.

    :func:`ensure` already revives a dead ticker, but only when something calls
    it — and its only caller is a subscribe, which the frontend issues when its
    token set CHANGES. A stream that died mid-session therefore stayed dead until
    somebody reloaded the page, and that is precisely the case that matters: an
    operator watching a board of live prices has no reason to touch anything, so
    nothing triggers the repair. Meanwhile every price falls back to the
    30-second REST heartbeat and still looks alive.

    Only a ``died`` ticker is touched — started, never stopped, not running. A
    ticker someone deliberately stopped has ``_active`` cleared and is therefore
    not ``died``, so this never fights an operator who shut the feed off.
    """
    while True:
        try:
            for uid, ticker in list(_tickers.items()):
                if not ticker.died:
                    continue
                log.warning(
                    "Ticker watchdog: %s stream had died (%s) — restarting",
                    uid, ticker.last_error or "no error recorded",
                )
                await ensure(uid)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A watchdog that dies on a bad iteration is worse than no watchdog,
            # because the thing it was watching is now unwatched AND silent.
            log.exception("Ticker watchdog iteration failed — continuing")
        await asyncio.sleep(interval)


async def stop_all() -> None:
    for uid in list(_tickers.keys()):
        await stop(uid)


def clear() -> None:
    """Test hook — drop references without awaiting (no live sockets in tests)."""
    _tickers.clear()
    _owners.clear()
