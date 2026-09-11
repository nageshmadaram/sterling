"""Bar-by-bar replay of the intraday pack, honest about what it cannot know.

This calls the SAME functions the live scanner calls — ``evaluate_all`` for
entries, ``should_exit`` / ``update_trail`` / ``spot_trail`` for management. A
backtest that reimplements the rules measures the reimplementation, and the
first thing that diverges is the thing you were trying to measure.

Four decisions make the difference between a result and a flattering one, and
all four are made against the strategy here:

* **Signals come off CLOSED bars, and fill at the NEXT bar's open.** A signal
  computed from a bar's close cannot be filled at that close — the close is the
  last print of the bar. Filling there is the single most common way an
  intraday backtest invents money.
* **A bar that trades through both the stop and the target is a LOSS.**
  Nothing in the data says which came first, so the harness assumes the worse.
* **Costs are charged on both legs, and slippage on both.** At 5 minutes on a
  weekly option, costs are not a rounding error: an earlier sweep in this repo
  found sub-hour timeframes losing essentially all of their gross edge to fees.
* **Nothing is flat-vol priced.** The default lens is the UNDERLYING, where a
  point is a point. The option lens exists but has to be asked for and says
  what it assumes, because flat-vol pricing has already faked a wing edge here
  once.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Iterable, Literal, Optional, Sequence

import numpy as np

from .config import IntradayConfig
from .models import Bars, IntradaySignal, to_bars
from .position import ratchet_trail
from .strategies import EVALUATORS, thesis_broken

Lens = Literal["underlying", "option"]


@dataclass(frozen=True)
class CostModel:
    """What one round trip actually costs, in the instrument's own units.

    Defaults are the Zerodha options schedule as of 2026 plus a slippage
    assumption. Slippage is the term that decides whether a 5-minute strategy
    is viable at all, and it is a PARAMETER rather than a constant precisely so
    a sweep can show how sensitive the answer is to it.
    """

    #: Flat brokerage per executed order, in rupees.
    brokerage_per_order: float = 20.0
    #: STT, charged on the SELL side of an option, on premium.
    stt_sell_pct: float = 0.10
    #: Exchange transaction charge, on premium, both sides.
    exchange_pct: float = 0.0495
    #: GST on (brokerage + exchange charges).
    gst_pct: float = 18.0
    #: SEBI turnover + stamp, rolled together. Small but not zero.
    misc_pct: float = 0.0031
    #: Half the bid/ask, paid on each leg, as a percentage of price. THE number
    #: that decides whether an intraday option strategy survives.
    slippage_pct: float = 0.50

    def round_trip(self, entry: float, exit_price: float, qty: int) -> float:
        """Rupees of cost for one complete trade. Never negative."""
        if qty <= 0 or entry <= 0:
            return 0.0
        buy_value = entry * qty
        sell_value = max(0.0, exit_price) * qty
        brokerage = 2 * self.brokerage_per_order
        exch = (buy_value + sell_value) * self.exchange_pct / 100.0
        stt = sell_value * self.stt_sell_pct / 100.0
        gst = (brokerage + exch) * self.gst_pct / 100.0
        misc = (buy_value + sell_value) * self.misc_pct / 100.0
        return round(brokerage + exch + stt + gst + misc, 2)

    def slip(self, price: float, side: Literal["buy", "sell"]) -> float:
        """The fill, after paying half the spread in the wrong direction."""
        adj = price * self.slippage_pct / 100.0
        return max(0.01, price + adj if side == "buy" else price - adj)


@dataclass
class BacktestTrade:
    strategy: str
    symbol: str
    thesis: str
    entry_ms: int
    exit_ms: int
    entry: float
    exit_price: float
    stop: float
    target: float
    qty: int
    gross: float
    cost: float
    net: float
    reason: str
    bars_held: int
    #: R multiple against the risk taken at entry, after costs.
    r: float

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class BacktestResult:
    strategy: str
    symbol: str
    trades: list[BacktestTrade] = field(default_factory=list)
    #: Bars the harness refused to evaluate, and why. Published so a run that
    #: found nothing can be told apart from a run that never looked.
    skipped: dict[str, int] = field(default_factory=dict)

    @property
    def net(self) -> float:
        return round(sum(t.net for t in self.trades), 2)

    @property
    def gross(self) -> float:
        return round(sum(t.gross for t in self.trades), 2)

    @property
    def costs(self) -> float:
        return round(sum(t.cost for t in self.trades), 2)

    def merge(self, other: "BacktestResult") -> "BacktestResult":
        self.trades.extend(other.trades)
        for k, v in other.skipped.items():
            self.skipped[k] = self.skipped.get(k, 0) + v
        return self


def _minute_of_day_ist(epoch_s: float) -> int:
    return int(((float(epoch_s) + 19800) % 86400) // 60)


def _hhmm(hhmm: str) -> int:
    hh, mm = str(hhmm).split(":")
    return int(hh) * 60 + int(mm)


def _atr_series(bars: Bars, period: int) -> np.ndarray:
    from app.engines.indicators import compute_atr
    return compute_atr(bars.high, bars.low, bars.close, period)


@dataclass
class _Open:
    """A position under replay. Deliberately the same shape the live one has."""
    sig: IntradaySignal
    entry: float
    stop: float
    target: float
    target2: Optional[float]
    qty: int
    entry_ms: int
    entry_i: int
    peak: float
    risk: float
    breakeven_done: bool = False


#: How much history each evaluation sees, in bars.
#:
#: A bounded window rather than the whole tape, for two reasons and only
#: incidentally for speed. The LIVE scanner fetches a fixed lookback too, so an
#: unbounded replay would be evaluating a longer series than production ever
#: sees — a backtest of something that does not exist. And an unbounded slice
#: makes the replay quadratic in tape length, which at 5-minute resolution is
#: the difference between a harness that runs and one nobody runs.
#:
#: The floor is what the rules actually need: the slowest EMA's warmup plus two
#: full sessions, because pivots are computed from the PRIOR session.
EVAL_WINDOW = 400


def replay(candles: Sequence, cfg: IntradayConfig, symbol: str, strategy: str, *,
           costs: Optional[CostModel] = None,
           lens: Lens = "underlying",
           qty: int = 1,
           capital: float = 100_000.0,
           window: Optional[int] = None) -> BacktestResult:
    """Replay one strategy over one symbol's tape.

    The ``underlying`` lens trades the index or stock itself, one unit per
    trade, so the result measures the SIGNAL and nothing else. That is the
    honest first question: an option overlay can turn a real edge into a bigger
    one or a small edge into a loss, but it cannot create one.
    """
    costs = costs or CostModel()
    bars = to_bars(list(candles or []))
    out = BacktestResult(strategy=strategy, symbol=symbol)
    n = len(bars)
    if n <= cfg.warmup_bars + 2:
        out.skipped["too few bars"] = 1
        return out
    evaluate = EVALUATORS.get(strategy)
    if evaluate is None:
        out.skipped["unknown strategy"] = 1
        return out

    window = max(int(window or EVAL_WINDOW), cfg.warmup_bars + 150)
    atr = _atr_series(bars, cfg.pb_atr_length)
    start_m, cut_m, end_m = (_hhmm(cfg.session_start), _hhmm(cfg.no_entry_after),
                             _hhmm(cfg.session_end))
    open_pos: Optional[_Open] = None
    last_fire_i = -10_000
    fired_today = 0
    today = ""
    cooldown = max(0, int(cfg.cooldown_bars))

    for i in range(cfg.warmup_bars, n - 1):
        day = bars.session_day[i]
        if day != today:
            today, fired_today = day, 0
        minute = _minute_of_day_ist(bars.time[i])
        session_over = minute >= end_m or bars.session_day[i + 1] != day

        # ── manage what is already on, on the NEXT bar, never this one ──────
        if open_pos is not None:
            j = i + 1
            hi, lo, close = float(bars.high[j]), float(bars.low[j]), float(bars.close[j])
            bullish = open_pos.sig.direction == "BULLISH"
            hit_stop = (lo <= open_pos.stop) if bullish else (hi >= open_pos.stop)
            final = open_pos.target2 or open_pos.target
            hit_target = (hi >= final) if bullish else (lo <= final)
            over = (bars.session_day[j] != day) or _minute_of_day_ist(bars.time[j]) >= end_m

            reason, px = "", 0.0
            if hit_stop:
                # Both in one bar is a LOSS. Nothing in the data says which came
                # first, so the harness assumes the worse.
                reason, px = ("stop" if not open_pos.breakeven_done
                              else "trailing stop"), open_pos.stop
            elif hit_target:
                reason, px = "target", final
            elif over and cfg.close_at_session_end:
                reason, px = "session end", close
            else:
                dead, why = thesis_broken(_slice(bars, j + 1, window), cfg, strategy,
                                          open_pos.sig.direction)
                if dead:
                    reason, px = why, close
            if reason:
                out.trades.append(_close(open_pos, px, int(bars.time[j] * 1000),
                                         j, reason, costs, lens, strategy, symbol))
                open_pos = None
            else:
                peak = max(open_pos.peak, hi) if bullish else min(open_pos.peak, lo)
                open_pos.peak = peak
                moved = _trail(open_pos, peak, float(atr[j]) if np.isfinite(atr[j]) else 0.0,
                               cfg, bullish)
                if moved != open_pos.stop:
                    open_pos.stop = moved
                    open_pos.breakeven_done = (
                        moved >= open_pos.entry if bullish else moved <= open_pos.entry)

        if open_pos is not None:
            continue
        if not (start_m <= minute < cut_m) or session_over:
            continue
        if i - last_fire_i < cooldown:
            out.skipped["cooldown"] = out.skipped.get("cooldown", 0) + 1
            continue
        if fired_today >= cfg.max_signals_per_symbol_per_day:
            out.skipped["daily cap"] = out.skipped.get("daily cap", 0) + 1
            continue

        ev = evaluate(_slice(bars, i + 1, window), cfg, symbol)
        if ev.signal is None:
            continue

        # ── the fill is the NEXT bar's open, never this bar's close ─────────
        j = i + 1
        fill = float(bars.open[j])
        if fill <= 0:
            continue
        bullish = ev.signal.direction == "BULLISH"
        side: Literal["buy", "sell"] = "buy" if lens == "option" else (
            "buy" if bullish else "sell")
        entry = costs.slip(fill, side)
        # The stop and target were computed against the SIGNAL bar's close, so
        # they are held at the same DISTANCE from the actual fill rather than at
        # the same price — otherwise a gap would silently change the risk.
        drift = entry - ev.signal.entry
        stop = ev.signal.stop + drift
        risk = abs(entry - stop)
        if risk <= 0:
            out.skipped["no risk after fill"] = out.skipped.get("no risk after fill", 0) + 1
            continue
        open_pos = _Open(sig=ev.signal, entry=entry, stop=stop,
                         target=ev.signal.target + drift,
                         target2=(ev.signal.target2 + drift)
                         if ev.signal.target2 is not None else None,
                         qty=qty, entry_ms=int(bars.time[j] * 1000), entry_i=j,
                         peak=entry, risk=risk)
        last_fire_i, fired_today = i, fired_today + 1

    return out


def _slice(bars: Bars, upto: int, window: int = EVAL_WINDOW) -> Bars:
    """The tape as it looked at bar ``upto - 1``, trimmed to ``window`` bars.

    Every evaluation in this harness goes through here, so "did the strategy see
    the future" has exactly one place to be wrong and one test to prove it is
    not. The trim is from the LEFT only — dropping old bars cannot leak the
    future, and it makes each evaluation see the same amount of history the live
    scanner sees.
    """
    lo = max(0, upto - int(window))
    return Bars(time=bars.time[lo:upto], open=bars.open[lo:upto],
                high=bars.high[lo:upto], low=bars.low[lo:upto],
                close=bars.close[lo:upto], volume=bars.volume[lo:upto],
                session_day=bars.session_day[lo:upto])


def _trail(pos: _Open, peak: float, atr: float, cfg: IntradayConfig,
           bullish: bool) -> float:
    """The spot trail, in replay. Same shape and same ratchet as the live one."""
    side = "long" if bullish else "short"
    sign = 1.0 if bullish else -1.0
    if pos.risk <= 0:
        return pos.stop
    if (peak - pos.entry) * sign / pos.risk < cfg.pb_breakeven_at_r:
        return pos.stop
    stop = ratchet_trail(pos.stop, pos.entry, side)
    if cfg.pb_trail_mode == "atr" and atr > 0:
        stop = ratchet_trail(stop, peak - sign * cfg.pb_trail_atr_mult * atr, side)
    return round(stop, 2)


def _close(pos: _Open, raw_exit: float, exit_ms: int, exit_i: int, reason: str,
           costs: CostModel, lens: Lens, strategy: str, symbol: str) -> BacktestTrade:
    bullish = pos.sig.direction == "BULLISH"
    side: Literal["buy", "sell"] = "sell" if lens == "option" else (
        "sell" if bullish else "buy")
    exit_price = costs.slip(max(0.01, raw_exit), side)
    move = (exit_price - pos.entry) if bullish else (pos.entry - exit_price)
    if lens == "option":
        # A bought option is long premium whichever way the thesis points, so
        # the leg is always entry -> exit on the premium itself.
        move = exit_price - pos.entry
    gross = move * pos.qty
    cost = costs.round_trip(pos.entry, exit_price, pos.qty)
    net = gross - cost
    return BacktestTrade(
        strategy=strategy, symbol=symbol, thesis=pos.sig.direction,
        entry_ms=pos.entry_ms, exit_ms=exit_ms, entry=round(pos.entry, 2),
        exit_price=round(exit_price, 2), stop=round(pos.stop, 2),
        target=round(pos.target, 2), qty=pos.qty, gross=round(gross, 2),
        cost=round(cost, 2), net=round(net, 2), reason=reason,
        bars_held=exit_i - pos.entry_i,
        r=round(net / (pos.risk * pos.qty), 3) if pos.risk > 0 else 0.0)
