"""ORB process lifecycle: restart recovery, protection disarm, expiry square-off.

Manual and Auto share one signal/ticket. This module does not generate signals.
It keeps live state honest across restarts and exits, and names the ticket both
paths must use.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.core.logging import get_logger

log = get_logger(__name__)
IST = ZoneInfo("Asia/Kolkata")
ORB_VEHICLES = frozenset({"otm_options", "deep_itm_options"})

# Fields that must be identical on the Manual board ticket and the Auto order.
# A divergence here is a product bug, not a cosmetic difference.
SAME_TICKET_FIELDS = (
    "symbol",
    "option_type",
    "strike",
    "expiry",
    "quantity",
    "underlying_entry",
    "stop_premium",
    "target_premium",
    "lot_size",
)


def ticket_fields(plan: dict[str, Any]) -> dict[str, Any]:
    """Canonical ticket the Manual board and Auto both consume."""
    contract = plan.get("contract") or {}
    expiry = str(contract.get("expiry") or "")[:10]
    return {
        "symbol": str(contract.get("symbol") or ""),
        "option_type": str(contract.get("option_type") or ""),
        "strike": float(contract.get("strike") or 0) or None,
        "expiry": expiry or None,
        "quantity": int(plan.get("quantity") or 0),
        "underlying_entry": float(plan.get("underlying_entry") or 0) or None,
        "stop_premium": float(plan.get("stop_premium") or 0) or None,
        "target_premium": float(plan.get("target_premium") or 0) or None,
        "lot_size": int(contract.get("lot_size") or 0),
    }


def ticket_fingerprint(plan: dict[str, Any], signal: dict[str, Any]) -> str:
    """Stable id for the Manual board ticket and the Auto order.

    Covers every ``SAME_TICKET_FIELDS`` value plus direction/timestamp so a
    change in qty, SL, lot, or underlying entry is a different ticket.
    """
    fields = ticket_fields(plan)
    return "|".join(
        [
            str(signal.get("direction") or ""),
            str(signal.get("timestamp") or ""),
            *[str(fields[k] or "") for k in SAME_TICKET_FIELDS],
        ]
    )


def attach_ticket(row: dict[str, Any]) -> dict[str, Any]:
    """Stamp fingerprint + same-ticket fields onto a scan row. Mutates and returns."""
    plan = row.get("trade") or {}
    signal = row.get("signal") or {}
    if not plan or not signal:
        return row
    row["ticket"] = ticket_fields(plan)
    row["ticket_fingerprint"] = ticket_fingerprint(plan, signal)
    return row


def manual_mode_response() -> dict[str, Any]:
    """Auto is off: show signals only; user places the same ticket."""
    return {
        "status": "manual",
        "mode": "signals_only",
        "message": "Auto off — board shows the trade ticket; place Buy yourself. Same signal Auto would trade.",
        "executed": [],
    }


def preview_auto_refusal(
    row: dict[str, Any],
    cfg,
    *,
    now: datetime,
    filled_today: int = 0,
    max_trades: int = 2,
) -> str | None:
    """Reasons Auto would refuse this scan row, without a broker call.

    Reason strings match ``execute_scan`` so Manual shows the same refusal Auto
    would emit. Broker-only gates (drift, live quote, contract search) stay on
    the order path — they cannot be previewed honestly from the scan snapshot.
    """
    from app.services.nifty_orb_execution import (
        _conservative_quantity,
        _entry_window_open,
        _market_open,
        _parse_timestamp,
    )

    if row.get("status") != "signal":
        return None
    if not _market_open(now):
        return "market_closed"
    plan = row.get("trade") or {}
    contract = plan.get("contract") or {}
    signal = row.get("signal") or {}
    direction = str(signal.get("direction") or "")
    expected = "CE" if direction == "LONG" else "PE" if direction == "SHORT" else ""
    if expected != str(contract.get("option_type") or ""):
        return "option direction mismatch"
    ts = _parse_timestamp(signal.get("timestamp"))
    age = None if ts is None else max(0.0, (now.astimezone(IST) - ts.astimezone(IST)).total_seconds())
    if age is None or age > cfg.interval_minutes * 60:
        return f"signal stale/invalid age={age}"
    if int(filled_today) >= int(max_trades):
        return "daily trade limit reached"
    if not _entry_window_open(now, cfg):
        return "outside entry window"
    try:
        expiry = datetime.strptime(str(contract.get("expiry") or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return "invalid contract expiry"
    dte = (expiry - now.astimezone(IST).date()).days
    if dte < cfg.expiry_dte_min or dte > cfg.expiry_dte_max or (cfg.avoid_expiry_day and dte == 0):
        return "contract outside configured expiry policy"
    requested = int(plan.get("quantity") or 0)
    lot = int(contract.get("lot_size") or 0)
    ask = float(contract.get("ask") or 0)
    if requested <= 0:
        return "one option lot exceeds conservative premium risk budget"
    if ask > 0 and lot > 0:
        sized = _conservative_quantity(requested, lot, ask, float(cfg.max_risk_inr))
        if sized <= 0:
            return "one option lot exceeds conservative premium risk budget"
        if sized != requested:
            return "live premium would change the ticket quantity"
    vol = float(contract.get("volume") or 0)
    oi = float(contract.get("open_interest") or 0)
    # 0/0 is the OptionContract default, not a measurement. A positive print
    # below the floor is what Auto would refuse from the live quote too.
    if (vol > 0 and vol < cfg.min_option_volume) or (oi > 0 and oi < cfg.min_open_interest):
        return "option liquidity below configured minimum"
    return None


def orb_open_positions(uid: str) -> list:
    """Positions this strategy opened — not every untagged kite row.

    Empty ``vehicle`` used to match SuperTrend / equity leftovers and let
    expiry square-off and restart recovery touch them. An ORB fill tags
    ``otm_options`` / ``deep_itm_options``, or carries ORB on the order id.
    """
    from app.services.kite_engine import positions

    out = []
    for p in positions.open_positions(uid):
        vehicle = str(getattr(p, "vehicle", "") or "")
        oid = str(getattr(p, "order_id", "") or "").upper()
        guard = str(getattr(p, "guard_key", "") or "").upper()
        if vehicle in ORB_VEHICLES or "ORB" in oid or "ORB" in guard:
            out.append(p)
    return out


def recover_trade_state(uid: str) -> dict[str, Any]:
    """Rebuild ORB day-count visibility after a process restart."""
    from app.services.nifty_orb_execution import _save_state, _state

    state = _state(uid)
    open_orb = [
        p
        for p in orb_open_positions(uid)
        if str(getattr(p, "vehicle", "") or "") in ORB_VEHICLES
        or "ORB" in str(getattr(p, "order_id", "") or "").upper()
        or "ORB" in str(getattr(p, "guard_key", "") or "").upper()
    ]
    underlyings = sorted(
        {str(getattr(p, "underlying", "") or "").upper() for p in open_orb if getattr(p, "underlying", "")}
    )
    state["recovered_open"] = [
        {
            "symbol": p.symbol,
            "underlying": getattr(p, "underlying", ""),
            "qty": int(p.qty or 0),
            "status": p.status,
            "expiry": getattr(p, "expiry", ""),
            "gtt_id": int(getattr(p, "gtt_id", 0) or 0),
        }
        for p in open_orb
    ]
    state["recovered_underlyings"] = underlyings
    _save_state(uid, state)
    return {
        "status": "recovered",
        "date": state.get("date"),
        "count": int(state.get("count", 0)),
        "open_orb": len(open_orb),
        "underlyings": underlyings,
    }


async def resubscribe_protection(uid: str) -> dict[str, Any]:
    """Re-attach tick subscriptions for open ORB positions after restart."""
    from app.services.exchanges.kite import constants as K
    from app.services.exchanges.kite import ticker_manager

    tokens = [int(p.token) for p in orb_open_positions(uid) if int(getattr(p, "token", 0) or 0)]
    if not tokens:
        return {"status": "nothing_to_subscribe", "tokens": []}
    try:
        await ticker_manager.subscribe(uid, tokens, mode=K.MODE_LTP)
        return {"status": "subscribed", "tokens": tokens}
    except Exception as exc:  # noqa: BLE001
        log.warning("ORB resubscribe failed for %s: %s", uid, exc)
        return {"status": "subscribe_failed", "tokens": tokens, "error": str(exc)}


async def recover_after_restart(uid: str) -> dict[str, Any]:
    """Full restart recovery for one user — call once when the ORB runner starts."""
    trade = recover_trade_state(uid)
    ticks = await resubscribe_protection(uid)
    return {"trade_state": trade, "ticks": ticks}


async def disarm_position(client, uid: str, *, symbol: str, reason: str = "disarmed") -> dict[str, Any]:
    """Cancel broker GTT (if any), drop tick watch, close registry row."""
    from app.services.kite_engine import positions, protective_stop, state

    p = positions.get(uid, symbol)
    if p is None:
        return {"status": "missing", "symbol": symbol}
    gtt_id = int(getattr(p, "gtt_id", 0) or 0)
    cancel_outcome = "none"
    if gtt_id:
        try:
            cancel_outcome = await protective_stop.cancel_stop_result(client, gtt_id)
        except Exception as exc:  # noqa: BLE001
            cancel_outcome = f"error:{exc}"
            log.warning("ORB disarm GTT cancel failed %s #%s: %s", symbol, gtt_id, exc)
    token = int(getattr(p, "token", 0) or 0)
    if token:
        try:
            from app.services.exchanges.kite import ticker_manager

            unsub = getattr(ticker_manager, "unsubscribe", None)
            if callable(unsub):
                await unsub(uid, [token])
        except Exception as exc:  # noqa: BLE001
            log.debug("ORB disarm unsubscribe %s: %s", symbol, exc)
    positions.close(uid, symbol, reason=reason)
    state.log(uid, "info", f"ORB protection disarmed for {symbol}: {reason} (gtt={cancel_outcome})")
    return {
        "status": "disarmed",
        "symbol": symbol,
        "gtt_id": gtt_id,
        "cancel": cancel_outcome,
        "reason": reason,
    }


async def square_off_expired(client, uid: str, *, today: datetime | None = None) -> dict[str, Any]:
    """Market-sell ORB option positions whose expiry is today or earlier (IST)."""
    from app.services.kite_engine import state
    from app.services.nifty_orb_execution import _sell_and_verify

    now = today or datetime.now(IST)
    session = now.astimezone(IST).date()
    closed: list[dict[str, Any]] = []
    for p in list(orb_open_positions(uid)):
        expiry_s = str(getattr(p, "expiry", "") or "")[:10]
        if not expiry_s:
            continue
        try:
            expiry = datetime.strptime(expiry_s, "%Y-%m-%d").date()
        except ValueError:
            continue
        if expiry > session:
            continue
        qty = int(p.qty or 0)
        ok, note = await _sell_and_verify(client, p.symbol, p.exchange, qty)
        disarm = await disarm_position(
            client, uid, symbol=p.symbol, reason=f"expiry_square_off:{expiry_s}"
        )
        if ok:
            state.log(uid, "info", f"ORB expiry square-off {p.symbol}: {note}")
        else:
            state.log(uid, "order_failed", f"ORB expiry square-off FAILED {p.symbol}: {note}")
        closed.append(
            {
                "symbol": p.symbol,
                "expiry": expiry_s,
                "sold": ok,
                "note": note,
                "disarm": disarm,
            }
        )
    return {"status": "ok", "squared": closed}
