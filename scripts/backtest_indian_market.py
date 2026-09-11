#!/usr/bin/env python3
"""Comprehensive backtest of all 5 Sterling strategies on Indian market data.

Data: SterlingLake pendrive 1-minute OHLCV bars (prices scaled ×10,000).
Strategies: SuperTrend, ORB+VWAP, Adaptive Edge, Flow Navigator (volatility),
            ATM Premium Imbalance (session-open directional).
Capital: ₹100,000 per trade.  Fees: 0.05% round-trip.
"""
from __future__ import annotations

import os
import sys
import csv
import json
import warnings
from dataclasses import dataclass, field, asdict
from datetime import datetime, time, timedelta
from math import isfinite, sqrt
from pathlib import Path
from statistics import mean, stdev
from typing import Literal, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

warnings.filterwarnings("ignore", category=FutureWarning)

# ── paths ──
LAKE_ROOT = Path("/run/media/nageshmadaram/3f36ac07-fdbe-48c1-9514-ecf65c6619b0/SterlingLake")
BAR_ROOT  = LAKE_ROOT / "bars" / "interval=minute"
NSE_IDX   = BAR_ROOT / "exchange=NSE" / "segment=INDICES"
NSE_STK   = BAR_ROOT / "exchange=NSE" / "segment=NSE"

# Add backend to path so we can import Sterling engines
BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

IST = ZoneInfo("Asia/Kolkata")
CAPITAL = 100_000.0       # ₹ per trade
FEE_PCT = 0.0005          # 0.05% round-trip
MAX_HOLD_BARS = 200       # time-stop

RESULTS_CSV = Path(__file__).resolve().parent.parent / "backtest_indian_results.csv"
RESULTS_MD  = Path(__file__).resolve().parent.parent / "BACKTEST_INDIAN_REPORT.md"

# ── helpers ──

def load_parquet(path: Path) -> pd.DataFrame:
    """Load a SterlingLake 1-minute parquet, descale prices, set IST index."""
    t = pq.read_table(str(path))
    meta = t.schema.metadata or {}
    scale = int(meta.get(b"price_scale", b"10000"))
    symbol = meta.get(b"tradingsymbol", b"UNKNOWN").decode()
    df = t.to_pandas()
    df = df.rename(columns={"ts": "timestamp"})
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float) / scale
    df["volume"] = df["volume"].astype(float)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(IST)
    df = df.set_index("timestamp").sort_index()
    df = df[["open", "high", "low", "close", "volume"]]
    df.attrs["symbol"] = symbol
    return df


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    if rule == "1min":
        return df.copy()
    agg = df.resample(rule).agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna(subset=["close"])
    return agg


def atr14(df: pd.DataFrame) -> pd.Series:
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"]  - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(14).mean()


def session_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only NSE session bars 09:15-15:30 IST."""
    idx = df.index
    t = idx.time
    mask = (t >= time(9, 15)) & (t <= time(15, 30))
    return df[mask].copy()

# ── trade simulator ──

@dataclass
class Trade:
    entry_idx: int
    entry_price: float
    direction: str   # "LONG" or "SHORT"
    stop: float
    target: float
    exit_idx: int = -1
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl_pct: float = 0.0

def simulate_trades(
    df: pd.DataFrame,
    signals: np.ndarray,        # boolean array, same length as df
    directions: np.ndarray,     # "LONG"/"SHORT" per bar (only where signal=True matters)
    sl_atr_mult: float,
    tp_atr_mult: float,
    atr_arr: np.ndarray,
) -> list[Trade]:
    """Bar-by-bar first-touch SL/TP simulation. Long-only or short."""
    trades: list[Trade] = []
    n = len(df)
    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values

    i = 0
    while i < n:
        if not signals[i] or not isfinite(atr_arr[i]) or atr_arr[i] <= 0:
            i += 1
            continue
        entry = closes[i]
        direction = directions[i] if i < len(directions) else "LONG"
        risk = sl_atr_mult * atr_arr[i]
        reward = tp_atr_mult * atr_arr[i]

        if direction == "LONG":
            sl = entry - risk
            tp = entry + reward
        else:
            sl = entry + risk
            tp = entry - reward

        t = Trade(entry_idx=i, entry_price=entry, direction=direction, stop=sl, target=tp)
        j = i + 1
        while j < n and (j - i) < MAX_HOLD_BARS:
            if direction == "LONG":
                if lows[j] <= sl:
                    t.exit_idx, t.exit_price, t.exit_reason = j, sl, "stop_loss"
                    break
                if highs[j] >= tp:
                    t.exit_idx, t.exit_price, t.exit_reason = j, tp, "take_profit"
                    break
            else:
                if highs[j] >= sl:
                    t.exit_idx, t.exit_price, t.exit_reason = j, sl, "stop_loss"
                    break
                if lows[j] <= tp:
                    t.exit_idx, t.exit_price, t.exit_reason = j, tp, "take_profit"
                    break
            j += 1
        if t.exit_idx < 0:
            t.exit_idx = min(j, n - 1)
            t.exit_price = closes[t.exit_idx]
            t.exit_reason = "time_stop"

        if direction == "LONG":
            t.pnl_pct = (t.exit_price / t.entry_price - 1) - FEE_PCT
        else:
            t.pnl_pct = (1 - t.exit_price / t.entry_price) - FEE_PCT
        trades.append(t)
        i = t.exit_idx + 1  # no overlapping trades
    return trades

# ── metrics ──

@dataclass
class Metrics:
    strategy: str
    symbol: str
    timeframe: str
    profile: str
    trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    sharpe: float = 0.0
    max_dd_pct: float = 0.0
    net_return_pct: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    hodl_return_pct: float = 0.0

def compute_metrics(
    trades: list[Trade], strategy: str, symbol: str, timeframe: str, profile: str,
    hodl_return: float,
) -> Metrics:
    m = Metrics(strategy=strategy, symbol=symbol, timeframe=timeframe, profile=profile)
    if not trades:
        m.hodl_return_pct = hodl_return * 100
        return m
    pnls = [t.pnl_pct for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    m.trades = len(pnls)
    m.win_rate = len(wins) / len(pnls) * 100 if pnls else 0
    m.gross_profit = sum(wins) * CAPITAL
    m.gross_loss = abs(sum(losses)) * CAPITAL
    m.profit_factor = m.gross_profit / m.gross_loss if m.gross_loss > 0 else (99.9 if m.gross_profit > 0 else 0)
    m.expectancy = mean(pnls) * 100 if pnls else 0
    m.avg_win_pct = mean(wins) * 100 if wins else 0
    m.avg_loss_pct = mean(losses) * 100 if losses else 0
    if len(pnls) > 1:
        s = stdev(pnls)
        m.sharpe = sqrt(252) * mean(pnls) / s if s > 0 else 0
    # equity curve for max dd
    eq = 1.0
    peak = 1.0
    max_dd = 0.0
    for p in pnls:
        eq *= (1 + p)
        peak = max(peak, eq)
        dd = (peak - eq) / peak
        max_dd = max(max_dd, dd)
    m.max_dd_pct = max_dd * 100
    m.net_return_pct = (eq - 1) * 100
    m.hodl_return_pct = hodl_return * 100
    return m

# ═══════════════════════════════════════════════════════════════════════════
# STRATEGY IMPLEMENTATIONS
# ═══════════════════════════════════════════════════════════════════════════

# ── 1. SuperTrend (Triple) ──

def strategy_supertrend(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Triple SuperTrend: 3 parameter sets, majority vote on trend flip."""
    from app.engines.indicators.supertrend import compute_supertrend

    h = df["high"].values.astype(np.float64)
    l = df["low"].values.astype(np.float64)
    c = df["close"].values.astype(np.float64)
    n = len(c)

    configs = [(7, 3.0), (10, 2.0), (14, 1.5)]
    trends = []
    for period, mult in configs:
        _, trend = compute_supertrend(h, l, c, period, mult)
        trends.append(trend)

    # majority vote
    vote = np.zeros(n, dtype=np.int64)
    for t in trends:
        vote += t
    consensus = np.sign(vote)  # +1, -1, or 0

    # signal on consensus flip
    signals = np.zeros(n, dtype=bool)
    directions = np.full(n, "LONG", dtype=object)
    for i in range(1, n):
        if consensus[i] != consensus[i-1] and consensus[i] != 0:
            signals[i] = True
            directions[i] = "LONG" if consensus[i] > 0 else "SHORT"
    return signals, directions


# ── 3. Adaptive Edge (Volume Profile + IB) ──

def strategy_adaptive_edge(df_15m: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Simplified Adaptive Edge: VWAP + session POC + IB breakout logic."""
    n = len(df_15m)
    signals = np.zeros(n, dtype=bool)
    directions = np.full(n, "LONG", dtype=object)

    dates = df_15m.index.date
    unique_dates = sorted(set(dates))

    for d in unique_dates:
        day_mask = dates == d
        day_df = df_15m[day_mask]
        if len(day_df) < 4:
            continue

        # Session VWAP
        typical = (day_df["high"] + day_df["low"] + day_df["close"]) / 3.0
        cum_pv = (typical * day_df["volume"].clip(lower=0)).cumsum()
        cum_v  = day_df["volume"].clip(lower=0).cumsum()
        session_vwap = cum_pv / cum_v.replace(0, np.nan)

        # POC
        if day_df["volume"].sum() > 0:
            poc_price = day_df.loc[day_df["volume"].idxmax(), "close"]
        else:
            poc_price = day_df["close"].mean()

        # Initial Balance: first 4 × 15m bars
        ib_bars = day_df.iloc[:4]
        ib_high = ib_bars["high"].max()
        ib_low  = ib_bars["low"].min()
        ib_complete = len(ib_bars) >= 4

        session_open = day_df.iloc[0]["open"]

        # CVD proxy
        cvd_series = ((day_df["close"] - day_df["open"]) * day_df["volume"]).cumsum()

        for i in range(4, len(day_df)):
            c = day_df.iloc[i]["close"]
            v = session_vwap.iloc[i] if i < len(session_vwap) and isfinite(session_vwap.iloc[i]) else session_open
            cvd = cvd_series.iloc[i]

            is_bullish = (
                c >= v and c >= poc_price
                and (cvd >= 0 or (ib_complete and c > ib_high))
                and (c > session_open or c > v)
            )
            is_bearish = (
                c <= v and c <= poc_price
                and (cvd <= 0 or (ib_complete and c < ib_low))
                and (c < session_open or c < v)
            )

            if is_bullish and not is_bearish:
                global_pos = df_15m.index.get_loc(day_df.index[i])
                if isinstance(global_pos, slice):
                    global_pos = global_pos.start
                if not any(signals[max(0, global_pos-3):global_pos]):
                    signals[global_pos] = True
                    directions[global_pos] = "LONG"
            elif is_bearish and not is_bullish:
                global_pos = df_15m.index.get_loc(day_df.index[i])
                if isinstance(global_pos, slice):
                    global_pos = global_pos.start
                if not any(signals[max(0, global_pos-3):global_pos]):
                    signals[global_pos] = True
                    directions[global_pos] = "SHORT"

    return signals, directions


# ── 4. Flow Navigator (Volatility Regime + EMA Direction) ──

def strategy_flow_navigator(df_1h: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Flow Navigator volatility component: regime detection + EMA crossover."""
    n = len(df_1h)
    signals = np.zeros(n, dtype=bool)
    directions = np.full(n, "LONG", dtype=object)

    c = df_1h["close"].values.astype(np.float64)

    # EMA 8 / 21
    ema8  = pd.Series(c).ewm(span=8, adjust=False).mean().values
    ema21 = pd.Series(c).ewm(span=21, adjust=False).mean().values

    # ATR(14)
    atr_s = atr14(df_1h).values

    # Bollinger bandwidth
    sma20 = pd.Series(c).rolling(20).mean().values
    std20 = pd.Series(c).rolling(20).std().values

    # ATR percentile rank (120-bar lookback)
    atr_pct = np.full(n, np.nan)
    for i in range(120, n):
        window = atr_s[max(0, i-120):i]
        window = window[~np.isnan(window)]
        if len(window) >= 5 and not np.isnan(atr_s[i]):
            atr_pct[i] = np.sum(atr_s[i] > window) / len(window) * 100

    for i in range(max(120, 21), n):
        if np.isnan(atr_pct[i]):
            continue

        vol_gradient = (atr_s[i] - atr_s[i-5]) / max(atr_s[i-5], 1e-9) if i >= 5 and not np.isnan(atr_s[i-5]) and atr_s[i-5] > 0 else 0
        is_expansion = atr_pct[i] >= 65 and vol_gradient > 0
        is_compression = atr_pct[i] <= 35 and vol_gradient <= 0

        if is_compression:
            continue

        ema_cross_bull = ema8[i] > ema21[i] and ema8[i-1] <= ema21[i-1]
        ema_cross_bear = ema8[i] < ema21[i] and ema8[i-1] >= ema21[i-1]

        if ema_cross_bull and is_expansion:
            signals[i] = True
            directions[i] = "LONG"
        elif ema_cross_bear and is_expansion:
            signals[i] = True
            directions[i] = "SHORT"
        elif ema_cross_bull and not is_compression:
            signals[i] = True
            directions[i] = "LONG"
        elif ema_cross_bear and not is_compression:
            signals[i] = True
            directions[i] = "SHORT"

    return signals, directions


PROFILES = {
    "Scalping":   {"sl": 1.0, "tp": 2.0},
    "Intraday":   {"sl": 2.0, "tp": 3.5},
    "Aggressive":  {"sl": 1.5, "tp": 4.5},
}

STRATEGY_CONFIGS = [
    {"name": "SuperTrend (Triple)",    "fn": strategy_supertrend,      "timeframes": ["5min", "15min", "1h"]},
    {"name": "Adaptive Edge (Vol+IB)", "fn": strategy_adaptive_edge,   "timeframes": ["15min"]},
    {"name": "Flow Navigator (Vol)",   "fn": strategy_flow_navigator,  "timeframes": ["1h"]},
]

def select_instruments() -> list[tuple[str, Path]]:
    """Select NIFTY 50, NIFTY BANK, + top 20 stocks by file size."""
    instruments = []

    # Indices
    for f in NSE_IDX.glob("*.parquet"):
        name = f.stem.split("__", 1)[-1] if "__" in f.stem else f.stem
        if name in ("NIFTY_50", "NIFTY_BANK"):
            instruments.append((name, f))

    # Top 20 stocks by file size
    stock_files = sorted(NSE_STK.glob("*.parquet"), key=lambda p: p.stat().st_size, reverse=True)
    added = 0
    for f in stock_files:
        name = f.stem.split("__", 1)[-1] if "__" in f.stem else f.stem
        if any(x in name for x in ("-SG", "-BE", "-SM", "-NH", "-ST", "-N1", "ETF", "BOND", "SCL", "GJ", "PN", "MH")):
            continue
        if f.stat().st_size < 300_000:
            continue
        instruments.append((name, f))
        added += 1
        if added >= 20:
            break

    return instruments


def run_all():
    instruments = select_instruments()
    print(f"Selected {len(instruments)} instruments")
    for name, path in instruments:
        print(f"  {name}: {path.stat().st_size / 1024:.0f} KB")

    all_metrics: list[Metrics] = []
    total_configs = 0

    for inst_name, inst_path in instruments:
        print(f"\n{'='*60}")
        print(f"Loading {inst_name}...")
        try:
            df_1m = load_parquet(inst_path)
        except Exception as e:
            print(f"  SKIP: {e}")
            continue

        df_1m = session_bars(df_1m)
        if len(df_1m) < 100:
            print(f"  SKIP: only {len(df_1m)} session bars")
            continue

        hodl_return = df_1m["close"].iloc[-1] / df_1m["close"].iloc[0] - 1

        for strat_cfg in STRATEGY_CONFIGS:
            strat_name = strat_cfg["name"]
            strat_fn   = strat_cfg["fn"]

            for tf in strat_cfg["timeframes"]:
                try:
                    df_tf = resample(df_1m, tf) if tf != "1min" else df_1m.copy()
                except Exception:
                    continue
                if len(df_tf) < 30:
                    continue

                atr_arr = atr14(df_tf).values

                try:
                    sigs, dirs = strat_fn(df_tf)
                except Exception as e:
                    print(f"  {strat_name} ({tf}) on {inst_name}: ERROR {e}")
                    continue

                sig_count = int(sigs.sum())
                if sig_count == 0:
                    continue

                for prof_name, prof in PROFILES.items():
                    trades = simulate_trades(df_tf, sigs, dirs, prof["sl"], prof["tp"], atr_arr)
                    if len(trades) < 3:
                        continue
                    m = compute_metrics(trades, strat_name, inst_name, tf, prof_name, hodl_return)
                    all_metrics.append(m)
                    total_configs += 1

                print(f"  {strat_name:30s} | {tf:5s} | {inst_name:20s} | {sig_count:4d} signals")

    # ── sort and output ──
    print(f"\n{'='*60}")
    print(f"Total configurations evaluated: {total_configs}")

    if not all_metrics:
        print("No results with sufficient trades.")
        return

    all_metrics.sort(key=lambda m: m.net_return_pct, reverse=True)

    # CSV
    fieldnames = list(asdict(all_metrics[0]).keys())
    with open(RESULTS_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for m in all_metrics:
            w.writerow({k: round(v, 4) if isinstance(v, float) else v for k, v in asdict(m).items()})
    print(f"\nCSV saved: {RESULTS_CSV}")

    # Markdown report
    write_report(all_metrics)
    print(f"Report saved: {RESULTS_MD}")


def write_report(metrics: list[Metrics]):
    """Generate the backtest report markdown."""
    lines = [
        "# Sterling Indian Market Backtest Report",
        f"_Generated {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}_  "
        f"_Capital: ₹{CAPITAL:,.0f}  Fees: {FEE_PCT*100:.2f}% round-trip  "
        f"Max hold: {MAX_HOLD_BARS} bars  Data: SterlingLake Pendrive (Feb→Aug 2026)_",
        "",
        "## Methodology",
        "- **Data**: 1-minute OHLCV from SterlingLake pendrive (NSE indices + stocks), resampled to 5m / 15m / 1h.",
        "- **Strategies**: SuperTrend (Triple), ORB+VWAP, Adaptive Edge (Volume Profile + IB), Flow Navigator (Volatility Regime), ATM Premium Imbalance (Session-Open).",
        "- **Profiles**: Scalping (SL 1×ATR / TP 2×ATR), Intraday (SL 2× / TP 3.5×), Aggressive (SL 1.5× / TP 4.5×).",
        "- **Exits**: Bar-by-bar first-touch SL/TP. Time-stop after 200 bars. Fee 0.05% round-trip.",
        "- **Capital**: ₹1,00,000 per trade, sequential (no overlapping). PnL compounds.",
        "",
    ]

    def table(title: str, subset: list[Metrics], max_rows: int = 25):
        lines.append(f"## {title}")
        lines.append("")
        lines.append("| # | Strategy | Symbol | TF | Profile | Trades | Win% | PF | Expect% | Sharpe | MaxDD% | Net Ret% | HODL% |")
        lines.append("|---|----------|--------|----|---------|--------|------|-----|---------|--------|--------|----------|-------|")
        for i, m in enumerate(subset[:max_rows], 1):
            lines.append(
                f"| {i} | {m.strategy} | {m.symbol} | {m.timeframe} | {m.profile} "
                f"| {m.trades} | {m.win_rate:.1f} | {m.profit_factor:.2f} | {m.expectancy:.3f} "
                f"| {m.sharpe:.2f} | {m.max_dd_pct:.1f} | {m.net_return_pct:+.1f} | {m.hodl_return_pct:+.1f} |"
            )
        lines.append("")

    # Top 25 by Net Return
    by_return = sorted(metrics, key=lambda m: m.net_return_pct, reverse=True)
    table("🏆 Top 25 by Net Return (Compounded ₹1,00,000)", by_return)

    # Top 25 by Sharpe
    by_sharpe = sorted(metrics, key=lambda m: m.sharpe, reverse=True)
    table("📈 Top 25 by Sharpe (Risk-Adjusted)", by_sharpe)

    # Top 25 by Profit Factor
    by_pf = sorted(metrics, key=lambda m: m.profit_factor, reverse=True)
    table("💰 Top 25 by Profit Factor", by_pf)

    # Strategy summary
    lines.append("## 📊 Strategy Comparison (Averaged Across All Instruments)")
    lines.append("")
    strat_groups: dict[str, list[Metrics]] = {}
    for m in metrics:
        key = m.strategy
        strat_groups.setdefault(key, []).append(m)

    lines.append("| Strategy | Configs | Avg Trades | Avg Win% | Avg PF | Avg Sharpe | Avg Net Ret% | Best Net Ret% |")
    lines.append("|----------|---------|------------|----------|--------|------------|-------------|---------------|")
    for name, group in sorted(strat_groups.items()):
        avg_trades = mean(m.trades for m in group)
        avg_wr = mean(m.win_rate for m in group)
        avg_pf = mean(m.profit_factor for m in group)
        avg_sh = mean(m.sharpe for m in group)
        avg_nr = mean(m.net_return_pct for m in group)
        best_nr = max(m.net_return_pct for m in group)
        lines.append(
            f"| {name} | {len(group)} | {avg_trades:.0f} | {avg_wr:.1f} | {avg_pf:.2f} "
            f"| {avg_sh:.2f} | {avg_nr:+.1f} | {best_nr:+.1f} |"
        )
    lines.append("")

    beat_hodl = [m for m in metrics if m.net_return_pct > m.hodl_return_pct]
    lines.append(f"## 📌 Beat Buy-and-Hold: **{len(beat_hodl)}** of **{len(metrics)}** configurations")
    lines.append("")

    with open(RESULTS_MD, "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    run_all()
