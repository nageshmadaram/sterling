"""Bar-by-bar replay of the Gamma Move engine over stored history."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone, timedelta
from typing import Optional, Sequence

from .config import GammaMoveConfig
from .models import Candle, OICandle, StrikeCandidate, q2
from .strategy import GammaMoveStrategy, Intent
from .trigger import session_day

_IST = timezone(timedelta(hours=5, minutes=30))


_SESSION_END = (15, 30)  # NSE cash close. A daily close is not known before this.


def _spot_asof(spot_candles: Sequence[Candle], ts_ms: int) -> Optional[float]:
    """Last *completed* daily close as of ts_ms.

    Daily candles are stamped at the session date (midnight or 09:15). Their
    close is the end-of-day print, so a 10:00 option bar must still see
    yesterday. Passing the raw stamp through would leak today's close into
    every earlier 15-minute bar.
    """
    asof = datetime.fromtimestamp(ts_ms / 1000, _IST)
    session_done = (asof.hour, asof.minute) >= _SESSION_END
    px = None
    for c in spot_candles:
        if c.close <= 0:
            continue
        day = datetime.fromtimestamp(c.ts_ms / 1000, _IST).date()
        if day < asof.date() or (day == asof.date() and session_done):
            px = float(c.close)
        elif day > asof.date():
            break
    return px


def replay_contract(candidate: StrikeCandidate, bars: Sequence[OICandle],
                    cfg: GammaMoveConfig, *, regime_by_day: Optional[dict] = None,
                    spot_candles: Optional[Sequence[Candle]] = None) -> dict:
    """Walk one contract's history and report every decision the engine made."""
    strat = GammaMoveStrategy(cfg)
    regime_by_day = regime_by_day or {}
    events: list[dict] = []
    series = list(bars)

    for i in range(2, len(series)):
        window = series[:i + 1]
        bar = series[i]
        day = session_day(bar.ts_ms)
        today = datetime.fromtimestamp(bar.ts_ms / 1000, _IST).date()
        strat.state.roll(day)
        regime = regime_by_day.get(day, "unknown")

        live = candidate
        if spot_candles:
            px = _spot_asof(spot_candles, bar.ts_ms)
            if px:
                live = replace(candidate, spot=px)

        for pos in list(strat.state.positions.values()):
            decision = strat.on_price(pos, bar.close, bar.ts_ms, day)
            if decision.intent is Intent.EXIT and decision.exit_position:
                pnl = strat.on_exit(decision.exit_position, bar.close, day)
                events.append({"kind": "exit", "ts_ms": bar.ts_ms, "day": day,
                               "price": q2(bar.close), "reason": decision.exit_reason,
                               "pnl_inr": pnl})

        signal = strat.evaluate(live, window, now_ms=bar.ts_ms, today=today,
                                regime=regime)
        if signal.state != "armed":
            continue
        blocker = strat.admit(signal, day)
        if blocker:
            events.append({"kind": "refused", "ts_ms": bar.ts_ms, "day": day,
                           "reason": blocker})
            continue
        strat.on_entry(signal, signal.entry or bar.close, bar.ts_ms, day)
        events.append({"kind": "entry", "ts_ms": bar.ts_ms, "day": day,
                       "price": signal.entry, "stop": signal.stop,
                       "lots": signal.lots, "quantity": signal.quantity,
                       "spot": live.spot,
                       "metrics": signal.metrics.as_dict() if signal.metrics else None})

    open_positions = [{"symbol": sym, "entry": p.entry, "stop": p.stop}
                      for sym, p in strat.state.positions.items()]
    return {
        "tradingsymbol": candidate.instrument.tradingsymbol,
        "underlying": candidate.underlying,
        "bars": len(series), "events": events,
        "record": strat.state.record.as_dict(),
        "open_at_end": open_positions,
        "caveats": [
            "fills are the bar close; no spread, no slippage, no brokerage",
            "spot-through uses the last completed daily close (visible only "
            "after 15:30 IST) when spot_candles are supplied; otherwise the "
            "candidate's frozen spot",
            "the level price is still held fixed for the whole replay",
        ],
    }


def summarise(results: Sequence[dict]) -> dict:
    entries = sum(1 for r in results for e in r["events"] if e["kind"] == "entry")
    closed = [e for r in results for e in r["events"] if e["kind"] == "exit"]
    wins = [e for e in closed if (e.get("pnl_inr") or 0) > 0]
    pnl = sum(e.get("pnl_inr") or 0 for e in closed)
    unresolved = sum(len(r["open_at_end"]) for r in results)
    return {
        "contracts": len(results), "entries": entries, "closed": len(closed),
        "unresolved": unresolved, "wins": len(wins),
        "win_rate": q2(100.0 * len(wins) / len(closed)) if closed else None,
        "gross_pnl_inr": q2(pnl),
        "break_even_win_rate": _break_even(closed),
        "verdict": ("no closed trades" if not closed
                    else f"{len(wins)}/{len(closed)} winners, gross Rs {pnl:,.0f}"),
    }


def _break_even(closed: Sequence[dict]) -> Optional[float]:
    wins = [e["pnl_inr"] for e in closed if (e.get("pnl_inr") or 0) > 0]
    losses = [-e["pnl_inr"] for e in closed if (e.get("pnl_inr") or 0) <= 0]
    if not wins or not losses:
        return None
    avg_w, avg_l = sum(wins) / len(wins), sum(losses) / len(losses)
    if avg_w + avg_l <= 0:
        return None
    return q2(100.0 * avg_l / (avg_w + avg_l))
