"""Bar-by-bar replay, honest about what it cannot know.

This calls the SAME :func:`~.strategy.entry_indices` the live scan calls and the
SAME contract picker. A backtest that reimplements the rules measures the
reimplementation, and the first thing to diverge is the thing being measured.

Five decisions are made against the strategy:

* **Signals come off CLOSED sessions and fill at the NEXT session's open.** A
  signal computed from a close cannot be filled at that close.
* **The premium at exit is valued at the ENTRY's implied vol.** The vega term is
  therefore exactly zero. That is deliberately unfair to this strategy: spot and
  vol are negatively correlated, so a put bought into strength gains vol when
  the fade works, and this harness refuses to credit it.
* **A session that trades through the stop is stopped, at the stop.** The worst
  point of the session is used for the stop test — the high for a put, the low
  for a call — because nothing in a daily bar says what came first.
* **Costs are the full Zerodha option schedule on both legs, plus slippage on
  both.** Brokerage is FLAT, so a small premium outlay pays a large fraction of
  it. A book sized by lots rather than by rupees reported -45R losses in this
  repo once, and the number was the fee model, not the strategy.
* **The premium is MODELLED.** No store here holds option price history. The
  model is stated as a multiple of trailing realised vol, and the honest output
  is not one return but the break-even multiple — the price at which the trade
  stops paying.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Callable, Iterable, Literal, Mapping, Optional, Sequence

import numpy as np

from app.engines.option_contracts import spec_for

from .config import SnapbackConfig
from .models import Bars, ist_day
from .pricing import bs_delta, bs_price, implied_vol_proxy, realized_vol, strike_for_delta
from .strategy import Features, entry_indices, features

ExitReason = Literal["horizon", "premium_stop", "premium_trail", "mean_touch",
                     "runner", "tape_ended"]


@dataclass(frozen=True)
class CostModel:
    """One round trip on a bought option, in rupees.

    Every percentage is charged on PREMIUM traded value. Pointing this schedule
    at an index notional overstates the cost by roughly two orders of magnitude,
    which is a mistake this repo has actually made and shipped a number from.
    """

    brokerage_per_order: float = 20.0
    #: STT on the SELL side of a bought option, on premium.
    stt_sell_pct: float = 0.10
    exchange_pct: float = 0.0495
    gst_pct: float = 18.0
    #: SEBI turnover plus stamp duty, rolled together.
    misc_pct: float = 0.0031
    #: Half the bid/ask, paid on each leg, as a percentage of premium.
    #:
    #: 0.5% is the default because that is roughly a liquid near-month option's
    #: half-spread. It is a PARAMETER and not a constant so a sweep can show how
    #: sensitive the answer is: at 2% the measured break-even VRP falls from
    #: 1.87 to 1.52, which is still above the market's 1.15-1.30 band.
    slippage_pct: float = 0.50

    def fill(self, premium: float, side: Literal["buy", "sell"]) -> float:
        adj = premium * self.slippage_pct / 100.0
        return max(0.05, premium + adj if side == "buy" else premium - adj)

    def charges(self, entry: float, exit_price: float, qty: int) -> float:
        if qty <= 0:
            return 0.0
        buy_v = max(entry, 0.0) * qty
        sell_v = max(exit_price, 0.0) * qty
        brokerage = 2 * self.brokerage_per_order
        exch = (buy_v + sell_v) * self.exchange_pct / 100.0
        stt = sell_v * self.stt_sell_pct / 100.0
        gst = (brokerage + exch) * self.gst_pct / 100.0
        misc = (buy_v + sell_v) * self.misc_pct / 100.0
        return round(brokerage + exch + stt + gst + misc, 2)


@dataclass
class Trade:
    symbol: str
    side: str
    option_type: str
    entry_ms: int
    exit_ms: int
    entry_day: str
    exit_day: str
    spot_in: float
    spot_out: float
    strike: float
    dte_in: int
    iv: float
    premium_in: float          # before slippage
    premium_out: float         # before slippage
    fill_in: float
    fill_out: float
    qty: int
    lots: int
    gross: float
    costs: float
    net: float
    ret: float                 # net / outlay, per rupee deployed
    held_days: int
    reason: ExitReason
    stretch: float
    #: The sold leg's strike, 0 for an outright.
    short_strike: float = 0.0
    #: What the hedge did, when there is one. Kept separate from ``net`` so a
    #: reader can see whether a result came from the edge or from the hedge
    #: being on the right side of the tape.
    beta: float = 0.0
    market_pnl: float = 0.0
    hedge_cost: float = 0.0
    gross_unhedged: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Result:
    trades: list[Trade] = field(default_factory=list)
    #: Symbols that produced no usable tape, and why. Never silently dropped.
    skipped: dict[str, str] = field(default_factory=dict)
    #: Signals that fired and were NOT traded, counted by reason.
    #:
    #: This exists because of a real defect it would have caught immediately:
    #: at the default 2% premium budget on 1 lakh of capital, one NIFTY lot of a
    #: 30-day 0.55-delta put costs about 21,750 — so the sizer returned zero lots
    #: and every single signal was dropped without a word. An empty result read
    #: as "the rule never fires", which is a different bug with a different fix.
    unsized: dict[str, int] = field(default_factory=dict)
    #: ``(symbol, fill day) -> why this particular signal produced no trade.``
    #:
    #: The counts above say what happened to the book; this says what happened
    #: to ONE row. The board needs the second: a signal with no trade rendered
    #: as a closed position with every column empty reads as missing data, which
    #: is the single complaint this engine's history view has already drawn.
    untraded: dict[tuple[str, str], str] = field(default_factory=dict)

    def note(self, reason: str) -> None:
        self.unsized[reason] = self.unsized.get(reason, 0) + 1

    def returns(self) -> np.ndarray:
        return np.array([t.ret for t in self.trades], dtype=float)

    def net(self) -> float:
        return float(sum(t.net for t in self.trades))

    def by_day(self) -> tuple[np.ndarray, np.ndarray]:
        """One observation per ENTRY day.

        Fifteen large caps entered on the day the index broke out are one bet
        with fifteen tickets. Every statistic downstream is computed on this,
        not on the trade list, because a t-statistic over correlated trades is
        inflated by roughly the square root of how many of them are the same
        bet — which turned a genuine 1.7 into a confident-looking 3.5 here.
        """
        if not self.trades:
            return np.array([]), np.array([])
        days: dict[str, list[float]] = {}
        for t in self.trades:
            days.setdefault(t.entry_day, []).append(t.ret)
        keys = sorted(days)
        return (np.array(keys),
                np.array([float(np.mean(days[k])) for k in keys]))


def replay(tapes: Mapping[str, Bars], cfg: SnapbackConfig, *,
           vrp: Optional[float] = None,
           cost: Optional[CostModel] = None,
           entry_override: Optional[Mapping[str, Sequence]] = None,
           entry_window: Optional[tuple[str, str]] = None) -> Result:
    """Run every symbol's tape through the rules, AS ONE PORTFOLIO.

    The portfolio part is not a refinement. The first version replayed each
    symbol independently, so it opened as many concurrent positions as the tape
    offered — 1,621 trades across 490 sessions with no cap — and the equity
    curve that came out of it compounded to -99% because on a bad day it was
    carrying twenty positions at a tenth of capital each. ``max_open_positions``
    existed in the config and was honoured only by the live scan. A backtest
    that ignores its own position limit is measuring a book nobody could hold.

    So candidates from every symbol are merged, ordered by the session they
    would be entered on, and taken only while there is room. A signal that
    arrives with the book full is DROPPED and counted, not queued — queueing
    would enter it at a price the rule never saw.

    ``entry_override`` replaces the signal's own entries with a given set of
    ``(bar_index, side)`` pairs, which is how the permutation test builds a book
    with identical exposure and random timing. The SIDE is supplied rather than
    re-derived: the first version chose it from the sign of the stretch at the
    random bar, which is the strategy's own rule with the threshold removed, so
    the null quietly contained a diluted copy of the signal.

    ``entry_window`` restricts ENTRIES to an IST day range while leaving the
    tape whole. A walk-forward fold needs both: the indicators need the sessions
    before the window to be computed at all, and the trades must not start
    before it. Slicing the tape instead hands a 60-session window to a rule with
    an 80-session warm-up, and every fold comes back with zero trades and no
    error.
    """
    cost = cost or CostModel()
    vrp = float(cfg.assumed_vrp if vrp is None else vrp)
    res = Result()
    # Built ONCE from the market tape inside this book, so every symbol is gated
    # on the same sessions and a replay of one instrument cannot disagree with a
    # replay of two hundred.
    from .regime import MARKET_SYMBOL, gate_for
    from .hedge import FuturesCost, rolling_beta
    gate = gate_for(tapes, market_filter=cfg.market_filter,
                    ema_period=cfg.market_ema)
    market = tapes.get(MARKET_SYMBOL)
    fut_cost = FuturesCost()
    betas: dict[str, dict[str, float]] = {}
    market_at: dict[str, float] = {}
    if cfg.hedge_mode != "none":
        if market is None:
            res.skipped.setdefault(MARKET_SYMBOL, "")
            res.skipped[MARKET_SYMBOL] = (
                f"hedge_mode is '{cfg.hedge_mode}' but there is no "
                f"{MARKET_SYMBOL} tape in this book — every trade ran UNHEDGED")
        else:
            market_at = {ist_day(float(market.time[i])): float(market.close[i])
                         for i in range(len(market))}
    if gate is None and cfg.market_filter != "off":
        res.skipped[MARKET_SYMBOL] = (
            f"market gate is '{cfg.market_filter}' but there is no "
            f"{MARKET_SYMBOL} tape in this book — every signal was taken ungated")

    prepared: dict[str, tuple] = {}
    candidates: list[tuple[float, str, int, str]] = []
    for symbol, bars in tapes.items():
        n = len(bars)
        if n < cfg.warmup_bars() + cfg.hold_days + 2:
            res.skipped[symbol] = f"only {n} sessions"
            continue
        spec = spec_for(symbol)
        if spec is None:
            res.skipped[symbol] = "no published strike step or lot size"
            continue
        f = features(bars, cfg)
        iv_series = implied_vol_proxy(f.rv, vrp)
        prepared[symbol] = (bars, f, iv_series, spec)
        if cfg.hedge_mode != "none" and market is not None:
            betas[symbol] = rolling_beta(bars, market)

        if entry_override is not None:
            plan = [(int(i), str(sd)) for i, sd in entry_override.get(symbol, ())]
        else:
            plan = []
            for side in cfg.sides():
                plan += [(int(i), side)
                         for i in entry_indices(bars, cfg, side, gate)]
        if entry_window is not None:
            lo, hi = entry_window
            # Filtered on the FILL session (bar i+1), not the signal session.
            # That is the day capital is committed, the day ``Trade.entry_day``
            # reports and the day ``by_day`` clusters on.
            plan = [(i, sd) for i, sd in plan
                    if i + 1 < n and lo <= ist_day(float(bars.time[i + 1])) < hi]
        for i, side in plan:
            if i + 1 < n:
                candidates.append((float(bars.time[i]), symbol, i, side))

    # One book, in the order the sessions happened. Ties broken by symbol so a
    # full book drops the same names on every run rather than whichever the
    # dictionary happened to yield first.
    candidates.sort(key=lambda c: (c[0], c[1]))
    open_until: dict[str, int] = {}          # symbol -> exit bar timestamp
    cap = max(int(cfg.max_open_positions), 1)

    for ts, symbol, i, side in candidates:
        for sym in [s for s, until in open_until.items() if until <= ts]:
            open_until.pop(sym, None)
        bars, f, iv_series, spec = prepared[symbol]
        fill_day = ist_day(float(bars.time[i + 1]))
        key = (symbol, fill_day)

        def note(reason: str, _k=key) -> None:
            res.note(reason)
            res.untraded.setdefault(_k, reason)

        if cfg.one_position_per_underlying and symbol in open_until:
            note("already holding this underlying")
            continue
        if len(open_until) >= cap:
            note(f"book full at {cap} open positions")
            continue
        t = _run_one(symbol, bars, f, iv_series, cfg, cost, i, side, spec, note,
                     beta_by_day=betas.get(symbol), market_at=market_at,
                     fut_cost=fut_cost)
        if t is None:
            res.untraded.setdefault(key, "the replay could not price this signal")
            continue
        res.untraded.pop(key, None)
        res.trades.append(t)
        open_until[symbol] = t.exit_ms // 1000
    res.trades.sort(key=lambda t: t.entry_ms)
    return res


def _run_one(symbol: str, bars: Bars, f: Features, iv: np.ndarray,
             cfg: SnapbackConfig, cost: CostModel, i: int, side: str,
             spec, note: Callable[[str], None], *,
             beta_by_day: Optional[Mapping[str, float]] = None,
             market_at: Optional[Mapping[str, float]] = None,
             fut_cost=None) -> Optional[Trade]:
    n = len(bars)
    call = side == "fade_down"
    entry_bar = i + 1                       # fill at the NEXT session's open
    S0 = float(bars.open[entry_bar])
    vol = float(iv[i])
    if S0 <= 0 or not math.isfinite(vol) or vol <= 0:
        note("no realised vol at entry")
        return None

    dte = int(cfg.min_dte)
    T0 = dte / 365.0
    strike = float(strike_for_delta(S0, vol, T0, cfg.target_delta,
                                    call=call, step=spec.strike_step))
    # The sold leg of a vertical, when one is configured. Its premium is
    # SUBTRACTED from the debit and its delta from the exposure, so every
    # downstream number — the stop, the sizing, the hedge — follows from the
    # spread rather than from the long leg alone.
    short_strike = 0.0
    if cfg.short_leg_delta > 0:
        short_strike = float(strike_for_delta(S0, vol, T0, cfg.short_leg_delta,
                                              call=call, step=spec.strike_step))
        if (call and short_strike <= strike) or (not call and short_strike >= strike):
            note("short leg did not resolve further out of the money")
            short_strike = 0.0
    prem_in = _value(S0, strike, short_strike, T0, vol, call,
                     cfg.smile_slope, S0, cfg.smile_itm_slope)
    if prem_in < max(cfg.min_option_premium, 0.05):
        note(f"premium below {cfg.min_option_premium:g}")
        return None

    fill_in = cost.fill(prem_in, "buy")
    lots = _lots(fill_in, spec.lot_size, cfg)
    if lots <= 0:
        note(f"one lot costs more than the premium budget "
             f"({cfg.premium_pct_of_capital:g}% of "
             f"{cfg.capital_inr:,.0f} = "
             f"{cfg.capital_inr * cfg.premium_pct_of_capital / 100:,.0f})")
        return None
    qty = lots * spec.lot_size

    stop_prem = fill_in * (1.0 - cfg.premium_stop_pct / 100.0)
    best_prem = fill_in
    reason: ExitReason = "horizon"
    horizon_bar = entry_bar + cfg.hold_days - 1
    # A runner may hold past the horizon, but never into the expiry cliff: the
    # last five days of a contract are gamma, not the drift this trade is for.
    last_bar = horizon_bar
    if cfg.runner_mult > 0:
        last_bar = entry_bar + max(cfg.hold_days, dte - 5) - 1
    best_run = 0.0
    exit_bar = min(horizon_bar, n - 1)
    prem_out = 0.0

    for d in range(entry_bar, min(last_bar + 1, n)):
        years = max(dte - (d - i), 1) / 365.0
        # The worst point of the session for a long option is the extreme that
        # moves against it. Nothing in a daily bar says whether that came before
        # or after the favourable one, so the harness assumes the worse.
        worst_spot = float(bars.high[d]) if not call else float(bars.low[d])
        worst_prem = _value(worst_spot, strike, short_strike, years, vol, call,
                            cfg.smile_slope, S0, cfg.smile_itm_slope)
        close_prem = _value(float(bars.close[d]), strike, short_strike, years,
                            vol, call, cfg.smile_slope, S0, cfg.smile_itm_slope)

        if cfg.premium_stop_pct < 100.0 and worst_prem <= stop_prem:
            exit_bar, prem_out, reason = d, stop_prem, "premium_stop"
            break
        if cfg.premium_trail_pct > 0:
            trail = best_prem * (1.0 - cfg.premium_trail_pct / 100.0)
            if worst_prem <= trail and d > entry_bar:
                exit_bar, prem_out, reason = d, trail, "premium_trail"
                break
            best_prem = max(best_prem,
                            _value(float(bars.low[d] if not call
                                         else bars.high[d]),
                                   strike, short_strike, years, vol, call,
                                   cfg.smile_slope, S0, cfg.smile_itm_slope))
        if cfg.exit_mode in ("mean_touch", "either") and d > entry_bar:
            mean = float(f.ema[d])
            touched = (bars.low[d] <= mean) if not call else (bars.high[d] >= mean)
            if touched:
                exit_bar, prem_out, reason = d, close_prem, "mean_touch"
                break
        # The horizon, and the one case that survives it. The edge here is a
        # right TAIL, and a fixed horizon closes a winner mid-move as readily as
        # it closes a loser. A trade already at ``runner_mult`` times its cost
        # keeps running under a give-back ratchet instead.
        if d >= horizon_bar:
            running = (cfg.runner_mult > 0
                       and close_prem >= fill_in * cfg.runner_mult)
            if d == horizon_bar and not running:
                exit_bar, prem_out, reason = d, close_prem, "horizon"
                break
            if d > horizon_bar:
                give = best_run * (1.0 - cfg.runner_trail_pct / 100.0)
                if cfg.runner_trail_pct > 0 and worst_prem <= give:
                    exit_bar, prem_out, reason = d, give, "runner"
                    break
                if not running or d == last_bar:
                    exit_bar, prem_out, reason = d, close_prem, "runner"
                    break
            best_run = max(best_run, close_prem)
        exit_bar, prem_out = d, close_prem
    else:
        # The loop ran out of TAPE rather than out of rules. That is a position
        # still open, not one that closed, and the distinction decides whether
        # the board shows an operator a realised exit on something they may be
        # holding right now. Stated as a for/else so it cannot drift from the
        # loop's own bounds the way a hand-written length test did.
        reason = "tape_ended"

    fill_out = cost.fill(prem_out, "sell") if prem_out > 0 else 0.0
    gross = (fill_out - fill_in) * qty
    charges = cost.charges(fill_in, fill_out, qty)
    if short_strike:
        # A spread trades TWO contracts each way. Charging the schedule on the
        # net debit would price a two-legged trade as a one-legged one, which is
        # exactly the flattery a spread is suspected of.
        charges *= 2.0
    net = gross - charges
    outlay = fill_in * qty
    gross_unhedged = net

    # The hedge, applied where the trade is priced so nothing downstream has to
    # reconstruct it. A trade whose beta or market marks are missing runs
    # UNHEDGED rather than being dropped — but it is counted, because a book
    # that silently mixes hedged and unhedged trades is measuring neither.
    beta = market_pnl_v = hedge_charge = 0.0
    if cfg.hedge_mode != "none":
        from .hedge import rebalanced as _rebalanced
        entry_day = ist_day(float(bars.time[entry_bar]))
        b = (beta_by_day or {}).get(entry_day)
        # One delta, one spot and one index mark per session HELD, entry first.
        # The hedge carried over session t is sized on day t's delta and earns
        # day t-to-t+1's index move, so nothing reads a price that had not
        # printed. Measured, this differs from a static entry-delta hedge by
        # 0.05 percentage points per entry day — the delta rising and the spot
        # falling nearly cancel — but the static version is an assumption and
        # this one is a calculation.
        deltas: list[float] = []
        spots: list[float] = []
        marks: list[float] = []
        for d in range(entry_bar, exit_bar + 1):
            m = (market_at or {}).get(ist_day(float(bars.time[d])))
            if m is None:
                break
            years = max(dte - (d - i), 1) / 365.0
            sp = float(bars.close[d]) if d > entry_bar else S0
            d_net = float(bs_delta(sp, strike, years, vol, call=call))
            if short_strike:
                d_net -= float(bs_delta(sp, short_strike, years, vol, call=call))
            deltas.append(d_net)
            spots.append(sp)
            marks.append(float(m))
        if b is None or len(marks) < 2:
            note("no beta or market marks — this trade ran unhedged")
        else:
            beta = float(b)
            market_pnl_v, hedge_charge = _rebalanced(
                deltas=deltas, beta=beta, spots=spots, qty=qty, market=marks,
                cost=fut_cost)
            net = net - market_pnl_v - hedge_charge
    return Trade(
        symbol=symbol, side=side, option_type="CE" if call else "PE",
        entry_ms=int(bars.time[entry_bar] * 1000),
        exit_ms=int(bars.time[exit_bar] * 1000),
        entry_day=ist_day(float(bars.time[entry_bar])),
        exit_day=ist_day(float(bars.time[exit_bar])),
        spot_in=S0, spot_out=float(bars.close[exit_bar]), strike=strike,
        short_strike=round(short_strike, 2),
        dte_in=dte, iv=vol, premium_in=prem_in, premium_out=prem_out,
        fill_in=fill_in, fill_out=fill_out, qty=qty, lots=lots,
        gross=round(gross, 2), costs=charges, net=round(net, 2),
        ret=float(net / outlay) if outlay > 0 else 0.0,
        held_days=exit_bar - entry_bar + 1, reason=reason,
        stretch=float(f.stretch[i]),
        beta=round(beta, 3), market_pnl=round(market_pnl_v, 2),
        hedge_cost=round(hedge_charge, 2), gross_unhedged=round(gross_unhedged, 2),
    )


def _value(spot: float, strike: float, short_strike: float, years: float,
           vol: float, call: bool, slope: float = 0.0, ref: float = 0.0,
           itm_slope: Optional[float] = None) -> float:
    """What the position is worth — one contract, or the spread's difference.

    Each STRIKE is priced at its own skewed vol. ``ref`` is the spot the skew is
    measured from and is fixed at ENTRY: re-anchoring it to the current spot
    would re-price the same contract onto a different point of the smile every
    day, which is a vol view the harness does not hold and cannot defend.
    """
    from .pricing import smile_vol
    anchor = ref or spot
    v1 = (float(smile_vol(anchor, strike, vol, slope, itm_slope=itm_slope))
          if slope else vol)
    v = float(bs_price(spot, strike, years, v1, call=call))
    if short_strike:
        v2 = (float(smile_vol(anchor, short_strike, vol, slope,
                              itm_slope=itm_slope)) if slope else vol)
        v -= float(bs_price(spot, short_strike, years, v2, call=call))
    return v


def _lots(premium: float, lot_size: int, cfg: SnapbackConfig) -> int:
    if cfg.sizing_mode == "LOTS":
        return max(1, min(int(cfg.lots), int(cfg.max_lots)))
    per_lot = max(premium, 0.0) * max(lot_size, 1)
    if per_lot <= 0:
        return 0
    budget = cfg.capital_inr * cfg.premium_pct_of_capital / 100.0
    return int(min(budget // per_lot, cfg.max_lots))


def returns_at_vrp(tapes: Mapping[str, Bars], cfg: SnapbackConfig,
                   cost: Optional[CostModel] = None) -> Callable[[float], np.ndarray]:
    """A callback for :func:`~.pricing.break_even_vrp`.

    Re-prices the WHOLE book at each multiple rather than scaling stored
    returns. The entry premium sets the strike, the quantity, the stop and the
    cost, so every one of them moves with the vol assumption — scaling a stored
    return by a ratio afterwards is wrong and looks right.
    """
    def at(vrp: float) -> np.ndarray:
        _, daily = replay(tapes, cfg, vrp=vrp, cost=cost).by_day()
        return daily if len(daily) else np.array([0.0])
    return at
