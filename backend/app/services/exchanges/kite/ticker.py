"""
KiteTicker — Zerodha live market-data WebSocket (binary protocol).

Connects to ``wss://ws.kite.trade`` and decodes the big-endian binary tick stream
into dicts. Modes: ltp / quote / full (full includes 5-level market depth + OI).

The binary frame is: ``[uint16 num_packets][ (uint16 len)(len bytes) ... ]``.
Per-packet field layout depends on the byte length (8 = LTP, 28/32 = index
quote/full, 44 = quote, 184 = full). Prices are integers scaled by a
segment-dependent divisor (see :func:`constants.price_divisor`).

The pure parsing functions (:func:`split_packets`, :func:`parse_binary`) are
isolated for unit testing against captured fixtures.
"""
from __future__ import annotations

import asyncio
import json
import struct
import time
from typing import Awaitable, Callable, Dict, List, Optional

from app.core.logging import get_logger

from . import constants as K

log = get_logger(__name__)

OnTicks = Callable[[List[dict]], Awaitable[None]]
OnOrderUpdate = Callable[[dict], Awaitable[None]]


# ─── Pure binary decoding ─────────────────────────────────────────────────────
def split_packets(data: bytes) -> List[bytes]:
    if len(data) < 2:
        return []
    num = struct.unpack(">H", data[0:2])[0]
    packets: List[bytes] = []
    j = 2
    for _ in range(num):
        if j + 2 > len(data):
            break
        plen = struct.unpack(">H", data[j:j + 2])[0]
        j += 2
        packets.append(data[j:j + plen])
        j += plen
    return packets


def _u32(b: bytes, o: int) -> int:
    return struct.unpack(">I", b[o:o + 4])[0]


def _i32(b: bytes, o: int) -> int:
    return struct.unpack(">i", b[o:o + 4])[0]


def _u16(b: bytes, o: int) -> int:
    return struct.unpack(">H", b[o:o + 2])[0]


def parse_packet(packet: bytes) -> dict:
    token = _u32(packet, 0)
    div = K.price_divisor(token)
    segment = token & 0xFF
    is_index = segment == K.SEGMENT_INDICES
    plen = len(packet)
    tick: dict = {"instrument_token": token, "tradable": not is_index, "mode": K.MODE_LTP}

    if plen == K.PACKET_LTP:
        tick["last_price"] = _u32(packet, 4) / div
        return tick

    if is_index and plen in (K.PACKET_INDEX_QUOTE, K.PACKET_INDEX_FULL):
        tick["mode"] = K.MODE_FULL if plen == K.PACKET_INDEX_FULL else K.MODE_QUOTE
        tick["last_price"] = _u32(packet, 4) / div
        tick["ohlc"] = {
            "high": _u32(packet, 8) / div, "low": _u32(packet, 12) / div,
            "open": _u32(packet, 16) / div, "close": _u32(packet, 20) / div,
        }
        tick["change"] = _i32(packet, 24) / div
        if plen == K.PACKET_INDEX_FULL:
            tick["exchange_timestamp"] = _u32(packet, 28)
        return tick

    if plen in (K.PACKET_QUOTE, K.PACKET_FULL):
        tick["mode"] = K.MODE_FULL if plen == K.PACKET_FULL else K.MODE_QUOTE
        tick["last_price"] = _u32(packet, 4) / div
        tick["last_traded_quantity"] = _u32(packet, 8)
        tick["average_traded_price"] = _u32(packet, 12) / div
        tick["volume_traded"] = _u32(packet, 16)
        tick["total_buy_quantity"] = _u32(packet, 20)
        tick["total_sell_quantity"] = _u32(packet, 24)
        tick["ohlc"] = {
            "open": _u32(packet, 28) / div, "high": _u32(packet, 32) / div,
            "low": _u32(packet, 36) / div, "close": _u32(packet, 40) / div,
        }
        if plen == K.PACKET_FULL:
            tick["last_trade_time"] = _u32(packet, 44)
            tick["oi"] = _u32(packet, 48)
            tick["oi_day_high"] = _u32(packet, 52)
            tick["oi_day_low"] = _u32(packet, 56)
            tick["exchange_timestamp"] = _u32(packet, 60)
            depth: Dict[str, list] = {"buy": [], "sell": []}
            for i in range(10):
                o = 64 + i * 12
                entry = {
                    "quantity": _u32(packet, o),
                    "price": _u32(packet, o + 4) / div,
                    "orders": _u16(packet, o + 8),
                }
                depth["buy" if i < 5 else "sell"].append(entry)
            tick["depth"] = depth
        return tick

    return tick  # unknown length — at least the token


def parse_binary(data: bytes) -> List[dict]:
    return [parse_packet(p) for p in split_packets(data) if len(p) >= K.PACKET_LTP]


# ─── Subscription message builders ────────────────────────────────────────────
def msg_subscribe(tokens: List[int]) -> str:
    return json.dumps({"a": "subscribe", "v": list(tokens)})


def msg_unsubscribe(tokens: List[int]) -> str:
    return json.dumps({"a": "unsubscribe", "v": list(tokens)})


def msg_mode(mode: str, tokens: List[int]) -> str:
    return json.dumps({"a": "mode", "v": [mode, list(tokens)]})


# ─── Connection manager ───────────────────────────────────────────────────────
class KiteTicker:
    def __init__(self, api_key: str, access_token: str, on_ticks: Optional[OnTicks] = None,
                 on_order_update: Optional[OnOrderUpdate] = None) -> None:
        self._api_key = api_key
        self._access_token = access_token
        self._on_ticks = on_ticks
        self._on_order_update = on_order_update
        self._subscribed: Dict[int, str] = {}   # token -> mode
        self._ticks: Dict[int, dict] = {}        # latest tick per token
        # When the last binary tick frame arrived. A connected socket that has
        # stopped delivering ticks looks identical to a healthy one without
        # this, and "connected" is not the question an operator is asking
        # before letting the system trade.
        self._last_tick_ms: int = 0
        self._active = False
        self._connected = False
        self._last_error: Optional[str] = None
        self._ws = None
        self._task: Optional[asyncio.Task] = None

    @property
    def is_active(self) -> bool:
        """Whether the stream is actually running.

        "Someone called ``start()``" is not the same as "the stream is running",
        and conflating them is how a dead feed stays dead. ``_active`` is only
        cleared by :meth:`stop` and by cancellation, so a ``_run()`` task that
        dies any other way leaves the flag set — and every caller then treats a
        ticker that will never produce another tick as healthy. Downstream, that
        showed up as every live price in the app quietly falling back to the
        30-second REST heartbeat while ``/ticker/status`` reported
        ``active: true``.
        """
        return self._active and self._task is not None and not self._task.done()

    @property
    def died(self) -> bool:
        """Started, never stopped, and yet not running. Restartable."""
        return self._active and self._task is not None and self._task.done()

    @property
    def last_error(self) -> Optional[str]:
        """Why the stream last dropped, so a caller can say more than "off"."""
        return self._last_error

    @property
    def connected(self) -> bool:
        return self._connected

    def ws_url(self) -> str:
        return f"{K.WS_URL}?api_key={self._api_key}&access_token={self._access_token}"

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._active = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._active = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self._connected = False

    async def subscribe(self, tokens: List[int], mode: str = K.MODE_QUOTE) -> None:
        for t in tokens:
            self._subscribed[int(t)] = mode
        if self._connected and self._ws is not None:
            await self._ws.send(msg_subscribe(tokens))
            await self._ws.send(msg_mode(mode, tokens))

    async def unsubscribe(self, tokens: List[int]) -> None:
        for t in tokens:
            self._subscribed.pop(int(t), None)
            self._ticks.pop(int(t), None)
        if self._connected and self._ws is not None:
            await self._ws.send(msg_unsubscribe(tokens))

    def snapshot(self) -> List[dict]:
        return list(self._ticks.values())

    def status(self) -> dict:
        return {
            # `active` now means running, not merely started. `started` keeps the
            # raw flag so a dead task is visible rather than indistinguishable
            # from a healthy one.
            "active": self.is_active,
            "started": self._active,
            "died": self.died,
            "last_error": self._last_error,
            "connected": self._connected,
            "subscribed": sorted(self._subscribed.keys()),
            "tick_count": len(self._ticks),
            # 0 means no tick has ever arrived on this socket — not "just now".
            "last_tick_ms": self._last_tick_ms,
        }

    async def _resubscribe_all(self) -> None:
        # group tokens by mode and (re)subscribe after a (re)connect
        by_mode: Dict[str, List[int]] = {}
        for tok, mode in self._subscribed.items():
            by_mode.setdefault(mode, []).append(tok)
        for mode, toks in by_mode.items():
            await self._ws.send(msg_subscribe(toks))
            await self._ws.send(msg_mode(mode, toks))

    async def _run(self) -> None:
        import websockets  # local import — only when WS is active
        delay = 3.0
        while self._active:
            try:
                async with websockets.connect(
                    self.ws_url(), ping_interval=20, ping_timeout=10,
                    close_timeout=5, open_timeout=10, max_size=None,
                ) as ws:
                    self._ws = ws
                    self._connected = True
                    delay = 3.0
                    log.info("KiteTicker connected (%d instruments)", len(self._subscribed))
                    if self._subscribed:
                        await self._resubscribe_all()
                    async for raw in ws:
                        await self._on_message(raw)
            except asyncio.CancelledError:
                self._connected = False
                self._active = False
                raise
            except Exception as exc:
                self._connected = False
                if not self._active:
                    break
                # WARNING, not debug. Every live price in the app degrades to the
                # slow REST heartbeat while this is down, and at debug level that
                # happened invisibly.
                self._last_error = f"{type(exc).__name__}: {exc}"[:200]
                log.warning("KiteTicker disconnected (%s) — reconnecting in %.0fs", exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)
        self._connected = False

    async def _on_message(self, raw) -> None:
        if isinstance(raw, (bytes, bytearray)):
            if len(raw) <= 1:   # 1-byte heartbeat
                return
            ticks = parse_binary(bytes(raw))
            for t in ticks:
                self._ticks[t["instrument_token"]] = t
            if ticks:
                self._last_tick_ms = int(time.time() * 1000)
            if ticks and self._on_ticks:
                try:
                    await self._on_ticks(ticks)
                except Exception as exc:
                    log.debug("KiteTicker on_ticks callback error: %s", exc)
            return
        # Text frames carry order/trade postbacks + server messages. Kite sends
        # ``{"type": "order", "data": {...}}`` on every order-state change — fan it
        # out so the UI updates fills/cancels live (no second public socket).
        await self._handle_text_frame(raw)

    async def _handle_text_frame(self, raw) -> None:
        if not self._on_order_update:
            return
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        if isinstance(msg, dict) and msg.get("type") == "order" and isinstance(msg.get("data"), dict):
            try:
                await self._on_order_update(msg["data"])
            except Exception as exc:
                log.debug("KiteTicker on_order_update callback error: %s", exc)
