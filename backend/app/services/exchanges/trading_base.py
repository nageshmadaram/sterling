"""
TradingExchangeAdapter — the enforced order-placement contract.

Defines the checked surface every order-capable Indian broker must implement.

Optional capability methods (cancel_replace_stop, market_reduce_close) default
to NotImplementedError so a partial adapter still
boots; OrderRouter feature-detects / guards each call (see order_router.py).
"""
from abc import abstractmethod
from typing import Optional

from app.services.exchanges.authenticated_base import AuthenticatedExchangeAdapter


class TradingExchangeAdapter(AuthenticatedExchangeAdapter):
    # ── required order surface ────────────────────────────────────────────
    @abstractmethod
    async def get_product_id(self, symbol: str) -> int:
        ...

    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        side: str,
        size: float,
        order_type: str = "market_order",
        limit_price: Optional[float] = None,
        time_in_force: str = "gtc",
        reduce_only: bool = False,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        trail_amount: Optional[float] = None,
        **kwargs,
    ) -> dict:
        ...

    @abstractmethod
    async def place_order_option(
        self,
        option_symbol: str,
        side: str,
        size: float,
        order_type: str = "market_order",
        limit_price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        **kwargs,
    ) -> dict:
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str, product_id: int) -> dict:
        ...

    # ── optional capabilities (override where supported) ──────────────────
    async def cancel_replace_stop(self, **kwargs) -> dict:
        raise NotImplementedError("cancel_replace_stop not supported by this adapter")

    async def market_reduce_close(self, **kwargs) -> dict:
        raise NotImplementedError("market_reduce_close not supported by this adapter")
