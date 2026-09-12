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
from .position import should_scale_out, spot_trail
from .strategies import EVALUATORS, thesis_broken

Lens = Literal["underlying", "option"]


@dataclass(frozen=True)
class CostModel:
    """What one round trip actually costs, in the instrument's own units.

    The percentages are charged on TRADED VALUE, so a model built for one
    instrument cannot be pointed at another. Options pay STT of 0.1% on the
    premium; a futures or delta-1 book pays 0.02% on the whole notional — a
    different rate against a different base. Charging the option schedule
    against an index notional overstates the cost by roughly two orders of
    magnitude.

    That is not hypothetical. The first run of this harness did exactly that and
    reported an average of -14.5R per trade, which is arithmetically impossible
    for a book whose stop is 1R: the number was the cost model, not the
    strategy. Use :meth:`for_lens` rather than the raw constructor unless you
    know which schedule you want.

    Slippage is the term that decides whether a 5-minute strategy is viable at
    all, and it is a PARAMETER rather than a constant precisely so a sweep can
    show how sensitive the answer is to it.
    """

    #: Flat brokerage per executed order, in rupees.
    brokerage_per_order: float = 20.0
    #: STT on the SELL side, as a percentage of traded value.
    stt_sell_pct: float = 0.10
    #: Exchange transaction charge, both sides.
    exchange_pct: float = 0.0495
    #: GST on (brokerage + exchange charges).
    gst_pct: float = 18.0
    #: SEBI turnover + stamp, rolled together. Small but not zero.
    misc_pct: float = 0.0031
    #: Half the bid/ask, paid on each leg, as a percentage of price.
    slippage_pct: float = 0.50

    @classmethod
    def for_lens(cls, lens: "Lens", *,
                 slippage_pct: Optional[float] = None) -> "CostModel":
        """The right schedule for what is actually being traded.

        ``option`` is the Zerodha options schedule on premium. ``underlying`` is
        the index-futures schedule on notional, which is what a delta-1 proxy
        for these signals would pay — and its slippage default is far smaller,
        because an index future's book is nothing like a weekly option's.
        """
        if lens == "option":
            return cls(brokerage_per_order=20.0, stt_sell_pct=0.10,
                       exchange_pct=0.0495, gst_pct=18.0, misc_pct=0.0031,
                       slippage_pct=0.50 if slippage_pct is None else slippage_pct)
        return cls(brokerage_per_order=20.0, stt_sell_pct=0.02,
                   exchange_pct=0.0019, gst_pct=18.0, misc_pct=0.0021,
                   slippage_pct=0.01 if slippage_pct is None else slippage_pct)

    def round_trip(self, entry: float, exit_price: float, qty: int) -> float:
        """Rupees of cost for one complete trade. Never negative.

        ``qty`` is UNITS, not lots. One unit of an index against a flat
        per-order brokerage makes the brokerage the entire result — a trade
        nobody places. Pass the real lot.
        """
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


@dataclass
class _Open:
    """A position under replay.

    The field NAMES match :class:`~app.engines.intraday.position.IntradayPosition`
    where the live management functions read them, so this object can be passed
    straight to :func:`spot_trail` and :func:`should_scale_out` rather than the
    replay keeping its own opinion about trailing and two-stage exits. Those
    opinions is exactly what diverged: the replay ran pivot_break's breakeven
    trail on all three strategies and jumped straight to the runner target
    without banking the first one, so it measured a strategy nobody had written.
    """
    sig: IntradaySignal
    strategy: str
    thesis: str
    entry: float
    stop: float
    target: float
    target2: float
    qty: int
    entry_ms: int
    entry_i: int
    peak: float
    risk: float
    breakeven_done: bool = False
    target1_done: bool = False

    # ── the names `spot_trail` reads ──────────────────────────────────────
    @property
    def spot_entry(self) -> float:
        return self.entry

    @property
    def spot_risk(self) -> float:
        return self.risk

    @property
    def spot_stop(self) -> float:
        return self.stop


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
    #: A signal decided on a CLOSED bar, waiting to fill at the next bar's open.
    pending: Optional[IntradaySignal] = None
    last_fire_i = -10_000
    fired_today = 0
    today = ""
    cooldown = max(0, int(cfg.cooldown_bars))

    for i in range(cfg.warmup_bars, n):
        day = bars.session_day[i]
        if day != today:
            today, fired_today = day, 0
        minute = _minute_of_day_ist(bars.time[i])
        o, hi, lo, close = (float(bars.open[i]), float(bars.high[i]),
                            float(bars.low[i]), float(bars.close[i]))
        a = float(atr[i]) if np.isfinite(atr[i]) else 0.0
        session_over = minute >= end_m or (i + 1 < n and bars.session_day[i + 1] != day)

        # ── A. fill what was decided on the previous CLOSED bar ─────────────
        if pending is not None and open_pos is None:
            open_pos = _fill(pending, o, i, bars, costs, lens, qty,
                             cfg.stop_widen_mult)
            pending = None
            if open_pos is None:
                out.skipped["no risk after fill"] = out.skipped.get(
                    "no risk after fill", 0) + 1

        # ── B. manage on THIS bar, INCLUDING the bar it filled on ───────────
        #
        # The bar a position fills on is live from the fill onward, and the
        # replay used to skip it: a trade stopped out on its own entry bar was
        # carried forward instead, which quietly removed the worst outcomes
        # from the book.
        if open_pos is not None:
            bull = open_pos.thesis == "BULLISH"
            hit_stop = (lo <= open_pos.stop) if bull else (hi >= open_pos.stop)
            reason, px = "", 0.0
            if hit_stop:
                # Both in one bar is a LOSS. Nothing in the data says which came
                # first, so the harness assumes the worse.
                #
                # And the fill is the WORSE of the stop and this bar's open. A
                # stop is not a limit: a bar that opens through it fills at the
                # open, not at the price you asked for. Booking every stop at
                # exactly its own level is a systematic gift, and it is largest
                # for exactly the strategies that trail tightly — the stop sits
                # right under price, so gaps through it are the common case, not
                # the rare one.
                fill_px = min(open_pos.stop, o) if bull else max(open_pos.stop, o)
                reason, px = ("trailing stop" if open_pos.breakeven_done
                              else "stop"), fill_px
            else:
                # The first target BANKS half on a two-stage trade rather than
                # closing it, which is what the live path does.
                if cfg.use_targets and should_scale_out(open_pos,
                                                        hi if bull else lo,
                                                        long=bull):
                    half = open_pos.qty // 2
                    if half > 0:
                        out.trades.append(_close(
                            open_pos, open_pos.target, int(bars.time[i] * 1000), i,
                            "target", costs, lens, strategy, symbol, qty=half))
                        open_pos.qty -= half
                    open_pos.target1_done = True
                    open_pos.breakeven_done = True
                    open_pos.stop = max(open_pos.stop, open_pos.entry) if bull \
                        else min(open_pos.stop, open_pos.entry)
                    # The stop MOVED inside this bar, so it has to be tested
                    # against this bar's range again. Without this the runner
                    # got a free pass for the rest of the bar it banked on: a
                    # bar whose high touched the first target and then collapsed
                    # booked half at +2R and carried the remainder to the next
                    # bar as though breakeven had held.
                    #
                    # This was worth roughly +0.6R per trade of pure invention.
                    # A random walk — which by construction has nothing to find
                    # — returned +0.69R through this path.
                    if (lo <= open_pos.stop) if bull else (hi >= open_pos.stop):
                        px2 = min(open_pos.stop, o) if bull else max(open_pos.stop, o)
                        out.trades.append(_close(
                            open_pos, px2, int(bars.time[i] * 1000), i,
                            "breakeven stop", costs, lens, strategy, symbol))
                        open_pos = None
                if open_pos is None:
                    continue
                final = 0.0 if not cfg.use_targets else (
                    open_pos.target2 if (open_pos.target2 > 0
                                         and open_pos.target1_done)
                    else (0.0 if open_pos.target2 > 0 else open_pos.target))
                if final > 0 and ((hi >= final) if bull else (lo <= final)):
                    reason = "target2" if open_pos.target1_done and \
                        open_pos.target2 > 0 else "target"
                    px = final
                elif cfg.exit_after_bars and (i - open_pos.entry_i) >= cfg.exit_after_bars:
                    # The holding period the forward-return measurement points
                    # at, rather than a level the noise reaches first.
                    reason, px = "time", close
                elif session_over and cfg.close_at_session_end:
                    reason, px = "session end", close
                else:
                    dead, why = thesis_broken(_slice(bars, i + 1, window), cfg,
                                              strategy, open_pos.thesis)
                    if dead:
                        reason, px = why, close
            if reason:
                out.trades.append(_close(open_pos, px, int(bars.time[i] * 1000), i,
                                         reason, costs, lens, strategy, symbol))
                open_pos = None
            else:
                open_pos.peak = max(open_pos.peak, hi) if bull else min(open_pos.peak, lo)
                # Each input only where its own strategy's trail reads it.
                # Computing a session VWAP on every bar for a strategy that
                # never looks at one tripled this loop's cost for nothing.
                moved, _ = spot_trail(
                    open_pos, cfg, spot=close, atr=a,
                    swing=(_swing(bars, i, cfg, bull)
                           if strategy == "pivot_break"
                           and cfg.pb_trail_mode == "structure" else None),
                    vwap=(_vwap_at(bars, i, cfg)
                          if strategy == "vwap_supertrend" else None))
                if moved != open_pos.stop:
                    open_pos.stop = moved
                    open_pos.breakeven_done = open_pos.breakeven_done or (
                        moved >= open_pos.entry if bull else moved <= open_pos.entry)

        if open_pos is not None or pending is not None:
            continue
        if not (start_m <= minute < cut_m) or session_over:
            continue
        if i - last_fire_i < cooldown:
            out.skipped["cooldown"] = out.skipped.get("cooldown", 0) + 1
            continue
        if fired_today >= cfg.max_signals_per_symbol_per_day:
            out.skipped["daily cap"] = out.skipped.get("daily cap", 0) + 1
            continue
        if i + 1 >= n:
            continue        # nothing left to fill against

        # ── C. decide on this CLOSED bar, to fill at the NEXT bar's open ────
        ev = evaluate(_slice(bars, i + 1, window), cfg, symbol)
        if ev.signal is None:
            continue
        pending = ev.signal
        last_fire_i, fired_today = i, fired_today + 1

    # A position still open when the window ends is a REAL trade that the window
    # cut short, not one that never happened. Dropping it silently removed the
    # trades a fold's edge happened to land on.
    if open_pos is not None:
        out.trades.append(_close(open_pos, float(bars.close[n - 1]),
                                 int(bars.time[n - 1] * 1000), n - 1,
                                 "window end", costs, lens, strategy, symbol))
        out.skipped["open at window end"] = out.skipped.get("open at window end", 0) + 1

    return out


def _fill(sig: IntradaySignal, open_px: float, i: int, bars: Bars,
          costs: CostModel, lens: Lens, qty: int,
          cfg_widen: float = 1.0) -> Optional[_Open]:
    """Open a position at this bar's open, at the rule's own PRICES.

    The stop and target are LEVELS, not distances. A candle's low does not move
    because the next bar opened higher, and the live path does not move it:
    ``arm()`` carries ``sig.stop`` and ``sig.target`` through unchanged.

    The replay used to shift both by the fill's drift, which preserved the
    reward-to-risk ratio across a gap — so every entry came out at a clean 1:2
    however badly it filled. That is exactly backwards: a gap-up entry means
    the structural stop is FURTHER away and the trade is worse, and quietly
    renormalising it converted the worst entries into average ones.

    A fill already beyond the stop or the target is a real outcome, not an
    error: the entry bar's own management resolves it on the bar it happened.
    """
    if open_px <= 0:
        return None
    bull = sig.direction == "BULLISH"
    side: Literal["buy", "sell"] = "buy" if lens == "option" else (
        "buy" if bull else "sell")
    entry = costs.slip(open_px, side)
    stop = sig.stop
    if cfg_widen != 1.0:
        # Push the structural stop further out, keeping its side.
        stop = entry - (entry - stop) * cfg_widen
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    # A fill already the wrong side of its own stop has no trade in it.
    if (entry <= stop) if bull else (entry >= stop):
        return None
    return _Open(sig=sig, strategy=sig.strategy, thesis=sig.direction,
                 entry=entry, stop=stop, target=sig.target,
                 target2=sig.target2 if sig.target2 is not None else 0.0,
                 qty=qty, entry_ms=int(bars.time[i] * 1000), entry_i=i,
                 peak=entry, risk=risk)


def _swing(bars: Bars, i: int, cfg: IntradayConfig, bullish: bool) -> Optional[float]:
    """The swing the move came off, for the structure trail."""
    look = max(3, int(cfg.pb_atr_length))
    lo = max(0, i - look + 1)
    if i < lo:
        return None
    return float(np.min(bars.low[lo:i + 1]) if bullish
                 else np.max(bars.high[lo:i + 1]))


def _vwap_at(bars: Bars, i: int, cfg: IntradayConfig) -> Optional[float]:
    """Session VWAP at bar ``i``, for VWAP SuperTrend's own trail."""
    from .indicators import session_vwap
    lo = max(0, i - EVAL_WINDOW + 1)
    cut = _slice(bars, i + 1, EVAL_WINDOW)
    if len(cut) == 0:
        return None
    v = session_vwap(cut.high, cut.low, cut.close, cut.volume, cut.session_starts)
    return float(v[-1])


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


def _close(pos: _Open, raw_exit: float, exit_ms: int, exit_i: int, reason: str,
           costs: CostModel, lens: Lens, strategy: str, symbol: str,
           qty: Optional[int] = None) -> BacktestTrade:
    """Book one leg. ``qty`` under the position's own size is a partial exit."""
    size = int(qty if qty is not None else pos.qty)
    bullish = pos.thesis == "BULLISH"
    side: Literal["buy", "sell"] = "sell" if lens == "option" else (
        "sell" if bullish else "buy")
    exit_price = costs.slip(max(0.01, raw_exit), side)
    move = (exit_price - pos.entry) if bullish else (pos.entry - exit_price)
    if lens == "option":
        # A bought option is long premium whichever way the thesis points, so
        # the leg is always entry -> exit on the premium itself.
        move = exit_price - pos.entry
    gross = move * size
    cost = costs.round_trip(pos.entry, exit_price, size)
    net = gross - cost
    return BacktestTrade(
        strategy=strategy, symbol=symbol, thesis=pos.sig.direction,
        entry_ms=pos.entry_ms, exit_ms=exit_ms, entry=round(pos.entry, 2),
        exit_price=round(exit_price, 2), stop=round(pos.stop, 2),
        target=round(pos.target, 2), qty=size, gross=round(gross, 2),
        cost=round(cost, 2), net=round(net, 2), reason=reason,
        bars_held=exit_i - pos.entry_i,
        r=round(net / (pos.risk * size), 3) if pos.risk > 0 and size else 0.0)
