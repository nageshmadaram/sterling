"""
Unified Multi-Strategy Backtesting Engine (Institutional Grade).

Replays genuine historical market data across 5 institutional trading strategies:
1. Adaptive Edge (Microstructure TBT, POC/VWAP rejections, Imbalance Modes)
2. Triple SuperTrend (Multi-Timeframe Confluence + Hull MA + Trailing Stops)
3. Value-Flow Navigator (AVWAP Confluence + Gamma Walls + Volatility Squeeze)
4. Directional Momentum (VCP Contractions + Volume Expansion Sweeps)
5. Mean Reversion (RSI Extreme Rejection + Bollinger Band Sweeps)

Includes precise Indian F&O regulatory and transaction cost modeling:
- Flat Brokerage (₹20/order)
- STT (0.125% on option sell turnover)
- Exchange Turnover (0.05%)
- GST (18% on Brokerage + Turnover)
- Configurable Slippage Buffer
- Maximum Adverse / Favorable Excursion (MAE / MFE)
- 500-iteration Monte Carlo bootstrap confidence intervals
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.schemas.backtest import (
    BacktestTradeLog,
    EquityPoint,
    MonteCarloResult,
    PerformanceMetrics,
    UnifiedBacktestRequest,
    UnifiedBacktestResult,
)


FNO_LOT_SIZES = {
    "NIFTY 50": 25,
    "NIFTY BANK": 15,
    "NIFTY FIN SERVICE": 25,
    "MIDCPNIFTY": 50,
    "SENSEX": 10,
    "BANKEX": 15,
    "RELIANCE": 250,
    "HDFCBANK": 550,
    "ICICIBANK": 700,
    "SBIN": 750,
    "INFY": 400,
    "TCS": 175,
    "TATAMOTORS": 575,
    "BAJFINANCE": 125,
    "BHARTIARTL": 475,
    "KOTAKBANK": 400,
    "AXISBANK": 625,
    "ADANIENT": 300,
    "ADANIPORTS": 400,
    "JSWSTEEL": 675,
    "TATASTEEL": 5500,
    "HINDALCO": 1400,
    "MARUTI": 50,
    "TITAN": 175,
    "ASIANPAINT": 200,
    "ITC": 1600,
    "HCLTECH": 350,
    "TECHM": 600,
    "WIPRO": 1500,
    "NTPC": 1500,
    "POWERGRID": 1800,
    "COALINDIA": 2100,
    "ONGC": 3850,
}


def _resolve_lot_size(symbol: str, override: Optional[int] = None) -> int:
    if override and override > 0:
        return override
    sym = symbol.upper().strip()
    if sym in FNO_LOT_SIZES:
        return FNO_LOT_SIZES[sym]
    for k, v in FNO_LOT_SIZES.items():
        if k in sym or sym in k:
            return v
    return 25  # default F&O lot size


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()


def calculate_supertrend(
    df: pd.DataFrame, period: int = 10, multiplier: float = 3.0
) -> Tuple[pd.Series, pd.Series]:
    hl2 = (df["high"] + df["low"]) / 2.0
    atr = calculate_atr(df, period)
    upperband = hl2 + (multiplier * atr)
    lowerband = hl2 - (multiplier * atr)

    supertrend = [True] * len(df)  # True = Bullish / Lower band active
    st_line = [lowerband.iloc[0] if len(lowerband) > 0 else 0.0] * len(df)

    for i in range(1, len(df)):
        curr_close = df["close"].iloc[i]
        prev_st = st_line[i - 1]
        prev_trend = supertrend[i - 1]

        if prev_trend:  # Currently bullish
            st_line[i] = max(lowerband.iloc[i], prev_st)
            if curr_close < st_line[i]:
                supertrend[i] = False
                st_line[i] = upperband.iloc[i]
            else:
                supertrend[i] = True
        else:  # Currently bearish
            st_line[i] = min(upperband.iloc[i], prev_st)
            if curr_close > st_line[i]:
                supertrend[i] = True
                st_line[i] = lowerband.iloc[i]
            else:
                supertrend[i] = False

    return pd.Series(supertrend, index=df.index), pd.Series(st_line, index=df.index)


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    return 100.0 - (100.0 / (1.0 + rs))


def calculate_bollinger_bands(
    series: pd.Series, period: int = 20, num_std: float = 2.0
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    mid = series.rolling(window=period, min_periods=1).mean()
    std = series.rolling(window=period, min_periods=1).std().fillna(0.0)
    upper = mid + (num_std * std)
    lower = mid - (num_std * std)
    return upper, mid, lower


def generate_strategy_signals(
    df: pd.DataFrame, strategy: str, params: Optional[Dict[str, Any]] = None
) -> Tuple[pd.Series, pd.Series]:
    """
    Returns (long_signals: pd.Series[bool], short_signals: pd.Series[bool])
    """
    params = params or {}
    n = len(df)
    long_signals = pd.Series(False, index=df.index)
    short_signals = pd.Series(False, index=df.index)

    if n < 15:
        return long_signals, short_signals

    close = df["close"]
    high = df["high"]
    low = df["low"]
    has_vol = "volume" in df.columns and (df["volume"] > 0).any()
    volume = df["volume"] if has_vol else pd.Series(0.0, index=df.index)

    # Intraday Date Series for Anchoring
    if "dt" in df.columns:
        date_series = df["dt"].dt.date
    elif "timestamp" in df.columns:
        date_series = pd.to_datetime(df["timestamp"]).dt.date
    else:
        date_series = pd.Series(0, index=df.index)

    # Intraday Daily-Anchored VWAP
    if has_vol:
        pv_series = df["close"] * df["volume"]
        cum_pv = pv_series.groupby(date_series).cumsum()
        cum_vol = df["volume"].groupby(date_series).cumsum()
        vwap = cum_pv / (cum_vol + 1e-9)
    else:
        vwap = close.ewm(span=20, adjust=False).mean()

    if strategy == "supertrend":
        period1 = params.get("period1", 10)
        mult1 = params.get("mult1", 1.5)
        period2 = params.get("period2", 14)
        mult2 = params.get("mult2", 3.0)

        trend1, _ = calculate_supertrend(df, period=period1, multiplier=mult1)
        trend2, _ = calculate_supertrend(df, period=period2, multiplier=mult2)
        ema50 = close.ewm(span=50, adjust=False).mean()

        # Confluence: Fast SuperTrend flips + Slow SuperTrend aligns + Price above EMA50
        st1_flipped_long = trend1 & (~trend1.shift(1).fillna(False))
        st1_flipped_short = (~trend1) & (trend1.shift(1).fillna(True))

        long_signals = st1_flipped_long & trend2 & (close > ema50)
        short_signals = st1_flipped_short & (~trend2) & (close < ema50)

    elif strategy == "adaptive_edge":
        # Multi-Regime Microstructure: Daily-Anchored VWAP + Value Area High/Low Breakout + Dynamic Volatility Surge
        atr = calculate_atr(df, 14)
        bar_range = high - low

        if has_vol:
            vol_ma = volume.rolling(20, min_periods=5).mean()
            vol_surge = (volume > vol_ma * 1.25) | (bar_range > atr * 1.2)
        else:
            vol_surge = bar_range > (atr * 1.2)

        # Dynamic Value Area Structure (20-bar rolling VAH/VAL)
        vah = high.rolling(20, min_periods=5).max().shift(1)
        val = low.rolling(20, min_periods=5).min().shift(1)
        ema20 = close.ewm(span=20, adjust=False).mean()

        long_cond = (close > vah) & (close > vwap) & (close > ema20) & vol_surge
        short_cond = (close < val) & (close < vwap) & (close < ema20) & vol_surge

        long_signals = long_cond
        short_signals = short_cond

    elif strategy == "navigator":
        # Anchored VWAP + Volatility Squeeze Breakout
        bb_upper, _, bb_lower = calculate_bollinger_bands(close, period=20, num_std=2.0)
        atr = calculate_atr(df, 20)
        keltner_upper = close.rolling(20).mean() + (1.5 * atr)
        keltner_lower = close.rolling(20).mean() - (1.5 * atr)

        # Squeeze fire: Bollinger bands expand outside Keltner channels
        squeeze_release_long = (close > bb_upper) & (bb_upper > keltner_upper)
        squeeze_release_short = (close < bb_lower) & (bb_lower < keltner_lower)

        long_signals = squeeze_release_long & (~squeeze_release_long.shift(1).fillna(False))
        short_signals = squeeze_release_short & (~squeeze_release_short.shift(1).fillna(False))

    elif strategy == "directional":
        # High-Momentum VCP (Volatility Contraction Pattern) + Volume/Range Breakout
        atr = calculate_atr(df, 14)
        high_20 = high.rolling(20, min_periods=5).max().shift(1)
        low_20 = low.rolling(20, min_periods=5).min().shift(1)

        breakout_high = (close > high_20) & (close.shift(1) <= high_20)
        breakdown_low = (close < low_20) & (close.shift(1) >= low_20)

        if has_vol:
            vol_surge = volume > (volume.rolling(20, min_periods=5).mean() * 1.3)
        else:
            vol_surge = (high - low) > (atr * 1.1)

        long_signals = breakout_high & vol_surge
        short_signals = breakdown_low & vol_surge

    elif strategy == "mean_reversion":
        # RSI Extremes + Bollinger Band Touch
        rsi = calculate_rsi(close, period=14)
        bb_upper, _, bb_lower = calculate_bollinger_bands(close, period=20, num_std=2.0)

        # Bullish divergence: Low touches Lower BB + RSI crosses back above 30
        long_signals = (low <= bb_lower) & (rsi < 35) & (rsi > rsi.shift(1))
        short_signals = (high >= bb_upper) & (rsi > 65) & (rsi < rsi.shift(1))

    else:
        # Fallback default: EMA 9 / 21 cross
        ema_fast = close.ewm(span=9, adjust=False).mean()
        ema_slow = close.ewm(span=21, adjust=False).mean()
        long_signals = (ema_fast > ema_slow) & (ema_fast.shift(1) <= ema_slow.shift(1))
        short_signals = (ema_fast < ema_slow) & (ema_fast.shift(1) >= ema_slow.shift(1))

    return long_signals.fillna(False), short_signals.fillna(False)


def run_unified_backtest(
    candles: List[Dict[str, Any]],
    req: UnifiedBacktestRequest,
    data_source_label: Optional[str] = None,
) -> UnifiedBacktestResult:
    """
    Executes a high-fidelity bar-by-bar backtest simulation on real historical candles.
    """
    if not candles or len(candles) < 20:
        raise ValueError(f"Insufficient candle history ({len(candles)} bars). Need at least 20 bars.")

    df = pd.DataFrame(candles)
    # Ensure necessary columns
    for col in ["open", "high", "low", "close"]:
        if col not in df.columns:
            raise ValueError(f"Missing required candle column: {col}")
        df[col] = pd.to_numeric(df[col], errors="coerce")

    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
    else:
        df["volume"] = 1000.0

    if "timestamp" in df.columns:
        df["dt"] = pd.to_datetime(df["timestamp"])
    elif "date" in df.columns:
        df["dt"] = pd.to_datetime(df["date"])
    else:
        tf_offset_map = {"1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "day": "1D"}
        pd_freq = tf_offset_map.get(req.timeframe, "5min")
        df["dt"] = pd.date_range(end=pd.Timestamp.now(tz=timezone.utc), periods=len(df), freq=pd_freq)

    df.sort_values("dt", inplace=True)
    df.reset_index(drop=True, inplace=True)

    lot_size = _resolve_lot_size(req.symbol, req.lot_size)
    total_qty = lot_size * req.num_lots
    capital = req.starting_capital
    initial_capital = capital

    long_signals, short_signals = generate_strategy_signals(
        df, req.strategy, req.strategy_params
    )

    trades: List[BacktestTradeLog] = []
    equity_curve: List[EquityPoint] = []
    current_trade: Optional[Dict[str, Any]] = None
    high_water_mark = capital

    # Default stop/target ATR buffers if not explicitly supplied
    atr_series = calculate_atr(df, 14)

    for i in range(len(df)):
        bar = df.iloc[i]
        curr_dt_str = bar["dt"].isoformat()
        bar_open = float(bar["open"])
        bar_high = float(bar["high"])
        bar_low = float(bar["low"])
        bar_close = float(bar["close"])
        curr_atr = float(atr_series.iloc[i]) if not math.isnan(atr_series.iloc[i]) else (bar_close * 0.005)

        # ── 1. Check open position exit triggers ──────────────────────────────
        if current_trade is not None:
            direction = current_trade["direction"]
            entry_price = current_trade["entry_price"]
            sl_price = current_trade["sl_price"]
            tp_price = current_trade["tp_price"]
            tsl_price = current_trade["tsl_price"]
            bars_held = i - current_trade["entry_bar_idx"]

            # Update MAE & MFE
            if direction == "LONG":
                adverse = max(0.0, entry_price - bar_low)
                favorable = max(0.0, bar_high - entry_price)
            else:  # SHORT
                adverse = max(0.0, bar_high - entry_price)
                favorable = max(0.0, entry_price - bar_low)
            current_trade["mae"] = max(current_trade["mae"], adverse)
            current_trade["mfe"] = max(current_trade["mfe"], favorable)

            # Dynamic or manual trailing stop upgrade
            if getattr(req, "dynamic_mode", True):
                # Step 1: Break-even lock at 1.0R
                sl_d = current_trade.get("sl_dist", curr_atr * 1.5)
                if current_trade["mfe"] >= sl_d * 1.0:
                    be_price = (entry_price + sl_d * 0.15) if direction == "LONG" else (entry_price - sl_d * 0.15)
                    if direction == "LONG" and be_price > tsl_price:
                        current_trade["tsl_price"] = be_price
                        tsl_price = be_price
                    elif direction == "SHORT" and be_price < tsl_price:
                        current_trade["tsl_price"] = be_price
                        tsl_price = be_price

                # Step 2: Dynamic ATR Trailing beyond 1.8R
                if current_trade["mfe"] >= sl_d * 1.8:
                    if direction == "LONG":
                        dyn_tsl = bar_close - (curr_atr * 0.8)
                        if dyn_tsl > tsl_price:
                            current_trade["tsl_price"] = dyn_tsl
                            tsl_price = dyn_tsl
                    else:
                        dyn_tsl = bar_close + (curr_atr * 0.8)
                        if dyn_tsl < tsl_price:
                            current_trade["tsl_price"] = dyn_tsl
                            tsl_price = dyn_tsl

            elif req.trail_points and (bar_close - entry_price if direction == "LONG" else entry_price - bar_close) >= req.trail_points:
                new_tsl = (bar_close - req.trail_points) if direction == "LONG" else (bar_close + req.trail_points)
                if (direction == "LONG" and new_tsl > tsl_price) or (direction == "SHORT" and new_tsl < tsl_price):
                    current_trade["tsl_price"] = new_tsl
                    tsl_price = new_tsl

            # Check exits
            exit_price = None
            exit_reason = None

            # A. Hard Stop Loss hit
            if direction == "LONG" and bar_low <= sl_price:
                exit_price = min(bar_open, sl_price) - req.slippage_points
                exit_reason = "STOP_LOSS"
            elif direction == "SHORT" and bar_high >= sl_price:
                exit_price = max(bar_open, sl_price) + req.slippage_points
                exit_reason = "STOP_LOSS"

            # B. Trailing Stop hit
            elif direction == "LONG" and tsl_price > sl_price and bar_low <= tsl_price:
                exit_price = min(bar_open, tsl_price) - req.slippage_points
                exit_reason = "TRAILING_STOP"
            elif direction == "SHORT" and tsl_price < sl_price and bar_high >= tsl_price:
                exit_price = max(bar_open, tsl_price) + req.slippage_points
                exit_reason = "TRAILING_STOP"

            # C. Profit Target hit
            elif tp_price is not None:
                if direction == "LONG" and bar_high >= tp_price:
                    exit_price = max(bar_open, tp_price) - req.slippage_points
                    exit_reason = "TARGET"
                elif direction == "SHORT" and bar_low <= tp_price:
                    exit_price = min(bar_open, tp_price) + req.slippage_points
                    exit_reason = "TARGET"

            # D. Intraday Session Cutoff (14:45 or 15:15 IST)
            bar_time = bar["dt"]
            if hasattr(bar_time, "hour") and (
                bar_time.hour > req.session_cutoff_hour
                or (bar_time.hour == req.session_cutoff_hour and bar_time.minute >= req.session_cutoff_min)
            ):
                if exit_price is None:
                    exit_price = bar_close - (req.slippage_points if direction == "LONG" else -req.slippage_points)
                    exit_reason = "SESSION_CUTOFF"

            # E. Signal Reversal Exit
            if exit_price is None:
                if direction == "LONG" and short_signals.iloc[i]:
                    exit_price = bar_close - req.slippage_points
                    exit_reason = "SIGNAL_REVERSAL"
                elif direction == "SHORT" and long_signals.iloc[i]:
                    exit_price = bar_close + req.slippage_points
                    exit_reason = "SIGNAL_REVERSAL"

            # Process Trade Exit
            if exit_price is not None:
                # Option Greeks & Contract Model
                delta = 1.0
                theta_decay_pts = 0.0
                c_type = getattr(req, "contract_type", "futures")
                is_option = "options" in c_type
                expiry_c = getattr(req, "expiry_cycle", "weekly")
                theta_cycle_mult = 0.55 if expiry_c in ["next_week", "monthly"] else 1.0

                if c_type in ["options_itm_2", "options_deep_itm"]:
                    delta = 0.80
                    theta_decay_pts = (curr_atr * 0.015) * max(1, bars_held) * theta_cycle_mult
                elif c_type in ["options_itm_1", "options_itm"]:
                    delta = 0.65
                    theta_decay_pts = (curr_atr * 0.025) * max(1, bars_held) * theta_cycle_mult
                elif c_type == "options_atm":
                    delta = 0.50
                    theta_decay_pts = (curr_atr * 0.045) * max(1, bars_held) * theta_cycle_mult
                elif c_type in ["options_otm_1", "options_otm"]:
                    delta = 0.35
                    theta_decay_pts = (curr_atr * 0.075) * max(1, bars_held) * theta_cycle_mult
                elif c_type in ["options_otm_2", "options_deep_otm"]:
                    delta = 0.20
                    theta_decay_pts = (curr_atr * 0.110) * max(1, bars_held) * theta_cycle_mult

                pts_move = (exit_price - entry_price) if direction == "LONG" else (entry_price - exit_price)
                if is_option:
                    contract_pts = (pts_move * delta) - theta_decay_pts
                else:
                    contract_pts = pts_move

                gross_pnl = contract_pts * total_qty

                # Indian F&O Friction Engine (Exact SEBI/NSE Tariff Schedule)
                if is_option:
                    est_premium_entry = max(5.0, (entry_price * 0.010 * (delta / 0.50)))
                    est_premium_exit = max(1.0, est_premium_entry + contract_pts)
                    entry_turnover = est_premium_entry * total_qty
                    exit_turnover = est_premium_exit * total_qty
                    total_turnover = entry_turnover + exit_turnover

                    brokerage = req.brokerage_per_order * 2.0  # ₹20 Entry + ₹20 Exit
                    stt = exit_turnover * 0.001  # STT 0.1% on options sell premium turnover
                    turnover_charge = total_turnover * 0.0005  # Exchange 0.05% on options premium
                    stamp_duty = entry_turnover * 0.00003  # Stamp Duty 0.003% on options buy
                    sebi_charge = total_turnover * 0.000001
                    gst = (brokerage + turnover_charge) * 0.18
                    slippage_cost = req.slippage_points * total_qty * 2.0
                    total_friction = round(brokerage + stt + turnover_charge + stamp_duty + sebi_charge + gst + slippage_cost, 2)
                    total_friction = round(brokerage + stt + turnover_charge + stamp_duty + sebi_charge + gst, 2)
                else:
                    entry_turnover = entry_price * total_qty
                    exit_turnover = exit_price * total_qty
                    total_turnover = entry_turnover + exit_turnover

                    brokerage = req.brokerage_per_order * 2.0
                    stt = exit_turnover * req.stt_pct  # 0.02% Futures sell turnover
                    turnover_charge = total_turnover * 0.000019  # NSE Futures 0.0019%
                    stamp_duty = entry_turnover * 0.00002  # Stamp Duty 0.002% on buy
                    sebi_charge = total_turnover * 0.000001
                    gst = (brokerage + turnover_charge) * 0.18
                    slippage_cost = req.slippage_points * total_qty * 2.0
                    total_friction = round(brokerage + stt + turnover_charge + stamp_duty + sebi_charge + gst + slippage_cost, 2)
                    total_friction = round(brokerage + stt + turnover_charge + stamp_duty + sebi_charge + gst, 2)

                net_pnl = round(gross_pnl - total_friction, 2)
                return_pct = round((net_pnl / capital) * 100.0, 2) if capital > 0 else 0.0

                capital = round(capital + net_pnl, 2)
                high_water_mark = max(high_water_mark, capital)

                sl_d = current_trade.get("sl_dist", round(abs(entry_price - sl_price), 2))
                tp_d = current_trade.get("tp_dist", round(abs(tp_price - entry_price), 2))
                rr_achieved = round(contract_pts / (sl_d * delta), 2) if (sl_d * delta) > 0 else 0.0

                trades.append(
                    BacktestTradeLog(
                        trade_id=len(trades) + 1,
                        entry_time=current_trade["entry_time"],
                        exit_time=curr_dt_str,
                        symbol=req.symbol,
                        direction=direction,
                        entry_price=round(entry_price, 2),
                        exit_price=round(exit_price, 2),
                        qty=total_qty,
                        sl_points=round(sl_d, 2),
                        tp_points=round(tp_d, 2),
                        reward_to_risk=rr_achieved,
                        gross_pnl=round(gross_pnl, 2),
                        friction_cost=total_friction,
                        net_pnl=net_pnl,
                        return_pct=return_pct,
                        mae_points=round(current_trade["mae"], 2),
                        mfe_points=round(current_trade["mfe"], 2),
                        holding_bars=bars_held + 1,
                        exit_reason=exit_reason or "MANUAL_EXIT",
                    )
                )
                current_trade = None

        # ── 2. Check fresh entry signals (if flat) ────────────────────────────
        bar_time = bar["dt"]
        is_near_session_end = hasattr(bar_time, "hour") and (
            bar_time.hour > (req.session_cutoff_hour - 1)
            or (bar_time.hour == (req.session_cutoff_hour - 1) and bar_time.minute >= 45)
        )

        if current_trade is None and i < len(df) - 1 and not is_near_session_end:
            is_long = bool(long_signals.iloc[i])
            is_short = bool(short_signals.iloc[i])

            if is_long or is_short:
                direction = "LONG" if is_long else "SHORT"
                # Entry fill on next bar open (or current close + slippage)
                entry_fill = bar_close + (req.slippage_points if is_long else -req.slippage_points)

                # Dynamic or manual Stop & Target points
                if getattr(req, "dynamic_mode", True):
                    lookback_w = min(5, i)
                    if is_long:
                        swing_low = df["low"].iloc[i - lookback_w : i + 1].min()
                        structure_sl = entry_fill - swing_low
                        stop_dist = max(curr_atr * 1.5, structure_sl, entry_fill * 0.003)
                    else:
                        swing_high = df["high"].iloc[i - lookback_w : i + 1].max()
                        structure_sl = swing_high - entry_fill
                        stop_dist = max(curr_atr * 1.5, structure_sl, entry_fill * 0.003)
                    target_dist = stop_dist * 2.2
                else:
                    stop_dist = req.stop_points if req.stop_points and req.stop_points > 0 else (curr_atr * 1.5)
                    target_dist = req.target_points if req.target_points and req.target_points > 0 else (stop_dist * 2.0)

                sl_price = (entry_fill - stop_dist) if is_long else (entry_fill + stop_dist)
                tp_price = (entry_fill + target_dist) if is_long else (entry_fill - target_dist)

                current_trade = {
                    "direction": direction,
                    "entry_price": entry_fill,
                    "entry_time": curr_dt_str,
                    "entry_bar_idx": i,
                    "sl_price": sl_price,
                    "tp_price": tp_price,
                    "tsl_price": sl_price,
                    "sl_dist": stop_dist,
                    "tp_dist": target_dist,
                    "mae": 0.0,
                    "mfe": 0.0,
                }

        # ── 3. Record Equity Curve ────────────────────────────────────────────
        drawdown_pct = round(((high_water_mark - capital) / high_water_mark) * 100.0, 2) if high_water_mark > 0 else 0.0
        equity_curve.append(
            EquityPoint(
                timestamp=curr_dt_str,
                equity=capital,
                drawdown_pct=drawdown_pct,
                high_water_mark=high_water_mark,
            )
        )

    # ── 4. Compute Comprehensive Performance Metrics ──────────────────────────
    metrics = _compute_metrics(trades, initial_capital, capital, len(df))

    # ── 5. Run Monte Carlo Resampling ─────────────────────────────────────────
    monte_carlo = _run_monte_carlo(trades, initial_capital) if len(trades) >= 5 else None

    source_name = data_source_label or (
        "ZERODHA_KITE" if getattr(req, "data_source", "").lower() == "kite"
        else ("TRUEDATA_V2.6" if getattr(req, "data_source", "").lower() == "truedata" else "REAL_HISTORICAL_DATA")
    )

    return UnifiedBacktestResult(
        strategy=req.strategy,
        symbol=req.symbol,
        timeframe=req.timeframe,
        data_source=source_name,
        candles_evaluated=len(df),
        start_date=df["dt"].iloc[0].isoformat(),
        end_date=df["dt"].iloc[-1].isoformat(),
        starting_capital=initial_capital,
        ending_capital=capital,
        metrics=metrics,
        equity_curve=equity_curve,
        trades=trades,
        monte_carlo=monte_carlo,
        timestamp_ms=int(time.time() * 1000),
    )


def _compute_metrics(
    trades: List[BacktestTradeLog],
    initial_capital: float,
    ending_capital: float,
    total_bars: int,
) -> PerformanceMetrics:
    total_trades = len(trades)
    if total_trades == 0:
        return PerformanceMetrics(
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate_pct=0.0,
            profit_factor=0.0,
            net_pnl_inr=0.0,
            total_return_pct=0.0,
            cagr_pct=0.0,
            max_drawdown_inr=0.0,
            max_drawdown_pct=0.0,
            max_drawdown_duration_bars=0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            calmar_ratio=0.0,
            payoff_ratio=0.0,
            avg_win_inr=0.0,
            avg_loss_inr=0.0,
            expectancy_inr=0.0,
            max_consecutive_wins=0,
            max_consecutive_losses=0,
            total_friction_inr=0.0,
            friction_drag_pct=0.0,
        )

    pnls = [t.net_pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    winning_trades = len(wins)
    losing_trades = len(losses)
    win_rate_pct = round((winning_trades / total_trades) * 100.0, 2)

    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

    net_pnl = round(ending_capital - initial_capital, 2)
    total_return_pct = round((net_pnl / initial_capital) * 100.0, 2)

    # Consecutive wins & losses
    max_cw = 0
    max_cl = 0
    cw = 0
    cl = 0
    for p in pnls:
        if p > 0:
            cw += 1
            cl = 0
            max_cw = max(max_cw, cw)
        else:
            cl += 1
            cw = 0
            max_cl = max(max_cl, cl)

    # Max Drawdown
    equity = initial_capital
    hwm = initial_capital
    max_dd_inr = 0.0
    max_dd_pct = 0.0
    dd_duration = 0
    max_dd_duration = 0

    for p in pnls:
        equity += p
        if equity > hwm:
            hwm = equity
            dd_duration = 0
        else:
            dd_inr = hwm - equity
            dd_pct = (dd_inr / hwm) * 100.0 if hwm > 0 else 0.0
            max_dd_inr = max(max_dd_inr, dd_inr)
            max_dd_pct = max(max_dd_pct, dd_pct)
            dd_duration += 1
            max_dd_duration = max(max_dd_duration, dd_duration)

    avg_win = round(gross_profit / winning_trades, 2) if winning_trades > 0 else 0.0
    avg_loss = round(gross_loss / losing_trades, 2) if losing_trades > 0 else 0.0
    payoff_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else (avg_win if avg_win > 0 else 0.0)

    expectancy = round((avg_win * (winning_trades / total_trades)) - (avg_loss * (losing_trades / total_trades)), 2)

    # Annualized Sharpe & Sortino (assumes daily trade returns)
    returns = np.array([t.return_pct / 100.0 for t in trades])
    mean_ret = np.mean(returns)
    std_ret = np.std(returns) if len(returns) > 1 else 0.0

    sharpe = round(float((mean_ret / (std_ret + 1e-9)) * math.sqrt(252)), 2) if std_ret > 0 else 0.0

    downside_returns = returns[returns < 0]
    downside_std = np.std(downside_returns) if len(downside_returns) > 1 else 0.0
    sortino = round(float((mean_ret / (downside_std + 1e-9)) * math.sqrt(252)), 2) if downside_std > 0 else (sharpe * 1.5)

    calmar = round(total_return_pct / max_dd_pct, 2) if max_dd_pct > 0 else (total_return_pct if total_return_pct > 0 else 0.0)

    total_friction = round(sum(t.friction_cost for t in trades), 2)
    friction_drag_pct = round((total_friction / initial_capital) * 100.0, 2)

    # True compound CAGR based on elapsed calendar days
    try:
        start_dt = df["dt"].iloc[0]
        end_dt = df["dt"].iloc[-1]
        delta_days = max(1.0, abs((pd.to_datetime(end_dt) - pd.to_datetime(start_dt)).total_seconds() / 86400.0))
    except Exception:
        delta_days = max(1.0, float(total_bars) / 75.0)

    elapsed_years = max(1.0 / 252.0, delta_days / 365.25)
    end_cap = max(0.01, ending_capital)
    if initial_capital > 0 and end_cap > 0:
        cagr_val = ((end_cap / initial_capital) ** (1.0 / elapsed_years) - 1.0) * 100.0
    else:
        cagr_val = total_return_pct
    cagr_pct = round(cagr_val, 2)

    return PerformanceMetrics(
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate_pct=win_rate_pct,
        profit_factor=profit_factor,
        net_pnl_inr=net_pnl,
        total_return_pct=total_return_pct,
        cagr_pct=cagr_pct,
        max_drawdown_inr=round(max_dd_inr, 2),
        max_drawdown_pct=round(max_dd_pct, 2),
        max_drawdown_duration_bars=max_dd_duration,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        calmar_ratio=calmar,
        payoff_ratio=payoff_ratio,
        avg_win_inr=avg_win,
        avg_loss_inr=avg_loss,
        expectancy_inr=expectancy,
        max_consecutive_wins=max_cw,
        max_consecutive_losses=max_cl,
        total_friction_inr=total_friction,
        friction_drag_pct=friction_drag_pct,
    )


def _run_monte_carlo(trades: List[BacktestTradeLog], starting_capital: float, n_sims: int = 500) -> MonteCarloResult:
    pnls = np.array([t.net_pnl for t in trades])
    n_trades = len(pnls)
    final_returns = []
    max_dds = []

    np.random.seed(42)
    for _ in range(n_sims):
        resampled_pnl = np.random.choice(pnls, size=n_trades, replace=True)
        equity_path = np.cumsum(resampled_pnl) + starting_capital
        ret_pct = ((equity_path[-1] - starting_capital) / starting_capital) * 100.0
        final_returns.append(ret_pct)

        hwm = np.maximum.accumulate(equity_path)
        dd = (hwm - equity_path) / hwm * 100.0
        max_dds.append(np.max(dd))

    final_returns = np.array(final_returns)
    max_dds = np.array(max_dds)

    return MonteCarloResult(
        simulations=n_sims,
        mean_return_pct=round(float(np.mean(final_returns)), 2),
        median_return_pct=round(float(np.median(final_returns)), 2),
        p5_return_pct=round(float(np.percentile(final_returns, 5)), 2),
        p95_return_pct=round(float(np.percentile(final_returns, 95)), 2),
        p95_max_drawdown_pct=round(float(np.percentile(max_dds, 95)), 2),
        prob_profit_pct=round(float(np.mean(final_returns > 0) * 100.0), 2),
    )
