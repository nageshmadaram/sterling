"""
KiteClient — Zerodha Kite Connect v3 REST adapter (full order surface).

Implements the :class:`TradingExchangeAdapter` contract (orders + cancel +
product resolution) on top of the read-only market-data/account surface that the
legacy ``ZerodhaAdapter`` shipped. httpx-based, async, form-encoded writes (Kite
orders/GTT are ``application/x-www-form-urlencoded``; margin calculators are JSON).

Auth:  Authorization: token {api_key}:{access_token} + X-Kite-Version: 3
Login: see :mod:`session` — daily request_token → access_token handshake.

Backwards-compat: re-exported as ``ZerodhaAdapter`` from the legacy adapter path.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple, Union

import httpx

from app.core.logging import get_logger
from app.schemas.account import (
    AccountFill, AccountOrder, AccountPosition, AssetBalance, PortfolioSnapshot,
)
from app.schemas.instruments import InstrumentMeta
from app.schemas.market import Candle, OptionSummary
from app.services.exchanges.trading_base import TradingExchangeAdapter

from . import constants as K
from . import session as _session
from .errors import KiteError, KiteOrderError, KiteTokenError, is_retryable, raise_for_kite
from .instruments import InstrumentCache

log = get_logger(__name__)

# Re-exported for backwards compat with the legacy adapter module.
_RESOLUTION_MAP = K.RESOLUTION_MAP
_INDIA_VIX_TOKEN = K.INDIA_VIX_TOKEN

_IST = timezone(timedelta(hours=5, minutes=30))


def _historical_days_needed(interval: str, n_bars: int) -> int:
    """Calendar days of history so ``n_bars`` of this interval can exist.

    A 1H heuristic (``n_bars / 6``) requested 205 days of 5-minute data for a
    15-session lookback and made the ORB universe scan unusable. Size the
    window from the interval; add a weekend/holiday pad.
    """
    minutes = {
        "minute": 1, "3minute": 3, "5minute": 5, "10minute": 10,
        "15minute": 15, "30minute": 30, "60minute": 60, "day": 24 * 60,
    }.get(interval, 60)
    session_minutes = 6 * 60 + 15
    per_day = 1 if minutes >= 24 * 60 else max(1, session_minutes // minutes)
    return max(2, int(n_bars / per_day) + 8)


def _parse_kite_ts(ts_str) -> int:
    """Parse a Kite timestamp ('2024-01-15 09:15:00' / '...+0530') → epoch ms."""
    if not ts_str:
        return int(time.time() * 1000)
    s = str(ts_str)
    try:
        dt = datetime.fromisoformat(s.replace("+0530", "+05:30"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_IST)
        return int(dt.timestamp() * 1000)
    except Exception as _exc:
        log.debug("suppressed: %s", _exc)
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_IST)
        return int(dt.timestamp() * 1000)
    except Exception:
        return int(time.time() * 1000)


def _aggregate_4h(candles_1h: List[Candle]) -> List[Candle]:
    """Group 1H candles into 4H buckets."""
    result: List[Candle] = []
    buf: List[Candle] = []
    for c in candles_1h:
        buf.append(c)
        if len(buf) == 4:
            result.append(Candle(
                timestamp_ms=buf[0].timestamp_ms,
                open=buf[0].open,
                high=max(x.high for x in buf),
                low=min(x.low for x in buf),
                close=buf[-1].close,
                volume=sum(x.volume for x in buf),
            ))
            buf = []
class AsyncOrderRateLimiter:
    """Token-bucket rate limiter enforcing Zerodha Kite's 3.0 req/sec limit across concurrent order submissions."""

    def __init__(self, rate: float = 3.0, capacity: float = 3.0) -> None:
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = max(0.0, now - self.last_update)
            self.last_update = now
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            if self.tokens < 1.0:
                wait_time = (1.0 - self.tokens) / self.rate
                await asyncio.sleep(wait_time)
                self.tokens = 0.0
                self.last_update = time.monotonic()
            else:
                self.tokens -= 1.0


_GLOBAL_KITE_ORDER_LIMITER = AsyncOrderRateLimiter(rate=3.0, capacity=3.0)


class KiteClient(TradingExchangeAdapter):
    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        access_token: str = "",
        is_paper: bool = True,
        base_url: str = K.BASE_URL,
        timeout: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._access_token = access_token
        self._is_paper = is_paper
        self._base = base_url
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        self._instruments = InstrumentCache(self._fetch_instruments_csv)
        self._mf_instruments = InstrumentCache(self._fetch_mf_instruments_csv)
        # WS price cache placeholder (live ticks flow via ticker_manager, not here)
        self._ws_prices: Dict[int, float] = {}

    # ─── HTTP plumbing ──────────────────────────────────────────────────────
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base,
                timeout=self._timeout,
                limits=httpx.Limits(max_keepalive_connections=50, max_connections=100, keepalive_expiry=120.0),
                headers={"X-Kite-Version": K.KITE_VERSION, "User-Agent": K.USER_AGENT},
            )
        return self._client

    def _auth_headers(self) -> dict:
        return {"Authorization": f"token {self._api_key}:{self._access_token}"}

    @property
    def access_token(self) -> str:
        return self._access_token

    def _handle(self, resp: httpx.Response):
        """Unwrap Kite's ``{status,data}`` envelope; raise a typed KiteError on failure."""
        try:
            body = resp.json()
        except Exception as exc:
            if not resp.is_success:
                raise_for_kite(f"HTTP {resp.status_code}: {resp.text[:200]}", "", resp.status_code)
            raise KiteError(
                f"Non-JSON response from {resp.request.url}",
                status_code=resp.status_code,
            ) from exc
        if isinstance(body, dict) and body.get("status") == "error":
            raise_for_kite(body.get("message", ""), body.get("error_type", ""), resp.status_code,
                           data=body.get("data") if isinstance(body.get("data"), dict) else None)
        if not resp.is_success:
            msg = body.get("message") if isinstance(body, dict) else str(body)
            etype = body.get("error_type", "") if isinstance(body, dict) else ""
            raise_for_kite(msg or f"HTTP {resp.status_code}", etype, resp.status_code)
        return body.get("data", body) if isinstance(body, dict) else body

    def _allow_sessionless_read(self) -> None:
        """Gate every read that answers with a stub when there is no ``access_token``.

        Paper mode has no session by design, so an empty list is the honest answer. A
        LIVE client with no token is a different thing entirely — the session expired or
        was never established — and the truthful answer is "I cannot tell you", not
        "empty".

        The difference is load-bearing. ``protective_stop.stop_status`` reads an empty
        GTT list plus an empty order book as positive evidence that NOTHING is protecting
        a position, and on that evidence places its own SELL. An expired token that
        answered "[]" instead of raising would manufacture exactly that evidence and go
        naked short on top of a live broker stop. Sizing has the same shape: an empty
        balance stub is fabricated capital to size against.
        """
        if not self._is_paper:
            raise KiteTokenError(
                "Kite session missing or expired (no access_token) — reconnect before "
                "reading live account state.",
                error_type="TokenException",
            )

    def _require_session(self) -> None:
        # Paper mode only simulates ORDER PLACEMENT — reads still use the real
        # session when a token is present, so a connected paper account shows its
        # real portfolio/orders/funds.
        if not self._api_key or not self._access_token:
            raise KiteTokenError(
                "Kite account access requires api_key + access_token — log in first.",
                error_type="TokenException",
            )

    async def _auth_request(
        self, method: str, path: str, *,
        params: Union[dict, list, None] = None,
        data: Optional[dict] = None,
        json_body=None,
    ):
        self._require_session()
        client = await self._get_client()
        headers = dict(self._auth_headers())
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            resp = await client.request(method, path, params=params or {}, json=json_body, headers=headers)
        else:
            resp = await client.request(method, path, params=params or {}, data=data, headers=headers)
        return self._handle(resp)

    async def _auth_get(self, path: str, params: Union[dict, list, None] = None):
        return await self._auth_request("GET", path, params=params)

    async def _auth_post(self, path: str, body: dict):
        return await self._auth_request("POST", path, data=body)

    async def _auth_put(self, path: str, body: dict):
        return await self._auth_request("PUT", path, data=body)

    async def _auth_delete(self, path: str, params: Union[dict, None] = None):
        return await self._auth_request("DELETE", path, params=params)

    async def _auth_post_json(self, path: str, json_body):
        return await self._auth_request("POST", path, json_body=json_body)

    # ─── Session / login ────────────────────────────────────────────────────
    def login_url(self) -> str:
        return _session.login_url(self._api_key)

    async def generate_session(self, request_token: str) -> dict:
        """Exchange a request_token for an access_token (and capture the profile)."""
        if not self._api_key or not self._api_secret:
            raise KiteError("api_key and api_secret are required to generate a session.")
        cs = _session.checksum(self._api_key, request_token, self._api_secret)
        client = await self._get_client()
        resp = await client.post(
            "/session/token",
            data={"api_key": self._api_key, "request_token": request_token, "checksum": cs},
        )
        data = self._handle(resp)
        self._access_token = (data or {}).get("access_token", "") if isinstance(data, dict) else ""
        return data or {}

    async def invalidate_session(self) -> bool:
        if not self._access_token:
            return True
        try:
            client = await self._get_client()
            resp = await client.delete(
                "/session/token",
                params={"api_key": self._api_key, "access_token": self._access_token},
                headers=self._auth_headers(),
            )
            self._handle(resp)
        except Exception as exc:
            log.debug("Kite logout (best-effort) failed: %s", exc)
        self._access_token = ""
        return True

    async def renew_access_token(self, refresh_token: str) -> dict:
        """Exchange a refresh_token for a fresh access_token (no re-login).

        Same checksum scheme as ``generate_session`` —
        ``sha256(api_key + token + api_secret)`` — against ``/session/refresh_token``.
        """
        if not self._api_key or not self._api_secret:
            raise KiteError("api_key and api_secret are required to refresh a session.")
        cs = _session.checksum(self._api_key, refresh_token, self._api_secret)
        client = await self._get_client()
        resp = await client.post(
            "/session/refresh_token",
            data={"api_key": self._api_key, "refresh_token": refresh_token, "checksum": cs},
        )
        data = self._handle(resp)
        self._access_token = (data or {}).get("access_token", "") if isinstance(data, dict) else ""
        return data or {}

    # ─── Instruments ────────────────────────────────────────────────────────
    async def _fetch_instruments_csv(self, exchange: str) -> str:
        client = await self._get_client()
        path = f"/instruments/{exchange}" if exchange else "/instruments"
        headers = self._auth_headers() if self._access_token else {}
        resp = await client.get(path, headers=headers)
        resp.raise_for_status()
        return resp.text

    async def _fetch_mf_instruments_csv(self, exchange: str = "") -> str:
        """Fetch the mutual-fund scheme master (``/mf/instruments``). The
        ``exchange`` arg is ignored — MF has a single, exchange-less dump — so the
        shared :class:`InstrumentCache` can drive it like the equity master."""
        client = await self._get_client()
        headers = self._auth_headers() if self._access_token else {}
        resp = await client.get("/mf/instruments", headers=headers)
        resp.raise_for_status()
        return resp.text

    async def search_instruments(self, query: str, exchange: str = "", limit: int = 50) -> List[dict]:
        """exchange="" → universal search across the full instruments dump."""
        return await self._instruments.search(query, exchange, limit)

    async def instrument_lot_sizes(self, symbols: list) -> dict:
        """EXCHANGE:TRADINGSYMBOL → lot_size for a batch (found instruments only)."""
        return await self._instruments.lot_sizes(symbols)

    async def instrument_expiries(self, symbols: list) -> dict:
        """EXCHANGE:TRADINGSYMBOL → expiry (YYYY-MM-DD) for a batch (dated F&O only)."""
        return await self._instruments.expiries(symbols)

    async def search_mf_instruments(self, query: str = "", limit: int = 50) -> List[dict]:
        """Search the mutual-fund scheme master by tradingsymbol/name/AMC."""
        return await self._mf_instruments.search(query, "", limit)

    async def resolve_token(self, tradingsymbol: str, exchange: str = K.EXCHANGE_NFO) -> int:
        return await self._instruments.resolve_token(tradingsymbol, exchange)

    @staticmethod
    def _split_symbol(symbol: str, exchange: Optional[str] = None) -> Tuple[str, str]:
        if ":" in symbol:
            ex, _, ts = symbol.partition(":")
            return ex.upper(), ts
        return (exchange or K.EXCHANGE_NFO).upper(), symbol

    async def get_product_id(self, symbol: str) -> int:
        """Resolve symbol → instrument_token (int).

        NOTE: for Kite this is *informational* — orders are placed by
        tradingsymbol+exchange, not by a numeric product id (unlike Delta).
        Returns 0 if the symbol can't be resolved rather than raising, so a
        partial instruments cache never blocks order flow.
        """
        exchange, tradingsymbol = self._split_symbol(symbol)
        try:
            return await self._instruments.resolve_token(tradingsymbol, exchange)
        except Exception:
            return 0

    # ─── Order placement (TradingExchangeAdapter) ─────────────────────────────
    async def _place(
        self, *, variety: str, exchange: str, tradingsymbol: str,
        transaction_type: str, quantity: int, product: str, order_type: str,
        price: Optional[float] = None, trigger_price: Optional[float] = None,
        validity: str = K.VALIDITY_DAY, disclosed_quantity: Optional[int] = None,
        validity_ttl: Optional[int] = None, iceberg_legs: Optional[int] = None,
        iceberg_quantity: Optional[int] = None, tag: Optional[str] = None,
        market_protection: float = -1, allow_amo: bool = True,
    ) -> dict:
        if self._is_paper:
            return {"order_id": "PAPER-" + uuid.uuid4().hex[:12], "paper": True}
        body: dict = {
            "exchange": exchange,
            "tradingsymbol": tradingsymbol,
            "transaction_type": transaction_type,
            "quantity": int(quantity),
            "product": product,
            "order_type": order_type,
            "validity": validity,
        }
        if price is not None and order_type in (K.ORDER_TYPE_LIMIT, K.ORDER_TYPE_SL):
            body["price"] = price
        if trigger_price is not None and order_type in (K.ORDER_TYPE_SL, K.ORDER_TYPE_SLM):
            body["trigger_price"] = trigger_price
        if disclosed_quantity is not None:
            body["disclosed_quantity"] = int(disclosed_quantity)
        if validity == K.VALIDITY_TTL and validity_ttl:
            body["validity_ttl"] = int(validity_ttl)
        if iceberg_legs:
            body["iceberg_legs"] = int(iceberg_legs)
        if iceberg_quantity:
            body["iceberg_quantity"] = int(iceberg_quantity)
        if tag:
            body["tag"] = str(tag)[:20]
        if order_type in (K.ORDER_TYPE_MARKET, K.ORDER_TYPE_SLM):
            from math import isfinite
            protection = float(market_protection)
            if not isfinite(protection) or not (protection == -1 or 0 < protection <= 100):
                raise KiteOrderError("Market orders require valid price protection.", error_type="InputException")
            body["market_protection"] = protection

        for attempt in range(3):
            await _GLOBAL_KITE_ORDER_LIMITER.acquire()
            try:
                return await self._auth_post(f"/orders/{variety}", body)
            except KiteError as exc:
                # Markets closed: Zerodha rejects a regular order with the hint
                # `switch_to_amo`. Mirror the Kite web app — auto-resubmit the same
                # order as an After-Market Order (queues for the next session open)
                # instead of failing the click. Flag the result so the UI can say so.
                if allow_amo and variety == K.VARIETY_REGULAR and "switch_to_amo" in exc.hints:
                    await _GLOBAL_KITE_ORDER_LIMITER.acquire()
                    result = await self._auth_post(f"/orders/{K.VARIETY_AMO}", body)
                    if isinstance(result, dict):
                        result["amo"] = True
                    return result
                # 429 Rate Limit retry
                if getattr(exc, "status_code", None) == 429:
                    if attempt < 2:
                        backoff = 0.35 * (2 ** attempt)
                        log.warning("Kite order rate limit hit (429), retrying in %.2fs (attempt %d/3)", backoff, attempt + 1)
                        await asyncio.sleep(backoff)
                        continue
                raise

    async def place_order(
        self, symbol: str, side: str, size: float,
        order_type: str = "market_order", limit_price: Optional[float] = None,
        time_in_force: str = "day", post_only: bool = False, reduce_only: bool = False,
        stop_loss: Optional[float] = None, take_profit: Optional[float] = None,
        trail_amount: Optional[float] = None, **kwargs,
    ) -> dict:
        """Generic TradingExchangeAdapter entry. Places the ENTRY leg only;
        Kite has no per-order bracket, so stop_loss/take_profit are honored only
        when an explicit ``kite_order_type`` (SL/SL-M) + trigger is supplied —
        otherwise wire protection via GTT. ``post_only`` is unsupported by Kite."""
        # Dropping this parameter let `post_only=True` fall into **kwargs and be
        # SILENTLY IGNORED: a caller asking for maker-only got an ordinary order
        # that can cross the spread and pay taker fees. Kite has no post-only, so
        # refusing is the only honest answer.
        if post_only:
            raise KiteOrderError("post_only (maker-only) is not supported by Kite.", error_type="OrderException")
        exchange, tradingsymbol = self._split_symbol(symbol, kwargs.get("exchange"))
        txn = K.TXN_BUY if str(side).lower() in ("buy", "long") else K.TXN_SELL
        kite_ot = kwargs.get("kite_order_type")
        if kite_ot is None:
            kite_ot = K.ORDER_TYPE_LIMIT if str(order_type).lower().startswith("limit") else K.ORDER_TYPE_MARKET
        trigger_price = kwargs.get("trigger_price", stop_loss)
        validity = K.VALIDITY_IOC if str(time_in_force).lower() == "ioc" else K.VALIDITY_DAY
        return await self._place(
            variety=kwargs.get("variety", K.VARIETY_REGULAR),
            exchange=exchange, tradingsymbol=tradingsymbol, transaction_type=txn,
            quantity=int(size), product=kwargs.get("product", K.PRODUCT_MIS),
            order_type=kite_ot,
            price=limit_price if kite_ot in (K.ORDER_TYPE_LIMIT, K.ORDER_TYPE_SL) else None,
            trigger_price=trigger_price if kite_ot in (K.ORDER_TYPE_SL, K.ORDER_TYPE_SLM) else None,
            validity=validity, tag=kwargs.get("tag"),
            market_protection=kwargs.get("market_protection", -1),
            allow_amo=bool(kwargs.get("allow_amo", True)),
        )

    async def place_order_option(
        self, option_symbol: str, side: str, size: float,
        order_type: str = "market_order", limit_price: Optional[float] = None,
        stop_loss: Optional[float] = None, take_profit: Optional[float] = None,
        exchange: str = K.EXCHANGE_NFO, tag: Optional[str] = None,
    ) -> dict:
        """Place an option order. ``exchange`` is NFO for NSE-segment options
        (NIFTY/BANKNIFTY/FINNIFTY + equity options) or BFO for SENSEX/BSE options."""
        return await self.place_order(
            option_symbol, side, size, order_type=order_type, limit_price=limit_price,
            exchange=exchange, product=K.PRODUCT_NRML, stop_loss=stop_loss, tag=tag, allow_amo=False,
        )

    async def place_order_future(
        self, tradingsymbol: str, side: str, size: float,
        order_type: str = "market_order", limit_price: Optional[float] = None,
        exchange: str = K.EXCHANGE_NFO, tag: Optional[str] = None,
    ) -> dict:
        """Place a futures order (BUY or SELL). Two-sided: directional mode
        opens with BUY (long) or SELL (short) and exits with the opposite.
        Uses NRML product for overnight carry."""
        return await self.place_order(
            tradingsymbol, side, size, order_type=order_type, limit_price=limit_price,
            exchange=exchange, product=K.PRODUCT_NRML, tag=tag, allow_amo=False,
        )

    async def cancel_order(self, order_id: str, product_id: int = 0, variety: str = K.VARIETY_REGULAR) -> dict:
        """Cancel an order. ``product_id`` is unused for Kite (kept to satisfy the
        TradingExchangeAdapter contract); cancellation needs ``variety``+order_id."""
        if self._is_paper:
            return {"order_id": str(order_id)}
        return await self._auth_delete(f"/orders/{variety}/{order_id}")

    async def modify_order(self, order_id: str, variety: str = K.VARIETY_REGULAR, **fields) -> dict:
        if self._is_paper:
            return {"order_id": str(order_id)}
        body = {k: v for k, v in fields.items() if v is not None}
        return await self._auth_put(f"/orders/{variety}/{order_id}", body)

    # ─── Orders / trades (reads) ──────────────────────────────────────────────
    async def get_orders(self) -> list:
        if not self._access_token:
            self._allow_sessionless_read()
            return []
        return await self._auth_get("/orders") or []

    async def get_trades(self) -> list:
        """Full tradebook for the day (raw)."""
        if not self._access_token:
            self._allow_sessionless_read()
            return []
        return await self._auth_get("/trades") or []

    async def get_order_history(self, order_id: str) -> list:
        return await self._auth_get(f"/orders/{order_id}") or []

    async def get_order_trades(self, order_id: str) -> list:
        return await self._auth_get(f"/orders/{order_id}/trades") or []

    # ─── GTT ──────────────────────────────────────────────────────────────────
    async def get_gtts(self) -> list:
        if not self._access_token:
            self._allow_sessionless_read()
            return []
        return await self._auth_get("/gtt/triggers") or []

    async def get_gtt(self, trigger_id: int) -> dict:
        return await self._auth_get(f"/gtt/triggers/{trigger_id}") or {}

    @staticmethod
    def _gtt_body(trigger_type, tradingsymbol, exchange, last_price, trigger_values, orders) -> dict:
        condition = {
            "exchange": exchange,
            "tradingsymbol": tradingsymbol,
            "trigger_values": list(trigger_values),
            "last_price": last_price,
        }
        return {
            "type": trigger_type,
            "condition": json.dumps(condition),
            "orders": json.dumps(list(orders)),
        }

    async def place_gtt(self, *, trigger_type, tradingsymbol, exchange, last_price, trigger_values, orders) -> dict:
        if self._is_paper:
            return {"trigger_id": int(time.time())}
        body = self._gtt_body(trigger_type, tradingsymbol, exchange, last_price, trigger_values, orders)
        return await self._auth_post("/gtt/triggers", body)

    async def modify_gtt(self, trigger_id, *, trigger_type, tradingsymbol, exchange, last_price, trigger_values, orders) -> dict:
        body = self._gtt_body(trigger_type, tradingsymbol, exchange, last_price, trigger_values, orders)
        return await self._auth_put(f"/gtt/triggers/{trigger_id}", body)

    async def delete_gtt(self, trigger_id) -> dict:
        if self._is_paper:
            return {"trigger_id": trigger_id}
        return await self._auth_delete(f"/gtt/triggers/{trigger_id}")

    # ─── Quotes / historical ──────────────────────────────────────────────────
    @staticmethod
    def _i_params(instruments: Sequence[str]) -> list:
        return [("i", i) for i in instruments]

    async def get_quote(self, instruments: Sequence[str]) -> dict:
        return await self._auth_get("/quote", params=self._i_params(instruments)) or {}

    async def get_ohlc(self, instruments: Sequence[str]) -> dict:
        return await self._auth_get("/quote/ohlc", params=self._i_params(instruments)) or {}

    async def get_ltp(self, instruments: Sequence[str]) -> dict:
        return await self._auth_get("/quote/ltp", params=self._i_params(instruments)) or {}

    async def get_historical(
        self, token: int, interval: str, frm: str, to: str,
        continuous: bool = False, oi: bool = False,
    ) -> dict:
        return await self._auth_get(
            f"/instruments/historical/{token}/{interval}",
            params={"from": frm, "to": to, "continuous": 1 if continuous else 0, "oi": 1 if oi else 0},
        ) or {}

    # ─── User / margins ────────────────────────────────────────────────────────
    async def get_profile(self) -> dict:
        return await self._auth_get("/user/profile") or {}

    async def get_margins(self, segment: Optional[str] = None) -> dict:
        path = f"/user/margins/{segment}" if segment else "/user/margins"
        return await self._auth_get(path) or {}

    async def order_margins(self, orders: list) -> list:
        return await self._auth_post_json("/margins/orders", orders) or []

    async def basket_margins(self, orders: list, consider_positions: bool = True) -> dict:
        return await self._auth_request(
            "POST", "/margins/basket",
            params={"consider_positions": "true" if consider_positions else "false"},
            json_body=orders,
        ) or {}

    async def order_charges(self, orders: list) -> list:
        return await self._auth_post_json("/charges/orders", orders) or []

    # ─── Mutual funds ──────────────────────────────────────────────────────────
    async def get_mf_holdings(self) -> list:
        if not self._access_token:
            self._allow_sessionless_read()
            return []
        return await self._auth_get("/mf/holdings") or []

    async def get_mf_orders(self) -> list:
        if not self._access_token:
            self._allow_sessionless_read()
            return []
        return await self._auth_get("/mf/orders") or []

    async def get_mf_sips(self) -> list:
        if not self._access_token:
            self._allow_sessionless_read()
            return []
        return await self._auth_get("/mf/sips") or []

    async def place_mf_order(self, *, tradingsymbol, transaction_type, amount=None, quantity=None, tag=None) -> dict:
        if self._is_paper:
            return {"order_id": "PAPER-MF-" + uuid.uuid4().hex[:10]}
        body = {"tradingsymbol": tradingsymbol, "transaction_type": transaction_type}
        if amount is not None:
            body["amount"] = amount
        if quantity is not None:
            body["quantity"] = quantity
        if tag:
            body["tag"] = str(tag)[:20]
        return await self._auth_post("/mf/orders", body)

    async def cancel_mf_order(self, order_id: str) -> dict:
        if self._is_paper:
            return {"order_id": order_id}
        return await self._auth_delete(f"/mf/orders/{order_id}")

    async def place_mf_sip(self, *, tradingsymbol, amount, instalments, frequency, initial_amount=None) -> dict:
        if self._is_paper:
            return {"sip_id": "PAPER-SIP-" + uuid.uuid4().hex[:10]}
        body = {
            "tradingsymbol": tradingsymbol, "amount": amount,
            "instalments": instalments, "frequency": frequency,
        }
        if initial_amount is not None:
            body["initial_amount"] = initial_amount
        return await self._auth_post("/mf/sips", body)

    async def get_mf_order(self, order_id: str) -> dict:
        """Individual MF order detail (status timeline, allotted NAV/units)."""
        return await self._auth_get(f"/mf/orders/{order_id}") or {}

    async def get_mf_sip(self, sip_id: str) -> dict:
        """Individual SIP detail."""
        return await self._auth_get(f"/mf/sips/{sip_id}") or {}

    async def modify_mf_sip(self, sip_id, *, amount=None, frequency=None,
                            instalments=None, instalment_day=None, status=None) -> dict:
        """Modify a SIP — amount/frequency/instalments or pause/resume via status."""
        if self._is_paper:
            return {"sip_id": str(sip_id)}
        body = {}
        if amount is not None:
            body["amount"] = amount
        if frequency is not None:
            body["frequency"] = frequency
        if instalments is not None:
            body["instalments"] = instalments
        if instalment_day is not None:
            body["instalment_day"] = instalment_day
        if status is not None:
            body["status"] = status
        return await self._auth_put(f"/mf/sips/{sip_id}", body)

    async def cancel_mf_sip(self, sip_id: str) -> dict:
        if self._is_paper:
            return {"sip_id": str(sip_id)}
        return await self._auth_delete(f"/mf/sips/{sip_id}")

    # ─── Portfolio (reads) ─────────────────────────────────────────────────────
    async def get_holdings(self) -> list:
        if not self._access_token:
            self._allow_sessionless_read()
            return []
        return await self._auth_get("/portfolio/holdings") or []

    async def get_positions_raw(self) -> dict:
        """Raw {net, day} positions (keeps exchange + instrument_token, unlike the
        mapped get_positions)."""
        if not self._access_token:
            self._allow_sessionless_read()
            return {"net": [], "day": []}
        return await self._auth_get("/portfolio/positions") or {"net": [], "day": []}

    async def convert_position(self, **fields) -> dict:
        if self._is_paper:
            return {"status": "paper"}
        body = {k: v for k, v in fields.items() if v is not None}
        return await self._auth_put("/portfolio/positions", body)

    async def get_auctions(self) -> list:
        """Instruments currently up for auction that the account is eligible for."""
        if not self._access_token:
            self._allow_sessionless_read()
            return []
        return await self._auth_get("/portfolio/holdings/auctions") or []

    async def initiate_holdings_auth(self, instruments: Optional[list] = None) -> dict:
        """Begin CDSL holdings authorisation (eDIS) so holdings can be sold via API.

        Returns ``{request_id}``; the caller redirects the user to Kite's consent
        page to complete TPIN. Pass ``instruments`` ([{isin, quantity}, ...]) to
        scope the authorisation, or omit for a blanket request.
        """
        isins: List[object] = []
        qtys: List[object] = []
        for it in (instruments or []):
            isin = it.get("isin") if isinstance(it, dict) else None
            if not isin:
                continue
            isins.append(isin)
            if it.get("quantity") is not None:
                qtys.append(it["quantity"])
        # httpx encodes a dict whose values are lists as repeated form keys
        # (isin=A&isin=B); quantity is sent only when every ISIN supplied one so the
        # positional pairing Kite expects stays intact.
        body: dict = {}
        if isins:
            body["isin"] = isins
        if qtys and len(qtys) == len(isins):
            body["quantity"] = qtys
        return await self._auth_post("/portfolio/holdings/authorise", body) or {}

    # ─── Alerts (native Kite Connect Alerts API) ──────────────────────────────
    async def get_alerts(self) -> list:
        if not self._access_token:
            self._allow_sessionless_read()
            return []
        return await self._auth_get("/alerts") or []

    async def get_alert(self, uuid: str) -> dict:
        return await self._auth_get(f"/alerts/{uuid}") or {}

    async def get_alert_history(self, uuid: str) -> list:
        return await self._auth_get(f"/alerts/{uuid}/history") or []

    @staticmethod
    def _alert_body(
        *, name, lhs_exchange, lhs_tradingsymbol, lhs_attribute, operator,
        rhs_constant=None, alert_type=K.ALERT_TYPE_SIMPLE, rhs_type="constant",
        rhs_exchange=None, rhs_tradingsymbol=None, rhs_attribute=None, basket=None,
    ) -> dict:
        body: dict = {
            "name": name, "type": alert_type,
            "lhs_exchange": lhs_exchange, "lhs_tradingsymbol": lhs_tradingsymbol,
            "lhs_attribute": lhs_attribute, "operator": operator, "rhs_type": rhs_type,
        }
        if rhs_type == "constant":
            body["rhs_constant"] = rhs_constant
        else:
            body["rhs_exchange"] = rhs_exchange
            body["rhs_tradingsymbol"] = rhs_tradingsymbol
            body["rhs_attribute"] = rhs_attribute
        if basket is not None:
            body["basket"] = basket if isinstance(basket, str) else json.dumps(basket)
        return body

    async def create_alert(self, **fields) -> dict:
        """Create a price/attribute alert. ``simple`` → notification only; ``ato``
        carries a ``basket`` of orders triggered when the condition is met.

        Paper mode simulates ``ato`` alerts (they would arm real orders on trigger)
        but lets ``simple`` notification alerts through — they have no market impact.
        """
        if self._is_paper and fields.get("alert_type") == K.ALERT_TYPE_ATO:
            return {"uuid": "PAPER-ATO-" + uuid.uuid4().hex[:10], "paper": True}
        return await self._auth_post("/alerts", self._alert_body(**fields))

    async def modify_alert(self, uuid: str, **fields) -> dict:
        body = {k: v for k, v in fields.items() if v is not None}
        if "basket" in body and not isinstance(body["basket"], str):
            body["basket"] = json.dumps(body["basket"])
        return await self._auth_put(f"/alerts/{uuid}", body)

    async def delete_alerts(self, uuids: Sequence[str]) -> dict:
        """Delete one or more alerts (Kite takes repeated ``?uuid=`` params)."""
        return await self._auth_request(
            "DELETE", "/alerts", params=[("uuid", u) for u in uuids]
        ) or {}

    # ─── BaseExchangeAdapter / market data (ported) ───────────────────────────
    async def ping(self) -> bool:
        if self._is_paper:
            return True
        try:
            client = await self._get_client()
            resp = await client.get("/")
            return resp.status_code < 500
        except Exception:
            return False

    async def get_index_price(self, instrument: InstrumentMeta) -> float:
        sym = instrument.zerodha_index_symbol or f"NSE:{instrument.index_name}"
        try:
            data = await self.get_ltp([sym])
            return float((data.get(sym) or {}).get("last_price") or 0.0)
        except Exception:
            return 0.0

    async def get_spot_price(self, instrument: InstrumentMeta) -> float:
        return await self.get_index_price(instrument)

    async def get_candles(self, instrument: InstrumentMeta, resolution: str, limit: int = 200) -> List[Candle]:
        token = instrument.zerodha_token
        if not token:
            log.warning("No zerodha_token for %s — cannot fetch candles", instrument.underlying)
            return []
        want_4h = resolution == "4H"
        interval = K.RESOLUTION_MAP.get(resolution, "60minute")
        now = datetime.now(_IST)
        n_bars = limit * 4 if want_4h else limit
        days_needed = _historical_days_needed(interval, n_bars)
        from_str = (now - timedelta(days=days_needed)).strftime("%Y-%m-%d %H:%M:%S")
        to_str = now.strftime("%Y-%m-%d %H:%M:%S")
        
        # Log underlying + interval only — never instrument tokens (confidential).
        log.info(
            "Fetching Kite candles: underlying=%s, resolution=%s, interval=%s, from=%s, to=%s",
            instrument.underlying, resolution, interval, from_str, to_str,
        )
        max_retries = 5
        raw: list = []
        for attempt in range(max_retries):
            try:
                data = await self.get_historical(token, interval, from_str, to_str)
                raw = data.get("candles", [])
                break  # success
            except Exception as exc:
                if not is_retryable(exc):
                    # A missing/expired session (or a rejected request) fails
                    # identically on every attempt. This used to burn all five
                    # retries with a 0.5s sleep each — 2s per symbol — and then
                    # log a full traceback, so a universe scan while logged out
                    # emitted one stack per contract and buried real errors.
                    # Fail fast, and say it once without a traceback.
                    log.warning(
                        "Kite candle fetch unavailable for %s: %s",
                        instrument.underlying, exc,
                    )
                    return []
                is_429 = "429" in str(exc)
                if attempt < max_retries - 1:
                    # Exponential backoff on rate-limit (429) so a big multi-contract
                    # scan recovers instead of dropping contracts; short retry otherwise.
                    sleep_time = min(8.0, 0.75 * (2 ** attempt)) if is_429 else 0.5
                    log.warning("Kite candle fetch failed for %s (attempt %d): %s. Retrying in %ss...",
                                instrument.underlying, attempt + 1, exc, sleep_time)
                    await asyncio.sleep(sleep_time)
                else:
                    log.exception("Kite candle fetch failed for %s after %d attempts: %s",
                              instrument.underlying, max_retries, exc)
                    return []
        candles: List[Candle] = []
        for row in raw:
            try:
                candles.append(Candle(
                    timestamp_ms=_parse_kite_ts(str(row[0])),
                    open=float(row[1]), high=float(row[2]), low=float(row[3]),
                    close=float(row[4]), volume=float(row[5]) if len(row) > 5 else 0.0,
                ))
            except (IndexError, ValueError, TypeError):
                continue
        candles.sort(key=lambda c: c.timestamp_ms)
        if want_4h:
            candles = _aggregate_4h(candles)
        return candles[-limit:]

    async def _load_nfo_instruments(self) -> List[Dict]:
        """Back-compat shim: NFO instruments via the shared cache."""
        try:
            return await self._instruments.load(K.EXCHANGE_NFO)
        except Exception as exc:
            log.warning("NFO instruments fetch failed: %s", exc)
            return []

    async def get_option_chain(self, instrument: InstrumentMeta) -> List[OptionSummary]:
        if not instrument.has_options or not self._access_token:
            return []
        spot = await self.get_index_price(instrument)
        if spot <= 0:
            return []
        name_filter = instrument.underlying
        now = datetime.now(_IST)
        now_ms = int(time.time() * 1000)
        try:
            all_instruments = await self._load_nfo_instruments()
        except Exception:
            return []
        max_strike_delta = spot * 0.20
        filtered: List[Dict] = []
        for row in all_instruments:
            if str(row.get("name", "")).upper() != name_filter:
                continue
            if row.get("instrument_type", "") not in ("CE", "PE"):
                continue
            if row.get("segment", "") != "NFO-OPT":
                continue
            try:
                strike = float(row["strike"])
                if abs(strike - spot) > max_strike_delta:
                    continue
                expiry_str = row.get("expiry", "")
                if not expiry_str:
                    continue
                dt_expiry = datetime.strptime(expiry_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                dte = (dt_expiry - now.replace(tzinfo=timezone.utc)).days
                if dte < instrument.min_dte or dte > 60:
                    continue
                filtered.append({**row, "_dte": dte, "_strike": strike})
            except (ValueError, TypeError):
                continue
        if not filtered:
            return []
        expiries = sorted({r["expiry"] for r in filtered})[:3]
        filtered = [r for r in filtered if r["expiry"] in expiries]
        symbols = [f"NFO:{r['tradingsymbol']}" for r in filtered[:400]]
        if not symbols:
            return []
        try:
            quotes = await self.get_quote(symbols)
        except Exception as exc:
            log.warning("NFO quote fetch failed: %s", exc)
            return []
        options: List[OptionSummary] = []
        for row in filtered:
            q = quotes.get(f"NFO:{row['tradingsymbol']}", {})
            if not q:
                continue
            try:
                depth = q.get("depth", {})
                buy_side = depth.get("buy", [{}])
                sell_side = depth.get("sell", [{}])
                bid = float((buy_side[0] if buy_side else {}).get("price") or 0.0)
                ask = float((sell_side[0] if sell_side else {}).get("price") or 0.0)
                ltp = float(q.get("last_price") or 0.0)
                mid = (bid + ask) / 2 if bid > 0 and ask > 0 else ltp
                iv = float(q.get("implied_volatility") or 0.0) / 100.0
                opt_type = "call" if row["instrument_type"] == "CE" else "put"
                strike = row["_strike"]
                moneyness = (spot - strike) / spot
                delta = (max(0.01, min(0.99, 0.5 + moneyness * 2)) if opt_type == "call"
                         else max(-0.99, min(-0.01, -0.5 + moneyness * 2)))
                options.append(OptionSummary(
                    instrument_name=row["tradingsymbol"], underlying=instrument.underlying,
                    strike=strike, expiry_date=row["expiry"], dte=row["_dte"],
                    option_type=opt_type, bid=bid, ask=ask, mark_price=ltp, mid_price=mid,
                    mark_iv=iv * 100, delta=delta,
                    open_interest=float(q.get("oi") or 0.0), volume_24h=float(q.get("volume") or 0.0),
                    last_updated_ms=now_ms,
                ))
            except (ValueError, TypeError, KeyError):
                continue
        return options

    # ─── AuthenticatedExchangeAdapter ─────────────────────────────────────────
    async def test_connection(self) -> bool:
        if not self._access_token:
            return bool(self._is_paper)  # paper w/o login = "ok"; live w/o login = fail
        try:
            await self.get_profile()
            return True
        except Exception:
            return False

    async def get_balances(self) -> List[AssetBalance]:
        if not self._access_token:
            self._allow_sessionless_read()
            return _paper_balances()
        data = await self.get_margins()
        balances = []
        for seg, info in (data or {}).items():
            try:
                balances.append(AssetBalance(
                    asset=f"INR ({seg})",
                    available=float(info.get("available", {}).get("live_balance") or 0.0),
                    locked=float(info.get("utilised", {}).get("debits") or 0.0),
                    total=float(info.get("net") or 0.0),
                    inr_value=float(info.get("net") or 0.0),
                ))
            except (TypeError, ValueError):
                continue
        return balances

    async def get_positions(self) -> List[AccountPosition]:
        if not self._access_token:
            self._allow_sessionless_read()
            return []
        data = await self._auth_get("/portfolio/positions")
        positions = []
        for p in (data.get("net") or []):
            try:
                qty = int(p.get("quantity") or 0)
                if qty == 0:
                    continue
                positions.append(AccountPosition(
                    symbol=str(p.get("tradingsymbol") or ""),
                    underlying=str(p.get("exchange") or "") + ":" + str(p.get("tradingsymbol", "")[:6]),
                    size=float(qty), side="long" if qty > 0 else "short",
                    entry_price=float(p.get("average_price") or 0.0),
                    mark_price=float(p.get("last_price") or 0.0),
                    unrealized_pnl=float(p.get("pnl") or 0.0),
                    realized_pnl=float(p.get("realised") or 0.0),
                    margin=float(p.get("value") or 0.0),
                    position_type=str(p.get("product") or "MIS"), created_at_ms=None,
                ))
            except (TypeError, ValueError):
                continue
        return positions

    async def get_open_orders(self, underlying: Optional[str] = None) -> List[AccountOrder]:
        if not self._access_token:
            self._allow_sessionless_read()
            return []
        data = await self._auth_get("/orders")
        orders = []
        for o in (data or []):
            try:
                if o.get("status") not in K.OPEN_ORDER_STATUSES:
                    continue
                orders.append(AccountOrder(
                    order_id=str(o.get("order_id") or ""),
                    symbol=str(o.get("tradingsymbol") or ""),
                    side="buy" if o.get("transaction_type") == "BUY" else "sell",
                    size=float(o.get("quantity") or 0.0),
                    price=float(o.get("price") or 0.0),
                    filled_size=float(o.get("filled_quantity") or 0.0),
                    status=str(o.get("status") or "open").lower(),
                    order_type=str(o.get("order_type") or "LIMIT").lower(),
                    created_at_ms=_parse_kite_ts(o.get("order_timestamp")),
                ))
            except (TypeError, ValueError):
                continue
        return orders

    async def get_fills(self, limit: int = 50) -> List[AccountFill]:
        if not self._access_token:
            self._allow_sessionless_read()
            return []
        data = await self._auth_get("/trades")
        fills = []
        for f in (data or [])[:limit]:
            try:
                fills.append(AccountFill(
                    fill_id=str(f.get("trade_id") or ""), order_id=str(f.get("order_id") or ""),
                    symbol=str(f.get("tradingsymbol") or ""),
                    side="buy" if f.get("transaction_type") == "BUY" else "sell",
                    size=float(f.get("quantity") or 0.0), price=float(f.get("average_price") or 0.0),
                    fee=0.0, fee_asset="INR", pnl=0.0,
                    created_at_ms=_parse_kite_ts(f.get("fill_timestamp")),
                ))
            except (TypeError, ValueError):
                continue
        return fills

    async def get_portfolio_snapshot(self) -> PortfolioSnapshot:
        balances = await self.get_balances()
        positions = await self.get_positions()
        orders = await self.get_open_orders()
        total_bal = sum(b.total for b in balances)
        unreal_pnl = sum(p.unrealized_pnl for p in positions)
        real_pnl = sum(p.realized_pnl for p in positions)
        margin_used = sum(abs(p.margin) for p in positions)
        return PortfolioSnapshot(
            exchange="zerodha", display_name="Zerodha Kite",
            total_balance_inr=round(total_bal, 2),
            unrealized_pnl_inr=round(unreal_pnl, 2), realized_pnl_inr=round(real_pnl, 2),
            margin_used=round(margin_used, 2),
            margin_available=max(0.0, round(total_bal - margin_used, 2)),
            positions_count=len(positions), open_orders_count=len(orders),
            balances=balances, timestamp_ms=int(time.time() * 1000),
        )

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


def _paper_balances() -> List[AssetBalance]:
    return [
        AssetBalance(asset="INR (equity)", available=500000.0, locked=50000.0, total=550000.0, inr_value=550000.0),
        AssetBalance(asset="INR (commodity)", available=100000.0, locked=0.0, total=100000.0, inr_value=100000.0),
    ]
