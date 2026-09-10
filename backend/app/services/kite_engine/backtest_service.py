"""Orchestration for the Kite options backtest (workstream H).

Resolves instrument tokens, fetches candles via the warm client, and dispatches to
the pure backtest engine (backtest.py) for each requested data mode. Returns plain
dicts the endpoint serializes into ``BacktestResponse``.
"""
from __future__ import annotations

import time
from typing import List, Optional

import numpy as np

from app.core.logging import get_logger
from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
from app.schemas.instruments import InstrumentMeta
from app.services.kite_engine import backtest as bt
from app.services.kite_engine.greeks import bs_price
from app.services.kite_engine.universe import build_universe, select_scan_universe

log = get_logger(__name__)

_BARS_PER_DAY = 6.0   # ~6 trading hours of 1H bars in an Indian session


def _inst(token: int, name: str) -> InstrumentMeta:
    return InstrumentMeta(
        underlying=name, tick_size=0.05, strike_step=1.0, exchange_currency="INR",
        index_name=name, has_options=True, exchange="zerodha",
        zerodha_token=int(token))


async def _resolve_underlying_token(client, symbol: str) -> Optional[tuple]:
    """Resolve an underlying display name / tradingsymbol → (token, name)."""
    try:
        nfo, bfo, nse, bse = (
            await client.search_instruments("", "NFO", limit=1_000_000),
            await client.search_instruments("", "BFO", limit=1_000_000),
            await client.search_instruments("", "NSE", limit=1_000_000),
            await client.search_instruments("", "BSE", limit=1_000_000),
        )
        uni = build_universe(nfo_instruments=nfo, bfo_instruments=bfo, equities=nse + bse)
        s = symbol.strip().upper()
        for it in uni:
            if it.name.upper() == s or it.tradingsymbol.upper() == s:
                return it.token, it.name
    except Exception as exc:  # noqa: BLE001
        log.warning("backtest underlying resolve failed for %s: %s", symbol, exc)
    return None


async def _resolve_option_token(client, option_symbol: str) -> Optional[tuple]:
    """Resolve an option tradingsymbol → (token, exchange)."""
    for exch in ("NFO", "BFO"):
        try:
            rows = await client.search_instruments(option_symbol, exch, limit=50)
            for r in rows:
                if str(r.get("tradingsymbol", "")).upper() == option_symbol.strip().upper():
                    return int(r.get("instrument_token") or r.get("token") or 0), exch
        except Exception:  # noqa: BLE001
            continue
    return None


def _costs(req) -> bt.OptionCosts:
    c = bt.OptionCosts()
    if req.slippage_pct is not None:
        c.slippage_pct = float(req.slippage_pct)
    if req.brokerage_per_order is not None:
        c.brokerage_per_order = float(req.brokerage_per_order)
    return c


def _arrays(candles):
    return (
        [c.timestamp_ms for c in candles],
        [c.open for c in candles], [c.high for c in candles],
        [c.low for c in candles], [c.close for c in candles],
    )


async def run_backtest(client, req) -> dict:
    """Execute the requested data mode(s) and return a response dict."""
    cfg = SterlingKiteEngineConfig(trail_target=req.trail_target, exit_mode=req.exit_mode)
    costs = _costs(req)
    runs: List[dict] = []
    notes: List[str] = []
    drift: Optional[float] = None

    sym_clean = str(req.symbol or "").strip().upper()
    is_stock = sym_clean not in {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "BANKEX", "MIDCPNIFTY", "NIFTY 50", "NIFTY BANK"} and not any(
        idx in sym_clean for idx in ("NIFTY", "SENSEX", "BANKEX")
    )

    want_syn = req.data_mode in ("synthetic", "both")
    want_real = req.data_mode in ("real", "both")

    # ── synthetic: ST on underlying history, BS-priced premium ────────────────
    if want_syn:
        resolved = await _resolve_underlying_token(client, req.symbol)
        if resolved is None:
            notes.append(f"Synthetic mode skipped — could not resolve underlying '{req.symbol}'. "
                         "For synthetic/both pass the UNDERLYING name (e.g. 'NIFTY 50').")
        else:
            token, name = resolved
            candles = await client.get_candles(_inst(token, name), "1H", req.lookback_bars)
            if len(candles) <= cfg.warmup + 2:
                notes.append(f"Synthetic mode skipped — only {len(candles)} candles for {name}.")
            else:
                ts, o, h, l, c = _arrays(candles)
                run = bt.run_synthetic(
                    timestamps_ms=ts, u_open=o, u_high=h, u_low=l, u_close=c,
                    cfg=cfg, trail_target=req.trail_target, exit_mode=req.exit_mode,
                    iv=req.iv, dte_days=req.dte_days,
                    bars_per_day=_BARS_PER_DAY, moneyness_offset_pct=req.moneyness_offset_pct,
                    qty=req.qty, costs=costs, starting_capital=req.starting_capital)
                    qty=req.qty, costs=costs, starting_capital=req.starting_capital,
                    is_stock=is_stock)
                runs.append(_run_dict(run))

    # ── real: ST on an actual option-premium series ───────────────────────────
    real_candles = None
    if want_real:
        resolved = await _resolve_option_token(client, req.symbol)
        if resolved is None or not resolved[0]:
            notes.append(f"Real mode skipped — '{req.symbol}' is not a currently-listed option "
                         "tradingsymbol (expired strikes have no fetchable premium history).")
        else:
            token, exch = resolved
            real_candles = await client.get_candles(_inst(token, req.symbol), "1H", req.lookback_bars)
            if len(real_candles) <= cfg.warmup + 2:
                notes.append(f"Real mode: only {len(real_candles)} premium candles — small sample, "
                             "treat results as indicative.")
            if real_candles:
                ts, o, h, l, c = _arrays(real_candles)
                run = bt.replay_premium_series(
                    timestamps_ms=ts, premium_open=o, premium_high=h, premium_low=l,
                    premium_close=c, cfg=cfg, trail_target=req.trail_target,
                    exit_mode=req.exit_mode, qty=req.qty,
                    costs=costs, starting_capital=req.starting_capital, direction_label="long")
                    costs=costs, starting_capital=req.starting_capital, direction_label="long",
                    is_stock=is_stock)
                run.mode = "real"
                run.caveat = ("Real fetched premium for a currently-listed contract — limited to "
                              "this expiry cycle (cannot test the strategy over full history).")
                runs.append(_run_dict(run))

    # ── both: calibration drift of BS vs the real premium ─────────────────────
    if req.data_mode == "both" and real_candles and len(real_candles) > 5:
        drift = _bs_drift(real_candles, req)
        if drift is not None:
            notes.append(f"BS-vs-real premium drift on the live contract ≈ {drift:.1f}% "
                         "(how far the synthetic model sits from real prices).")

    return {
        "symbol": req.symbol,
        "data_mode": req.data_mode,
        "generated_ms": int(time.time() * 1000),
        "runs": runs,
        "bs_vs_real_drift_pct": drift,
        "notes": notes,
    }


def _bs_drift(real_candles, req) -> Optional[float]:
    """Mean abs % gap between the real premium and a BS reprice of it. Parses the
    strike/type from the option tradingsymbol; returns None if unparseable."""
    import re
    m = re.search(r"(\d+)(CE|PE)$", req.symbol.upper())
    if not m:
        return None
    strike = float(m.group(1))
    opt_type = m.group(2)
    # crude spot proxy: strike (ATM assumption) — drift is a sanity scale, not exact.
    diffs = []
    n = len(real_candles)
    for k, c in enumerate(real_candles):
        dte = max(0.1, req.dte_days - k / _BARS_PER_DAY)
        modeled = bs_price(spot=strike, strike=strike, dte_days=dte, iv=req.iv, option_type=opt_type)
        real = float(c.close)
        if real > 0:
            diffs.append(abs(modeled - real) / real)
    if not diffs:
        return None
    return round(float(np.mean(diffs)) * 100, 1)


def _run_dict(run: bt.BacktestRun) -> dict:
    s = run.stats
    return {
        "mode": run.mode,
        "caveat": run.caveat,
        "trades": [t.__dict__ for t in run.trades],
        "equity_curve": run.equity_curve,
        "stats": s.__dict__,
    }
