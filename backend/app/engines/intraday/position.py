"""A live intraday position, and the rules that move its stop.

Two price ladders live here on purpose.

The **spot** ladder is the strategy's thesis: a candle's low, the slow EMA,
VWAP. It is what the rule actually said and it is what decides whether the idea
is still alive.

The **premium** ladder is the money. Every one of these three strategies BUYS an
option, so the position is long premium whichever way the thesis points — and
the premium can round-trip while the spot rule is still perfectly intact. That
gap is where an open drawdown builds, and it is the reason both ladders are
tracked rather than one being derived from the other on demand.

``side`` is always ``"long"``. It is a separate field from ``thesis`` because
conflating the two is a bug this codebase has already paid for: a red counter
that read ``direction`` sold every PE at entry, because a bearish thesis is
still a BOUGHT option.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Literal, Optional

from app.engines.common.trailing import ratchet_trail

from .config import IntradayConfig

PENDING, OPEN, CLOSED, REJECTED = "pending", "open", "closed", "rejected"

Thesis = Literal["BULLISH", "BEARISH"]


def q2(v: float) -> float:
    return round(float(v or 0.0), 2)


def align_to_tick(price: float, tick: float = 0.05, *, side: str = "buy") -> float:
    """Round to a tradable tick. A buy rounds UP, a sell DOWN.

    Rounding the wrong way produces a limit the exchange will not fill and a
    position that sits unprotected while an operator watches a resting order.
    """
    t = float(tick or 0.05) or 0.05
    n = float(price) / t
    import math
    n = math.ceil(n - 1e-9) if side == "buy" else math.floor(n + 1e-9)
    return round(n * t, 2)


@dataclass
class ContractRef:
    tradingsymbol: str
    exchange: str = "NFO"
    token: int = 0
    option_type: str = "CE"
    strike: float = 0.0
    expiry: str = ""
    lot_size: int = 1
    tick_size: float = 0.05


@dataclass
class IntradayPosition:
    strategy: str
    signal_id: str
    underlying: str
    contract: ContractRef
    thesis: Thesis
    #: Always "long". See the module docstring.
    side: str = "long"

    # ── the strategy's own ladder, in the underlying's points ──────────────
    spot_entry: float = 0.0
    spot_stop: float = 0.0
    spot_target: float = 0.0
    spot_target2: Optional[float] = None
    spot_risk: float = 0.0

    # ── the money ──────────────────────────────────────────────────────────
    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    #: The runner's objective. ``pivot_break`` banks at 1:2 and lets the rest go
    #: to 1:3 behind a breakeven stop; the other two have one objective and
    #: leave this at 0.
    target2: float = 0.0
    quantity: int = 0
    lots: int = 0
    fill_price: float = 0.0
    peak: float = 0.0
    #: Where the broker's resting stop currently sits, so the trail only sends
    #: a modify when it has actually moved.
    gtt_at: float = 0.0

    breakeven_done: bool = False
    #: The first target has been banked and part of the position is gone. The
    #: board says "runner" on these rows, so the live path has to actually do
    #: it — a UI claiming behaviour the backend does not honour is the recurring
    #: bug in this codebase.
    target1_done: bool = False
    #: Units sold at the first target, so P&L and the remaining size are both
    #: honest afterwards.
    scaled_qty: int = 0
    scaled_price: float = 0.0
    #: Claimed by whichever path is exiting, so two of them cannot both sell.
    exiting: bool = False

    order_id: str = ""
    gtt_id: int = 0
    stop_mode: str = "both"
    idempotency_key: str = ""
    status: str = PENDING

    #: The UNDERLYING's instrument token, kept so the exit rules can be
    #: re-evaluated on the same series the entry came from. Reading the
    #: contract's own premium chart instead would test a different series, and
    #: a live exit that disagrees with the backtest for that reason is nearly
    #: impossible to find afterwards.
    underlying_token: int = 0

    entered_ms: int = 0
    entry_day: str = ""
    exit_price: float = 0.0
    exit_reason: str = ""
    realised_inr: float = 0.0
    notes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ views

    @property
    def is_open(self) -> bool:
        return self.status in (PENDING, OPEN)

    @property
    def effective_entry(self) -> float:
        """What was actually paid. The limit price until a fill says otherwise."""
        return self.fill_price if self.fill_price > 0 else self.entry

    @property
    def premium_risk(self) -> float:
        return max(0.0, self.effective_entry - self.stop)

    def r_multiple(self, premium: float) -> float:
        """Premium R. Zero risk means zero R — never a division."""
        base = self.effective_entry - self.initial_stop
        if base <= 0:
            return 0.0
        return (float(premium) - self.effective_entry) / base

    #: The stop as it was at entry. The live ``stop`` ratchets, so R measured
    #: against it would shrink towards zero and every trade would look like a
    #: winner the moment the trail moved.
    initial_stop: float = 0.0

    def unrealised_inr(self, premium: float) -> float:
        return (float(premium) - self.effective_entry) * self.quantity

    @property
    def banked_inr(self) -> float:
        """Realised on the part already sold at the first target."""
        if self.scaled_qty <= 0 or self.scaled_price <= 0:
            return 0.0
        return round((self.scaled_price - self.effective_entry) * self.scaled_qty, 2)

    def scale_out_qty(self, lot_size: int) -> int:
        """How much to sell at the first target: half, rounded to whole lots.

        Zero when the position is a single lot. Selling "half" of one lot is not
        a thing the exchange will do, and an engine that tries it gets a
        rejection at the exact moment it was trying to bank a win.
        """
        lot = max(1, int(lot_size or 1))
        lots = self.quantity // lot
        return (lots // 2) * lot

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["contract"] = asdict(self.contract)
        d["is_open"] = self.is_open
        d["effective_entry"] = self.effective_entry
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Optional["IntradayPosition"]:
        try:
            cfields = {f.name for f in fields(ContractRef)}
            contract = ContractRef(**{k: v for k, v in (d.get("contract") or {}).items()
                                      if k in cfields})
            known = {f.name for f in fields(cls)} - {"contract"}
            return cls(contract=contract,
                       **{k: v for k, v in d.items() if k in known})
        except (TypeError, ValueError):
            return None


# ------------------------------------------------------------------- trailing

def premium_stop_for(entry: float, spot_entry: float, spot_stop: float,
                     cfg: IntradayConfig, *, delta: Optional[float] = None) -> float:
    """Turn the strategy's spot stop into a premium stop.

    An option does not move one-for-one with its underlying, so a spot stop of
    20 points is NOT a premium stop of 20 points. With a quoted delta the
    conversion is arithmetic. Without one the fallback is a straight percentage
    of the premium — deliberately a fixed fraction rather than a guessed delta,
    because a guessed delta that is too high produces a stop below zero and a
    position that can never be stopped out at all.

    The result is never below one tick: a stop at or under zero is not a stop.
    """
    spot_move = abs(float(spot_entry) - float(spot_stop))
    by_pct = float(entry) * (1.0 - cfg.premium_stop_pct / 100.0)
    if delta and 0.05 <= abs(float(delta)) <= 1.0 and spot_move > 0:
        by_delta = float(entry) - spot_move * abs(float(delta))
        # Take the SAFER (higher) of the two: the delta estimate is a
        # first-order approximation that ignores theta and vega, both of which
        # work against a bought option.
        return max(0.05, q2(max(by_delta, by_pct)))
    return max(0.05, q2(by_pct))


def update_trail(pos: IntradayPosition, premium: float,
                 cfg: IntradayConfig) -> tuple[float, str]:
    """The premium ratchet. Returns ``(new_stop, why)``; ``why`` is "" if unmoved.

    Two stages, in order:

    1. **Breakeven.** Once ``trail_activate_r`` is banked the stop goes to what
       was actually paid, so a trade that has worked cannot become a loss.
    2. **Give-back cap.** After that the stop rides ``premium_trail_pct`` below
       the best premium seen. This is the part that matters for an option: the
       spot rule can still be intact while the premium has round-tripped.

    The stop only ever ratchets up — :func:`ratchet_trail` is the shared helper
    every engine here uses for exactly that, so "the stop moved against me" is
    not a thing that can happen in one engine and not another.
    """
    px = float(premium or 0.0)
    if px <= 0 or pos.quantity <= 0:
        return pos.stop, ""
    stop = pos.stop
    why = ""
    if pos.r_multiple(pos.peak or px) >= cfg.trail_activate_r:
        if not pos.breakeven_done:
            moved = ratchet_trail(stop, pos.effective_entry, "long")
            if moved > stop:
                stop, why = moved, "breakeven"
        give_back = (pos.peak or px) * (1.0 - cfg.premium_trail_pct / 100.0)
        moved = ratchet_trail(stop, q2(give_back), "long")
        if moved > stop:
            stop = moved
            why = why or f"trail {cfg.premium_trail_pct:g}% off {q2(pos.peak or px)}"
    return q2(stop), why


def spot_trail(pos: IntradayPosition, cfg: IntradayConfig, *, spot: float,
               atr: float = 0.0, swing: Optional[float] = None,
               vwap: Optional[float] = None) -> tuple[float, str]:
    """Ratchet the SPOT stop as the underlying moves in the trade's favour.

    The premium trail protects the money; this protects the thesis. They are
    not the same thing and neither replaces the other: an option can hold its
    premium on vega while the underlying walks back through the level the trade
    was taken against, and it can lose premium to theta while the underlying is
    doing exactly what the entry predicted.

    Which rule applies is the strategy's own:

    * ``pivot_break`` follows ``pb_trail_mode`` — the swing that made the move
      (``structure``), a multiple of ATR, breakeven only, or nothing.
    * ``vwap_supertrend`` follows VWAP once ``vs_trail_after_points`` is banked,
      which is the trail its own specification states.
    * ``ma_ribbon`` has no spot trail: it is held until the opposite full cross,
      and a trail that closed it earlier would be a different strategy.

    Only ever ratchets towards price, via the shared :func:`ratchet_trail`.
    Returns ``(new_stop, why)``; ``why`` is "" when nothing moved.
    """
    if pos.spot_stop <= 0 or spot <= 0:
        return pos.spot_stop, ""
    side = "long" if pos.thesis == "BULLISH" else "short"
    sign = 1.0 if pos.thesis == "BULLISH" else -1.0
    moved = (spot - pos.spot_entry) * sign
    stop = pos.spot_stop
    why = ""

    if pos.strategy == "vwap_supertrend":
        if vwap is None or moved < cfg.vs_trail_after_points:
            return stop, ""
        cand = ratchet_trail(stop, q2(float(vwap)), side)
        return (cand, f"VWAP after {cfg.vs_trail_after_points:g} pts") if cand != stop \
            else (stop, "")

    if pos.strategy != "pivot_break" or cfg.pb_trail_mode == "none":
        return stop, ""

    risk = pos.spot_risk or abs(pos.spot_entry - pos.spot_stop)
    if risk <= 0:
        return stop, ""
    if moved / risk < cfg.pb_breakeven_at_r:
        return stop, ""
    cand = ratchet_trail(stop, q2(pos.spot_entry), side)
    if cand != stop:
        stop, why = cand, "spot breakeven"
    if cfg.pb_trail_mode == "atr" and atr > 0:
        cand = ratchet_trail(stop, q2(spot - sign * cfg.pb_trail_atr_mult * atr), side)
        if cand != stop:
            stop, why = cand, f"{cfg.pb_trail_atr_mult:g}x ATR behind {q2(spot)}"
    elif cfg.pb_trail_mode == "structure" and swing is not None:
        cand = ratchet_trail(stop, q2(float(swing)), side)
        if cand != stop:
            stop, why = cand, "the swing that made the move"
    return stop, why


def should_scale_out(pos: IntradayPosition, price: float, *,
                     long: bool = True) -> bool:
    """Whether the first target has been reached on a position that has a runner.

    Deliberately NOT part of ``should_exit``: banking half is not an exit, and
    folding the two together is how a two-stage strategy quietly becomes a
    one-stage one — the first target closes everything and the 1:3 leg the
    board advertises never exists.

    ``long`` defaults True because the LIVE caller passes a premium, and a
    bought option is long premium whichever way the thesis points. A caller
    working in the UNDERLYING's prices must pass the actual direction: a short's
    target sits BELOW its entry, so a `>=` test fires the instant the position
    opens.

    That is not hypothetical. Without this parameter the replay banked half of
    every short at its target price on the entry bar, for a guaranteed +2R that
    the tape never offered — worth about +0.6R per trade, and enough to make a
    pure random walk look like a strategy.
    """
    if pos.target2 <= 0 or pos.target1_done or pos.target <= 0:
        return False
    px = float(price or 0.0)
    return px >= pos.target if long else px <= pos.target


def should_exit(pos: IntradayPosition, premium: float, *,
                spot: Optional[float] = None,
                session_over: bool = False,
                rule_broken: str = "") -> tuple[bool, str]:
    """Whether this position is done, and why.

    Order matters and is deliberate. The STOP is checked first: a tick that is
    through both the stop and the target is a loss, not a win. Assuming the good
    fill is the most common way a replay engine flatters itself, and the live
    path must not disagree with the replay about which one happened.
    """
    px = float(premium or 0.0)
    if px > 0 and pos.stop > 0 and px <= pos.stop:
        if pos.breakeven_done and pos.stop >= pos.effective_entry:
            return True, "trailing stop"
        return True, "stop"
    # A position with a runner does not close at the first target — it banks
    # half there (see `should_scale_out`) and runs the rest to `target2`.
    final = pos.target2 if (pos.target2 > 0 and pos.target1_done) else (
        0.0 if pos.target2 > 0 else pos.target)
    if px > 0 and final > 0 and px >= final:
        return True, "target2" if pos.target1_done and pos.target2 > 0 else "target"
    # The spot thesis, in the underlying's own terms. A bought CE is wrong when
    # spot breaks the level the entry was taken against, whatever the premium is
    # doing — an illiquid contract can sit at a stale price through it.
    if spot is not None and pos.spot_stop > 0:
        if pos.thesis == "BULLISH" and float(spot) <= pos.spot_stop:
            return True, "spot stop"
        if pos.thesis == "BEARISH" and float(spot) >= pos.spot_stop:
            return True, "spot stop"
    if rule_broken:
        return True, rule_broken
    if session_over:
        return True, "session end"
    return False, ""
