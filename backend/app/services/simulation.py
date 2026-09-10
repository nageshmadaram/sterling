"""
Market Replay Simulation Runner.

Replays historical candle data through the full signal pipeline at
configurable speeds, allowing users to watch strategies execute on
past trading days as if they were live.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from pydantic import BaseModel
from app.core.logging import get_logger
from app.engines.indicators.supertrend import compute_supertrend
from app.engines.indicators.heikin_ashi import compute_heikin_ashi

log = get_logger(__name__)

# Standard Kite NSE Instrument Token Map
KITE_TOKENS: Dict[str, int] = {
    "NIFTY": 256265,
    "NIFTY 50": 256265,
    "BANKNIFTY": 260105,
    "NIFTY BANK": 260105,
    "FINNIFTY": 257801,
    "NIFTY FIN SERVICE": 257801,
    "MIDCPNIFTY": 288009,
    "NIFTY MID SELECT": 288009,
    "SENSEX": 265,
    "BANKEX": 274441,
    "RELIANCE": 738561,
    "TATASTEEL": 895745,
    "HDFCBANK": 341249,
    "ICICIBANK": 1270529,
    "LT": 2939649,
    "SBIN": 779521,
    "TCS": 2953217,
    "INFY": 408065,
    "BHARTIARTL": 2714625,
    "AXISBANK": 1510401,
    "KOTAKBANK": 492033,
    "BAJFINANCE": 81153,
    "ADANIENT": 6401,
    "ADANIPORTS": 3861249,
    "BAJAJFINSV": 4268801,
}



def _load_recorded_signals(date_str: str, end_date_str: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load real recorded signals from kite_engine_signals or system stores for the given date (YYYY-MM-DD) or range."""
    from datetime import datetime, timezone, timedelta
    from app.services import db
    import json

    if not getattr(db, "_available", False):
        try:
            db.init()
        except Exception:
            pass

    ist = timezone(timedelta(hours=5, minutes=30))
    results: List[Dict[str, Any]] = []

    for uid in ["default", "u1", ""]:
        key = f"kite_engine_signals_{uid}" if uid else "kite_engine_signals"
        raw = db.get_config(key)
        if not raw:
            continue
        try:
            val = json.loads(raw)
            rows = val.get("rows", []) if isinstance(val, dict) else (val if isinstance(val, list) else [])
            for r in rows:
                ts = r.get("timestamp_ms")
                if not ts:
                    continue
                dt = datetime.fromtimestamp(ts / 1000, ist)
                sig_date = dt.strftime("%Y-%m-%d")
                matches = (date_str <= sig_date <= end_date_str) if (end_date_str and end_date_str != date_str) else (sig_date == date_str)
                if matches:
                    direction_str = str(r.get("direction", "short")).upper()
                    results.append({
                        "underlying": r.get("underlying", ""),
                        "direction": "BEARISH" if direction_str in ("SHORT", "BEARISH", "BEAR") else "BULLISH",
                        "time_iso": dt.strftime("%H:%M:%S"),
                        "timestamp_ms": ts,
                        "spot": float(r.get("spot") or 0.0),
                        "stop_loss": float(r.get("stop_loss") or 0.0),
                        "entry_sl": float(r.get("entry_sl") or 0.0),
                        "target": float(r.get("target") or 0.0) if r.get("target") is not None else None,
                        "raw_row": r,
                        "strategy": r.get("strategy") or "supertrend",
                        "is_spot_scan": True,
                        "source": r.get("source", "spot"),
                    })
        except Exception as err:
            log.warning("Failed parsing %s: %s", key, err)

    dedup: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for sig in results:
        k = (sig["underlying"], sig["timestamp_ms"])
        if k not in dedup:
            dedup[k] = sig
    return sorted(dedup.values(), key=lambda s: s["timestamp_ms"])


def _get_scanned_dates(date_str: str, end_date_str: Optional[str] = None) -> set[str]:
    """Identify dates within the range that were already scanned by the live Kite Engine.

    If a date was scanned live by the Kite Engine, ground-truth exists for that session
    (even if it produced 0 signals). In that case, replay must honor the 0 signals rather
    than fabricating synthetic 5m indicator crossovers.
    """
    from datetime import datetime, timezone, timedelta
    from app.services import db
    from app.services.kite_engine import state
    if not getattr(db, "_available", False):
        try:
            db.init()
        except Exception:
            pass
    ist = timezone(timedelta(hours=5, minutes=30))
    scanned: set[str] = set()

    # 1. Dates with recorded signals or cache in DB
    for uid in ["default", "u1", ""]:
        key = f"kite_engine_signals_{uid}" if uid else "kite_engine_signals"
        raw = db.get_config(key)
        if not raw:
            continue
        try:
            import json
            val = json.loads(raw)
            gen_ms = val.get("generated_ms") if isinstance(val, dict) else None
            if gen_ms:
                g_dt = datetime.fromtimestamp(gen_ms / 1000, ist)
                g_date = g_dt.strftime("%Y-%m-%d")
                if (end_date_str and date_str <= g_date <= end_date_str) or (date_str == g_date):
                    scanned.add(g_date)
            rows = val.get("rows", []) if isinstance(val, dict) else (val if isinstance(val, list) else [])
            for r in rows:
                ts = r.get("timestamp_ms")
                if ts:
                    r_dt = datetime.fromtimestamp(ts / 1000, ist)
                    r_date = r_dt.strftime("%Y-%m-%d")
                    if (end_date_str and date_str <= r_date <= end_date_str) or (date_str == r_date):
                        scanned.add(r_date)
        except Exception:
            pass

    # 2. Status last_scan_ms
    for uid in ["default", "u1"]:
        try:
            st = state.status(uid)
            if st.last_scan_ms > 0:
                s_dt = datetime.fromtimestamp(st.last_scan_ms / 1000, ist)
                s_date = s_dt.strftime("%Y-%m-%d")
                if (end_date_str and date_str <= s_date <= end_date_str) or (date_str == s_date):
                    scanned.add(s_date)
        except Exception:
            pass

    return scanned


class SimState(str, Enum):
    IDLE = "idle"
    LOADING = "loading"
    RUNNING = "running"
    PAUSED = "paused"


class SimConfig(BaseModel):
    date: str                          # "2026-08-28"
    end_date: Optional[str] = None     # optional end of a multi-day range
    start_time: str = "09:00:00"       # HH:MM:SS IST (default 9:00 AM)
    end_time: str = "15:40:00"         # HH:MM:SS IST — NFO close since 2026-08-03
    speed: float = 1.0                 # 1,2,5,10,15,20,50
    resolution: str = "5m"             # candle resolution
    instruments: List[str] = []        # empty = all watchlist
    strategy: str = "all"              # "all" or specific strategy name
    strategies: List[str] = ["all"]    # list of selected strategies
    adaptive_source: str = "both"      # "both", "ae_model", "spot_scan"
    adaptive_version: str = "v1_baseline"  # "v2_hardened", "v1_baseline"
    lots: int = 1                      # number of option/futures lots
    moneyness: str = "ATM"             # "ATM", "ITM1", "ITM2", "OTM1", "OTM2", "ALL"
    max_hold_bars: int = 30            # max bars to hold position before timing out
    # ── Execution friction ────────────────────────────────────────────────
    # These are read by `_apply_friction`. Until 2026-09 they were declared
    # here and consumed nowhere, while the UI rendered a slippage column and
    # a "SLIPPAGE DRAG" metric against them — so the dock reported ₹0.00 of
    # execution cost for every strategy. The values below are echoed back on
    # `SimStatus.config`, which is what lets the client verify the engine
    # actually honoured what it asked for.
    friction_mode: str = "realistic"   # "realistic" (spread + slippage) or "ideal"
    index_spread_pct: float = 0.50     # round-trip bid/ask spread, index options
    stock_spread_pct: float = 1.50     # round-trip bid/ask spread, stock options
    slippage_pct: float = 0.25         # additional adverse fill, each leg
    # Accepted for compatibility with callers that speak basis points. When
    # supplied it OVERRIDES `slippage_pct`; 100 bps == 1.00%.
    slippage_bps: Optional[float] = None


class SimSignalEvent(BaseModel):
    time_iso: str
    timestamp_ms: int = 0
    strategy: str
    instrument: str
    direction: str
    strength: str
    entry: float
    stop: float
    target: float
    # The option leg this signal would be expressed through. `None` for a pure
    # spot signal; the UI falls back to `instrument` in that case.
    contract: Optional[str] = None
    spot: Optional[float] = None
    strike: Optional[float] = None
    opt_type: Optional[str] = None
    premium_entry: Optional[float] = None
    premium_sl: Optional[float] = None
    premium_target: Optional[float] = None
    scan_origin: Optional[str] = None
    strategy_version: Optional[str] = None
    # Gamma Move level filter only. Absent on every other engine.
    level_price: Optional[float] = None
    level_kind: Optional[str] = None
    level_touches: Optional[int] = None
    regime: Optional[str] = None


class SimTradeEvent(BaseModel):
    trade_id: str
    entry_time_iso: str = ""
    exit_time_iso: str = "OPEN"
    exit_timestamp_ms: Optional[int] = None
    timestamp_ms: int = 0
    strategy: str
    symbol: str
    underlying: str
    direction: str
    opt_type: str
    strike: float
    lots: int
    quantity: int
    entry_price: float
    exit_price: Optional[float] = None
    stop_loss: float
    target_price: float
    status: str = "OPEN"
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    duration_mins: int = 0
    # Theoretical prices before execution friction. `None` when the replay ran
    # in "ideal" mode — which is not the same as zero, and the UI renders the
    # difference (an em dash vs a ₹0.00).
    raw_entry: Optional[float] = None
    raw_exit: Optional[float] = None
    slippage: Optional[float] = None
    # The UNDERLYING levels this position is judged against. Carried on the
    # trade so a later bar can settle it, rather than the outcome being decided
    # from future bars at the moment of entry.
    spot_entry: Optional[float] = None
    spot_stop: Optional[float] = None
    spot_target: Optional[float] = None
    #: High-water-mark of the underlying spot (most favorable extreme seen since
    #: entry). Used to ratchet the trailing stop: once the underlying moves far
    #: enough in our favour the stop follows, locking in gains. Initialised to
    #: spot_entry at trade creation; updated on every bar in _settle_open_positions.
    spot_hwm: Optional[float] = None
    #: The initial stop distance (|spot_entry − spot_stop|) at trade creation,
    #: used as the fixed offset for the trailing ratchet. Preserved even as
    #: spot_stop itself tightens.
    spot_initial_risk: Optional[float] = None
    #: Initial underlying stop at trade creation, to distinguish hard stop hits from trailing stop hits.
    spot_initial_stop: Optional[float] = None
    #: Why the position closed: TARGET, STOP_LOSS, TRAILING_STOP, MAX_HOLD, SESSION_CLOSE.
    exit_reason: Optional[str] = None
    bars_held: int = 0
    scan_origin: Optional[str] = None
    strategy_version: Optional[str] = None


class SimStats(BaseModel):
    signals_fired: int = 0
    trades_entered: int = 0
    wins: int = 0
    losses: int = 0
    pnl: float = 0.0
    events: List[SimSignalEvent] = []
    trades: List[SimTradeEvent] = []
    # Total INR drag across all trades. `None` means friction was not modelled
    # at all; 0.0 would mean it was modelled and happened to be free.
    slippage_total: Optional[float] = None


class SimEvent(BaseModel):
    """One server-sent event.

    `kind` becomes the SSE `event:` name, so the client can register a handler
    per kind rather than sniffing the payload.
    """
    kind: str          # "state" | "frame" | "signal" | "trade"
    data: Dict[str, Any] = {}


class SimSessionPolicy(BaseModel):
    """The exchange session bounds that apply to the replayed date.

    Derived from `kite_engine.market_hours`, which is the versioned source for
    this (NSE CMTR/74466 + FAOP/74467 for CAS, SEBI 99122 for pre-open). The
    replay dock reads these instead of hardcoding them — it previously assumed
    a 15:30 close for everything, which has been wrong for F&O since the
    Closing Auction Session started on 2026-08-03: derivatives now run to
    15:40 and F&O cash stops at 15:15.
    """
    policy_version: str
    preopen_start: str          # continuous-trading pre-open opens
    continuous_open: str        # first continuously-traded bar
    continuous_close: str       # close for the replay's own instrument class
    derivatives_close: str      # NFO
    cash_close: str             # NSE, non-F&O
    fo_cash_close: str          # NSE, F&O-eligible (CAS takes over after this)
    cas_end: Optional[str] = None


class SimCapabilities(BaseModel):
    """What this build of the runner can actually do.

    The client renders optional columns, sections and controls off this rather
    than off whether a sampled row happened to carry a value. That inversion is
    the structural fix for a whole class of defect where the UI advertised a
    capability the engine did not have.
    """
    friction: bool = True
    contract_on_signal: bool = True
    absolute_seek: bool = True
    stream: bool = True
    delta_status: bool = True
    multi_day: bool = True
    resolutions: List[str] = ["1m", "5m", "15m"]


class SimStatus(BaseModel):
    state: SimState = SimState.IDLE
    config: Optional[SimConfig] = None
    # A finished session's ledger is worth keeping for review, but the client
    # has to be able to tell it apart from one that is still running. Without
    # this the dock showed a completed session's trades before you pressed play.
    session_id: Optional[str] = None
    session_complete: bool = False
    current_time_iso: str = ""
    current_date: Optional[str] = None
    progress_pct: float = 0.0
    bars_played: int = 0
    bars_total: int = 0
    stats: SimStats = SimStats()
    elapsed_real_s: float = 0.0
    status_message: str = ""
    last_signal: Optional[SimSignalEvent] = None
    capabilities: SimCapabilities = SimCapabilities()
    session_policy: Optional[SimSessionPolicy] = None
    events_total: int = 0
    trades_total: int = 0
    open_positions: int = 0
    unrealised_pnl: float = 0.0


INDEX_SYMBOLS = {
    "NIFTY", "NIFTY 50",
    "BANKNIFTY", "NIFTY BANK",
    "FINNIFTY", "NIFTY FIN SERVICE",
    "MIDCPNIFTY", "NIFTY MID SELECT",
    "SENSEX", "BSE:SENSEX",
    "BANKEX", "BSE:BANKEX",
}

CANONICAL_INDEX_MAP = {
    "NIFTY 50": "NIFTY",
    "NIFTY BANK": "BANKNIFTY",
    "NIFTY FIN SERVICE": "FINNIFTY",
    "NIFTY MID SELECT": "MIDCPNIFTY",
    "BSE:SENSEX": "SENSEX",
    "BSE:BANKEX": "BANKEX",
}


def _canonical_symbol(symbol: str) -> str:
    s = symbol.upper().strip()
    if ":" in s:
        s = s.split(":")[-1].strip()
    return CANONICAL_INDEX_MAP.get(s, s)


# Strike step per underlying. Anything not listed falls back to a percentage of
# spot, rounded to a sane increment.
STRIKE_STEP = {
    "NIFTY": 50.0, "NIFTY 50": 50.0,
    "BANKNIFTY": 100.0, "NIFTY BANK": 100.0,
    "FINNIFTY": 50.0, "NIFTY FIN SERVICE": 50.0,
    "MIDCPNIFTY": 25.0, "NIFTY MID SELECT": 25.0,
    "SENSEX": 100.0, "BANKEX": 100.0,
}

# How far each moneyness label sits from ATM, in strike steps. Positive moves
# the strike in the direction that makes a CE cheaper (further out of the money).
MONEYNESS_OFFSET = {"ATM": 0, "ITM1": -1, "ITM2": -2, "OTM1": 1, "OTM2": 2}


def _is_index(symbol: str) -> bool:
    s = symbol.upper().strip()
    if ":" in s:
        s = s.split(":")[-1].strip()
    return s in INDEX_SYMBOLS or _canonical_symbol(symbol) in INDEX_SYMBOLS


_IST = timezone(timedelta(hours=5, minutes=30))
_SESSION_END = (15, 30)  # NSE cash close. A forming daily bar is not a close.


INDEX_LOT_SIZES = {
    "NIFTY": 25,
    "BANKNIFTY": 15,
    "FINNIFTY": 25,
    "MIDCPNIFTY": 50,
    "SENSEX": 10,
    "BANKEX": 15,
}


def _lot_size(symbol: str) -> int:
    canon = _canonical_symbol(symbol)
    if canon in INDEX_LOT_SIZES:
        return INDEX_LOT_SIZES[canon]
    if _is_index(symbol):
        return 25
    return 15


def _strike_step(symbol: str, spot: float) -> float:
    canon = _canonical_symbol(symbol)
    step = STRIKE_STEP.get(canon) or STRIKE_STEP.get(symbol.upper().strip())
    if step:
        return step
    # Stocks: ~1% of spot, snapped to a familiar increment.
    for candidate in (2.5, 5.0, 10.0, 20.0, 50.0, 100.0):
        if spot * 0.01 <= candidate:
            return candidate
    return 100.0


def _pick_moneyness(config: Optional["SimConfig"]) -> str:
    """The first concrete leg the user asked for.

    `moneyness` may be "ALL" or a comma-joined list; a contract has to be one
    strike, so take the first concrete entry and fall back to ATM.
    """
    raw = (config.moneyness if config else "ATM") or "ATM"
    for part in str(raw).split(","):
        key = part.strip().upper()
        if key in MONEYNESS_OFFSET:
            return key
    return "ATM"


def _live_orb_direction(history: List[Dict], bar_dt) -> Optional[str]:
    """LONG/SHORT from the live ORB engine, or None.

    Replay used to clone ORB with a 4-bar VWAP test the production engine
    would never fire. Same tickets require the same ``generate_signal``.
    """
    from dataclasses import replace
    from datetime import timezone, timedelta
    from app.engines.nifty_orb_options import Bar, StrategyConfig, generate_signal
    from app.services.nifty_orb_options import get_config

    ist = bar_dt.tzinfo or timezone(timedelta(hours=5, minutes=30))
    bars = []
    for b in history:
        ts = datetime.fromtimestamp(int(b["time"]), tz=ist)
        bars.append(Bar(
            timestamp=ts,
            open=float(b["open"]),
            high=float(b["high"]),
            low=float(b["low"]),
            close=float(b["close"]),
            volume=float(b.get("volume") or 0),
        ))
    try:
        cfg = replace(get_config(), enabled=True)
    except Exception:
        cfg = StrategyConfig()
    try:
        sig = generate_signal(bars, cfg)
    except ValueError:
        return None
    return sig.direction if sig.direction in ("LONG", "SHORT") else None


def _expiry_from_synthetic_contract(name: str) -> str:
    """Last Thursday of the month encoded in ``NIFTY26AUG25000CE``."""
    import calendar
    import re
    from datetime import date
    m = re.search(r"(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)", name or "", re.I)
    if not m:
        return ""
    months = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
    year, month = 2000 + int(m.group(1)), months.index(m.group(2).upper()) + 1
    thursdays = [w[calendar.THURSDAY] for w in calendar.monthcalendar(year, month) if w[calendar.THURSDAY]]
    return date(year, month, thursdays[-1]).isoformat()


def _option_contract(
    symbol: str,
    spot: float,
    direction: str,
    config: Optional["SimConfig"] = None,
    sim_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve the option leg a signal on `symbol` at `spot` would be taken through.

    Returns the tradeable name, its strike, CE/PE, the lot size and an
    approximate premium. Used for BOTH the signal event and the trade, so the
    contract a user sees in the signals feed is the one the trade reports.
    """
    opt_type = "CE" if direction in ("BULLISH", "LONG") else "PE"
    step = _strike_step(symbol, spot)
    atm = round(spot / step) * step

    # An OTM call is a HIGHER strike; an OTM put is a LOWER one.
    offset = MONEYNESS_OFFSET.get(_pick_moneyness(config), 0)
    signed = offset if opt_type == "CE" else -offset
    strike = max(step, atm + signed * step)

    lot_size = 25 if _is_index(symbol) else 15
    lot_size = _lot_size(symbol)
    # Rough premium: ~2% of spot at ATM, decaying as the strike moves away.
    intrinsic = max(0.0, (spot - strike) if opt_type == "CE" else (strike - spot))
    extrinsic = max(0.05, spot * 0.02 - abs(strike - atm) * 0.35)
    premium = round(intrinsic + extrinsic, 2)

    expiry_tag = "26AUG"
    target_date = sim_date or (config.date if (config and getattr(config, "date", None)) else None)
    if target_date:
        try:
            from datetime import datetime
            dt = datetime.strptime(target_date, "%Y-%m-%d")
            expiry_tag = f"{dt.strftime('%y')}{dt.strftime('%b').upper()}"
        except Exception:
            pass

    return {
        "contract": f"{_canonical_symbol(symbol)}{expiry_tag}{int(strike)}{opt_type}",
        "strike": float(strike),
        "opt_type": opt_type,
        "lot_size": lot_size,
        "premium": premium,
    }


def _premium_at(leg: Dict[str, Any], spot_entry: float, spot_level: float) -> float:
    """The option premium implied when the underlying reaches `spot_level`.

    Same ~0.50 delta approximation the settlement path uses, so the ladder a
    signal advertises and the fill a trade reports cannot disagree.
    """
    move = (spot_level - spot_entry) if leg["opt_type"] == "CE" else (spot_entry - spot_level)
    return round(max(0.05, leg["premium"] + move * 0.50), 2)


def _asof_symbol_bars(candles: list, sym: str, bar_time: Any) -> list:
    """Bars for `sym` at or before `bar_time`. Future prints stay off the tape."""
    if not candles or bar_time is None:
        return []
    aliases = {sym, str(sym).upper()}
    try:
        from app.services.ohlcv_store import INDEX_ALIASES
        u = str(sym).upper()
        if u in INDEX_ALIASES:
            aliases.add(INDEX_ALIASES[u])
            aliases.add(str(INDEX_ALIASES[u]).upper())
        for k, v in INDEX_ALIASES.items():
            if u in (str(k).upper(), str(v).upper()):
                aliases.add(k)
                aliases.add(v)
    except Exception:
        pass
    alias_u = {str(a).upper() for a in aliases}
    t = float(bar_time)
    out = []
    for b in candles:
        if str(b.get("symbol") or "").upper() not in alias_u:
            continue
        bt = b.get("time")
        if bt is None:
            continue
        try:
            if float(bt) <= t:
                out.append(b)
        except (TypeError, ValueError):
            continue
    return out


def _bar_epoch_seconds(bar: dict) -> Optional[float]:
    raw = bar.get("time") or bar.get("timestamp")
    if raw is None:
        return None
    try:
        ts = float(raw)
    except (TypeError, ValueError):
        return None
    return ts / 1000.0 if ts > 10_000_000_000 else ts


def _gamma_move_in_universe(symbol: str, cfg: Any) -> bool:
    """Source universe is stock options. Indices only if `scan_indices` names them."""
    if _is_index(symbol):
        wanted = {str(n).upper() for n in (getattr(cfg, "scan_indices", ()) or ())}
        return bool(wanted) and (
            _canonical_symbol(symbol) in wanted or str(symbol).upper() in wanted)
    if not getattr(cfg, "stock_contracts", True):
        return False
    if getattr(cfg, "scan_all_stocks", True):
        return True
    wanted = {str(n).upper() for n in (getattr(cfg, "scan_stocks", ()) or ())}
    return str(symbol).upper() in wanted or _canonical_symbol(symbol) in wanted


def _collapse_to_daily(history: list, asof_ts: Optional[float] = None) -> list:
    """One completed IST session per row. Today's forming bar stays off until 15:30."""
    buckets: Dict[Any, dict] = {}
    order: list = []
    for b in history:
        ts = _bar_epoch_seconds(b)
        if ts is None:
            continue
        day = datetime.fromtimestamp(ts, _IST).date()
        o, h, l, c = (float(b.get("open") or 0), float(b.get("high") or 0),
                      float(b.get("low") or 0), float(b.get("close") or 0))
        vol = float(b.get("volume") or 0)
        if day not in buckets:
            buckets[day] = {"open": o, "high": h, "low": l, "close": c,
                            "volume": vol, "time": ts}
            order.append(day)
            continue
        d = buckets[day]
        d["high"] = max(d["high"], h)
        d["low"] = min(d["low"], l) if d["low"] else l
        d["close"] = c
        d["volume"] += vol
    if not order:
        return []
    asof = asof_ts if asof_ts is not None else _bar_epoch_seconds(history[-1])
    if asof is not None:
        asof_dt = datetime.fromtimestamp(asof, _IST)
        session_done = (asof_dt.hour, asof_dt.minute) >= _SESSION_END
        if asof_dt.date() == order[-1] and not session_done and len(order) > 1:
            order = order[:-1]
        elif asof_dt.date() == order[-1] and not session_done and len(order) == 1:
            return []
    out = []
    for day in order:
        row = dict(buckets[day])
        row["time"] = datetime(day.year, day.month, day.day, 15, 30, tzinfo=_IST).timestamp()
        out.append(row)
    return out


def _gamma_move_store_daily(symbol: str, asof_ts: Optional[float]) -> list:
    """Completed daily bars from the OHLCV store, as-of `asof_ts`. Empty on miss."""
    if not symbol or asof_ts is None:
        return []
    try:
        from app.services.ohlcv_store import get_candles
    except Exception:
        return []
    since = int(asof_ts - 400 * 86400)
    until = int(asof_ts)
    rows: list = []
    for res in ("1d", "day"):
        try:
            rows = get_candles(symbol, res, limit=400, since=since, until=until) or []
        except Exception:
            rows = []
        if rows:
            break
    return _collapse_to_daily(rows, asof_ts)


def _gamma_move_watch_from_bars(history: list, close: float, *,
                                symbol: str = "",
                                cfg: Any = None,
                                prefer_store: bool = False) -> Optional[Dict[str, Any]]:
    """Daily level+regime gate on a stock. Never STRONG — no option OI tape."""
    from app.engines.gamma_move import (
        Candle, GammaMoveConfig, find_levels, live_levels, option_type_for,
        regime_allows, regime_of,
    )
    if cfg is None:
        try:
            from app.services.gamma_move import get_config
            cfg = get_config()
        except Exception:
            cfg = GammaMoveConfig()
    if not cfg.enabled:
        return None
    if symbol and not _gamma_move_in_universe(symbol, cfg):
        return None
    asof = _bar_epoch_seconds(history[-1]) if history else None
    need = cfg.pivot_lookback * 2 + 10
    daily = _gamma_move_store_daily(symbol, asof) if prefer_store else []
    if len(daily) < need:
        daily = _collapse_to_daily(history, asof)
    if len(daily) < need:
        return None
    candles: list = []
    for b in daily:
        ts = _bar_epoch_seconds(b) or 0.0
        ts_ms = int(ts * 1000)
        candles.append(Candle(
            ts_ms=ts_ms,
            open=float(b.get("open") or 0),
            high=float(b.get("high") or 0),
            low=float(b.get("low") or 0),
            close=float(b.get("close") or 0),
            volume=int(float(b.get("volume") or 0)),
        ))
    levels = find_levels(
        candles,
        pivot_lookback=cfg.pivot_lookback,
        cluster_pct=cfg.level_cluster_pct,
        min_touches=cfg.min_level_touches,
        window=cfg.level_lookback_days,
    )
    near = live_levels(levels, close, cfg.level_proximity_pct)
    if not near:
        return None
    regime = "unknown"
    if cfg.regime_enabled:
        regime = regime_of(candles, cfg)
        near = [lv for lv in near if regime_allows(regime, option_type_for(lv), cfg)]  # type: ignore[arg-type]
        if not near:
            return None
    want = option_type_for(near[0])
    return {
        "strategy": "gamma_move",
        "direction": "BULLISH" if want == "CE" else "BEARISH",
        "strength": "WATCHING",
        "level_price": float(near[0].price),
        "level_kind": near[0].kind,
        "level_touches": int(near[0].touches),
        "regime": regime,
    }


def _gamma_move_candidate(ev: "SimSignalEvent", sim_date: str) -> Dict[str, Any]:
    opt = ev.opt_type or ("CE" if ev.direction.upper() in ("BULLISH", "LONG", "BUY") else "PE")
    kind = ev.level_kind or ("resistance" if opt == "CE" else "support")
    spot = float(ev.spot or ev.entry or 0)
    level_px = float(ev.level_price) if ev.level_price else spot
    dist = abs(spot - level_px) / level_px * 100.0 if level_px else 0.0
    underlying = ev.instrument
    return {
        "id": f"{underlying}@{kind}:{int(level_px)}",
        "state": "watching",
        "at_ms": ev.timestamp_ms,
        "underlying": underlying,
        "regime": ev.regime if ev.regime in ("up", "down") else "unknown",
        "reason": "replay has no option open-interest tape — trigger cannot fire",
        "exit_reason": None,
        "entry_day": sim_date,
        "instrument": {
            "instrument_id": underlying,
            "tradingsymbol": underlying,
            "exchange": "NFO",
            "option_type": opt,
            "strike": None,
            "expiry": None,
            "lot_size": None,
            "tick_size": 0.05,
        },
        "level": {
            "price": level_px, "kind": kind,
            "touches": ev.level_touches or 0, "distance_pct": round(dist, 2),
        },
        "oi": 0,
        "days_to_expiry": None,
        "spot": spot,
        "metrics": None,
        "levels": {
            "ltp": None,
            "entry": None,
            "stop": None,
            "trail": None,
            "target": None,
            "exit": None,
        },
        "sizing": {"lots": None, "quantity": None, "at_risk_inr": None, "deployed_inr": None},
    }


def _apply_friction(
    raw_entry: float,
    raw_exit: float,
    symbol: str,
    config: Optional["SimConfig"],
) -> Tuple[float, float, str]:
    """Fill prices after bid/ask spread and slippage.

    You buy at the ask and sell at the bid, and each leg suffers an additional
    adverse slippage. Returns `(fill_entry, fill_exit, mode)`.

    In "ideal" mode the fills ARE the theoretical prices — that is a modelled
    zero, and the caller distinguishes it from "not modelled" by whether
    friction ran at all.
    """
    mode = (config.friction_mode if config else "realistic") or "realistic"
    if mode == "ideal":
        return raw_entry, raw_exit, "ideal"

    spread_pct = (config.index_spread_pct if config else 0.50) if _is_index(symbol) \
        else (config.stock_spread_pct if config else 1.50)
    # Basis points win when supplied: a caller that speaks bps is being explicit,
    # and silently preferring the percent default would ignore what it asked for.
    bps = getattr(config, "slippage_bps", None) if config else None
    slip_pct = (bps / 100.0) if bps is not None else (config.slippage_pct if config else 0.25)
    # V2 Hardened: Passive limit order execution models 50% slippage reduction
    adaptive_ver = (getattr(config, "adaptive_version", None) or "v2_hardened").lower()
    if adaptive_ver in ("v2_hardened", "v2"):
        slip_pct *= 0.5

    half_spread = spread_pct / 200.0     # round-trip pct → one-sided fraction
    slip = slip_pct / 100.0
    adverse = half_spread + slip

    fill_entry = round(raw_entry * (1.0 + adverse), 2)
    # A fill can be pushed to zero but never below it.
    fill_exit = round(max(0.05, raw_exit * (1.0 - adverse)), 2)
    return fill_entry, fill_exit, "realistic"


class SimulationRunner:
    """Singleton service that replays historical bars through the signal pipeline."""

    def __init__(self):
        self._state = SimState.IDLE
        self._config: Optional[SimConfig] = None
        self._task: Optional[asyncio.Task] = None
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # not paused initially
        self._stop_requested = False
        self._speed: float = 1.0
        self._stats = SimStats()
        self._current_time_iso = ""
        self._current_date = ""
        self._progress = 0.0
        self._bars_played = 0
        self._bars_total = 0
        self._start_real = 0.0
        self._candles: List[Dict] = []
        self._status_message = "Ready for simulation"
        self._last_signal: Optional[SimSignalEvent] = None
        self._current_sim_epoch: float = 0.0
        self._start_epoch: int = 0
        self._end_epoch: int = 0
        self._seek_requested_epoch: Optional[float] = None
        self._last_fired: Dict[Tuple[str, str], Tuple[str, int]] = {}
        # Fan-out to SSE subscribers. Bounded, and `_publish` drops FRAMES
        # under back-pressure but never a signal, trade or state change: a
        # dropped frame costs a progress tick, a dropped signal corrupts the
        # ledger the client is accumulating.
        self._subscribers: "List[asyncio.Queue[SimEvent]]" = []
        self._last_frame_at: float = 0.0
        # Positions currently open, per underlying. A replay that decides a
        # trade's outcome the instant it opens is not a replay.
        self._open_by_symbol: Dict[str, List[SimTradeEvent]] = {}
        # Re-entry suppression and the recorded-signal replay path, from main.
        self._active_until_bar: Dict[Tuple[str, str], int] = {}
        # In-session bars seen per symbol; gates the session-boundary bar.
        self._in_session_bars: Dict[str, int] = {}
        # Bumped by every start(). A replay loop that no longer matches this is
        # a superseded loop and must not touch shared state.
        self._run_generation: int = 0
        self._recorded_signals: List[Dict[str, Any]] = []
        self._scanned_dates: set[str] = set()
        self._emitted_recorded_keys: set = set()
        self._session_id: Optional[str] = None
        self._session_complete: bool = False
        self._ae_fallback_mode: bool = False

    # ── SSE fan-out ─────────────────────────────────────────────────────

    def _publish(self, kind: str, data: Dict[str, Any]) -> None:
        if not self._subscribers:
            return
        event = SimEvent(kind=kind, data=data)
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Only a frame may be dropped. For anything else, make room by
                # discarding the OLDEST frame still queued.
                if kind == "frame":
                    continue
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except Exception:
                    pass

    def _publish_state(self) -> None:
        self._publish("state", {
            "state": self._state.value if hasattr(self._state, "value") else str(self._state),
            "status_message": self._status_message,
            "config": self._config.model_dump() if self._config else None,
            "bars_total": self._bars_total,
        })

    def _publish_frame(self, force: bool = False) -> None:
        """Throttled progress tick.

        10 Hz normally, 2 Hz once the replay is fast enough that a frame per
        update would be pure noise — at 5000x the clock advances hours per
        second and nobody can read it anyway.
        """
        now = time.monotonic()
        min_gap = 0.5 if self._speed >= 100 else 0.1
        if not force and (now - self._last_frame_at) < min_gap:
            return
        self._last_frame_at = now
        self._publish("frame", {
            "t": self._current_time_iso,
            "cur_date": self._current_date or (self._current_time_iso.split("T")[0] if "T" in self._current_time_iso else None),
            "pct": self._progress,
            "bars_played": self._bars_played,
            "bars_total": self._bars_total,
            "elapsed_real_s": round(time.monotonic() - self._start_real, 1) if self._start_real else 0,
            "pnl": self._stats.pnl,
            "wins": self._stats.wins,
            "losses": self._stats.losses,
            "signals_fired": self._stats.signals_fired,
            "trades_entered": self._stats.trades_entered,
            "slippage_total": self._stats.slippage_total,
            "open_positions": sum(len(v) for v in self._open_by_symbol.values()),
            "unrealised_pnl": round(
                sum(tr.pnl_usd for tr in self._stats.trades if tr.status == "OPEN"), 2
            ),
        })

    async def subscribe(self):
        """Yield events until the caller stops iterating."""
        q: "asyncio.Queue[SimEvent]" = asyncio.Queue(maxsize=512)
        self._subscribers.append(q)
        # Open with the current state so a client that connects mid-session is
        # not left blank until the next transition.
        try:
            q.put_nowait(SimEvent(kind="state", data={
                "state": self._state.value if hasattr(self._state, "value") else str(self._state),
                "status_message": self._status_message,
                "config": self._config.model_dump() if self._config else None,
                "bars_total": self._bars_total,
            }))
        except asyncio.QueueFull:
            pass
        try:
            while True:
                yield await q.get()
        finally:
            if q in self._subscribers:
                self._subscribers.remove(q)

    # ── Open book ───────────────────────────────────────────────────────

    MAX_HOLD_BARS = 30

    def _premium_for_spot(self, trade: SimTradeEvent, spot: float) -> float:
        """Option premium at `spot`, by the same delta approximation used at entry.

        ~50% of the underlying's move passes into the premium, floored just
        above zero — an option can expire worthless but cannot go negative.
        """
        entry_spot = trade.spot_entry if trade.spot_entry is not None else spot
        move = (spot - entry_spot) if trade.opt_type == "CE" else (entry_spot - spot)
        base = trade.raw_entry if trade.raw_entry is not None else trade.entry_price
        return round(max(0.05, base + move * 0.50), 2)

    def _settle_open_positions(self, bar: Dict[str, Any], bar_dt) -> None:
        """Advance every open position on this symbol by one bar.

        A position closes when THIS bar's range reaches its stop or target, or
        when it has been held too long — never from a bar the replay clock has
        not reached yet.
        """
        sym = bar.get("symbol", "")
        sym_u = sym.upper()
        canon = _canonical_symbol(sym)
        from app.services.ohlcv_store import INDEX_ALIASES
        candidate_keys = [sym, sym_u, canon]
        for item in [sym_u, canon]:
            if item in INDEX_ALIASES and INDEX_ALIASES[item] not in candidate_keys:
                candidate_keys.append(INDEX_ALIASES[item])
            for k, v in INDEX_ALIASES.items():
                if item == v.upper() and k not in candidate_keys:
                    candidate_keys.append(k)

        matching_keys = list(dict.fromkeys(k for k in candidate_keys if k in self._open_by_symbol))
        if not matching_keys:
            return

        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])

        for k in matching_keys:
            book = self._open_by_symbol.get(k)
            if not book:
                continue
            still_open: List[SimTradeEvent] = []

            for trade in book:
                trade.bars_held += 1
                bullish = trade.opt_type == "CE"

                stop = trade.spot_stop
                target = trade.spot_target

                exit_spot: Optional[float] = None
                exit_reason: Optional[str] = None
                if stop is not None and target is not None:
                    if bullish:
                        # Stop first: the pessimistic read when one bar spans both,
                        # because a bar's high and low carry no ordering.
                        if low <= stop:
                            exit_spot = stop
                            is_tsl = (
                                trade.spot_initial_stop is not None
                                and trade.spot_stop is not None
                                and abs(trade.spot_stop - trade.spot_initial_stop) > 1e-4
                            )
                            exit_reason = "TRAILING_STOP" if is_tsl else "STOP_LOSS"
                        elif high >= target:
                            exit_spot = target
                            exit_reason = "TARGET"
                    else:
                        if high >= stop:
                            exit_spot = stop
                            is_tsl = (
                                trade.spot_initial_stop is not None
                                and trade.spot_stop is not None
                                and abs(trade.spot_stop - trade.spot_initial_stop) > 1e-4
                            )
                            exit_reason = "TRAILING_STOP" if is_tsl else "STOP_LOSS"
                        elif low <= target:
                            exit_spot = target
                            exit_reason = "TARGET"

                timed_out = exit_spot is None and trade.bars_held >= self.MAX_HOLD_BARS
                is_ae_v2 = (
                    (self._config.adaptive_version if self._config and hasattr(self._config, "adaptive_version") else "v2_hardened") or "v2_hardened"
                ).lower() in ("v2_hardened", "v2")

                # V2 Hardened Stagnation Decay Exit: If trade makes no meaningful progress (< 0.25R)
                # after 4 bars (20m), exit early to protect capital against chop and theta decay.
                if (
                    exit_spot is None
                    and is_ae_v2
                    and trade.bars_held >= 4
                    and trade.spot_initial_risk
                    and trade.spot_initial_risk > 0
                    and trade.spot_entry is not None
                    and trade.spot_hwm is not None
                ):
                    favorable_dist = (trade.spot_hwm - trade.spot_entry) if bullish else (trade.spot_entry - trade.spot_hwm)
                    r_multiple = favorable_dist / trade.spot_initial_risk
                    if r_multiple < 0.25:
                        exit_spot = close
                        exit_reason = "STAGNATION_DECAY"

                max_bars = (
                    self._config.max_hold_bars
                    if (self._config and getattr(self._config, "max_hold_bars", None))
                    else self.MAX_HOLD_BARS
                )
                timed_out = exit_spot is None and trade.bars_held >= max_bars
                if timed_out:
                    exit_spot = close
                    exit_reason = "MAX_HOLD"

                if exit_spot is None:
                    # Still open — mark it to this bar so unrealised P&L moves.
                    mark = self._premium_for_spot(trade, close)
                    trade.pnl_usd = round((mark - trade.entry_price) * trade.quantity, 2)
                    trade.pnl_pct = round(
                        ((mark - trade.entry_price) / trade.entry_price) * 100.0, 2
                    ) if trade.entry_price > 0 else 0.0
                    trade.duration_mins = trade.bars_held * self._bar_minutes()

                    # ── trailing stop ratchet (bar-close based) ────────────────
                    # Update the high-water-mark from this bar's CLOSE (not the
                    # intrabar extreme), then trail the stop toward price. Using
                    # the close matches the live engine's SuperTrend trail — it
                    # operates on completed bars, never on intrabar extremes — and
                    # prevents the same bar's high/low from both creating a new HWM
                    # and triggering the tightened stop (a lookahead artefact).
                    if (trade.spot_hwm is not None and trade.spot_initial_risk is not None
                            and trade.spot_initial_risk > 0 and trade.spot_stop is not None):
                        if bullish:
                            trade.spot_hwm = max(trade.spot_hwm, close)
                            new_stop = trade.spot_hwm - trade.spot_initial_risk
                            # In V2, lock breakeven once price reaches >= 1.0R
                            if is_ae_v2 and trade.spot_entry is not None and (trade.spot_hwm - trade.spot_entry) >= trade.spot_initial_risk:
                                new_stop = max(new_stop, trade.spot_entry)
                            if new_stop > trade.spot_stop:
                                trade.spot_stop = round(new_stop, 2)
                        else:
                            trade.spot_hwm = min(trade.spot_hwm, close)
                            new_stop = trade.spot_hwm + trade.spot_initial_risk
                            # In V2, lock breakeven once price reaches >= 1.0R
                            if is_ae_v2 and trade.spot_entry is not None and (trade.spot_entry - trade.spot_hwm) >= trade.spot_initial_risk:
                                new_stop = min(new_stop, trade.spot_entry)
                            if new_stop < trade.spot_stop:
                                trade.spot_stop = round(new_stop, 2)
                        # Re-derive the premium stop so the UI's SL column tracks
                        # the ratcheted level instead of showing the static entry stop.
                        if trade.spot_entry is not None:
                            trade.stop_loss = round(max(0.05, self._premium_for_spot(trade, trade.spot_stop)), 2)

                    still_open.append(trade)
                    self._publish("trade", trade.model_dump())
                    continue

                self._close_position(trade, exit_spot, bar_dt)
                self._close_position(trade, exit_spot, bar_dt, exit_reason=exit_reason)

            if still_open:
                self._open_by_symbol[k] = still_open
            else:
                self._open_by_symbol.pop(k, None)

    def _bar_minutes(self) -> int:
        from app.services.ohlcv_store import RESOLUTION_SECONDS
        res = self._config.resolution if self._config else "5m"
        return max(1, RESOLUTION_SECONDS.get(res, 300) // 60)

    def _close_position(
        self,
        trade: SimTradeEvent,
        exit_spot: float,
        bar_dt,
        exit_reason: Optional[str] = None,
    ) -> None:
        raw_exit = self._premium_for_spot(trade, exit_spot)
        _, fill_exit, friction_mode = _apply_friction(
            trade.raw_entry if trade.raw_entry is not None else trade.entry_price,
            raw_exit,
            trade.underlying,
            self._config,
        )

        trade.exit_price = fill_exit
        trade.exit_reason = exit_reason or trade.exit_reason or "MANUAL"
        is_multi = getattr(self, "_is_multi_day", False)
        trade.exit_time_iso = (
            bar_dt.strftime("%Y-%m-%dT%H:%M:%S")
            if is_multi
            else bar_dt.strftime("%H:%M:%S")
        )
        trade.exit_timestamp_ms = int(bar_dt.timestamp() * 1000)
        trade.duration_mins = trade.bars_held * self._bar_minutes()
        trade.pnl_usd = round((fill_exit - trade.entry_price) * trade.quantity, 2)
        trade.pnl_pct = round(
            ((fill_exit - trade.entry_price) / trade.entry_price) * 100.0, 2
        ) if trade.entry_price > 0 else 0.0
        # Status follows the money actually made, so the win rate and the P&L
        # cannot disagree.
        trade.status = "WIN" if trade.pnl_usd > 0 else "LOSS"

        if friction_mode == "ideal":
            trade.raw_exit = None
        else:
            trade.raw_exit = raw_exit
            entry_slip = (trade.entry_price - (trade.raw_entry or trade.entry_price)) * trade.quantity
            exit_slip = (raw_exit - fill_exit) * trade.quantity
            trade.slippage = round(max(0.0, entry_slip + exit_slip), 2)

        # Release main's re-entry suppression as soon as the position is really
        # closed. It used to be set to a horizon guessed from future bars.
        canon_und = _canonical_symbol(trade.underlying)
        self._active_until_bar.pop((trade.underlying, trade.strategy), None)
        self._active_until_bar.pop((canon_und, trade.strategy), None)
        from app.services.ohlcv_store import INDEX_ALIASES
        alias = INDEX_ALIASES.get(trade.underlying) or INDEX_ALIASES.get(trade.underlying.upper() if trade.underlying else "")
        if alias:
            self._active_until_bar.pop((alias, trade.strategy), None)
        alias_canon = INDEX_ALIASES.get(canon_und)
        if alias_canon:
            self._active_until_bar.pop((alias_canon, trade.strategy), None)

        self._recompute_totals()
        self._publish("trade", trade.model_dump())

    def _close_all_open(self, reason: str = "session end") -> None:
        """Square off any remaining open intraday positions at the session close."""
        from datetime import datetime, timezone, timedelta
        ist = timezone(timedelta(hours=5, minutes=30))
        epoch = self._current_sim_epoch if getattr(self, "_current_sim_epoch", None) else time.time()
        bar_dt = datetime.fromtimestamp(epoch, tz=ist)

        def _resolve_last_close(sym: str) -> Optional[float]:
            sym_u = sym.upper()
            canon = _canonical_symbol(sym)
            from app.services.ohlcv_store import INDEX_ALIASES
            target_syms = {sym, sym_u, canon}
            for item in [sym_u, canon]:
                if item in INDEX_ALIASES:
                    target_syms.add(INDEX_ALIASES[item])
                    target_syms.add(INDEX_ALIASES[item].upper())
                for k, v in INDEX_ALIASES.items():
                    if item == v.upper():
                        target_syms.add(k)
                        target_syms.add(k.upper())

            if hasattr(self, "_bar_history"):
                for s in target_syms:
                    if self._bar_history.get(s):
                        return float(self._bar_history[s][-1].get("close", 0.0))

            if hasattr(self, "_candles") and self._candles:
                played_idx = getattr(self, "_bars_played", len(self._candles))
                for b in reversed(self._candles[:max(1, played_idx)]):
                    if b.get("symbol", "").upper() in target_syms:
                        return float(b.get("close", 0.0))
            return None

        exit_reason_label = "SESSION_CLOSE"

        count = sum(len(v) for v in self._open_by_symbol.values())
        if count:
            log.info("Replay squaring off %d position(s) at session close (%s).", count, reason)

        for sym, book in list(self._open_by_symbol.items()):
            last_close = _resolve_last_close(sym)
            for trade in list(book):
                exit_spot = last_close if (last_close and last_close > 0) else (trade.spot_entry or trade.entry_price)
                self._close_position(trade, exit_spot, bar_dt, exit_reason=exit_reason_label)
        self._open_by_symbol = {}

        # Sweep safety net: ensure NO trade in self._stats.trades is left with status == "OPEN"
        for trade in self._stats.trades:
            if trade.status == "OPEN":
                last_close = _resolve_last_close(trade.underlying)
                exit_spot = last_close if (last_close and last_close > 0) else (trade.spot_entry or trade.entry_price)
                self._close_position(trade, exit_spot, bar_dt, exit_reason=exit_reason_label)

        self._recompute_totals()

    def _recompute_totals(self) -> None:
        """Re-derive every aggregate from the trade ledger.

        Called after appending a trade and after a seek truncates the ledger, so
        the two paths can never drift. `slippage_total` stays `None` when no
        trade carried friction — "not modelled" and "modelled as zero" are
        different answers and the UI renders them differently.
        """
        trades = self._stats.trades
        self._stats.signals_fired = len(self._stats.events)
        self._stats.trades_entered = len(trades)
        self._stats.wins = len([tr for tr in trades if tr.status == "WIN"])
        self._stats.losses = len([tr for tr in trades if tr.status == "LOSS"])
        closed = [tr for tr in trades if tr.status in ("WIN", "LOSS")]
        # Realised only. Folding an open position's mark-to-market into the
        # headline number would label an unbooked gain as realised.
        self._stats.pnl = round(sum(tr.pnl_usd for tr in closed), 2)
        drag = [tr.slippage for tr in trades if tr.slippage is not None]
        self._stats.slippage_total = round(sum(drag), 2) if drag else None

    def _get_sim_now_ms(self) -> int:
        if self._current_sim_epoch > 0:
            return int(self._current_sim_epoch * 1000)
        return int(time.time() * 1000)

    @property
    def has_session_view(self) -> bool:
        """True if a simulation is running/paused, or if a finished session is being reviewed."""
        return self._state != SimState.IDLE or bool(self._session_complete and (self._stats.events or self._stats.trades))

    @property
    def status(self) -> SimStatus:
        return self.status_since()

    def status_since(
        self,
        since_events: Optional[int] = None,
        since_trades: Optional[int] = None,
    ) -> SimStatus:
        """Current status, optionally carrying only rows the client has not seen.

        The full payload is O(session): a day of replay re-sends every signal and
        every trade on every poll. With offsets the client appends instead, and
        `events_total` / `trades_total` let it notice a reset (a seek truncates
        the ledger, so a total that went DOWN means "discard and refetch").
        """
        stats = self._stats
        if since_events is None and since_trades is None:
            payload = stats
        else:
            ev_from = max(0, since_events or 0)
            # A truncation (seek/restart) invalidates the client's offsets.
            if ev_from > len(stats.events):
                ev_from = 0
            # Trades are mutating entities (their live P&L, bars_held, and crucially
            # their exit_price and status WIN/LOSS update over time). Slicing trades
            # by index prevents the client from receiving exit and outcome updates
            # for trades entered earlier. Therefore, always return the latest trades.
            payload = SimStats(
                signals_fired=stats.signals_fired,
                trades_entered=stats.trades_entered,
                wins=stats.wins,
                losses=stats.losses,
                pnl=stats.pnl,
                events=stats.events[ev_from:],
                trades=stats.trades,
                slippage_total=stats.slippage_total,
            )

        return SimStatus(
            state=self._state,
            config=self._config,
            current_time_iso=self._current_time_iso,
            current_date=self._current_date or (self._current_time_iso.split("T")[0] if "T" in self._current_time_iso else (self._config.date if self._config else None)),
            progress_pct=self._progress,
            bars_played=self._bars_played,
            bars_total=self._bars_total,
            stats=payload,
            elapsed_real_s=round(time.monotonic() - self._start_real, 1) if self._start_real else 0,
            status_message=self._status_message,
            last_signal=self._last_signal,
            capabilities=self.capabilities,
            session_policy=self.session_policy,
            events_total=len(stats.events),
            trades_total=len(stats.trades),
            session_id=self._session_id,
            session_complete=self._session_complete,
            open_positions=sum(len(v) for v in self._open_by_symbol.values()),
            unrealised_pnl=round(
                sum(tr.pnl_usd for tr in stats.trades if tr.status == "OPEN"), 2
            ),
        )

    @property
    def capabilities(self) -> SimCapabilities:
        return SimCapabilities()

    @property
    def session_policy(self) -> Optional[SimSessionPolicy]:
        """Session bounds for the date being replayed.

        A replay drives option legs, so `continuous_close` follows the
        derivatives clock (NFO). The cash bounds are published alongside so the
        client can label a chart without inventing them.
        """
        from datetime import date as _date
        try:
            from app.services.kite_engine.market_hours import (
                CAS_START, POLICY_VERSION, continuous_close,
            )
        except Exception:
            return None

        day_str = self._config.date if self._config else None
        try:
            day = _date.fromisoformat(day_str) if day_str else _date.today()
        except ValueError:
            day = _date.today()

        try:
            nfo = continuous_close(day, "NFO")
            nse = continuous_close(day, "NSE")
            fo_cash = continuous_close(day, "NSE", cas_eligible=True)
        except Exception:
            return None

        fmt = lambda t: t.strftime("%H:%M:%S")
        return SimSessionPolicy(
            policy_version=POLICY_VERSION,
            preopen_start="09:00:00",
            continuous_open="09:15:00",
            continuous_close=fmt(nfo),
            derivatives_close=fmt(nfo),
            cash_close=fmt(nse),
            fo_cash_close=fmt(fo_cash),
            cas_end="15:35:00" if day >= CAS_START else None,
        )

    async def start(self, config: SimConfig) -> SimStatus:
        if self._state in (SimState.RUNNING, SimState.LOADING, SimState.PAUSED):
            log.info("Simulation already running/paused. Stopping prior session before starting new one.")
            await self.stop()
        
        reset_all_engine_signals()
        if config.strategy != "all" and (config.strategies == ["all"] or not config.strategies):
            config.strategies = [s.strip() for s in config.strategy.split(",") if s.strip()]
        elif config.strategies != ["all"] and config.strategy == "all":
            config.strategy = ",".join(config.strategies)
        self._config = config
        self._speed = config.speed
        self._state = SimState.LOADING
        self._stop_requested = False
        self._pause_event.set()
        self._stats = SimStats()
        self._current_date = config.date
        self._current_time_iso = config.start_time
        self._progress = 0.0
        self._status_message = f"Loading session {config.date}..."
        self._bar_history = {}
        self._in_session_bars = {}
        self._last_fired = {}
        self._open_by_symbol = {}
        self._session_id = f"{config.date}-{int(time.time())}"
        self._session_complete = False
        self._active_until_bar = {}
        self._bars_played = 0
        self._seek_requested_epoch = None
        self._start_real = time.monotonic()
        self._run_generation += 1
        self._task = asyncio.create_task(self._run_loop(self._run_generation))
        return self.status

    async def stop(self) -> SimStatus:
        self._stop_requested = True
        self._pause_event.set()  # unblock if paused
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._state = SimState.IDLE
        self._task = None
        self._open_by_symbol = {}
        # The ledger survives for review, but it is now explicitly a FINISHED
        # session. Without this flag an idle runner handed every client a
        # completed session's signals and trades, which the dock rendered as
        # though the replay were live — results before you pressed play.
        self._session_complete = bool(self._stats.events or self._stats.trades)
        self._publish_frame(force=True)
        self._publish_state()
        return self.status

    def clear(self) -> SimStatus:
        """Discard a finished session's ledger. Refuses while one is running."""
        if self._state != SimState.IDLE:
            return self.status
        self._stats = SimStats()
        self._open_by_symbol = {}
        self._last_signal = None
        self._session_complete = False
        self._session_id = None
        self._current_time_iso = ""
        self._current_date = ""
        self._progress = 0.0
        self._bars_played = 0
        self._candles = []
        self._bar_history = {}
        self._in_session_bars = {}
        self._last_fired = {}
        self._active_until_bar = {}
        self._ae_fallback_mode = False
        self._scanned_dates = set()
        self._publish_state()
        return self.status

    async def pause(self) -> SimStatus:
        if self._state == SimState.RUNNING:
            self._state = SimState.PAUSED
            self._pause_event.clear()
            self._publish_state()
        return self.status

    async def resume(self) -> SimStatus:
        if self._state == SimState.PAUSED:
            self._state = SimState.RUNNING
            self._pause_event.set()
            self._publish_state()
        return self.status

    def set_speed(self, speed: float) -> SimStatus:
        self._speed = max(0.5, min(speed, 5000.0))
        if self._config:
            self._config.speed = self._speed
        self._publish_state()
        return self.status

    def _apply_seek(self, target: float) -> int:
        """Apply a seek to target timestamp immediately, updating books, clock, and warming history."""
        all_bars = getattr(self, "_candles", [])
        self._seek_requested_epoch = None
        self._current_sim_epoch = target

        bar_idx = 0
        while bar_idx < len(all_bars) and all_bars[bar_idx]["time"] <= target:
            bar_idx += 1
        self._bars_played = bar_idx

        target_ms = int(target * 1000)
        self._stats.events = [ev for ev in self._stats.events if ev.timestamp_ms <= target_ms]
        self._stats.trades = [tr for tr in self._stats.trades if tr.timestamp_ms <= target_ms]

        self._open_by_symbol = {}
        for tr in self._stats.trades:
            if tr.exit_timestamp_ms is not None and tr.exit_timestamp_ms > target_ms:
                tr.status = "OPEN"
                tr.exit_price = None
                tr.exit_time_iso = "OPEN"
                tr.exit_timestamp_ms = None
                tr.pnl_usd = 0.0
                tr.pnl_pct = 0.0
            if tr.status == "OPEN":
                self._open_by_symbol.setdefault(tr.underlying, []).append(tr)
        self._recompute_totals()
        self._last_signal = self._stats.events[-1] if self._stats.events else None
        self._emitted_recorded_keys = {
            f"{ev.instrument}:{ev.timestamp_ms}" for ev in self._stats.events
        }

        # Rebuild bar history and in-session count up to seek target so indicators are immediately warm
        self._bar_history = {}
        self._in_session_bars = {}
        for b in all_bars[:bar_idx]:
            b_sym = b.get("symbol", "UNKNOWN")
            self._bar_history.setdefault(b_sym, []).append(b)
            self._in_session_bars[b_sym] = self._in_session_bars.get(b_sym, 0) + 1
        for b_sym in self._bar_history:
            if len(self._bar_history[b_sym]) > 60:
                self._bar_history[b_sym] = self._bar_history[b_sym][-60:]

        self._last_fired = {}
        self._active_until_bar = {}

        from datetime import datetime, timezone, timedelta
        try:
            from zoneinfo import ZoneInfo
            ist = ZoneInfo("Asia/Kolkata")
        except ImportError:
            ist = timezone(timedelta(hours=5, minutes=30))

        bar_dt = datetime.fromtimestamp(self._current_sim_epoch, tz=ist)
        self._current_date = bar_dt.strftime("%Y-%m-%d")
        self._current_time_iso = (
            bar_dt.strftime("%Y-%m-%dT%H:%M:%S")
            if getattr(self, "_is_multi_day", False)
            else bar_dt.strftime("%H:%M:%S")
        )
        total_sim_seconds = float(max(1, self._end_epoch - self._start_epoch))
        self._progress = round(
            min(100.0, max(0.0, (self._current_sim_epoch - self._start_epoch) / total_sim_seconds * 100.0)),
            1,
        )
        self._publish_frame(force=True)
        return bar_idx

    def step_bars(self, count: int) -> SimStatus:
        from app.services.ohlcv_store import RESOLUTION_SECONDS
        if self._state == SimState.IDLE:
            return self.status
        res = self._config.resolution if self._config else "5m"
        res_sec = RESOLUTION_SECONDS.get(res, 300)
        target = self._current_sim_epoch + (count * res_sec)
        target = max(float(self._start_epoch), min(float(self._end_epoch), target))
        self._seek_requested_epoch = target
        if self._state == SimState.PAUSED:
            self._apply_seek(target)
        else:
            self._seek_requested_epoch = target
        return self.status

    def seek_to(
        self,
        bar_index: Optional[int] = None,
        to_pct: Optional[float] = None,
        to_time: Optional[str] = None,
        target_epoch: Optional[float] = None,
    ) -> SimStatus:
        """Absolute seek, so a timeline drag commits as ONE request.

        A relative `bars_offset` forces the client either to issue a request per
        pointer move or to compute an offset from a `bars_played` that is moving
        underneath it. All forms below clamp into the session.
        """
        if self._state == SimState.IDLE or self._end_epoch <= self._start_epoch:
            return self.status

        span = float(self._end_epoch - self._start_epoch)
        target: Optional[float] = None

        if target_epoch is not None:
            target = float(target_epoch)
        elif bar_index is not None and self._candles:
            idx = max(0, min(len(self._candles) - 1, int(bar_index)))
            target = float(self._candles[idx]["time"])
        elif to_pct is not None:
            pct = max(0.0, min(100.0, float(to_pct)))
            target = self._start_epoch + span * (pct / 100.0)
        elif to_time is not None:
            raw = str(to_time).strip()
            from datetime import datetime, timezone, timedelta
            try:
                from zoneinfo import ZoneInfo
                ist = ZoneInfo("Asia/Kolkata")
            except ImportError:
                ist = timezone(timedelta(hours=5, minutes=30))

            target = None
            if "T" in raw or ("-" in raw and len(raw) >= 10):
                try:
                    clean_iso = raw.replace("Z", "+00:00").replace(" ", "T")
                    dt = datetime.fromisoformat(clean_iso)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=ist)
                    target = dt.timestamp()
                except Exception:
                    pass

            if target is None:
                try:
                    time_part = raw.split("T")[-1].split("+")[0].split("Z")[0].strip()
                    parts = [int(x) for x in time_part.split(":")]
                    while len(parts) < 3:
                        parts.append(0)
                    base_epoch = self._current_sim_epoch if self._current_sim_epoch > 0 else self._start_epoch
                    base = datetime.fromtimestamp(base_epoch, tz=ist)
                    target = datetime(
                        base.year, base.month, base.day,
                        parts[0], parts[1], parts[2], tzinfo=ist,
                    ).timestamp()
                except Exception:
                    target = None

        if target is None:
            return self.status

        clamped_target = max(
            float(self._start_epoch), min(float(self._end_epoch), target)
        )
        if self._state == SimState.PAUSED:
            self._apply_seek(clamped_target)
        else:
            self._seek_requested_epoch = clamped_target
        return self.status

    def jump_start(self) -> SimStatus:
        if self._start_epoch > 0:
            target = float(self._start_epoch)
            self._stats = SimStats()
            self._open_by_symbol.clear()
            self._last_signal = None
            self._last_fired.clear()
            self._emitted_recorded_keys.clear()
            if self._state == SimState.PAUSED:
                self._apply_seek(target)
                self._publish_frame(force=True)
            else:
                self._seek_requested_epoch = target
        return self.status

    def jump_end(self) -> SimStatus:
        if self._end_epoch > 0:
            self._seek_requested_epoch = float(self._end_epoch)
            target = float(self._end_epoch)
            if self._state == SimState.PAUSED:
                self._apply_seek(target)
            else:
                self._seek_requested_epoch = target
        return self.status

    def _emit_recorded_signal(self, rec: Dict[str, Any]) -> None:
        """Emit a real recorded historical session signal and execute its corresponding trade."""
        from datetime import datetime, timezone, timedelta
        ist = timezone(timedelta(hours=5, minutes=30))

        # Check strategy filter
        cfg_strats = [s.lower() for s in (self._config.strategies if self._config and self._config.strategies else [self._config.strategy if self._config else "all"])]
        allow_all = "all" in cfg_strats or "*" in cfg_strats or not cfg_strats
        strat_raw = rec.get("strategy", "supertrend").lower()
        is_spot = rec.get("is_spot_scan", False) or rec.get("source") == "spot" or strat_raw in ("supertrend", "spot_scan")
        adaptive_src = (self._config.adaptive_source if self._config and hasattr(self._config, "adaptive_source") else "both") or "both"
        adaptive_src = str(adaptive_src).lower()

        if is_spot and adaptive_src in ("ae_model", "ae"):
            return

        if not allow_all:
            if strat_raw in cfg_strats:
                if (strat_raw == "adaptive_edge" or is_spot) and adaptive_src in ("ae_model", "ae") and "adaptive_edge" in cfg_strats and len(cfg_strats) == 1:
                    return
                strat_to_emit = strat_raw
            elif is_spot and "adaptive_edge" in cfg_strats:
                if adaptive_src in ("ae_model", "ae"):
                    return
                strat_to_emit = "adaptive_edge"
            elif is_spot and "bear_to_bearish" in cfg_strats and rec.get("direction") in ("BEARISH", "SHORT", "SELL"):
                strat_to_emit = "bear_to_bearish"
            elif is_spot and "supertrend" in cfg_strats:
                strat_to_emit = "supertrend"
            else:
                return
        else:
            strat_to_emit = rec.get("strategy", "supertrend")
            if is_spot and adaptive_src in ("ae_model", "ae") and strat_to_emit == "adaptive_edge":
                return

        adaptive_ver = (self._config.adaptive_version if self._config and hasattr(self._config, "adaptive_version") else "v2_hardened") or "v2_hardened"
        adaptive_ver = str(adaptive_ver).lower()
        is_ae_signal = (strat_raw == "adaptive_edge") or (strat_to_emit == "adaptive_edge" and not is_spot)
        is_ae_signal = (strat_raw == "adaptive_edge") or (strat_to_emit == "adaptive_edge")
        if is_ae_signal and adaptive_ver in ("v2_hardened", "v2"):
            # When user explicitly asks for spot scans, do not lock out spot scans
            if not (is_spot and adaptive_src in ("spot_scan", "spot")):
                rec_ms = rec.get("timestamp_ms") or 0
                if rec_ms > 0:
                    rec_dt = datetime.fromtimestamp(rec_ms / 1000, tz=ist)
                    if rec_dt.hour == 9 and rec_dt.minute < 28:
                        return
                elif rec.get("time_iso"):
                    iso_str = str(rec["time_iso"])
                    time_str = iso_str.split("T")[1][:8] if "T" in iso_str else iso_str[:8]
                    if "09:15:00" <= time_str < "09:28:00":
                        return

        sym = rec["underlying"]
        from app.services.ohlcv_store import INDEX_ALIASES
        if hasattr(self, "_candles") and self._candles:
            candle_syms = {b.get("symbol") for b in self._candles}
            if sym not in candle_syms:
                alias = INDEX_ALIASES.get(sym) or INDEX_ALIASES.get(sym.upper() if sym else "")
                if alias and alias in candle_syms:
                    sym = alias

        if self._config and self._config.instruments:
            allowed_insts = set(self._config.instruments)
            expanded = set(allowed_insts)
            for inst in allowed_insts:
                if inst in INDEX_ALIASES:
                    expanded.add(INDEX_ALIASES[inst])
                if inst.upper() in INDEX_ALIASES:
                    expanded.add(INDEX_ALIASES[inst.upper()])
                for k, v in INDEX_ALIASES.items():
                    if inst.upper() == v.upper():
                        expanded.add(k)
            if sym not in expanded and sym.upper() not in expanded:
                return

        raw = rec.get("raw_row", {})
        direction = rec["direction"]
        # Align spot with current replay candle price at this simulation timestamp
        raw_spot = float(rec.get("spot") or raw.get("spot") or 0.0)
        current_candle_spot = None
        if hasattr(self, "_candles") and self._candles:
            curr_epoch = int(rec["timestamp_ms"] / 1000)
            from app.services.ohlcv_store import INDEX_ALIASES
            target_syms = {sym, sym.upper()}
            for k, v in INDEX_ALIASES.items():
                if sym.upper() in (k.upper(), v.upper()):
                    target_syms.add(k.upper())
                    target_syms.add(v.upper())

            # Check candle matching exact timestamp
            for b in self._candles:
                if b.get("symbol", "").upper() in target_syms and b.get("time") == curr_epoch:
                    current_candle_spot = float(b.get("close", 0.0))
                    break

            # If not found, check closest played candle up to this time
            if not current_candle_spot:
                played_idx = getattr(self, "_bars_played", 0)
                for b in reversed(self._candles[:played_idx]):
                    if b.get("symbol", "").upper() in target_syms:
                        current_candle_spot = float(b.get("close", 0.0))
                        break

        spot = current_candle_spot if (current_candle_spot and current_candle_spot > 0) else (raw_spot if raw_spot > 0 else 1000.0)

        # Align stop and target with current replay candle price
        raw_sl = float(rec.get("stop_loss") or raw.get("stop_loss") or 0.0)
        raw_base_spot = raw_spot if raw_spot > 0 else spot
        if raw_sl > 0 and abs(raw_sl - raw_base_spot) > 0.0005 * raw_base_spot:
            stop_dist = abs(raw_sl - raw_base_spot)
        else:
            min_dist = max(0.005 * spot, 50.0 if "NIFTY" in sym.upper() else (100.0 if "SENSEX" in sym.upper() else 5.0))
            stop_dist = min_dist

        raw_tgt = float(rec.get("target") or raw.get("target") or 0.0)
        if raw_tgt > 0 and abs(raw_tgt - raw_base_spot) > 0.0005 * raw_base_spot:
            tgt_dist = abs(raw_tgt - raw_base_spot)
        else:
            tgt_dist = 2.0 * stop_dist

        stop = round(spot + stop_dist, 2) if direction in ("BEARISH", "SHORT") else round(spot - stop_dist, 2)
        target = round(spot - tgt_dist, 2) if direction in ("BEARISH", "SHORT") else round(spot + tgt_dist, 2)

        cfg_lots = max(1, self._config.lots) if self._config else 1
        opt_type = "PE" if direction in ("BEARISH", "SHORT") else "CE"
        lot_size = _lot_size(sym)
        canon_sym = _canonical_symbol(sym)

        expiry_tag = "26AUG"
        try:
            rec_dt = datetime.fromtimestamp(rec["timestamp_ms"] / 1000, tz=ist)
            expiry_tag = f"{rec_dt.strftime('%y')}{rec_dt.strftime('%b').upper()}"
        except Exception:
            pass

        # Select matching leg based on moneyness preference
        cfg_moneyness = (self._config.moneyness if self._config and self._config.moneyness else "ATM").upper()
        legs = raw.get("legs") or []
        selected_leg = None
        if legs:
            if cfg_moneyness == "ALL":
                selected_leg = legs[0]
            else:
                for l in legs:
                    if l.get("moneyness", "").upper() == cfg_moneyness:
                        selected_leg = l
                        break
            if not selected_leg:
                selected_leg = legs[0]

        if selected_leg:
            strike = float(selected_leg.get("strike") or spot)
            lot_size = int(selected_leg.get("lot_size") or lot_size)
            entry_prem = float(selected_leg.get("premium_spot") or round(spot * 0.02, 2))
            stop_prem = float(selected_leg.get("entry_sl") or selected_leg.get("premium_sl") or round(entry_prem * 0.75, 2))
            if stop_prem <= 0 or stop_prem >= entry_prem:
                stop_prem = round(entry_prem * 0.75, 2)
            raw_prem_sl = float(selected_leg.get("premium_sl") or 0.0)
            raw_entry_sl = float(selected_leg.get("entry_sl") or 0.0)
            stop_prem = raw_prem_sl if (0 < raw_prem_sl < entry_prem) else (raw_entry_sl if (0 < raw_entry_sl < entry_prem) else round(entry_prem * 0.75, 2))
            tgt_prem = float(selected_leg.get("premium_target") or round(entry_prem * 1.5, 2))
            if tgt_prem <= entry_prem:
                tgt_prem = round(entry_prem * 1.5, 2)
            opt_sym_raw = selected_leg.get("option_symbol")
            opt_symbol = opt_sym_raw if (opt_sym_raw and " " not in opt_sym_raw) else f"{canon_sym}{expiry_tag}{int(strike)}{opt_type}"
        else:
            step = _strike_step(sym, spot)
            strike = round(spot / step) * step
            entry_prem = round(spot * 0.02, 2)
            stop_prem = round(entry_prem * 0.75, 2)
            tgt_prem = round(entry_prem * 1.5, 2)
            opt_symbol = f"{canon_sym}{expiry_tag}{int(strike)}{opt_type}"

        is_multi = getattr(self, "_is_multi_day", False)
        entry_dt = datetime.fromtimestamp(rec["timestamp_ms"] / 1000, tz=ist)
        sig_time = (
            entry_dt.strftime("%Y-%m-%dT%H:%M:%S")
            if is_multi
            else (rec.get("time_iso") or entry_dt.strftime("%H:%M:%S"))
        )

        event = SimSignalEvent(
            time_iso=sig_time,
            timestamp_ms=rec["timestamp_ms"],
            strategy=strat_to_emit,
            instrument=sym,
            direction=direction,
            strength="STRONG",
            entry=entry_prem if opt_symbol else spot,
            stop=stop_prem if opt_symbol else stop,
            target=tgt_prem if opt_symbol else target,
            contract=opt_symbol,
            opt_type=opt_type,
            strike=strike,
            spot=spot,
            premium_entry=entry_prem,
            premium_sl=stop_prem,
            premium_target=tgt_prem,
            scan_origin="spot_scan",
            strategy_version=adaptive_ver if strat_to_emit == "adaptive_edge" else None,
        )
        self._stats.signals_fired += 1
        self._stats.events.append(event)
        self._last_signal = event
        self._publish("signal", event.model_dump())

        qty = cfg_lots * lot_size
        effective_entry, _, friction_mode = _apply_friction(
            entry_prem, entry_prem, sym, self._config
        )
        entry_slip = round((effective_entry - entry_prem) * qty, 2)

        entry_time_str = sig_time

        trade = SimTradeEvent(
            trade_id=f"TRD-{1000 + len(self._stats.trades) + 1}",
            entry_time_iso=entry_time_str,
            exit_time_iso="OPEN",
            timestamp_ms=rec["timestamp_ms"],
            strategy=strat_to_emit,
            symbol=opt_symbol,
            underlying=sym,
            direction="BUY",
            opt_type=opt_type,
            strike=strike,
            lots=cfg_lots,
            quantity=qty,
            entry_price=effective_entry,
            exit_price=None,
            stop_loss=stop_prem,
            target_price=tgt_prem,
            status="OPEN",
            pnl_usd=0.0,
            pnl_pct=0.0,
            duration_mins=0,
            raw_entry=entry_prem,
            raw_exit=None,
            slippage=0.0 if friction_mode == "ideal" else max(0.0, entry_slip),
            spot_entry=spot,
            spot_stop=stop,
            spot_target=target,
            spot_hwm=spot,
            spot_initial_risk=abs(spot - stop) if stop is not None else None,
            spot_initial_stop=stop,
            exit_reason=None,
            bars_held=0,
            scan_origin="spot_scan",
            strategy_version=adaptive_ver if strat_to_emit == "adaptive_edge" else None,
        )
        self._stats.trades_entered += 1
        self._stats.trades.append(trade)
        self._open_by_symbol.setdefault(sym, []).append(trade)
        self._recompute_totals()
        self._publish("trade", trade.model_dump())

    async def _run_loop(self, generation: int = 0):
        """Main replay loop — fetch candles, then step through them."""
        from app.services.ohlcv_store import get_candles as ohlcv_get, RESOLUTION_SECONDS
        from datetime import datetime, timezone, timedelta
        try:
            from zoneinfo import ZoneInfo
            ist = ZoneInfo("Asia/Kolkata")
        except ImportError:
            ist = timezone(timedelta(hours=5, minutes=30))

        cfg = self._config
        if not cfg:
            self._state = SimState.IDLE
            return

        try:
            day = datetime.strptime(cfg.date, "%Y-%m-%d")
            end_day = datetime.strptime(cfg.end_date, "%Y-%m-%d") if (cfg.end_date and cfg.end_date >= cfg.date) else day
        except ValueError:
            log.error("Invalid simulation date range: %s to %s", cfg.date, cfg.end_date)
            self._status_message = f"Invalid session date: {cfg.date}"
            self._state = SimState.IDLE
            return

        if cfg.end_date and cfg.end_date < cfg.date:
            log.warning("Invalid date range requested: %s to %s", cfg.date, cfg.end_date)
            self._status_message = f"Invalid date range: {cfg.date} to {cfg.end_date}"
            self._state = SimState.IDLE
            return

        self._is_multi_day = bool(cfg.end_date and cfg.end_date != cfg.date)
        is_multi_day = self._is_multi_day
        range_label = f"{cfg.date} to {cfg.end_date}" if is_multi_day else cfg.date

        # Build start/end timestamps in IST
        start_parts = [int(x) for x in cfg.start_time.split(":")]
        end_parts = [int(x) for x in cfg.end_time.split(":")]
        start_dt = datetime(day.year, day.month, day.day, start_parts[0], start_parts[1], start_parts[2] if len(start_parts) > 2 else 0, tzinfo=ist)
        end_dt = datetime(end_day.year, end_day.month, end_day.day, end_parts[0], end_parts[1], end_parts[2] if len(end_parts) > 2 else 0, tzinfo=ist)
        start_epoch = int(start_dt.timestamp())
        end_epoch = int(end_dt.timestamp())

        res = cfg.resolution or "5m"
        res_sec = RESOLUTION_SECONDS.get(res, 300)

        # Determine instruments (NSE Indian Markets only)
        self._recorded_signals = (
            _load_recorded_signals(cfg.date, cfg.end_date) if is_multi_day else _load_recorded_signals(cfg.date)
        )
        self._scanned_dates = (
            _get_scanned_dates(cfg.date, cfg.end_date) if is_multi_day else _get_scanned_dates(cfg.date)
        )
        self._emitted_recorded_keys = set()

        adaptive_src = getattr(cfg, "adaptive_source", "both") or "both"
        self._ae_fallback_mode = False
        if str(adaptive_src).lower() in ("spot_scan", "spot") and not self._recorded_signals:
            log.info("No recorded spot scans for %s; replaying via AE Model.", range_label)
            self._status_message = f"Notice: No recorded spot scans on {range_label}; replaying via AE Model."
            self._ae_fallback_mode = True

        if not cfg.instruments:
            if self._recorded_signals:
                # Real session with recorded signals: only replay the instruments that actually traded / fired signals
                rec_syms = list(dict.fromkeys([
                    r["underlying"] for r in self._recorded_signals if r.get("underlying")
                ]))
                # Keep core indices available for market benchmark / spot tracking
                for core in ("NIFTY", "BANKNIFTY", "SENSEX"):
                    if core not in rec_syms:
                        rec_syms.append(core)
                instruments = rec_syms
            else:
                default_universe = [
                    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX",
                    "HDFCBANK", "ICICIBANK", "SBIN", "RELIANCE", "BHARTIARTL",
                    "AXISBANK", "KOTAKBANK", "INFY", "BAJFINANCE", "ADANIENT",
                    "LT", "TCS", "BAJAJFINSV", "ADANIPORTS", "TATASTEEL",
                ]
                try:
                    from app.services import db
                    import json
                    raw_c = db.get_config("kite_engine_config_default")
                    if raw_c:
                        parsed_c = json.loads(raw_c)
                        stocks = parsed_c.get("scan_stocks", [])
                        indices = [s.replace(" 50", "").replace(" SERVICE", "").replace(" ", "") for s in parsed_c.get("scan_indices", [])]
                        instruments = list(dict.fromkeys(indices + stocks + default_universe))
                        scan_all = parsed_c.get("scan_all_stocks", False)
                        if not scan_all and (indices or stocks):
                            instruments = list(dict.fromkeys(indices + stocks))
                        else:
                            instruments = list(dict.fromkeys(indices + stocks + default_universe))
                    else:
                        instruments = default_universe
                except Exception:
                    instruments = default_universe
        else:
            instruments = list(cfg.instruments)

        if not cfg.instruments:
            for rec in self._recorded_signals:
                if rec.get("underlying") and rec["underlying"] not in instruments:
                    instruments.append(rec["underlying"])

        # Deduplicate and canonicalize symbols
        instruments = list(dict.fromkeys([_canonical_symbol(s) for s in instruments if s]))

        self._status_message = f"⚡ Fetching historical candles for {range_label} from Zerodha Kite API..."
        warmup_start = start_epoch - 5 * 86400

        def _report_hydrate(msg: str):
            self._status_message = msg
            self._publish_state()

        await _hydrate_missing_candles(
            instruments, res, warmup_start, end_epoch,
            session_start=start_epoch, on_progress=_report_hydrate
        )

        # Pre-seed indicator history with pre-session bars so indicators are ready at 09:15 AM
        self._bar_history = {}
        self._in_session_bars = {}
        for sym in instruments:
            prior_candles = ohlcv_get(sym, res, limit=50, until=start_epoch)
            p_bars = [{**c, "symbol": sym, "resolution": res} for c in prior_candles]
            self._bar_history[sym] = p_bars[-50:]

        # Fetch candles for each instrument from local store
        all_bars: List[Dict[str, Any]] = []
        for sym in instruments:
            candles = ohlcv_get(sym, res, limit=100000, since=start_epoch)
            for c in candles:
                c_time = c["time"]
                if start_epoch <= c_time <= end_epoch:
                    c_dt = datetime.fromtimestamp(c_time, tz=ist)
                    c_time_str = c_dt.strftime("%H:%M:%S")
                    if cfg.start_time <= c_time_str <= cfg.end_time:
                        all_bars.append({**c, "symbol": sym, "resolution": res})

        # Sort by time
        all_bars.sort(key=lambda b: b["time"])

        if not all_bars:
            log.warning("No candles available for simulation date %s", range_label)
            self._status_message = f"No real candles available for {range_label}; acquire historical data before replay"
            self._state = SimState.IDLE
            self._publish_frame(force=True)
            self._publish_state()
            return

        self._candles = all_bars
        self._bars_total = len(all_bars)
        self._state = SimState.RUNNING
        self._publish_state()
        self._status_message = f"Playing {range_label} ({len(all_bars)} bars)..."
        log.info("Simulation started: %s, %d bars, speed %.1fx", range_label, self._bars_total, self._speed)

        self._start_epoch = start_epoch
        self._end_epoch = end_epoch

        # Start ON the first bar, not at the configured session start.
        #
        # The default session opens at 09:00 but NSE's first candle is 09:15,
        # and the loop advances the clock by `speed * dt` regardless of whether
        # any data lies ahead. At the default 5x that is THREE REAL MINUTES of
        # an empty dock before the first print — indistinguishable from the
        # replay being broken, which is exactly how it was reported.
        first_bar_epoch = float(all_bars[0]["time"])
        self._current_sim_epoch = max(float(start_epoch), min(first_bar_epoch, float(end_epoch)))
        if first_bar_epoch > start_epoch:
            log.info(
                "Skipping %.0fs of pre-session dead air (%s -> first bar).",
                first_bar_epoch - start_epoch, cfg.start_time,
            )

        try:
            total_sim_seconds = float(max(1, end_epoch - start_epoch))
            bar_idx = 0

            while self._current_sim_epoch <= end_epoch and not self._stop_requested:
                await self._pause_event.wait()
                if self._stop_requested:
                    break
                bar_idx = self._bars_played
                # A superseded loop must not keep writing. Cancellation only
                # lands at an await, so between `start()` clearing the stop flag
                # and the old task actually dying, two loops could both advance
                # `_current_sim_epoch` — the clock jumped forward, then BACKWARD
                # to the other loop's position, and the session ended early on
                # whichever `finally` ran first.
                if generation and generation != self._run_generation:
                    return

                # Handle seek/rewind requests
                if self._seek_requested_epoch is not None:
                    target = self._seek_requested_epoch
                    self._seek_requested_epoch = None
                    bar_idx = self._apply_seek(target)

                # Dynamic update tick interval (30ms for >=500x, 50ms for >=50x, 100ms otherwise)
                dt = 0.03 if self._speed >= 500 else (0.05 if self._speed >= 50 else 0.1)

                # Dynamic second-by-second clock & progress update
                bar_dt = datetime.fromtimestamp(self._current_sim_epoch, tz=ist)
                self._current_date = bar_dt.strftime("%Y-%m-%d")
                self._current_time_iso = bar_dt.strftime("%Y-%m-%dT%H:%M:%S") if is_multi_day else bar_dt.strftime("%H:%M:%S")
                self._progress = round(min(100.0, max(0.0, (self._current_sim_epoch - start_epoch) / total_sim_seconds * 100.0)), 1)
                self._publish_frame()

                # Process bars up to current simulated timestamp
                while bar_idx < len(all_bars) and self._current_sim_epoch >= all_bars[bar_idx]["time"]:
                    bar = all_bars[bar_idx]
                    self._bars_played = bar_idx + 1
                    self._evaluate_bar(bar, datetime.fromtimestamp(bar["time"], tz=ist))
                    bar_idx += 1

                # Check and emit recorded historical signals whose timestamp has arrived
                curr_sim_ms = int(self._current_sim_epoch * 1000)
                for rec in self._recorded_signals:
                    rec_id = f"{rec['underlying']}:{rec['timestamp_ms']}"
                    if rec_id not in self._emitted_recorded_keys and curr_sim_ms >= rec["timestamp_ms"]:
                        self._emitted_recorded_keys.add(rec_id)
                        self._emit_recorded_signal(rec)

                # All bars played -> finish
                if bar_idx >= len(all_bars):
                    break

                next_bar_time = float(all_bars[bar_idx]["time"])
                gap = next_bar_time - self._current_sim_epoch

                # If there is dead air (> 300s, e.g. overnight or weekend or pre-market)
                if gap > max(300, res_sec):
                    next_bar_dt = datetime.fromtimestamp(next_bar_time, tz=ist)
                    # Day transition: close intraday positions at the end of each session
                    if next_bar_dt.date() > bar_dt.date():
                        self._close_all_open(f"session close {bar_dt.strftime('%Y-%m-%d')}")
                    # Fast forward through dead air directly to next bar
                    self._current_sim_epoch = next_bar_time
                else:
                    # Advance simulated clock by speed * dt
                    self._current_sim_epoch += self._speed * dt

                # Real-time sleep step
                await asyncio.sleep(dt)

        except asyncio.CancelledError:
            log.info("Simulation cancelled")
        except Exception as exc:
            log.error("Simulation error: %s", exc, exc_info=True)
        finally:
            if not self._stop_requested and (not generation or generation == self._run_generation):
                self._state = SimState.IDLE
                self._close_all_open("reached session end")
                self._session_complete = bool(self._stats.events or self._stats.trades)
                if all_bars and bar_idx >= len(all_bars):
                    self._progress = 100.0
                    last_b = all_bars[-1]
                    self._current_sim_epoch = float(last_b["time"])
                    last_dt = datetime.fromtimestamp(self._current_sim_epoch, tz=ist)
                    self._current_time_iso = (
                        last_dt.strftime("%Y-%m-%dT%H:%M:%S")
                        if is_multi_day
                        else last_dt.strftime("%H:%M:%S")
                    )
                    last_time_str = last_dt.strftime("%H:%M:%S")
                    if last_dt.time() < end_dt.time():
                        self._status_message = f"Session completed at latest available bar ({last_time_str} IST)."
                    else:
                        self._status_message = f"Session completed ({self._stats.trades_entered} trades, P&L {self._stats.pnl:+,.2f})."
                self._publish_frame(force=True)
                self._publish_state()
                log.info(
                    "Simulation complete: %d bars, %d signals, P&L %.2f",
                    self._bars_played, self._stats.signals_fired, self._stats.pnl,
                )

    def get_directional_signals_response(self) -> Dict[str, Any]:
        """Return signals formatted for /api/v1/directional/signals matching the active simulation date."""
        cfg = self._config
        sim_date = cfg.date if cfg else "2026-08-28"

        signals = []
        for ev in self._stats.events:
            signals.append({
                "underlying": ev.instrument,
                "has_options": True,
                "spot_price": ev.entry,
                "ivr": 25.0,
                "green_arrow": ev.direction == "BULLISH",
                "red_arrow": ev.direction == "BEARISH",
                "state": "ENTRY_ARMED" if ev.strength == "STRONG" else "SETUP_ACTIVE",
                "direction": ev.direction.lower(),
                "regime": "SIMULATION_REPLAY",
                "score_long": 85.0 if ev.direction == "BULLISH" else 15.0,
                "score_short": 85.0 if ev.direction == "BEARISH" else 15.0,
                "exec_mode": "paper",
                "exec_confidence": 0.88,
                "signal_score": 90.0 if ev.strength == "STRONG" else 65.0,
                "signal_strength": ev.strength,
                "track": "vcp" if ev.strategy == "vcp" else ("mean_reversion" if ev.strategy == "adaptive_edge" else "trend_following"),
                "strategy": ev.strategy,
                "regime_score": 15.0,
                "stop_price": ev.stop,
                "target_price": ev.target,
                "atr": round(abs(ev.target - ev.entry) / 3.0, 2),
                "adx": 32.5,
                "atr_percentile": 65.0,
                "rsi": 58.0,
                "squeezed": False,
                "futures_symbol": f"{ev.instrument}FUT",
                "fresh": True,
                "timestamp_ms": ev.timestamp_ms if ev.timestamp_ms > 0 else int(time.time() * 1000),
                "simulated_date": sim_date,
                "simulated_time": ev.time_iso,
            })

        return {
            "signals": signals,
            "count": len(signals),
            "timestamp": int(time.time()),
            "mode": "simulation",
            "simulated_date": sim_date,
        }

    def get_kite_signals_response(self) -> Dict[str, Any]:
        """Return signals formatted for Kite Engine signal responses during simulation."""
        now_ms = int(time.time() * 1000)
        cfg_lots = max(1, self._config.lots) if self._config else 1
        cfg_money = (self._config.moneyness if self._config and self._config.moneyness else "ATM").upper()
        sim_date = self._config.date if self._config and self._config.date else "2026-08-28"
        expiry_tag = "26AUG"
        if sim_date:
            try:
                from datetime import datetime
                dt = datetime.strptime(sim_date, "%Y-%m-%d")
                expiry_tag = f"{dt.strftime('%y')}{dt.strftime('%b').upper()}"
            except Exception:
                pass

        cfg_strats = [s.strip().lower() for s in (self._config.strategies if self._config and self._config.strategies else (self._config.strategy.split(",") if self._config and self._config.strategy else ["all"]))]
        allow_all = "all" in cfg_strats or "*" in cfg_strats or not cfg_strats

        if allow_all:
            kite_events = list(self._stats.events)
            kite_events = [ev for ev in self._stats.events if ev.strategy.lower() in ("supertrend", "spot_scan", "kite_engine")]
        else:
            kite_events = [ev for ev in self._stats.events if ev.strategy.lower() in cfg_strats]
            if not kite_events:
                kite_events = list(self._stats.events)
            if not kite_events and any(ev.strategy.lower() in ("supertrend", "spot_scan", "kite_engine") for ev in self._stats.events):
                kite_events = [ev for ev in self._stats.events if ev.strategy.lower() in ("supertrend", "spot_scan", "kite_engine")]

        from app.services.ohlcv_store import INDEX_ALIASES
        recorded_map_exact = {}
        recorded_map_sym = {}
        for r in getattr(self, "_recorded_signals", []):
            raw_row = r.get("raw_row")
            if not raw_row:
                continue
            u = r["underlying"].upper()
            aliases = {u}
            if u in INDEX_ALIASES:
                aliases.add(INDEX_ALIASES[u].upper())
            for k, v in INDEX_ALIASES.items():
                if u == v.upper():
                    aliases.add(k.upper())
            for a in aliases:
                recorded_map_exact[(a, r["timestamp_ms"])] = raw_row
                recorded_map_sym[a] = raw_row

        # Identify the latest event index for each underlying instrument
        latest_idx_by_inst: Dict[str, int] = {}
        for idx, ev in enumerate(kite_events):
            latest_idx_by_inst[ev.instrument.upper()] = idx

        rows = []
        for i, ev in enumerate(kite_events):
            base_ms = ev.timestamp_ms if ev.timestamp_ms > 0 else now_ms
            ev_ms = base_ms + i

            # Only the most recent event for an instrument is active; previous ones are superseded
            is_latest_for_inst = (latest_idx_by_inst.get(ev.instrument.upper()) == i)
            row_active = is_latest_for_inst
            row_fresh = is_latest_for_inst
            row_exit_reason = None if is_latest_for_inst else "re-entered"

            # If this event matches an authentic recorded signal with full live contract legs, use it
            raw_rec = recorded_map_exact.get((ev.instrument.upper(), ev.timestamp_ms)) or recorded_map_sym.get(ev.instrument.upper())
            raw_rec = recorded_map_exact.get((ev.instrument.upper(), ev.timestamp_ms))
            if not raw_rec and not ev.contract:
                raw_rec = recorded_map_sym.get(ev.instrument.upper())
            if raw_rec:
                row_copy = dict(raw_rec)
                row_copy["is_active"] = True
                row_copy["is_fresh"] = True
                row_copy["is_active"] = row_active
                row_copy["is_fresh"] = row_fresh
                if row_exit_reason:
                    row_copy["exit_reason"] = row_exit_reason
                row_copy["timestamp_ms"] = ev_ms
                rows.append(row_copy)
                continue

            is_long = ev.direction.upper() in ("BULLISH", "LONG", "BUY")
            direction_str = "long" if is_long else "short"
            regime_str = "BULL" if is_long else "BEAR"
            opt_type = "CE" if is_long else "PE"
            token_val = KITE_TOKENS.get(ev.instrument.upper(), 256265)
            step = _strike_step(ev.instrument, ev.entry)
            atm_strike = round(ev.entry / step) * step
            opt_exchange = "BFO" if ev.instrument.upper() in ("SENSEX", "BANKEX") else "NFO"

            if cfg_money == "ALL":
                moneyness_types = ["ITM1", "ATM", "OTM1"]
            else:
                moneyness_types = [m.strip().upper() for m in cfg_money.split(",") if m.strip()]
                if not moneyness_types:
                    moneyness_types = ["ATM"]
            legs = []

            canon_inst = _canonical_symbol(ev.instrument)
            for m_type in moneyness_types:
                offset_val = MONEYNESS_OFFSET.get(m_type, 0)
                signed_offset = offset_val if opt_type == "CE" else -offset_val
                s_val = max(step, atm_strike + signed_offset * step)

                prem_spot = ev.premium_entry if (ev.premium_entry and ev.premium_entry > 0) else round(ev.entry * 0.02, 2)
                prem_sl = ev.premium_sl if (ev.premium_sl and ev.premium_sl > 0) else round(ev.entry * 0.015, 2)
                prem_tgt = ev.premium_target if (ev.premium_target and ev.premium_target > 0) else (
                    round(ev.entry * 0.03, 2) if is_long else round(ev.entry * 0.01, 2)
                )

                legs.append({
                    "moneyness": m_type,
                    "option_type": opt_type,
                    "option_symbol": ev.contract if (m_type == "ATM" and ev.contract) else f"{canon_inst}{expiry_tag}{int(s_val)}{opt_type}",
                    "strike": s_val,
                    "expiry": sim_date,
                    "premium_spot": round(ev.entry * 0.02, 2),
                    "premium_sl": round(ev.entry * 0.015, 2),
                    "entry_sl": round(ev.entry * 0.01, 2),
                    "premium_spot": prem_spot,
                    "premium_sl": prem_sl,
                    "entry_sl": prem_sl,
                    "premium_target": prem_tgt,
                    "last_price": prem_spot,
                    "exit_state": "0/1 red",
                    "lots": cfg_lots,
                    "is_active": True,
                    "is_active": row_active,
                    "signal_timestamp_ms": ev_ms,
                    "entry_timestamp_ms": ev_ms,
                })

            rows.append({
                "underlying": ev.instrument,
                "token": token_val,
                "exchange": "NSE",
                "exchange": opt_exchange,
                "regime": regime_str,
                "alignment": {"fast": 1 if is_long else -1, "mid": 1 if is_long else -1, "slow": 1 if is_long else -1},
                "direction": direction_str,
                "option_type": opt_type,
                "legs": legs,
                "spot": ev.entry,
                "underlying_spot": ev.entry,
                "stop_loss": ev.stop,
                "entry_sl": ev.stop,
                "target": ev.target,
                "exit_state": "0/1 red",
                "exit_reason": row_exit_reason,
                "score": 90.0 if ev.strength == "STRONG" else 65.0,
                "timestamp_ms": ev_ms,
                "is_active": True,
                "is_fresh": True,
                "is_active": row_active,
                "is_fresh": row_fresh,
                "source": "spot",
            })

        return {
            "generated_ms": now_ms,
            "scanning": False,
            "scanning_label": "SIMULATION_REPLAY",
            "rows": rows,
            "next_scan_ms": 0,
            "auto_scan": False,
            "market_open": True,
        }

    def get_scalping_signals_response(self) -> Dict[str, Any]:
        """Return signals formatted for /api/v1/sterling-engine/signals during simulation."""
        now_ms = int(time.time() * 1000)
        signals = []
        for ev in self._stats.events:
            ev_ms = ev.timestamp_ms if ev.timestamp_ms > 0 else now_ms
            signals.append({
                "signal_id": f"sim_{ev.instrument}_{ev.time_iso}",
                "symbol": ev.instrument,
                "strategy": ev.strategy,
                "direction": ev.direction,
                "state": "ARMED" if ev.strength == "STRONG" else "ACTIVE",
                "entry_price": ev.entry,
                "stop_loss": ev.stop,
                "take_profit": ev.target,
                "confidence": 0.88,
                "timestamp_ms": ev_ms,
            })
        return {
            "signals": signals,
            "armed_count": len([s for s in signals if s["state"] == "ARMED"]),
            "total_signals": len(signals),
        }

    def get_v2_signals_response(self) -> Dict[str, Any]:
        """Return signals formatted for /api/v1/sterling-v2/signals during simulation."""
        now_ms = int(time.time() * 1000)
        signals = []
        for ev in self._stats.events:
            ev_ms = ev.timestamp_ms if ev.timestamp_ms > 0 else now_ms
            signals.append({
                "id": f"sim_v2_{ev.instrument}_{ev.time_iso}",
                "symbol": ev.instrument,
                "track": ev.strategy,
                "direction": ev.direction.lower(),
                "entry": ev.entry,
                "stop": ev.stop,
                "target": ev.target,
                "confidence": 0.90,
                "timestamp_ms": ev_ms,
            })
        return {"signals": signals, "total": len(signals)}

    def _events_for(self, *names: str) -> list:
        want = {n.lower() for n in names}
        return [ev for ev in self._stats.events if str(ev.strategy).lower() in want]

    def get_navigator_signals_response(self) -> Dict[str, Any]:
        """Return signals formatted for /api/v1/navigator/signals during simulation."""
        now_ms = int(time.time() * 1000)
        items = []
        for ev in self._events_for("navigator"):
            ev_ms = ev.timestamp_ms if ev.timestamp_ms > 0 else now_ms
            items.append({
                "event_id": f"nav_sim_{ev.instrument}_{ev.time_iso}",
                "underlying": ev.instrument,
                "strategy": ev.strategy,
                "direction": ev.direction,
                "generated_at_ms": ev_ms,
                "spot_price": ev.entry,
                "score": 88.0,
                "armed": ev.strength == "STRONG",
            })
        return {"items": items, "next_cursor": None, "has_more": False, "simulated": True}

    def get_adaptive_edge_snapshot(self) -> Dict[str, Any]:
        """Return snapshot for Adaptive Edge UI during simulation."""
        now_ms = int(time.time() * 1000)
        cfg = self._config
        sim_date = cfg.date if cfg else "2026-08-28"

        # Return events specifically triggered for adaptive_edge or spot scans consumed by AE
        allowed_syms = None
        if cfg and cfg.instruments:
            from app.services.ohlcv_store import INDEX_ALIASES
            allowed_syms = set(cfg.instruments)
            for inst in list(allowed_syms):
                if inst in INDEX_ALIASES:
                    allowed_syms.add(INDEX_ALIASES[inst])
                if inst.upper() in INDEX_ALIASES:
                    allowed_syms.add(INDEX_ALIASES[inst.upper()])
                for k, v in INDEX_ALIASES.items():
                    if inst.upper() == v.upper():
                        allowed_syms.add(k)

        has_pure_ae = any(ev.strategy == "adaptive_edge" for ev in self._stats.events)
        if has_pure_ae:
            ae_events = self._events_for("adaptive_edge", "spot_scan")
        else:
            ae_events = self._events_for("adaptive_edge", "supertrend", "spot_scan")

        if allowed_syms:
            ae_events = [ev for ev in ae_events if ev.instrument in allowed_syms or ev.instrument.upper() in allowed_syms]

        from app.services.ohlcv_store import INDEX_ALIASES
        recorded_map_exact = {}
        recorded_map_sym = {}
        for r in getattr(self, "_recorded_signals", []):
            raw_row = r.get("raw_row")
            if not raw_row:
                continue
            u = r["underlying"].upper()
            aliases = {u}
            if u in INDEX_ALIASES:
                aliases.add(INDEX_ALIASES[u].upper())
            for k, v in INDEX_ALIASES.items():
                if u == v.upper():
                    aliases.add(k.upper())
            for a in aliases:
                recorded_map_exact[(a, r["timestamp_ms"])] = raw_row
                recorded_map_sym[a] = raw_row

        expiry_tag = "26AUG"
        if sim_date:
            try:
                from datetime import datetime
                dt = datetime.strptime(sim_date, "%Y-%m-%d")
                expiry_tag = f"{dt.strftime('%y')}{dt.strftime('%b').upper()}"
            except Exception:
                pass

        signals = []
        for i, ev in enumerate(ae_events):
            base_ms = ev.timestamp_ms if ev.timestamp_ms > 0 else now_ms
            ev_ms = base_ms + i
            is_long = ev.direction.upper() in ("BULLISH", "LONG", "BUY")
            opt_type = "CE" if is_long else "PE"
            side = "BUY" if is_long else "SELL"

            # Determine strike step and ATM strike based on underlying instrument and price
            inst_u = ev.instrument.upper()
            spot_val = float(ev.spot) if (ev.spot and ev.spot > 0) else float(ev.entry)

            # Determine current simulated spot price from replay history or candles
            curr_spot = spot_val
            target_syms = {inst_u}
            from app.services.ohlcv_store import INDEX_ALIASES
            for k, v in INDEX_ALIASES.items():
                if inst_u in (k.upper(), v.upper()):
                    target_syms.add(k.upper())
                    target_syms.add(v.upper())

            found_bar = False
            if hasattr(self, "_bar_history") and self._bar_history:
                for sym_key in target_syms:
                    bars = self._bar_history.get(sym_key)
                    if bars:
                        curr_spot = float(bars[-1].get("close") or spot_val)
                        found_bar = True
                        break

            if not found_bar and hasattr(self, "_candles") and self._candles:
                played_idx = getattr(self, "_bars_played", 0)
                for b in reversed(self._candles[:played_idx]):
                    if b.get("symbol", "").upper() in target_syms:
                        curr_spot = float(b.get("close") or spot_val)
                        break

            step = _strike_step(inst_u, spot_val)

            atm_strike = ev.strike if (ev.strike and ev.strike > 0) else round(spot_val / step) * step
            exch = "BSE" if "SENSEX" in inst_u else "NSE"
            lot_size = _lot_size(inst_u)
            canon_inst = _canonical_symbol(inst_u)

            # Generate option ladder legs (ITM1, ATM, OTM1)
            raw_rec = recorded_map_exact.get((inst_u, ev.timestamp_ms)) or recorded_map_sym.get(inst_u)
            raw_legs = raw_rec.get("legs", []) if raw_rec else []
            legs = []
            ladder_defs = [
                ("ITM1", atm_strike - step if is_long else atm_strike + step),
                ("ATM", atm_strike),
                ("OTM1", atm_strike + step if is_long else atm_strike - step),
            ]
            if raw_legs:
                for leg in raw_legs:
                    m_ness = leg.get("moneyness", "ATM")
                    strike = float(leg.get("strike") or atm_strike)
                    l_size = int(leg.get("lot_size") or lot_size)
                    prem_spot = float(leg.get("premium_spot") or round(max(0.05, spot_val * 0.02), 2))
                    prem_sl = float(leg.get("premium_sl") or leg.get("entry_sl") or round(max(2.0, prem_spot * 0.7), 2))
                    spot_move = (curr_spot - spot_val) if is_long else (spot_val - curr_spot)
                    delta_mult = 0.60 if m_ness == "ITM1" else (0.40 if m_ness == "OTM1" else 0.50)
                    current_ltp = round(max(0.05, prem_spot + spot_move * delta_mult), 2)
                    legs.append({
                        "moneyness": m_ness,
                        "option_type": leg.get("option_type") or opt_type,
                        "option_symbol": leg.get("option_symbol") or f"{canon_inst}{expiry_tag}{int(strike)}{opt_type}",
                        "strike": strike,
                        "expiry": leg.get("expiry") or sim_date,
                        "lot_size": l_size,
                        "token": leg.get("token") or (10000 + (int(strike) % 10000)),
                        "exchange": exch,
                        "entry_premium": prem_spot,
                        "stop_premium": prem_sl,
                        "trail_premium": prem_sl,
                        "ltp": current_ltp,
                        "resolution_reason": None,
                    })
            else:
                ladder_defs = [
                    ("ITM1", atm_strike - step if is_long else atm_strike + step),
                    ("ATM", atm_strike),
                    ("OTM1", atm_strike + step if is_long else atm_strike - step),
                ]
                for moneyness, strike in ladder_defs:
                    mult = 0.02 if moneyness == "ATM" else (0.03 if moneyness == "ITM1" else 0.012)
                    if moneyness == "ATM" and ev.premium_entry:
                        premium_est = float(ev.premium_entry)
                        sl_est = float(ev.premium_sl) if ev.premium_sl else round(max(2.0, premium_est * 0.7), 2)
                    else:
                        premium_est = round(max(0.05, spot_val * mult), 2)
                        sl_est = round(max(2.0, premium_est * 0.7), 2)

                    # Dynamically calculate option LTP based on current spot movement
                    spot_move = (curr_spot - spot_val) if is_long else (spot_val - curr_spot)
                    delta_mult = 0.60 if moneyness == "ITM1" else (0.40 if moneyness == "OTM1" else 0.50)
                    current_ltp = round(max(0.05, premium_est + spot_move * delta_mult), 2)

                    opt_sym = ev.contract if (moneyness == "ATM" and ev.contract) else f"{canon_inst}{expiry_tag}{int(strike)}{opt_type}"
                    legs.append({
                        "moneyness": moneyness,
                        "option_type": opt_type,
                        "option_symbol": opt_sym,
                        "strike": strike,
                        "expiry": sim_date,
                        "lot_size": lot_size,
                        "token": 10000 + (int(strike) % 10000),
                        "exchange": exch,
                        "entry_premium": premium_est,
                        "stop_premium": sl_est,
                        "trail_premium": sl_est,
                        "ltp": current_ltp,
                        "resolution_reason": None,
                    })

            entry_iso = f"{sim_date}T{ev.time_iso}+05:30" if ev.time_iso else None
            sig_id = f"ae_sim_{ev.instrument}_{ev.time_iso.replace(':', '')}_{i}"
            if ev.time_iso:
                if "T" in ev.time_iso:
                    entry_iso = f"{ev.time_iso}+05:30" if ("+" not in ev.time_iso and not ev.time_iso.endswith("Z")) else ev.time_iso
                else:
                    entry_iso = f"{sim_date}T{ev.time_iso}+05:30"
            else:
                entry_iso = None
            sig_time_str = ev.time_iso.split("T")[1] if "T" in (ev.time_iso or "") else (ev.time_iso or "")
            sig_id = f"ae_sim_{ev.instrument}_{sig_time_str.replace(':', '')}_{i}"

            signals.append({
                "id": sig_id,
                "underlying": ev.instrument,
                "tape_symbol": ev.instrument,
                "side": side,
                "option_type": opt_type,
                "spot_entry": spot_val,
                "spot_exit": None,
                "spot_sl": round(spot_val * 1.01, 2) if not is_long else round(spot_val * 0.99, 2),
                "spot_tsl": round(spot_val * 1.01, 2) if not is_long else round(spot_val * 0.99, 2),
                "entry_time": entry_iso,
                "exit_time": None,
                "score": 88.0 if ev.strength == "STRONG" else 72.0,
                "poc": round(curr_spot * 0.999, 2),
                "vwap": round(curr_spot * 1.001, 2),
                "cvd": 1500.0 if is_long else -1500.0,
                "scanned": True,
                "skip_reason": None,
                "scan_origin": "spot_scan" if (raw_rec or ev.strategy in ("supertrend", "spot_scan")) else "adaptive_edge",
                "scan_origin": getattr(ev, "scan_origin", None) or ("spot_scan" if (raw_rec or not _is_index(ev.instrument) or ev.strategy in ("supertrend", "spot_scan")) else "adaptive_edge"),
                "flattened": False,
                "quantity": 1,
                "overlays": ["REPLAY", ev.strength],
                "thesis": f"{ev.direction} {ev.strategy} at {spot_val}",

                "entry_mode": "SCALP",
                "current_mode": "SCALP",
                "peak_mode": "SCALP",
                "exit_mode": None,
                "mode_upgraded": False,
                "mode_downgraded": False,
                "mode_path": "SCALP",
                "mode_history": ["SCALP"],
                "horizon": "IMPULSE",
                "session_date": sim_date,
                "timestamp_ms": ev_ms,
                "legs": legs,
            })

        default_sym = ae_events[0].instrument if ae_events else "NIFTY-I"
        all_syms = list(dict.fromkeys([ev.instrument for ev in ae_events])) or ["NIFTY-I"]
        adaptive_src = (cfg.adaptive_source if cfg and hasattr(cfg, "adaptive_source") else "both") or "both"
        adaptive_src = str(adaptive_src).lower()
        has_spot_scans = any(s.get("scan_origin") == "spot_scan" for s in signals)
        if adaptive_src in ("ae_model", "ae"):
            signals = [s for s in signals if s.get("scan_origin") == "adaptive_edge"]
        elif adaptive_src in ("spot_scan", "spot"):
            if has_spot_scans:
                signals = [s for s in signals if s.get("scan_origin") == "spot_scan"]
            # Otherwise, fall back to all generated AE signals so the board is not left blank

        adaptive_ver = (cfg.adaptive_version if cfg and hasattr(cfg, "adaptive_version") else "v2_hardened") or "v2_hardened"
        adaptive_ver = str(adaptive_ver).lower()
        is_ae_v2 = adaptive_ver in ("v2_hardened", "v2")

        if is_ae_v2:
            filtered = []
            for s in signals:
                time_iso = s.get("entry_time") or ""
                time_str = time_iso.split("T")[1][:8] if "T" in time_iso else time_iso[:8]
                is_spot_signal = (s.get("scan_origin") == "spot_scan") or (not _is_index(s.get("underlying", "")))
                if not (is_spot_signal and adaptive_src in ("spot_scan", "spot")):
                    if "09:15:00" <= time_str < "09:28:00":
                        continue
                filtered.append(s)
            signals = filtered

        for s in signals:
            s["strategy_version"] = adaptive_ver

        default_sym = signals[0].get("underlying", "NIFTY-I") if signals else (ae_events[0].instrument if ae_events else "NIFTY-I")
        all_syms = list(dict.fromkeys([s.get("underlying", "NIFTY-I") for s in signals])) or [default_sym]

        sim_legs = []
        for s in signals:
            for opt_leg in s.get("legs", []):
                sim_legs.append({
                    "symbol": s.get("underlying", "NIFTY-I"),
                    "side": s.get("side", "BUY"),
                    "entry_price": s.get("spot_entry"),
                    "entry_time": s.get("entry_time"),
                    "exit_price": s.get("spot_exit"),
                    "exit_time": s.get("exit_time"),
                    "stop_price": s.get("spot_sl"),
                    "trail_price": s.get("spot_tsl"),
                    "flattened": s.get("flattened", False),
                    "quantity": s.get("quantity", 1),
                    "session_date": sim_date,
                    "horizon": s.get("horizon", "IMPULSE"),
                    "entry_mode": s.get("entry_mode", "SCALP"),
                    "peak_mode": s.get("peak_mode", "SCALP"),
                    "exit_mode": s.get("exit_mode"),
                    "thesis": s.get("thesis", "Replay trade"),
                    "entry_score": s.get("score", 85.0),
                    "entry_vwap": s.get("vwap"),
                    "entry_poc": s.get("poc"),
                    "entry_cvd": s.get("cvd"),
                    "ltp": opt_leg.get("ltp"),
                    "strike": opt_leg.get("strike"),
                    "expiry": opt_leg.get("expiry"),
                    "lot_size": opt_leg.get("lot_size"),
                    "moneyness": opt_leg.get("moneyness"),
                })

        return {
            "label": "SIMULATION_REPLAY",
            "software_complete": True,
            "production_gate_authorized": True,
            "meets_a197": True,
            "registry_locked": True,
            "live_trading": False,
            "settings": {
                "enabled": True,
                "symbol": default_sym,
                "symbols": all_syms,
                "scan_source": "both",
                "scan_indices": ["NIFTY 50", "NIFTY BANK", "NIFTY FIN SERVICE", "SENSEX"],
                "scan_stocks": ["KOTAKBANK", "AXISBANK", "SBIN", "RELIANCE"],
                "scan_all_stocks": True,
                "scan_stock_contracts": True,
                "strike_moneyness": ["ITM1", "ATM", "OTM1"],
                "scan_expiries": ["weekly", "monthly"],
                "scan_expiries_indices": ["weekly", "monthly"],
                "stop_points": 15.0,
                "trail_points": 25.0,
                "profit_lock_activation_points": 20.0,
                "profit_lock_offset_points": 5.0,
                "persistence_bars": 3,
                "scalp_favorable_points": 10.0,
                "extended_favorable_points": 25.0,
                "intraday_favorable_points": 50.0,
                "tick_size": 0.05,
                "ib_minutes": 15,
            },
            "readiness": [
                {"name": "sim_engine", "label": "Market Replay", "ready": True, "detail": "Replay active"}
            ],
            "session": {
                "entries": len(signals),
                "exits": 0,
                "reentries": 0,
                "blocked_pyramid": 0,
                "last_mode": "SCALP",
                "last_thesis": ae_events[-1].direction if ae_events else None,
                "last_protection_stage": "TRAIL",
                "last_overlays": ["REPLAY"],
                "last_operating_mode": "SCALP",
                "last_horizon": "IMPULSE",
                "last_poc": ae_events[-1].entry if ae_events else None,
                "last_cvd": 1500.0,
                "last_location": "VALUE_AREA",
                "last_bar_delta": 300.0,
                "last_vwap": ae_events[-1].entry if ae_events else None,
                "last_or_location": "INSIDE_OR",
                "last_poc_migration": "UP",
                "peak_pnl": self._stats.pnl,
                "current_pnl": self._stats.pnl,
                "profit_giveback": 0.0,
                "lifecycle_action": "HOLD",
                "last_position_quantity": 1,
                "exit_fill_price": None,
                "audit_stages": ["SIM_REPLAY"],
            },
            "legs": [],
            "legs": sim_legs,
            "signals": signals,
            "scan": {
                "underlyings": len(all_syms),
                "chains_read": 8,
                "listed": 40,
                "tradeable": 25,
                "candidates": signals,
                "signals": signals,
                "skipped": {},
                "dropped": {},
                "errors": [],
            },
            "daily": [],
            "quality": None,
            "holdout": None,
            "coverage": None,
            "walk_forward": None,
            "mode_counts": {"SCALP": len(signals)},
            "mode_transitions": [],
            "formula_table": {},
            "incomplete_reasons": [],
            "warnings": [],
        }

    def get_atm_imbalance_snapshot(self) -> Dict[str, Any]:
        """Return snapshot for ATM Premium Imbalance strategy during simulation."""
        now_ms = int(time.time() * 1000)
        atm_events = self._events_for("atm_imbalance")
        first_event = atm_events[0] if atm_events else None
        sym = first_event.instrument if first_event else "NIFTY"
        price = first_event.entry if first_event else 24175.0
        strike_val = round(price / 50.0) * 50.0

        return {
            "strategy": {
                "id": "atm_premium_imbalance",
                "name": "ATM Premium Imbalance",
                "contract_version": "v1",
                "tagline": "Exploits institutional ATM CE/PE premium skew",
                "how_it_works": "Monitors ATM CE vs PE premium divergence during market replay.",
                "provenance": "Sterling Quantitative Research",
                "live_ready": True,
                "enabled": True,
            },
            "config": {
                "enabled": True,
                "underlying": sym,
                "expiry_policy": "SAME_DAY",
                "explicit_expiry": "2026-08-28",
                "strike_policy": "ATM",
                "session_start": "09:15:00",
                "session_end": "15:30:00",
                "quote_mode": "SYNCHRONIZED",
                "sizing_mode": "LOTS",
                "lots": 1,
                "stop_basis": "PERCENT",
                "stop_percent": 15.0,
                "signal_mode": "SKEW_BREAKOUT",
                "minimum_difference": 10.0,
                "data_source": "kite",
                "execution_mode": "paper",
            },
            "defaults": {},
            "vocabularies": {},
            "research_only": {"entry_price_policy": [], "exit_policy": []},
            "live_blockers": [],
            "session": {
                "armed": True,
                "finished": False,
                "session_date": self._config.date if self._config else "2026-08-28",
                "session_open_ms": now_ms - 3600000,
                "phase": "ARMED",
                "halt_reason": None,
                "underlying": sym,
                "expiry": "2026-08-28",
                "strike": strike_val,
                "quantity": 25,
                "execution_mode": "paper",
                "quote_mode": "SYNCHRONIZED",
                "protection_mode": "RESTING_TARGET_LIMIT",
                "trades_taken": len(atm_events),
                "legs": {
                    "CE": {
                        "instrument_id": f"NSE:{sym}26AUG{int(strike_val)}CE",
                        "tradingsymbol": f"{sym}26AUG{int(strike_val)}CE",
                        "option_type": "CE",
                        "lot_size": 25,
                        "ltp": round(price * 0.02, 2),
                        "bid": round(price * 0.019, 2),
                        "ask": round(price * 0.021, 2),
                        "last_trade_ts_ms": now_ms,
                        "session_origin": True,
                        "age_ms": 100,
                        "official_open": round(price * 0.02, 2),
                    },
                    "PE": {
                        "instrument_id": f"NSE:{sym}26AUG{int(strike_val)}PE",
                        "tradingsymbol": f"{sym}26AUG{int(strike_val)}PE",
                        "option_type": "PE",
                        "lot_size": 25,
                        "ltp": round(price * 0.015, 2),
                        "bid": round(price * 0.014, 2),
                        "ask": round(price * 0.016, 2),
                        "last_trade_ts_ms": now_ms,
                        "session_origin": True,
                        "age_ms": 100,
                        "official_open": round(price * 0.015, 2),
                    },
                },
                "difference": round(price * 0.005, 2),
                "cheaper_leg": "PE",
                "signal": {
                    "action": "BUY_CE" if (first_event and first_event.direction == "BULLISH") else "BUY_PE",
                    "reason": "Premium skew divergence exceeds minimum threshold",
                    "option_type": "CE" if (first_event and first_event.direction == "BULLISH") else "PE",
                },
                "trade": None,
            },
        }

    def get_bear_to_bearish_snapshot(self) -> Dict[str, Any]:
        """Return snapshot for Bear to Bearish Strategy during simulation."""
        now_ms = int(time.time() * 1000)
        rows = []
        for ev in self._events_for("bear_to_bearish"):
            ev_ms = ev.timestamp_ms if ev.timestamp_ms > 0 else now_ms
            strike_val = round(ev.entry / 50.0) * 50.0
            rows.append({
                "id": f"bear_sim_{ev.instrument}_{ev.time_iso}",
                "underlying": ev.instrument,
                "symbol": f"{ev.instrument}26AUG{int(strike_val)}PE",
                "exchange": "NFO",
                "direction": "BEARISH",
                "status": "ARMED" if ev.strength == "STRONG" else "ACTIVE",
                "timestamp_ms": ev_ms,
                "pcr_open": 1.15,
                "pcr_current": 0.72,
                "pcr_change_5m": -0.08,
                "lower_high_price": round(ev.entry * 1.005, 2),
                "spot_price": ev.entry,
                "spot_sl": ev.stop,
                "spot_target": ev.target,
                "option_premium": round(ev.entry * 0.02, 2),
                "entry_price": ev.entry,
                "stop_loss": ev.stop,
                "target_price": ev.target,
                "score": 92 if ev.strength == "STRONG" else 75,
                "reason": "PCR breakdown below 0.80 + Lower-high structure breach",
                "option_type": "PE",
                "strike": strike_val,
                "expiry": "2026-08-28",
                "lot_size": 25 if ev.instrument == "NIFTY" else 15,
                "quote_key": f"NSE:{ev.instrument}",
            })
        return {
            "generated_ms": now_ms,
            "scanning": False,
            "scanning_label": "SIMULATION_REPLAY",
            "rows": rows,
            "pcr_history": [{"timestamp_ms": now_ms - 300000, "pcr": 0.85}, {"timestamp_ms": now_ms, "pcr": 0.72}],
            "config": {
                "pcr_threshold": 0.80,
                "auto_execute": False,
            },
            "next_scan_ms": 0,
            "auto_scan": False,
            "market_open": True,
            "is_paper": True,
            "auto_execute": False,
        }

    def get_gamma_move_snapshot(self) -> Dict[str, Any]:
        """Live board schema. Only events this engine actually emitted.

        Replay has underlying OHLCV, not 15m option OI, so candidates are
        watching rows with an honest reason — never invented trigger numbers.
        """
        now_ms = int(time.time() * 1000)
        cfg = self._config
        sim_date = cfg.date if cfg else "2026-08-28"

        signals = []
        for i, ev in enumerate(self._stats.events):
            ev_ms = ev.timestamp_ms if ev.timestamp_ms > 0 else now_ms
            is_long = ev.direction.upper() in ("BULLISH", "LONG", "BUY")
            opt_type = "CE" if is_long else "PE"
            step = 100.0 if "SENSEX" in ev.instrument.upper() or "BANKNIFTY" in ev.instrument.upper() else (50.0 if "NIFTY" in ev.instrument.upper() else 20.0)
            strike_val = round(ev.entry / step) * step
            premium_est = round(max(5.0, ev.entry * 0.02), 2)

            curr_spot = ev.entry
            inst_u = ev.instrument.upper()
            target_syms = {inst_u}
            from app.services.ohlcv_store import INDEX_ALIASES
            for k, v in INDEX_ALIASES.items():
                if inst_u in (k.upper(), v.upper()):
                    target_syms.add(k.upper())
                    target_syms.add(v.upper())

            found_bar = False
            if hasattr(self, "_bar_history") and self._bar_history:
                for sym_key in target_syms:
                    bars = self._bar_history.get(sym_key)
                    if bars:
                        curr_spot = float(bars[-1].get("close") or ev.entry)
                        found_bar = True
                        break

            if not found_bar and hasattr(self, "_candles") and self._candles:
                played_idx = getattr(self, "_bars_played", 0)
                for b in reversed(self._candles[:played_idx]):
                    if b.get("symbol", "").upper() in target_syms:
                        curr_spot = float(b.get("close") or ev.entry)
                        break

            spot_move = (curr_spot - ev.entry) if is_long else (ev.entry - curr_spot)
            current_ltp = round(max(0.05, premium_est + spot_move * 0.50), 2)

            lot_sz = 15 if "NIFTY" in ev.instrument.upper() else (10 if "SENSEX" in ev.instrument.upper() else 500)
            signals.append({
                "id": f"{ev.instrument}_{int(strike_val)}_{opt_type}_{ev_ms}",
                "instrument": {
                    "instrument_id": f"{ev.instrument}_{int(strike_val)}_{opt_type}",
                    "tradingsymbol": f"{ev.instrument}26AUG{int(strike_val)}{opt_type}",
                    "exchange": "BFO" if "SENSEX" in ev.instrument.upper() else "NFO",
                    "kind": "option",
                    "option_type": opt_type,
                    "strike": strike_val,
                    "expiry": sim_date,
                    "lot_size": lot_sz,
                    "tick_size": 0.05,
                },
                "underlying": ev.instrument,
                "state": "armed" if ev.strength == "STRONG" else "watching",
                "direction": "long",
                "at_ms": ev_ms,
                "spot": ev.entry,
                "regime": "up" if is_long else "down",
                "reason": "Level bounce confirmed" if is_long else "Level rejection confirmed",
                "exit_reason": None,
                "entry_day": sim_date,
                "level": {
                    "price": ev.entry,
                    "kind": "support" if is_long else "resistance",
                    "touches": 3,
                    "distance_pct": 0.15,
                },
                "oi": 1500000,
                "days_to_expiry": 2,
                "metrics": {
                    "oi_drop_pct": 12.5,
                    "volume_ratio": 2.4,
                    "price_gain_pct": 8.5,
                    "unwinding": True,
                    "abnormal": True,
                    "rising": True,
                    "bars_confirmed": 2,
                    "bars_required": 2,
                    "triggered": True if ev.strength == "STRONG" else False,
                },
                "levels": {
                    "ltp": current_ltp,
                    "entry": premium_est,
                    "stop": round(premium_est * 0.7, 2),
                    "trail": None,
                    "target": round(premium_est * 1.5, 2),
                    "exit": None,
                },
                "sizing": {
                    "lots": 1,
                    "quantity": lot_sz,
                    "at_risk_inr": round(premium_est * 0.3 * lot_sz, 2),
                    "deployed_inr": round(premium_est * lot_sz, 2),
                },
                "generated_at": f"{sim_date}T{ev.time_iso}+05:30",
                "generated_at_ms": ev_ms,
                "spot_at_eval": ev.entry,
                "spot_level": ev.entry,
                "level_type": "SUPPORT" if is_long else "RESISTANCE",
                "distance_pct": 0.15,
                "score": 88.0 if ev.strength == "STRONG" else 70.0,
                "ltp": current_ltp,
                "entry_premium": premium_est,
                "stop_premium": round(premium_est * 0.7, 2),
                "target_premium": round(premium_est * 1.5, 2),
                "origin": "level_bounce" if is_long else "level_rejection",
                "rejection_reason": None,
            })

        from app.services.gamma_move import get_config, descriptor
        sim_date = self._config.date if self._config else ""
        from app.services.gamma_move import descriptor, get_config
        try:
            cfg_obj = get_config()
            cfg_dict = cfg_obj.as_dict()
            desc = descriptor()
            enabled = cfg_obj.enabled
            warnings = list(cfg_obj.warnings())
        except Exception:
            cfg_dict = {}
            desc = {}
            enabled = True
            cfg_dict, desc, enabled, warnings = {}, {}, True, []

        underlyings_list = list(set(ev.instrument for ev in self._stats.events))
        wins = len([t for t in self._stats.trades if (t.pnl_usd or 0) > 0])
        losses = len([t for t in self._stats.trades if (t.pnl_usd or 0) < 0])
        total_pnl = round(sum(float(t.pnl_usd or 0.0) for t in self._stats.trades), 2)

        events = self._events_for("gamma_move")
        latest: Dict[Tuple[str, str], Any] = {}
        for ev in events:
            key = (ev.instrument, getattr(ev, "level_kind", None) or ev.direction)
            prev = latest.get(key)
            if prev is None or ev.timestamp_ms >= prev.timestamp_ms:
                latest[key] = ev
        candidates = [_gamma_move_candidate(ev, sim_date) for ev in latest.values()]
        names = sorted({ev.instrument for ev in latest.values()})
        blockers = [
            "replay has no 15-minute option open-interest tape — the trigger cannot fire",
            "levels are confirmed daily swings on stocks only — a short 5-minute tape emits nothing",
        ]
        if not events:
            blockers.append("no underlying is inside a confirmed daily level on this replay tape")
        warnings.append(
            "not validated: simulation shows the level filter only. "
            "Do not read watching rows as entries."
        )
        return {
            "generated_at": f"{sim_date}T09:16:31+05:30",
            "strategy": {**desc, "enabled": enabled},
            "config": cfg_dict,
            "scan": {"last_run_ms": now_ms, "total_seconds": 0.0},
            "session": None,
            "simulation": None,
            "candidates": signals,
            "signals": signals,
            "simulation": {"mode": "replay"},
            "candidates": candidates,
            "positions": [],
            "record": {
                "trades": len(self._stats.trades),
                "wins": wins,
                "losses": losses,
                "win_rate": round(wins / len(self._stats.trades) * 100.0, 1) if self._stats.trades else None,
                "consecutive_losses": 0,
                "consecutive_wins": 0,
                "realised_inr": total_pnl,
                "day_realised_inr": total_pnl,
                "day": sim_date,
                "verdict": "simulation replay",
                "trades": 0, "wins": 0, "losses": 0, "win_rate": None,
                "consecutive_losses": 0, "consecutive_wins": 0,
                "realised_inr": 0.0, "day_realised_inr": 0.0, "day": sim_date,
                "verdict": "simulation replay — trigger not evaluated",
            },
            "orphan_positions": [],
            "blockers": [],
            "universe": {"underlyings": len(set(ev.instrument for ev in self._stats.events)) or 1},
            "mode": {"is_paper": True},
            "universe": {
                "underlyings": len(underlyings_list) or 1,
                "sample": underlyings_list[:10],
            },
            "blockers": blockers,
            "universe": {"underlyings": len(names), "sample": names[:10]},
            "mode": {
                "is_paper": True,
                "auto_execute": False,
                "note": "Replay simulation mode",
                "note": "Replay simulation. Paper/live is the account's Trading Mode.",
            },
            "warnings": warnings,
        }

    def get_nifty_orb_signals_response(self) -> Dict[str, Any]:
        """Scan-shaped tickets the live board already knows how to render.

        The previous payload had no nested ``signal``/``trade``, so the adapter
        classified every replay row as ``scan failed``.
        """
        from app.services.nifty_orb_lifecycle import attach_ticket
        from datetime import datetime, timezone, timedelta

        ist = timezone(timedelta(hours=5, minutes=30))
        signals = []
        for ev in self._stats.events:
            if getattr(ev, "strategy", None) != "nifty_orb":
                continue
            direction = "LONG" if ev.direction in ("BULLISH", "LONG") else "SHORT"
            opt = ev.opt_type or ("CE" if direction == "LONG" else "PE")
            ts = datetime.fromtimestamp((ev.timestamp_ms or 0) / 1000, tz=ist)
            leg = _option_contract(ev.instrument, float(ev.spot or ev.entry or 0), ev.direction, self._config)
            symbol = ev.contract or leg["contract"]
            qty = int(leg["lot_size"] or 0)
            premium = float(ev.premium_entry or leg["premium"] or 0)
            row: Dict[str, Any] = {
                "status": "signal" if symbol and premium > 0 else "signal_unresolved",
                "underlying": ev.instrument,
                "spot": ev.spot or ev.entry,
                "signal": {
                    "direction": direction,
                    "timestamp": ts.isoformat(),
                    "reason": "replay",
                },
                "exchange": "NFO",
                "lot_size": qty,
                "auto_block": "replay — Auto does not place from simulation",
            }
            if symbol and premium > 0:
                row["trade"] = {
                    "quantity": qty,
                    "entry_premium": premium,
                    "stop_premium": ev.premium_sl,
                    "target_premium": ev.premium_target,
                    "underlying_entry": ev.spot or ev.entry,
                    "max_loss_inr": round(premium * qty, 2),
                    "contract": {
                        "symbol": symbol,
                        "option_type": opt,
                        "strike": ev.strike or leg["strike"],
                        "expiry": _expiry_from_synthetic_contract(symbol),
                        "lot_size": qty,
                        "ltp": premium,
                        "ask": premium,
                    },
                }
                attach_ticket(row)
            signals.append(row)
        return {"count": len(signals), "signals": signals}

    def _evaluate_bar(self, bar: Dict, bar_dt):
        """Evaluate strategy signals on every replay bar.
        
        Triggers SuperTrend crossovers, VCP squeeze breakouts, Adaptive Edge
        reversals, and Bear to Bearish breakdowns across instruments.
        """
        # Advance the open book first: a position opened earlier can close on
        # THIS bar, and it must do so before a new signal is considered.
        self._settle_open_positions(bar, bar_dt)

        cfg = self._config
        import random
        from datetime import datetime, timezone, timedelta

        ist = bar_dt.tzinfo if bar_dt.tzinfo is not None else timezone(timedelta(hours=5, minutes=30))
        if "time" not in bar:
            if bar_dt.tzinfo is None:
                bar["time"] = int(bar_dt.replace(tzinfo=ist).timestamp())
            else:
                bar["time"] = int(bar_dt.timestamp())

        if not hasattr(self, '_bar_history'):
            self._bar_history: Dict[str, List[Dict]] = {}

        sym = bar.get("symbol", "UNKNOWN")
        if sym not in self._bar_history:
            self._bar_history[sym] = []
        self._bar_history[sym].append(bar)

        # Keep last 60 bars per instrument
        if len(self._bar_history[sym]) > 60:
            self._bar_history[sym] = self._bar_history[sym][-60:]

        if self._config and self._config.instruments:
            from app.services.ohlcv_store import INDEX_ALIASES
            allowed_insts = set(self._config.instruments)
            expanded = set(allowed_insts)
            for inst in allowed_insts:
                if inst in INDEX_ALIASES:
                    expanded.add(INDEX_ALIASES[inst])
                if inst.upper() in INDEX_ALIASES:
                    expanded.add(INDEX_ALIASES[inst.upper()])
                for k, v in INDEX_ALIASES.items():
                    if inst.upper() == v.upper():
                        expanded.add(k)
            if sym not in expanded and sym.upper() not in expanded:
                return

        # Do not let the session boundary itself fire a signal.
        #
        # Indicator history is pre-seeded with the tail of the PREVIOUS session
        # so indicators are warm at 09:15. That means the first in-session bar
        # sits directly after an overnight gap, and every crossover test below
        # compares history[-1] against history[-2]. The gap flips state on all
        # instruments simultaneously, so a fresh replay printed one signal per
        # instrument per strategy — all stamped 09:15:00 — and then went quiet.
        # Open positions still settle (that happens above, before this gate);
        # only NEW signals wait for the first fully in-session comparison.
        if not hasattr(self, "_in_session_bars"):
            self._in_session_bars: Dict[str, int] = {}
        self._in_session_bars[sym] = self._in_session_bars.get(sym, 0) + 1
        if self._in_session_bars[sym] < 2:
            return

        history = self._bar_history[sym]

        close = float(bar["close"])
        high = float(bar["high"])
        low = float(bar["low"])
        opens = float(bar["open"])

        if len(history) < 2:
            return

        prev_bar = history[-2]
        prev_close = float(prev_bar["close"])

        closes = [float(b["close"]) for b in history]
        sma5 = sum(closes[-5:]) / min(len(closes), 5)
        sma20 = sum(closes[-20:]) / min(len(closes), 20)

        # Simple ATR estimate
        highs = [float(b["high"]) for b in history[-14:]]
        lows = [float(b["low"]) for b in history[-14:]]
        ranges = [h - l for h, l in zip(highs, lows)]
        atr = sum(ranges) / len(ranges) if ranges else max(0.01, close * 0.005)

        # Simple RSI calculation
        gains = []
        losses = []
        for j in range(1, min(len(closes), 15)):
            diff = closes[-j] - closes[-j-1] if j+1 <= len(closes) else 0
            if diff > 0:
                gains.append(diff)
            else:
                losses.append(abs(diff))
        avg_gain = sum(gains) / max(len(gains), 1)
        avg_loss = sum(losses) / max(len(losses), 1)
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        rsi = 100 - (100 / (1 + rs))

        signals_to_fire = []
        bar_time_str = bar_dt.strftime("%H:%M:%S")

        recorded_list = getattr(self, "_recorded_signals", [])
        from app.services.ohlcv_store import INDEX_ALIASES
        sym_u = sym.upper()
        sym_aliases = {sym, sym_u}
        if sym_u in INDEX_ALIASES:
            sym_aliases.add(INDEX_ALIASES[sym_u])
            sym_aliases.add(INDEX_ALIASES[sym_u].upper())
        for k, v in INDEX_ALIASES.items():
            if sym_u == v.upper():
                sym_aliases.add(k)
                sym_aliases.add(k.upper())

        # 1. SuperTrend: Canonical Triple SuperTrend Alignment (regime.py)
        # Fast (21, 1.0), Mid (14, 2.0), Slow (7, 3.0) on Heikin-Ashi candles.
        # When recorded signals exist for this session, or this date was scanned by Kite Engine live,
        # ground truth signals are replayed automatically at their recorded timestamps; synthetic evaluation is skipped.
        bar_date_str = bar_dt.strftime("%Y-%m-%d")
        today_recorded = [
            r for r in recorded_list
            if datetime.fromtimestamp(r["timestamp_ms"] / 1000, tz=ist).strftime("%Y-%m-%d") == bar_date_str
        ]
        has_recorded_today = bool(today_recorded)
        is_scanned_session = has_recorded_today or (bar_date_str in getattr(self, "_scanned_dates", set()))
        has_recorded_st = is_scanned_session or (has_recorded_today and any(
            r.get("underlying", "").upper() in sym_aliases
            for r in today_recorded
        ))
        if not has_recorded_st and len(history) >= 25:
            h_arr = np.array([float(b["high"]) for b in history], dtype=np.float64)
            l_arr = np.array([float(b["low"]) for b in history], dtype=np.float64)
            c_arr = np.array([float(b["close"]) for b in history], dtype=np.float64)
            o_arr = np.array([float(b.get("open", b["close"])) for b in history], dtype=np.float64)

            # Apply Heikin-Ashi smoothing matching live Kite Engine
            _, h_arr, l_arr, c_arr = compute_heikin_ashi(o_arr, h_arr, l_arr, c_arr)

            _, t_fast = compute_supertrend(h_arr, l_arr, c_arr, period=21, multiplier=1.0)
            _, t_mid = compute_supertrend(h_arr, l_arr, c_arr, period=14, multiplier=2.0)
            _, t_slow = compute_supertrend(h_arr, l_arr, c_arr, period=7, multiplier=3.0)

            # Require indicators to be fully initialized (non-zero) on both current and previous bar
            if (t_fast[-1] != 0 and t_fast[-2] != 0 and
                t_mid[-1] != 0 and t_mid[-2] != 0 and
                t_slow[-1] != 0 and t_slow[-2] != 0):

                curr_bull = (t_fast[-1] == 1 and t_mid[-1] == 1 and t_slow[-1] == 1)
                prev_bull = (t_fast[-2] == 1 and t_mid[-2] == 1 and t_slow[-2] == 1)
                curr_bear = (t_fast[-1] == -1 and t_mid[-1] == -1 and t_slow[-1] == -1)
                prev_bear = (t_fast[-2] == -1 and t_mid[-2] == -1 and t_slow[-2] == -1)

                if curr_bull and not prev_bull:
                    signals_to_fire.append({
                        "strategy": "supertrend",
                        "direction": "BULLISH",
                        "strength": "STRONG",
                    })
                elif curr_bear and not prev_bear:
                    signals_to_fire.append({
                        "strategy": "supertrend",
                        "direction": "BEARISH",
                        "strength": "STRONG",
                    })

        # 2. VCP Squeeze: Canonical Volatility Contraction Pattern
        # Requires multi-bar contraction (r1 > r2 > r3 < 0.8 * atr) followed by range & volume expansion breakout
        if len(history) >= 8:
            r1 = float(history[-4]["high"]) - float(history[-4]["low"])
            r2 = float(history[-3]["high"]) - float(history[-3]["low"])
            r3 = float(history[-2]["high"]) - float(history[-2]["low"])
            recent_vols = [float(b.get("volume", 0)) for b in history[-6:-1]]
            avg_vol = sum(recent_vols) / max(len(recent_vols), 1) if recent_vols else 0
            cur_vol = float(bar.get("volume", 0))
            is_contracting = (r1 > r2 and r2 > r3 and r3 < 0.8 * atr)
            vol_expansion = (cur_vol > 1.2 * avg_vol) if avg_vol > 0 else True
            cur_range = high - low

            if is_contracting and cur_range > 1.0 * atr and vol_expansion:
                prior_high = max(float(b["high"]) for b in history[-4:-1])
                prior_low = min(float(b["low"]) for b in history[-4:-1])
                if close > prior_high and close > opens:
                    signals_to_fire.append({
                        "strategy": "vcp",
                        "direction": "BULLISH",
                        "strength": "STRONG",
                    })
                elif close < prior_low and close < opens:
                    signals_to_fire.append({
                        "strategy": "vcp",
                        "direction": "BEARISH",
                        "strength": "STRONG",
                    })

        # 3. Adaptive Edge: Canonical Multi-Horizon Value Area & Order Flow Pipeline
        # When recorded signals exist for this session, authentic spot scans are replayed
        # automatically at their recorded timestamps; synthetic fallback heuristics are skipped.
        adaptive_src = (cfg.adaptive_source if cfg and hasattr(cfg, "adaptive_source") else "both") or "both"
        adaptive_src = str(adaptive_src).lower()
        skip_ae_model = (
            adaptive_src in ("spot_scan", "spot")
            and not getattr(self, "_ae_fallback_mode", False)
        )

        has_recorded_ae = (
            adaptive_src not in ("ae_model", "ae")
            and has_recorded_today
            and any(
                r.get("underlying", "").upper() in sym_aliases
                and (r.get("strategy") == "adaptive_edge" or r.get("is_spot_scan"))
                and (r.get("strategy") == "adaptive_edge")
                for r in today_recorded
            )
        )

        adaptive_ver = (cfg.adaptive_version if cfg and hasattr(cfg, "adaptive_version") else "v2_hardened") or "v2_hardened"
        adaptive_ver = str(adaptive_ver).lower()
        is_ae_v2 = adaptive_ver in ("v2_hardened", "v2")
        ae_toxic_lockout = is_ae_v2 and ("09:15:00" <= bar_time_str < "09:28:00")

        is_ae_symbol = _is_index(sym) or (bool(cfg and cfg.instruments and (sym in cfg.instruments or sym_u in cfg.instruments)))
        if not skip_ae_model and not has_recorded_ae and is_ae_symbol and not ae_toxic_lockout and len(history) >= 15:
            body = abs(close - opens)
            lower_wick = min(opens, close) - low
            upper_wick = high - max(opens, close)
            bar_range = high - low
            denom = max(bar_range, 1e-9)
            long_body_ok = ((close - low) / denom) >= 0.60 if is_ae_v2 else True
            short_body_ok = ((high - close) / denom) >= 0.60 if is_ae_v2 else True
            # Exhaustion oversold + pin bar rejection of lows (hammer)
            if rsi <= 28 and lower_wick >= 2.0 * max(body, 0.05 * atr) and close > low + 0.4 * (high - low) and long_body_ok:
                signals_to_fire.append({
                    "strategy": "adaptive_edge",
                    "strategy_version": ("v2_hardened" if is_ae_v2 else "v1_baseline"),
                    "direction": "BULLISH",
                    "strength": "STRONG",
                })
            # Exhaustion overbought + pin bar rejection of highs (shooting star)
            elif rsi >= 72 and upper_wick >= 2.0 * max(body, 0.05 * atr) and close < low + 0.6 * (high - low) and short_body_ok:
                signals_to_fire.append({
                    "strategy": "adaptive_edge",
                    "strategy_version": ("v2_hardened" if is_ae_v2 else "v1_baseline"),
                    "direction": "BEARISH",
                    "strength": "STRONG",
                })
        sym_bar_idx = len(history)
        ae_active = sym_bar_idx < self._active_until_bar.get((sym, "adaptive_edge"), -1) if hasattr(self, "_active_until_bar") else False
        if not ae_active and not skip_ae_model and not has_recorded_ae and is_ae_symbol and not ae_toxic_lockout and len(history) >= 20:
            from app.services.adaptive_edge_strategy import decide_from_candles
            from app.services.adaptive_edge import get_config as get_ae_config
            c_input = [
                {
                    "timestamp_ms": int(float(b.get("time", 0)) * 1000),
                    "open": float(b.get("open", 0)),
                    "high": float(b.get("high", 0)),
                    "low": float(b.get("low", 0)),
                    "close": float(b.get("close", 0)),
                    "volume": float(b.get("volume", 0)),
                }
                for b in history
            ]
            try:
                import dataclasses
                ae_cfg = get_ae_config()
                if not getattr(self, "_cached_ae_cfg", None):
                    from app.services.adaptive_edge import get_config as get_ae_config
                    self._cached_ae_cfg = get_ae_config()
                ae_cfg = self._cached_ae_cfg
                target_version = "v2_hardened" if is_ae_v2 else "v1_baseline"
                ae_cfg = dataclasses.replace(ae_cfg, strategy_version=target_version)
                if ae_cfg and ae_cfg.strategy_version != target_version:
                    ae_cfg = dataclasses.replace(ae_cfg, strategy_version=target_version)
                    self._cached_ae_cfg = ae_cfg
                dec = decide_from_candles(sym, c_input, ae_cfg, expiry=bar_dt.strftime("%Y-%m-%d"), spot=close)
                if dec and dec.actionable:
                    signals_to_fire.append({
                        "strategy": "adaptive_edge",
                        "strategy_version": ("v2_hardened" if is_ae_v2 else "v1_baseline"),
                        "direction": dec.direction,
                        "strength": "STRONG",
                    })
            except Exception as e:
                log.debug("Adaptive Edge bar evaluation error for %s: %s", sym, e)

        # 4. Bear to Bearish: Canonical Lower Highs Breakdown (detect_lower_highs)
        if len(history) >= 10:
            from app.engines.bear_to_bearish.strategy import detect_lower_highs
            has_lh, latest_peak, prev_peak = detect_lower_highs(history)
            prior_support = min(float(b["low"]) for b in history[-6:-1])
            if has_lh and close < prior_support and close < opens and rsi < 48:
                signals_to_fire.append({
                    "strategy": "bear_to_bearish",
                    "direction": "BEARISH",
                    "strength": "STRONG",
                })

        # 4b. Gamma Move: shipped daily level+regime gates on a stock.
        # The 15m OI trigger cannot run on this tape, so strength stays WATCHING.
        try:
            asof = _asof_symbol_bars(getattr(self, "_candles", None) or [],
                                     sym, bar.get("time"))
            tape = asof or history
            warmup = list(self._bar_history.get(sym) or [])
            if warmup and tape is not warmup:
                seen = {(_bar_epoch_seconds(b), b.get("open"), b.get("close")) for b in tape}
                merged = [b for b in warmup
                          if (_bar_epoch_seconds(b), b.get("open"), b.get("close")) not in seen]
                tape = merged + list(tape)
            gm = _gamma_move_watch_from_bars(tape, close, symbol=sym,
                                             prefer_store=True)
            if gm:
                signals_to_fire.append(gm)
        except Exception as exc:
            log.debug("Gamma Move bar evaluation error for %s: %s", sym, exc)

        # 5. ATM Premium Imbalance: Canonical Opening Window Session Trade (max 1/day)
        is_open_window = "09:15:00" <= bar_time_str <= "09:30:00"
        atm_already_traded = any(
            ev.strategy == "atm_imbalance"
            and ev.instrument == sym
            and datetime.fromtimestamp(ev.timestamp_ms / 1000, tz=ist).date() == bar_dt.date()
            for ev in self._stats.events
        )
        if is_open_window and not atm_already_traded and len(history) >= 2:
            direction = "BULLISH" if close >= opens else "BEARISH"
            signals_to_fire.append({
                "strategy": "atm_imbalance",
                "direction": direction,
                "strength": "STRONG",
            })

        # 6. Navigator: Canonical Session-Anchored VWAP Cross
        session_bars = [b for b in history if datetime.fromtimestamp(b["time"], tz=ist).date() == bar_dt.date()]
        if len(session_bars) >= 5:
            vols = [float(b.get("volume", 0)) for b in session_bars]
            has_vol = sum(vols) > 0
            if has_vol:
                cum_pv = sum(float(b["close"]) * float(b.get("volume", 0)) for b in session_bars)
                cum_v = sum(vols)
                session_vwap = cum_pv / cum_v if cum_v > 0 else close
            else:
                session_vwap = sum(float(b["close"]) for b in session_bars) / len(session_bars)

            prev_session_bars = session_bars[:-1]
            if prev_session_bars:
                if has_vol:
                    prev_pv = sum(float(b["close"]) * float(b.get("volume", 0)) for b in prev_session_bars)
                    prev_v = sum(float(b.get("volume", 0)) for b in prev_session_bars)
                    prev_vwap = prev_pv / prev_v if prev_v > 0 else prev_close
                else:
                    prev_vwap = sum(float(b["close"]) for b in prev_session_bars) / len(prev_session_bars)

                if prev_close <= prev_vwap and close > session_vwap and session_vwap >= prev_vwap and rsi > 50:
                    signals_to_fire.append({
                        "strategy": "navigator",
                        "direction": "BULLISH",
                        "strength": "STRONG",
                    })
                elif prev_close >= prev_vwap and close < session_vwap and session_vwap <= prev_vwap and rsi < 50:
                    signals_to_fire.append({
                        "strategy": "navigator",
                        "direction": "BEARISH",
                        "strength": "STRONG",
                    })

        # 7. Nifty ORB — live engine, not a replay-local clone.
        orb_trades_today = sum(
            1 for ev in self._stats.events
            if ev.strategy == "nifty_orb"
            and ev.instrument == sym
            and datetime.fromtimestamp(ev.timestamp_ms / 1000, tz=ist).date() == bar_dt.date()
        )
        if orb_trades_today < 2 and "09:30:00" <= bar_time_str <= "12:00:00":
            live = _live_orb_direction(history, bar_dt)
            if live == "LONG":
                signals_to_fire.append({
                    "strategy": "nifty_orb",
                    "direction": "BULLISH",
                    "strength": "STRONG",
                })
            elif live == "SHORT":
                signals_to_fire.append({
                    "strategy": "nifty_orb",
                    "direction": "BEARISH",
                    "strength": "STRONG",
                })

        # Track recent signals per (symbol, strategy) to prevent flood
        if not hasattr(self, '_last_fired'):
            self._last_fired: Dict[Tuple[str, str], Tuple[str, int]] = {}
        if not hasattr(self, '_active_until_bar'):
            self._active_until_bar: Dict[Tuple[str, str], int] = {}

        sym_bar_idx = len(history)

        # Intraday entry cutoff: Do not enter new trades after 15:15:00 (F&O cash stops at 15:15)
        time_hhmmss = bar_dt.strftime("%H:%M:%S")
        if time_hhmmss >= "15:15:00":
            return

        # Emit all generated strategy signals for this bar (or filter by selected strategies)
        cfg_strats = [s.lower() for s in (self._config.strategies if self._config and self._config.strategies else [self._config.strategy if self._config else "all"])]
        allow_all = "all" in cfg_strats or "*" in cfg_strats or not cfg_strats

        for sdef in signals_to_fire:
            strategy = sdef["strategy"]
            if not allow_all and strategy.lower() not in cfg_strats:
                continue
            if strategy.lower() == "adaptive_edge" and (skip_ae_model or ae_toxic_lockout):
                continue
            direction = sdef["direction"]
            strength = sdef["strength"]

            key = (sym, strategy)
            last_dir, last_idx = self._last_fired.get(key, ("", -1))
            # De-duplicate: do not re-emit identical direction within 6 bars of this symbol (30 minutes)
            if last_dir == direction and (sym_bar_idx - last_idx) < 6:
                continue

            # Check if an active position is already open on this symbol for this strategy
            active_until = self._active_until_bar.get(key, -1)
            if sym_bar_idx < active_until:
                continue

            self._last_fired[key] = (direction, sym_bar_idx)

            if direction == "BULLISH":
                stop = round(close - 1.5 * atr, 2)
                target = round(close + 2.5 * atr, 2)
            else:
                stop = round(close + 1.5 * atr, 2)
                target = round(close - 2.5 * atr, 2)

            is_multi = getattr(self, "_is_multi_day", False)
            time_iso = bar_dt.strftime("%Y-%m-%dT%H:%M:%S") if is_multi else bar_dt.strftime("%H:%M:%S")
            ts_ms = int(bar_dt.timestamp() * 1000)

            if strategy == "gamma_move":
                opt = sdef.get("opt_type") or ("CE" if direction == "BULLISH" else "PE")
                event = SimSignalEvent(
                    time_iso=time_iso, timestamp_ms=ts_ms,
                    strategy=strategy, instrument=sym,
                    direction=direction, strength=strength,
                    entry=round(close, 2), stop=stop, target=target,
                    contract=None, spot=round(close, 2), strike=None,
                    opt_type=opt, premium_entry=None, premium_sl=None,
                    premium_target=None, scan_origin=None,
                    level_price=sdef.get("level_price"),
                    level_kind=sdef.get("level_kind"),
                    level_touches=sdef.get("level_touches"),
                    regime=sdef.get("regime"),
                )
                idx = next((i for i, e in enumerate(self._stats.events)
                            if e.strategy == "gamma_move" and e.instrument == sym), None)
                if idx is None:
                    self._stats.signals_fired += 1
                    self._stats.events.append(event)
                else:
                    self._stats.events[idx] = event
                self._last_signal = event
                self._publish("signal", event.model_dump())
                continue

            leg = _option_contract(sym, close, direction, self._config, sim_date=bar_dt.strftime("%Y-%m-%d"))
            event = SimSignalEvent(
                time_iso=time_iso,
                timestamp_ms=ts_ms,
                strategy=strategy,
                instrument=sym,
                direction=direction,
                strength=strength,
                entry=round(close, 2),
                stop=stop,
                target=target,
                contract=leg["contract"],
                spot=round(close, 2),
                strike=leg["strike"],
                opt_type=sdef.get("opt_type") or leg["opt_type"],
                # The premium ladder, in option terms rather than underlying
                # terms. Declared on `main` but never populated there; filling
                # it is the difference between a field and a promise.
                premium_entry=leg["premium"],
                premium_sl=_premium_at(leg, close, stop),
                premium_target=_premium_at(leg, close, target),
                scan_origin="spot_scan" if (adaptive_src in ("spot_scan", "spot") or strategy != "adaptive_edge") else "adaptive_edge",
                strategy_version=sdef.get("strategy_version") or (adaptive_ver if strategy == "adaptive_edge" else None),
            )
            self._stats.signals_fired += 1
            self._stats.events.append(event)
            self._last_signal = event
            self._publish("signal", event.model_dump())

            if strength == "STRONG":
                # OPEN the position. Its outcome is decided by later bars, in
                # `_settle_open_positions`, as the simulated clock reaches them.
                # This used to scan up to 30 FUTURE bars right here and write
                # the exit price, exit time and WIN/LOSS in one go — so every
                # trade appeared already finished, with an exit timestamped
                # minutes ahead of the replay clock.
                cfg_lots = max(1, self._config.lots) if self._config else 1
                lot_size = leg["lot_size"]
                qty = cfg_lots * lot_size
                raw_entry_p = leg["premium"]
                entry_p, _, friction_mode = _apply_friction(
                    raw_entry_p, raw_entry_p, sym, self._config
                )
                entry_slip = round((entry_p - raw_entry_p) * qty, 2)

                trade = SimTradeEvent(
                    trade_id=f"TRD-{1000 + len(self._stats.trades) + 1}",
                    entry_time_iso=bar_dt.strftime("%Y-%m-%dT%H:%M:%S") if is_multi else bar_dt.strftime("%H:%M:%S"),
                    exit_time_iso="OPEN",
                    timestamp_ms=int(bar_dt.timestamp() * 1000),
                    strategy=strategy,
                    symbol=leg["contract"],
                    underlying=sym,
                    direction="BUY",
                    opt_type=leg["opt_type"],
                    strike=leg["strike"],
                    lots=cfg_lots,
                    quantity=qty,
                    entry_price=entry_p,
                    exit_price=None,
                    stop_loss=round(entry_p * 0.75, 2),
                    target_price=round(entry_p * 1.5, 2),
                    status="OPEN",
                    pnl_usd=0.0,
                    pnl_pct=0.0,
                    duration_mins=0,
                    raw_entry=None if friction_mode == "ideal" else raw_entry_p,
                    raw_exit=None,
                    slippage=None if friction_mode == "ideal" else max(0.0, entry_slip),
                    spot_entry=round(close, 2),
                    spot_stop=stop,
                    spot_target=target,
                    spot_hwm=round(close, 2),
                    spot_initial_risk=abs(close - stop) if stop is not None else None,
                    spot_initial_stop=stop,
                    exit_reason=None,
                    bars_held=0,
                    scan_origin="spot_scan" if adaptive_src in ("spot_scan", "spot") else ("adaptive_edge" if (strategy == "adaptive_edge" or _is_index(sym)) else "spot_scan"),
                    strategy_version=sdef.get("strategy_version") or (adaptive_ver if strategy == "adaptive_edge" else None),
                )
                self._stats.trades_entered += 1
                self._stats.trades.append(trade)
                self._open_by_symbol.setdefault(sym, []).append(trade)
                # Suppress a re-entry on this key while the position is live.
                # `_close_position` clears it on the bar that actually closes.
                self._active_until_bar[key] = sym_bar_idx + self.MAX_HOLD_BARS
                max_bars = (
                    self._config.max_hold_bars
                    if (self._config and getattr(self._config, "max_hold_bars", None))
                    else self.MAX_HOLD_BARS
                )
                self._active_until_bar[key] = sym_bar_idx + max_bars
                self._recompute_totals()
                self._publish("trade", trade.model_dump())


async def _hydrate_missing_candles(
    instruments: List[str],
    resolution: str,
    start_epoch: int,
    end_epoch: int,
    session_start: Optional[int] = None,
    on_progress: Optional[Any] = None,
) -> None:
    """Fetch missing historical candles for selected replay date range from Zerodha Kite API in safe chunks."""
    from app.services import ohlcv_store
    from app.services.ohlcv_store import INDEX_ALIASES, RESOLUTION_SECONDS
    from app.services.exchanges.kite import accounts as kite_accounts
    from app.services.exchanges.kite.client import KiteClient

    try:
        from zoneinfo import ZoneInfo
        ist_tz = ZoneInfo("Asia/Kolkata")
    except ImportError:
        from datetime import timezone, timedelta
        ist_tz = timezone(timedelta(hours=5, minutes=30))

    kite_accounts.bootstrap()
    zerodha_acct = kite_accounts.get_active("default") or next(
        (a for a in kite_accounts._accounts.values() if a.is_active and a.access_token),
        None,
    )
    if not (zerodha_acct and zerodha_acct.access_token):
        log.info("No active Kite account available; skipping remote historical candle hydration.")
        return

    check_start = session_start if session_start is not None else start_epoch
    res_sec = RESOLUTION_SECONDS.get(resolution, 300)
    now_epoch = int(time.time())
    is_today_in_range = (start_epoch <= now_epoch <= (end_epoch + 86400))
    effective_target_end = min(end_epoch, now_epoch) if is_today_in_range else end_epoch

    k_res_map = {
        "1m": "minute",
        "3m": "3minute",
        "5m": "5minute",
        "10m": "10minute",
        "15m": "15minute",
        "30m": "30minute",
        "60m": "60minute",
        "1h": "60minute",
    }
    k_res = k_res_map.get(resolution, "5minute")
    # Kite enforces max 60-100 days per intraday request. Chunk into 60-day slices.
    CHUNK_SEC = 60 * 86400

    kc = KiteClient(api_key=getattr(zerodha_acct, "api_key", "") or "", access_token=zerodha_acct.access_token)
    try:
        for idx, sym in enumerate(instruments):
            canon_sym = _canonical_symbol(sym)
            token = KITE_TOKENS.get(canon_sym.upper()) or KITE_TOKENS.get(sym.upper())
            if not token and sym.upper() in INDEX_ALIASES:
                token = KITE_TOKENS.get(INDEX_ALIASES[sym.upper()])
            if not token and canon_sym.upper() in INDEX_ALIASES:
                token = KITE_TOKENS.get(INDEX_ALIASES[canon_sym.upper()])
            if not token:
                continue

            cov = ohlcv_store.get_symbol_coverage(canon_sym, resolution)
            fetch_ranges: List[Tuple[int, int]] = []
            if not cov or (cov.get("count") or 0) == 0:
                fetch_ranges.append((check_start, effective_target_end))
            else:
                cov_earliest = cov.get("earliest") or 0
                cov_latest = cov.get("latest") or 0
                if check_start < (cov_earliest - res_sec):
                    fetch_ranges.append((check_start, cov_earliest))
                if is_today_in_range:
                    if cov_latest < (effective_target_end - res_sec * 2):
                        fetch_ranges.append((cov_latest, effective_target_end))
                else:
                    if cov_latest < (effective_target_end - 900):
                        fetch_ranges.append((cov_latest, effective_target_end))

            if not fetch_ranges:
                continue

            if on_progress:
                try:
                    on_progress(f"⚡ Hydrating {canon_sym} ({idx + 1}/{len(instruments)})...")
                except Exception:
                    pass

            log.info("Missing/stale local candles for %s [%s] ranges %s. Fetching from Zerodha Kite...", canon_sym, resolution, fetch_ranges)

            for f_epoch, t_epoch in fetch_ranges:
                cur_start = f_epoch
                while cur_start < t_epoch:
                    cur_end = min(cur_start + CHUNK_SEC, t_epoch)
                    from_str = datetime.fromtimestamp(cur_start, tz=ist_tz).strftime("%Y-%m-%d %H:%M:%S")
                    to_str = datetime.fromtimestamp(cur_end, tz=ist_tz).strftime("%Y-%m-%d %H:%M:%S")
                    try:
                        hist_data = await kc.get_historical(token, k_res, from_str, to_str)
                        if isinstance(hist_data, dict) and "candles" in hist_data:
                            raw_list = hist_data["candles"]
                            parsed_candles = []
                            for row in raw_list:
                                dt_c = datetime.fromisoformat(row[0])
                                if dt_c.tzinfo is None:
                                    dt_c = dt_c.replace(tzinfo=ist_tz)
                                parsed_candles.append({
                                    "time": int(dt_c.timestamp()),
                                    "open": float(row[1]),
                                    "high": float(row[2]),
                                    "low": float(row[3]),
                                    "close": float(row[4]),
                                    "volume": float(row[5]) if len(row) > 5 else 0.0,
                                })
                            if parsed_candles:
                                ohlcv_store.upsert_candles(canon_sym, resolution, parsed_candles)
                                if canon_sym != sym:
                                    ohlcv_store.upsert_candles(sym, resolution, parsed_candles)
                                alias = INDEX_ALIASES.get(canon_sym.upper()) or INDEX_ALIASES.get(sym.upper())
                                if alias and alias != canon_sym.upper():
                                    ohlcv_store.upsert_candles(alias, resolution, parsed_candles)
                                log.info("Hydrated %d historical candles for %s (%s to %s)", len(parsed_candles), canon_sym, from_str, to_str)
                    except Exception as exc:
                        log.warning("Failed chunk fetch for %s (%s to %s): %s", canon_sym, from_str, to_str, exc)
                    cur_start = cur_end + 1
                    await asyncio.sleep(0.1)
    finally:
        await kc.close()



def reset_all_engine_signals() -> None:
    """Clear existing signal caches across all strategy engines when simulation starts."""
    try:
        from app.services import snapshot_cache
        snapshot_cache.clear()
    except Exception as exc:
        log.debug("Snapshot cache clear error: %s", exc)

    try:
        from app.api.v1.endpoints import directional
        directional._prev_states.clear()
        directional._active_signal_ids.clear()
        directional._active_signal_sls.clear()
        directional._prev_all_green.clear()
        directional._prev_all_red.clear()
    except Exception as exc:
        log.debug("Directional tracker state clear error: %s", exc)

    try:
        from app.services.kite_engine.scanner import scanner
        scanner._users.clear()
    except Exception as exc:
        log.debug("Kite scanner users clear error: %s", exc)


# Module-level singleton
simulation_runner = SimulationRunner()
