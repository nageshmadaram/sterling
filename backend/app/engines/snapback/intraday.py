"""Causal, cost-aware intraday Snapback research on observed option candles.

Times are candle OPEN times in epoch seconds. A signal exists only once its
candle has closed; replay enters at the NEXT candle's open. Premium targets
are option points, never underlying points. They are soft continuation triggers:
a completed strong candle may promote a runner, otherwise a touch exits at the
observed close (not a hindsight fill at the target). Stops already in force win
ambiguous OHLC bars. New trailing stops become effective on the following bar.

``make_plan`` returns quote-referenced levels and an all-in cost estimate, with
an explicit minimum target required to pay costs and meet net reward/risk.
``open_trade`` / ``advance_trade`` are immutable lifecycle transitions.
``replay_intraday`` requires one real, synchronized option contract, identified
by option_type and lot_size. It does not manufacture minute option prices from
spot/Black-Scholes, and leaves incomplete-session positions unresolved.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
import math
import time
from typing import Any

import numpy as np

from .config import SnapbackConfig
from .models import Bars, IST, SnapbackSignal, ist_day
from ..indicators.adx import adx
from ..indicators.ema import compute_ema

_SESSION_START = 9 * 60 + 15
_SESSION_END = 15 * 60 + 30
_WARMUP = 30
_TICK = 0.05


def _minute(epoch: float) -> float:
    d = datetime.fromtimestamp(float(epoch), IST)
    return d.hour * 60 + d.minute + d.second / 60 + d.microsecond / 60_000_000


def _slice(bars: Bars, mask: Any) -> Bars:
    return Bars(*(np.asarray(getattr(bars, name))[mask] for name in
                  ("time", "open", "high", "low", "close", "volume")))


def _validate(bars: Bars, timeframe: int, *, contiguous: bool = False) -> None:
    if timeframe not in (1, 3, 5):
        raise ValueError("Intraday timeframe must be 1, 3 or 5 minutes")
    cols = [np.asarray(getattr(bars, n), dtype=float) for n in
            ("time", "open", "high", "low", "close", "volume")]
    if any(x.ndim != 1 or len(x) != len(bars) for x in cols):
        raise ValueError("Candle columns must have equal one-dimensional lengths")
    if not len(bars):
        return
    if any(not np.all(np.isfinite(x)) for x in cols):
        raise ValueError("Candles must contain finite numbers")
    ts, op, hi, lo, cl, vol = cols
    if (np.any(np.diff(ts) <= 0) or np.any(np.minimum.reduce((op, hi, lo, cl)) <= 0)
            or np.any(hi < np.maximum(op, cl)) or np.any(lo > np.minimum(op, cl))
            or np.any(hi < lo) or np.any(vol < 0)):
        raise ValueError("Candles must be ordered, unique and valid OHLCV")
    for i, t in enumerate(ts):
        minute = _minute(t)
        if (not _SESSION_START <= minute < _SESSION_END
                or not math.isclose((minute - _SESSION_START) % timeframe, 0, abs_tol=1e-8)):
            raise ValueError("Candles must be aligned intraday bars in the IST trading session")
        if i and ist_day(t) == ist_day(ts[i - 1]):
            delta = (t - ts[i - 1]) / (timeframe * 60)
            if (not math.isclose(delta, round(delta), abs_tol=1e-8)
                    or (contiguous and not math.isclose(delta, 1.0))):
                raise ValueError("Candles do not match the configured intraday timeframe or contain gaps")


def evaluate_intraday(bars: Bars, cfg: SnapbackConfig, symbol: str = "",
                      asof: float | None = None) -> list[SnapbackSignal]:
    """Return at most one setup on the latest fresh, CLOSED current-session bar.

    Session gaps restart the warmup. ADX needs 29 observations, so neither its
    zero-filled warmup nor yesterday's regime can authorize a trade today.
    """
    if getattr(cfg, "trading_mode", "swing") not in ("scalp", "intraday"):
        return []
    now = time.time() if asof is None else float(asof)
    tf = cfg.scalp_timeframe_minutes
    try:
        if not math.isfinite(now):
            return []
        minute = _minute(now)
        if not cfg.scalp_entry_start_minute <= minute <= cfg.scalp_entry_end_minute:
            return []
        day = ist_day(now)
        mask = np.array([ist_day(t) == day and t + tf * 60 <= now for t in bars.time], dtype=bool)
        b = _slice(bars, mask)
        _validate(b, tf)
    except (ValueError, OverflowError, OSError, TypeError):
        return []
    if len(b) < _WARMUP or now - (b.time[-1] + tf * 60) >= tf * 60:
        return []
    gaps = np.flatnonzero(np.diff(b.time) != tf * 60)
    if len(gaps):
        b = _slice(b, slice(int(gaps[-1]) + 1, None))
    if len(b) < _WARMUP:
        return []

    c = b.close
    previous_window, current_window = c[-22:-2], c[-21:-1]
    prior_mean, mean = float(np.mean(previous_window)), float(np.mean(current_window))
    prior_sd, sd = float(np.std(previous_window, ddof=1)), float(np.std(current_window, ddof=1))
    if min(prior_sd, sd) <= 0:
        return []
    lo, hi = mean - 2 * sd, mean + 2 * sd
    prior_lo, prior_hi = prior_mean - 2 * prior_sd, prior_mean + 2 * prior_sd
    ema = compute_ema(c, 9)
    strength = float(adx(b.high, b.low, c, 14)[-1])
    use_adx = getattr(cfg, "use_adx_filter", False) or cfg.scalp_max_adx < 25.0
    if use_adx and strength > cfg.scalp_max_adx:
        return []
    volume_mean = float(np.mean(b.volume[-21:-1]))
    rvol = float(b.volume[-1] / volume_mean) if volume_mean > 0 else None
    if cfg.scalp_min_relative_volume > 0 and (rvol is None or rvol < cfg.scalp_min_relative_volume):
        return []

    use_ema = getattr(cfg, "use_ema_confirmation", False)
    up = (c[-2] > prior_hi and mean < c[-1] < hi and c[-1] < b.open[-1]
          and c[-1] < c[-2])
    if use_ema:
        up = up and (c[-1] <= ema[-1])

    down = (cfg.allow_fade_down and c[-2] < prior_lo and lo < c[-1] < mean
            and c[-1] > b.open[-1] and c[-1] > c[-2])
    if use_ema:
        down = down and (c[-1] >= ema[-1])

    if not (up or down):
        return []
    tr = np.maximum.reduce((b.high[1:] - b.low[1:],
                            np.abs(b.high[1:] - c[:-1]), np.abs(b.low[1:] - c[:-1])))
    atr = float(np.mean(tr[-14:]))
    if atr <= 0:
        return []
    # Actual minute return variance, annualized at the configured observations
    # per NSE trading session. This is a noisy short-window proxy, not an IV quote.
    rv = float(np.std(np.diff(np.log(c[-21:])), ddof=1)
               * math.sqrt(252 * ((_SESSION_END - _SESSION_START) / tf)))
    assumed_iv = max(0.05, min(3.0, rv))
    side = "fade_up" if up else "fade_down"
    return [SnapbackSignal(
        symbol=symbol, side=side, direction="BEARISH" if up else "BULLISH",
        option_type="PE" if up else "CE", timestamp_ms=int(b.time[-1] * 1000),
        entry=float(c[-1]), mean_target=mean, stretch=abs(float(c[-2]) - prior_mean) / atr,
        atr=atr, realized_vol=rv, assumed_iv=assumed_iv,
        level=prior_hi if up else prior_lo, strength="MODERATE",
        reasons=("Prior Bollinger stretch reversed inside the band",
                 "Closed candle confirms reversal across EMA9",
                 f"ADX14 {strength:.1f} is below the trend cutoff",
                 "Premium costs and risk must pass the contract plan before entry"),
        metrics={"trading_mode": cfg.trading_mode, "timeframe_minutes": tf,
                 "signal_bar_closed": True, "signal_close_timestamp_ms": int((b.time[-1] + tf * 60) * 1000),
                 "adx": strength, "ema9": float(ema[-1]), "relative_volume": rvol,
                 "bollinger_upper": hi, "bollinger_lower": lo,
                 "volatility_basis": "same-session minute returns, annualized 252 x 375/timeframe; not quoted IV",
                 "iv_source": "intraday_realized_proxy", "research_only": True},
    )]


def _ceil_tick(price: float) -> float:
    return round(math.ceil((price - 1e-10) / _TICK) * _TICK, 2)


def make_plan(premium: float, lot_size: int, cfg: SnapbackConfig,
              spread_points: float = 0.0) -> dict[str, Any]:
    """Size first, then raise the target to the minimum net reward/risk floor.

    Estimated variable costs already include spread/slippage/fees; an observed
    spread is a floor on that estimate, not charged twice. Fixed round-trip
    fees are per complete trade, not per lot. Risk limits assume stop execution;
    gaps can still lose more. Levels reference the supplied observed premium.
    """
    rejected: list[str] = []
    if (not math.isfinite(float(premium)) or premium <= 0 or isinstance(lot_size, bool)
            or int(lot_size) != lot_size or lot_size <= 0
            or not math.isfinite(float(spread_points)) or spread_points < 0):
        return {"accepted": False, "rejection_reasons": ["Invalid premium, lot size or spread"],
                "lots": 0, "quantity": 0}
    cost = max(float(cfg.scalp_round_trip_cost_points), float(spread_points))
    fixed = float(cfg.scalp_fixed_cost_inr)
    stop_points = float(cfg.scalp_stop_points)
    if premium <= stop_points:
        return {"accepted": False, "rejection_reasons": ["Premium must exceed the requested stop distance"],
                "lots": 0, "quantity": 0}
    stop = max(_TICK, math.floor((premium - stop_points + 1e-10) / _TICK) * _TICK)
    effective_stop = premium - stop
    capital = float(cfg.capital_inr)
    risk_budget = capital * cfg.scalp_risk_pct / 100
    risk_lots = max(0, math.floor((risk_budget - fixed) / ((max(effective_stop, 0) + cost) * lot_size)))
    cash_lots = max(0, math.floor((capital - fixed) / ((premium + cost) * lot_size)))
    cap = int(cfg.max_lots)
    if cfg.sizing_mode == "LOTS":
        cap = min(cap, int(cfg.lots))
    else:
        premium_budget = capital * cfg.premium_pct_of_capital / 100
        cap = min(cap, max(0, math.floor((premium_budget - fixed) / ((premium + cost) * lot_size))))
    lots = min(cap, risk_lots, cash_lots)
    if lots < 1:
        rejected.append("One lot exceeds the cash, premium allocation or stop-risk budget")
    qty = max(0, lots) * int(lot_size)
    # Compute informative one-lot economics even when the plan is rejected.
    unit_cost = cost + fixed / max(qty, lot_size)
    requested = float(cfg.scalp_target_points)
    minimum = unit_cost + cfg.scalp_min_net_rr * (max(effective_stop, 0) + unit_cost)
    target_points = _ceil_tick(max(requested, minimum,
                                  unit_cost + cfg.scalp_lock_points + _TICK))
    target = _ceil_tick(premium + target_points)
    target_points = target - premium
    return {"accepted": not rejected, "rejection_reasons": rejected,
            "trading_mode": cfg.trading_mode, "premium_point_basis": "option_premium",
            "entry": float(premium), "stop": round(stop, 2), "target": target,
            "runner_trigger": target, "target_is_soft_trigger": True,
            "breakeven": _ceil_tick(premium + unit_cost),
            "profit_lock": _ceil_tick(premium + unit_cost + cfg.scalp_lock_points),
            "requested_target_points": requested, "effective_target_points": round(target_points, 4),
            "stop_points": effective_stop, "trail_points": cfg.scalp_trail_points,
            "lots": max(0, lots), "lot_size": int(lot_size), "quantity": qty,
            "estimated_variable_cost_points": cost, "estimated_fixed_cost_inr": fixed,
            "estimated_total_cost_inr": round(cost * qty + fixed, 6),
            "estimated_net_target_inr": round((target_points - cost) * qty - fixed, 6),
            "estimated_stop_loss_inr": round((effective_stop + cost) * qty + fixed, 6),
            "net_reward_risk": round((target_points - unit_cost) / (max(effective_stop, 0) + unit_cost), 8),
            "risk_budget_inr": risk_budget, "premium_outlay_inr": premium * qty,
            "risk_note": "Stop-risk estimate; gaps and execution costs can exceed it"}


@dataclass(frozen=True)
class PremiumTradeState:
    entry: float
    entry_time: float
    stop: float
    target: float
    profit_lock: float
    quantity: int
    variable_cost_points: float
    fixed_cost_inr: float
    symbol: str = ""
    option_type: str = ""
    high_water: float = 0.0
    bars_held: int = 0
    runner: bool = False
    exited: bool = False
    exit_time: float | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    net_pnl_inr: float | None = None


def open_trade(plan: dict[str, Any], timestamp: float, *, symbol: str = "",
               option_type: str = "") -> PremiumTradeState:
    if not plan.get("accepted") or plan.get("quantity", 0) <= 0:
        raise ValueError("Cannot open a rejected premium plan")
    return PremiumTradeState(
        entry=plan["entry"], entry_time=float(timestamp), stop=plan["stop"],
        target=plan["runner_trigger"], profit_lock=plan["profit_lock"],
        quantity=plan["quantity"], variable_cost_points=plan["estimated_variable_cost_points"],
        fixed_cost_inr=plan["estimated_fixed_cost_inr"], symbol=symbol,
        option_type=option_type, high_water=plan["entry"],
    )


def _exit(state: PremiumTradeState, price: float, timestamp: float, reason: str) -> PremiumTradeState:
    pnl = ((price - state.entry - state.variable_cost_points) * state.quantity
           - state.fixed_cost_inr)
    return replace(state, exited=True, exit_time=float(timestamp), exit_price=float(price),
                   exit_reason=reason, net_pnl_inr=round(pnl, 6))


def advance_trade(state: PremiumTradeState, *, timestamp: float, open: float,
                  high: float, low: float, close: float, cfg: SnapbackConfig) -> PremiumTradeState:
    """Consume one option candle. Existing stops win; close-derived stops wait.

    Caller supplies each candle once, at most one timeframe after the previous
    transition. The first candle begins at entry_time, the next-bar-open fill.
    Target retracement exits use candle CLOSE; OHLC cannot prove target execution
    and a later continuation decision at the same time.
    """
    if state.exited:
        return state
    vals = (timestamp, open, high, low, close)
    if (not all(math.isfinite(float(x)) for x in vals) or min(open, high, low, close) <= 0
            or high < max(open, low, close) or low > min(open, high, close)):
        raise ValueError("Invalid option candle")
    expected = state.entry_time + state.bars_held * cfg.scalp_timeframe_minutes * 60
    if not math.isclose(timestamp, expected, abs_tol=1e-6):
        raise ValueError("Trade candles must be contiguous, chronological and consumed once")
    state = replace(state, bars_held=state.bars_held + 1)
    end = timestamp + cfg.scalp_timeframe_minutes * 60
    if open <= state.stop:
        return _exit(state, open, timestamp, "gap_stop")
    if _minute(timestamp) >= cfg.scalp_square_off_minute or ist_day(timestamp) != ist_day(state.entry_time):
        return _exit(state, open, timestamp, "session_exit")
    if low <= state.stop:
        return _exit(state, state.stop, end, "trailing_stop" if state.stop > state.entry else "stop")
    if _minute(end) >= cfg.scalp_square_off_minute:
        return _exit(state, close, end, "session_exit")
    if not state.runner and high >= state.target:
        strong_close = close >= state.target and close >= low + 0.65 * (high - low)
        if not strong_close:
            return _exit(state, close, end, "target_retracement")
        state = replace(state, runner=True)
    max_bars = cfg.scalp_runner_max_bars if state.runner else cfg.scalp_max_hold_bars
    if state.bars_held >= max_bars:
        return _exit(state, close, end, "runner_timeout" if state.runner else "timeout")
    # Close-confirmed high water avoids a same-bar high/low ordering assumption.
    high_water = max(state.high_water, close)
    stop = state.stop
    if state.runner or close >= state.profit_lock + max(cfg.scalp_trail_points, _TICK):
        stop = max(stop, state.profit_lock, math.floor((high_water - cfg.scalp_trail_points) / _TICK) * _TICK)
    return replace(state, stop=round(stop, 2), high_water=high_water)


def replay_intraday(underlying: Bars, premium_bars: Bars, cfg: SnapbackConfig,
                    symbol: str = "", *, option_type: str, lot_size: int,
                    option_symbol: str = "") -> dict[str, Any]:
    """Historical research for one observed option contract; never live execution.

    Both tapes must contain exactly the same aligned, consecutive timestamps.
    Each entry uses only a previous closed underlying signal of matching CE/PE
    type. Cash/risk sizing is recalculated from realized equity. One position,
    daily realized loss circuit breaker, cooldown after exit and entry limit
    apply. A session truncated before square-off leaves an unresolved position;
    no later entries are permitted until its outcome is known.
    """
    if cfg.trading_mode not in ("scalp", "intraday"):
        raise ValueError("Select scalp or intraday mode for option-candle replay")
    if option_type not in ("CE", "PE") or isinstance(lot_size, bool) or int(lot_size) != lot_size or lot_size <= 0:
        raise ValueError("A CE/PE option type and positive integer contract lot size are required")
    tf = cfg.scalp_timeframe_minutes
    _validate(underlying, tf, contiguous=True)
    _validate(premium_bars, tf, contiguous=True)
    if len(underlying) != len(premium_bars) or not np.array_equal(underlying.time, premium_bars.time):
        raise ValueError("Replay requires synchronized observed underlying and option candles; no gaps or synthetic prices")
    trades: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    position: PremiumTradeState | None = None
    pending: SnapbackSignal | None = None
    capital = float(cfg.capital_inr)
    initial = capital
    peak = capital
    max_drawdown = 0.0
    day = ""
    day_start = capital
    day_pnl = 0.0
    entries_today = 0
    last_exit_index = -10 ** 9
    rejected = 0
    unresolved_reason = None
    session_start_index = 0
    for i, ts_raw in enumerate(underlying.time):
        ts = float(ts_raw)
        current_day = ist_day(ts)
        if current_day != day:
            if position is not None:
                unresolved_reason = "Previous session ended before position could be squared off"
                break
            day, day_start, day_pnl, entries_today = current_day, capital, 0.0, 0
            session_start_index = i
            pending = None
            last_exit_index = -10 ** 9
        entry_window = cfg.scalp_entry_start_minute <= _minute(ts) <= cfg.scalp_entry_end_minute
        if (pending is not None and position is None and entry_window
                and pending.option_type == option_type and capital > 0
                and entries_today < cfg.scalp_max_trades_per_day
                and day_pnl > -day_start * cfg.scalp_daily_loss_pct / 100
                and i - last_exit_index > cfg.scalp_cooldown_bars):
            remaining_daily_risk = max(0.0, day_start * cfg.scalp_daily_loss_pct / 100 + day_pnl)
            bounded_risk_pct = min(cfg.scalp_risk_pct, remaining_daily_risk / capital * 100)
            plan = make_plan(float(premium_bars.open[i]), lot_size,
                             replace(cfg, capital_inr=capital, scalp_risk_pct=bounded_risk_pct))
            if plan["accepted"]:
                position = open_trade(plan, ts, symbol=option_symbol or symbol, option_type=option_type)
                plans.append({"entry_time": ts, "signal_time_ms": pending.timestamp_ms, **plan})
                entries_today += 1
            else:
                rejected += 1
        pending = None
        if position is not None:
            position = advance_trade(position, timestamp=ts, open=float(premium_bars.open[i]),
                                     high=float(premium_bars.high[i]), low=float(premium_bars.low[i]),
                                     close=float(premium_bars.close[i]), cfg=cfg)
            if position.exited:
                trade = asdict(position)
                trade["gross_pnl_inr"] = (position.exit_price - position.entry) * position.quantity
                trade["estimated_cost_inr"] = position.variable_cost_points * position.quantity + position.fixed_cost_inr
                trades.append(trade)
                capital += float(position.net_pnl_inr)
                day_pnl += float(position.net_pnl_inr)
                peak = max(peak, capital)
                max_drawdown = max(max_drawdown, peak - capital)
                last_exit_index = i
                position = None
        if position is None:
            signals = evaluate_intraday(_slice(underlying, slice(session_start_index, i + 1)), cfg, symbol,
                                         asof=ts + tf * 60)
            pending = signals[-1] if signals else None
    if position is not None and unresolved_reason is None:
        unresolved_reason = "Option tape ended with an open position before a valid exit"
    pnl = capital - initial
    winners = sum(t["net_pnl_inr"] > 0 for t in trades)
    return {"research_only": True, "validation_status": "unvalidated_intraday_research",
            "pricing_basis": "observed synchronized option OHLC; all-in estimated round-trip costs",
            "cost_assumption": "Observed OHLC fill prices are reference prices; estimated round-trip spread, slippage and variable fees plus fixed fees are deducted once from each closed trade's gross P&L",
            "fill_assumption": "Next-bar open entries; existing stop first, gap at open; soft target continuation assessed at close; updated trail effective next bar",
            "symbol": symbol, "option_symbol": option_symbol, "option_type": option_type,
            "lot_size": int(lot_size), "trading_mode": cfg.trading_mode,
            "trades": trades, "plans": plans, "trade_count": len(trades),
            "winning_trades": winners, "win_rate_pct": 100 * winners / len(trades) if trades else 0.0,
            "initial_capital_inr": initial, "realized_equity_inr": capital,
            "net_pnl_inr": round(pnl, 6), "return_pct": pnl / initial * 100 if initial > 0 else 0.0,
            "max_realized_drawdown_inr": max_drawdown,
            "rejected_plans": rejected, "unresolved_position": asdict(position) if position else None,
            "unresolved_reason": unresolved_reason,
            "complete": position is None,
            "limitations": ["Not evidence of a profitable strategy; no out-of-sample promotion",
                            "Candle fills cannot model queue position or order-book depth at high quantity",
                            "Open-position P&L is excluded from realized results and drawdown"]}
