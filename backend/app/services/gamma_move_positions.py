"""Durable open-position registry for Gamma Move."""
from __future__ import annotations

import json
from dataclasses import asdict, fields
from typing import Optional

from app.core.logging import get_logger
from app.engines.gamma_move import InstrumentRef, PositionState, TradeRecord

log = get_logger(__name__)

PENDING, OPEN, CLOSED, REJECTED = "pending", "open", "closed", "rejected"

_cache: dict[str, dict[str, PositionState]] = {}
_record_cache: dict[str, TradeRecord] = {}


def _key(uid: str) -> str:
    return f"gamma_move_positions_{uid}"


def _record_key(uid: str) -> str:
    return f"gamma_move_record_{uid}"


def _to_dict(p: PositionState) -> dict:
    d = asdict(p)
    d["instrument"] = asdict(p.instrument)
    return d


def _from_dict(d: dict) -> Optional[PositionState]:
    try:
        inst_fields = {f.name for f in fields(InstrumentRef)}
        inst = InstrumentRef(**{k: v for k, v in (d.get("instrument") or {}).items()
                                if k in inst_fields})
        known = {f.name for f in fields(PositionState)} - {"instrument"}
        return PositionState(instrument=inst,
                             **{k: v for k, v in d.items() if k in known})
    except (TypeError, ValueError) as exc:
        log.error("gamma_move: unreadable persisted position dropped (%s): %s", exc, d)
        return None


def load(uid: str) -> dict[str, PositionState]:
    if uid in _cache:
        return _cache[uid]
    out: dict[str, PositionState] = {}
    try:
        from app.services import db
        raw = db.get_config(_key(uid))
        for d in (json.loads(raw) if raw else []):
            p = _from_dict(d)
            if p is not None:
                out[p.instrument.tradingsymbol] = p
    except Exception as exc:                                       # noqa: BLE001
        log.error("gamma_move: position registry unreadable for %s: %s", uid, exc)
    _cache[uid] = out
    return out


def persist(uid: str) -> None:
    try:
        from app.services import db
        db.set_config(_key(uid), json.dumps(
            [_to_dict(p) for p in _cache.get(uid, {}).values()], separators=(",", ":")))
    except Exception as exc:                                       # noqa: BLE001
        log.error("gamma_move: FAILED to persist positions for %s: %s", uid, exc)


def put(uid: str, pos: PositionState) -> PositionState:
    load(uid)[pos.instrument.tradingsymbol] = pos
    persist(uid)
    return pos


def get(uid: str, symbol: str) -> Optional[PositionState]:
    return load(uid).get(symbol)


def open_positions(uid: str) -> list[PositionState]:
    return [p for p in load(uid).values() if p.is_open]


def mark_filled(uid: str, symbol: str, fill_price: float, *,
                gtt_id: int = 0) -> Optional[PositionState]:
    pos = get(uid, symbol)
    if pos is None:
        return None
    pos.status = OPEN
    if fill_price > 0:
        drift = fill_price - pos.entry
        pos.fill_price = fill_price
        if drift and pos.stop > 0:
            pos.stop = round(pos.stop + drift, 2)
        pos.high_water = max(pos.high_water, fill_price)
    if gtt_id:
        pos.gtt_id = int(gtt_id)
    persist(uid)
    return pos


def mark_rejected(uid: str, symbol: str, reason: str = "") -> Optional[PositionState]:
    pos = get(uid, symbol)
    if pos is None:
        return None
    pos.status = REJECTED
    pos.exit_reason = reason
    persist(uid)
    return pos


def close(uid: str, symbol: str, reason: str = "") -> Optional[PositionState]:
    pos = get(uid, symbol)
    if pos is None:
        return None
    pos.status = CLOSED
    pos.exit_reason = reason
    persist(uid)
    return pos


def forget_closed(uid: str) -> None:
    live = {k: v for k, v in load(uid).items() if v.is_open}
    _cache[uid] = live
    persist(uid)


def load_record(uid: str) -> TradeRecord:
    if uid in _record_cache:
        return _record_cache[uid]
    rec = TradeRecord()
    try:
        from app.services import db
        raw = db.get_config(_record_key(uid))
        if raw:
            data = json.loads(raw) if isinstance(raw, str) else raw
            rec = TradeRecord.from_dict(data if isinstance(data, dict) else {})
    except Exception as exc:                                       # noqa: BLE001
        log.error("gamma_move: trade record unreadable for %s: %s", uid, exc)
    _record_cache[uid] = rec
    return rec


def save_record(uid: str, record: TradeRecord) -> TradeRecord:
    _record_cache[uid] = record
    try:
        from app.services import db
        db.set_config(_record_key(uid), json.dumps(record.as_dict(), separators=(",", ":")))
    except Exception as exc:                                       # noqa: BLE001
        log.error("gamma_move: FAILED to persist trade record for %s: %s", uid, exc)
    return record


def reset(uid: str = "") -> None:
    if uid:
        _cache.pop(uid, None)
        _record_cache.pop(uid, None)
    else:
        _cache.clear()
        _record_cache.clear()
