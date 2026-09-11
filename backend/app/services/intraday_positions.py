"""Durable open-position registry for the intraday pack.

Durable because the alternative is a restart that loses track of real money.
A position held only in memory is protected only while this process lives, and
the one moment protection matters most is the moment it does not.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from app.core.logging import get_logger
from app.engines.intraday.position import (CLOSED, OPEN, PENDING, REJECTED,
                                           IntradayPosition)

log = get_logger(__name__)

_cache: dict[str, dict[str, IntradayPosition]] = {}
_record_cache: dict[str, "DayRecord"] = {}


@dataclass
class DayRecord:
    """This engine's own realised record for one IST day.

    Its own, rather than read from the account: the account-wide daily-loss
    breaker covers everything trading, and an engine also needs to know when
    IT has lost enough to stop — otherwise one strategy's bad morning is funded
    by another's good one until the account-wide number finally trips.
    """

    day: str = ""
    trades: int = 0
    wins: int = 0
    realised_inr: float = 0.0
    consecutive_losses: int = 0

    def roll(self, today: str) -> "DayRecord":
        if self.day != today:
            self.day, self.trades, self.wins = today, 0, 0
            self.realised_inr = 0.0
            self.consecutive_losses = 0
        return self

    def record(self, pnl: float) -> "DayRecord":
        self.trades += 1
        self.realised_inr += float(pnl)
        if pnl > 0:
            self.wins += 1
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
        return self

    @property
    def win_rate(self) -> Optional[float]:
        return (self.wins / self.trades * 100.0) if self.trades else None

    def as_dict(self) -> dict:
        return {"day": self.day, "trades": self.trades, "wins": self.wins,
                "realised_inr": round(self.realised_inr, 2),
                "consecutive_losses": self.consecutive_losses,
                "win_rate": self.win_rate}

    @classmethod
    def from_dict(cls, d: dict) -> "DayRecord":
        return cls(day=str(d.get("day") or ""), trades=int(d.get("trades") or 0),
                   wins=int(d.get("wins") or 0),
                   realised_inr=float(d.get("realised_inr") or 0.0),
                   consecutive_losses=int(d.get("consecutive_losses") or 0))


def _key(uid: str) -> str:
    return f"intraday_positions_{uid}"


def _record_key(uid: str) -> str:
    return f"intraday_record_{uid}"


def load(uid: str) -> dict[str, IntradayPosition]:
    if uid in _cache:
        return _cache[uid]
    out: dict[str, IntradayPosition] = {}
    try:
        from app.services import db
        raw = db.get_config(_key(uid))
        for d in (json.loads(raw) if raw else []):
            p = IntradayPosition.from_dict(d)
            if p is None:
                # Never silently. An unreadable row is a position we may still
                # be holding, and it has just stopped being managed.
                log.error("intraday: unreadable persisted position dropped: %s", d)
                continue
            out[p.contract.tradingsymbol] = p
    except Exception as exc:                                       # noqa: BLE001
        log.error("intraday: position registry unreadable for %s: %s", uid, exc)
    _cache[uid] = out
    return out


def persist(uid: str) -> None:
    try:
        from app.services import db
        db.set_config(_key(uid), json.dumps(
            [p.as_dict() for p in _cache.get(uid, {}).values()], separators=(",", ":")))
    except Exception as exc:                                       # noqa: BLE001
        log.error("intraday: FAILED to persist positions for %s: %s", uid, exc)


def put(uid: str, pos: IntradayPosition) -> IntradayPosition:
    load(uid)[pos.contract.tradingsymbol] = pos
    persist(uid)
    return pos


def get(uid: str, symbol: str) -> Optional[IntradayPosition]:
    return load(uid).get(symbol)


def open_positions(uid: str) -> list[IntradayPosition]:
    return [p for p in load(uid).values() if p.is_open]


def mark_filled(uid: str, symbol: str, fill_price: float, *,
                gtt_id: int = 0) -> Optional[IntradayPosition]:
    """Record the real fill and slide the stop by the same drift.

    A fill worse than the limit does not make the trade riskier by itself — the
    stop was chosen as a distance, not as an absolute price — so the distance is
    what is preserved. Leaving the stop where it was would silently widen or
    tighten the risk by the slippage.
    """
    pos = get(uid, symbol)
    if pos is None:
        return None
    pos.status = OPEN
    if fill_price > 0:
        drift = fill_price - pos.entry
        pos.fill_price = fill_price
        if drift and pos.stop > 0:
            pos.stop = round(pos.stop + drift, 2)
            pos.initial_stop = round((pos.initial_stop or pos.stop) + drift, 2)
        if pos.target > 0 and drift:
            pos.target = round(pos.target + drift, 2)
        pos.peak = max(pos.peak, fill_price)
    if gtt_id:
        pos.gtt_id = int(gtt_id)
    persist(uid)
    return pos


def mark_rejected(uid: str, symbol: str, reason: str = "") -> Optional[IntradayPosition]:
    pos = get(uid, symbol)
    if pos is None:
        return None
    pos.status = REJECTED
    pos.exit_reason = reason
    persist(uid)
    return pos


def close(uid: str, symbol: str, reason: str = "",
          exit_price: float = 0.0) -> Optional[IntradayPosition]:
    pos = get(uid, symbol)
    if pos is None:
        return None
    pos.status = CLOSED
    pos.exit_reason = reason
    if exit_price > 0:
        pos.exit_price = float(exit_price)
        pos.realised_inr = round(
            (float(exit_price) - pos.effective_entry) * pos.quantity, 2)
    pos.exiting = False
    persist(uid)
    return pos


def forget_closed(uid: str) -> None:
    _cache[uid] = {k: v for k, v in load(uid).items() if v.is_open}
    persist(uid)


def closed_today(uid: str, day: str) -> list[IntradayPosition]:
    return [p for p in load(uid).values()
            if p.status == CLOSED and p.entry_day == day]


def load_record(uid: str) -> DayRecord:
    if uid in _record_cache:
        return _record_cache[uid]
    rec = DayRecord()
    try:
        from app.services import db
        raw = db.get_config(_record_key(uid))
        if raw:
            data = json.loads(raw) if isinstance(raw, str) else raw
            rec = DayRecord.from_dict(data if isinstance(data, dict) else {})
    except Exception as exc:                                       # noqa: BLE001
        log.error("intraday: day record unreadable for %s: %s", uid, exc)
    _record_cache[uid] = rec
    return rec


def save_record(uid: str, record: DayRecord) -> DayRecord:
    _record_cache[uid] = record
    try:
        from app.services import db
        db.set_config(_record_key(uid),
                      json.dumps(record.as_dict(), separators=(",", ":")))
    except Exception as exc:                                       # noqa: BLE001
        log.error("intraday: FAILED to persist the day record for %s: %s", uid, exc)
    return record


def reset(uid: str = "") -> None:
    if uid:
        _cache.pop(uid, None)
        _record_cache.pop(uid, None)
    else:
        _cache.clear()
        _record_cache.clear()
