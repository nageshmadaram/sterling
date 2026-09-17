"""What could actually have been filled, given the book that was there.

Paper and shadow execution must price against observed depth. Assuming the
whole quantity filled at the last traded price is the single easiest way to
manufacture a profitable backtest: LTP is a print from the past, and the size
behind it is usually a fraction of what a strategy wants to trade.

So a request larger than the visible liquidity is ``NO_FILL_INSUFFICIENT_DEPTH``
— a real operability finding that belongs in the denominator, not a strategy
loss and not a silent full fill.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Iterable, Sequence

NO_FILL_INSUFFICIENT_DEPTH: Final[str] = "NO_FILL_INSUFFICIENT_DEPTH"
NO_FILL_NO_BOOK: Final[str] = "NO_FILL_NO_BOOK"
NO_FILL_SPREAD_TOO_WIDE: Final[str] = "NO_FILL_SPREAD_TOO_WIDE"


@dataclass(frozen=True)
class DepthLevel:
    price: float
    quantity: int


@dataclass(frozen=True)
class FillResult:
    """What the book would have given, and at what average price."""

    filled_quantity: int
    average_price: float
    reason: str = ""
    consumed_levels: int = 0
    slippage_vs_touch: float = 0.0
    #: Levels observed, so a report can say how thin the book was.
    visible_quantity: int = 0

    @property
    def filled(self) -> bool:
        return self.filled_quantity > 0 and not self.reason

    def as_dict(self) -> dict:
        return {
            "filled_quantity": self.filled_quantity,
            "average_price": round(self.average_price, 4),
            "reason": self.reason,
            "consumed_levels": self.consumed_levels,
            "slippage_vs_touch": round(self.slippage_vs_touch, 4),
            "visible_quantity": self.visible_quantity,
        }


def _levels(raw: Iterable) -> list[DepthLevel]:
    out: list[DepthLevel] = []
    for entry in raw or []:
        if isinstance(entry, DepthLevel):
            level = entry
        elif isinstance(entry, dict):
            level = DepthLevel(
                float(entry.get("price") or 0.0), int(entry.get("quantity") or 0)
            )
        else:
            price, quantity = entry
            level = DepthLevel(float(price), int(quantity))
        if level.price > 0 and level.quantity > 0:
            out.append(level)
    return out


def simulate_fill(
    *,
    side: str,
    quantity: int,
    levels: Sequence,
    max_spread_pct: float | None = None,
    opposite_touch: float | None = None,
    partial_allowed: bool = False,
) -> FillResult:
    """Walk the observed book for ``quantity``.

    ``partial_allowed`` is off by default. A partial entry is a different trade
    from the one the strategy asked for — differently sized, differently
    hedged — so taking one silently would record a trade nobody decided to make.
    """
    if quantity <= 0:
        raise ValueError("quantity must be positive")

    book = _levels(levels)
    if not book:
        return FillResult(0, 0.0, NO_FILL_NO_BOOK)

    visible = sum(level.quantity for level in book)
    touch = book[0].price

    if max_spread_pct is not None and opposite_touch:
        spread = abs(touch - opposite_touch)
        mid = (touch + opposite_touch) / 2.0
        if mid > 0 and (spread / mid) * 100.0 > max_spread_pct:
            return FillResult(
                0, 0.0, NO_FILL_SPREAD_TOO_WIDE, visible_quantity=visible
            )

    if visible < quantity and not partial_allowed:
        return FillResult(
            0, 0.0, NO_FILL_INSUFFICIENT_DEPTH, visible_quantity=visible
        )

    remaining = quantity
    notional = 0.0
    consumed = 0
    for level in book:
        if remaining <= 0:
            break
        take = min(remaining, level.quantity)
        notional += take * level.price
        remaining -= take
        consumed += 1

    got = quantity - remaining
    if got <= 0:
        return FillResult(0, 0.0, NO_FILL_INSUFFICIENT_DEPTH, visible_quantity=visible)

    average = notional / got
    # Positive slippage always means "worse than the touch", whichever side.
    slippage = (average - touch) if side.upper() == "BUY" else (touch - average)
    return FillResult(
        filled_quantity=got,
        average_price=average,
        reason="" if got == quantity else "",
        consumed_levels=consumed,
        slippage_vs_touch=slippage,
        visible_quantity=visible,
    )
