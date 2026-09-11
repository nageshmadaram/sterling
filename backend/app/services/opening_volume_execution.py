"""Fail-closed option execution for Sterling Opening Leaders.

The scanner remains independent from order submission.  This module consumes a
completed scan only when every Sterling decision gate passes and the repository's
global algo mode, router mode, Kite auto-execute switch, broker state, quote,
risk sizing, idempotency, and protection checks all agree.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.engines.opening_volume_leaders import IST


async def _find_contract(client, symbol: str, underlying: str):
    for exchange in ("NFO", "BFO"):
        try:
            rows = await client.search_instruments(underlying, exchange, limit=10000)
        except Exception:
            continue
        for row in rows or []:
            if str(row.get("tradingsymbol") or "").upper() == symbol.upper():
                return exchange, row
    return None, None


async def _existing_order_by_tag(client, tag: str):
    try:
        orders = await client.get_orders()
    except Exception:
        return False, None
    return True, next((o for o in orders or [] if str(o.get("tag") or "") == tag), None)


def _parse_timestamp(value: Any):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=IST)
    if isinstance(value, (int, float)):
        x = float(value)
        x = x / 1000 if x > 10_000_000_000 else x
        return datetime.fromtimestamp(x, tz=timezone.utc).astimezone(IST)
    s = str(value).strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=IST)
    except ValueError:
        return None


async def _resolve_fill(client, order_id: str, timeout_s: float = 5.0):
    deadline = asyncio.get_running_loop().time() + timeout_s
    last_status = "UNKNOWN"
    while True:
        latest = {}
        try:
            h = await client.get_order_history(order_id)
            latest = h[-1] if isinstance(h, list) and h else (h if isinstance(h, dict) else {})
        except Exception:
            pass
        status = str(latest.get("status") or "").upper()
        last_status = status or last_status
        filled = int(float(latest.get("filled_quantity") or latest.get("filled_qty") or 0))
        avg = float(latest.get("average_price") or latest.get("average_price_filled") or 0)
        try:
            trades = await client.get_order_trades(order_id)
            if trades:
                q = sum(int(float(t.get("quantity") or 0)) for t in trades)
                v = sum(float(t.get("quantity") or 0) * float(t.get("average_price") or t.get("price") or 0) for t in trades)
                if q:
                    filled, avg = q, v / q
        except Exception:
            pass
        if status in {"COMPLETE", "PARTIALLY FILLED", "PARTIAL", "CANCELLED", "REJECTED", "EXPIRED"} or filled > 0:
            return filled, avg, status or last_status
        if asyncio.get_running_loop().time() >= deadline:
            return filled, avg, last_status
        await asyncio.sleep(0.25)


async def _cancel_and_reconcile(client, order_id: str, expected_total: int):
    try:
        await client.cancel_order(order_id)
    except Exception:
        return (*await _resolve_fill(client, order_id, 2.0), False)
    filled, avg, status = await _resolve_fill(client, order_id, 3.0)
    return filled, avg, status, (status in {"CANCELLED", "REJECTED", "EXPIRED"} and filled < expected_total) or filled >= expected_total


async def _sell_and_verify(client, symbol: str, exchange: str, quantity: int):
    if quantity <= 0:
        return True, "nothing to close"
    try:
        r = await client.place_order_option(symbol, "sell", quantity, exchange=exchange, tag="OPENING-PROTECTION-FAIL-CLOSE")
        oid = str((r or {}).get("order_id") or (r or {}).get("orderId") or "")
        if not oid:
            return False, "emergency close returned no order id"
        filled, _, status = await _resolve_fill(client, oid, 5.0)
        return (filled >= quantity, f"closed {filled}/{quantity} ({status})")
    except Exception as exc:
        return False, f"emergency close failed: {exc}"


def _quote_age(value: Any):
    ts = _parse_timestamp(value)
    return None if ts is None else max(0, (datetime.now(IST) - ts.astimezone(IST)).total_seconds())


async def _fresh_quote(client, exchange, symbol, max_age_s, max_spread_pct):
    key = f"{exchange}:{symbol}"
    payload = await client.get_quote([key])
    q = (payload or {}).get(key)
    if not q:
        raise RuntimeError("live option quote unavailable")
    dep = q.get("depth") or {}
    buys = dep.get("buy") or []
    sells = dep.get("sell") or []
    bid = float((buys[0] if buys else {}).get("price") or 0)
    ask = float((sells[0] if sells else {}).get("price") or 0)
    ltp = float(q.get("last_price") or 0)
    if bid <= 0 or ask <= 0 or ask < bid or ltp <= 0:
        raise RuntimeError("option quote is not executable")
    age = _quote_age(q.get("timestamp") or q.get("last_trade_time"))
    if age is None or age > max_age_s:
        raise RuntimeError(f"option quote stale/untimestamped: age={age}")
    mid = (bid + ask) / 2
    spread = (ask - bid) / mid * 100 if mid else float("inf")
    if spread > max_spread_pct:
        raise RuntimeError(f"spread {spread:.2f}% exceeds {max_spread_pct:.2f}%")
    return {"bid": bid, "ask": ask, "ltp": ltp, "spread_pct": spread, "age_s": age, "volume": float(q.get("volume") or 0), "oi": float(q.get("oi") or 0)}


@dataclass(frozen=True)
class OpeningExecutionConfig:
    enabled: bool = True
    min_score: float = 55.0
    min_conviction: int = 5
    max_trades_per_day: int = 2
    risk_pct: float = 1.0
    max_lots: int = 2
    max_quote_staleness_s: float = 5.0
    max_spread_pct: float = 5.0
    max_underlying_drift_pct: float = 0.30
    min_dte: int = 2

    def validate(self) -> OpeningExecutionConfig:
        if not 0 <= self.min_score <= 100:
            raise ValueError("min_score must be between 0 and 100")
        if not 1 <= self.min_conviction <= 7:
            raise ValueError("min_conviction must be between 1 and 7")
        if not 1 <= self.max_trades_per_day <= 10:
            raise ValueError("max_trades_per_day must be between 1 and 10")
        if not 0 < self.risk_pct <= 2:
            raise ValueError("risk_pct must be greater than 0 and at most 2")
        if not 1 <= self.max_lots <= 10:
            raise ValueError("max_lots must be between 1 and 10")
        if not 1 <= self.max_quote_staleness_s <= 30:
            raise ValueError("max_quote_staleness_s must be between 1 and 30")
        if not 0 < self.max_spread_pct <= 20:
            raise ValueError("max_spread_pct must be greater than 0 and at most 20")
        if not 0 < self.max_underlying_drift_pct <= 2:
            raise ValueError("max_underlying_drift_pct must be greater than 0 and at most 2")
        if not 0 <= self.min_dte <= 30:
            raise ValueError("min_dte must be between 0 and 30")
        return self


def _config_key(uid: str) -> str:
    return f"opening_volume_execution_config:{uid}"


def get_config(uid: str) -> OpeningExecutionConfig:
    from app.services import db

    raw = db.get_config(_config_key(uid))
    if not raw:
        return OpeningExecutionConfig()
    try:
        payload = json.loads(raw)
        allowed = {field.name for field in fields(OpeningExecutionConfig)}
        return OpeningExecutionConfig(
            **{key: value for key, value in payload.items() if key in allowed}
        ).validate()
    except Exception as exc:
        raise RuntimeError(f"opening-leader execution config is invalid: {exc}") from exc


def set_config(uid: str, values: dict[str, Any]) -> OpeningExecutionConfig:
    from app.services import db

    allowed = {field.name for field in fields(OpeningExecutionConfig)}
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError("unknown execution settings: " + ", ".join(unknown))
    config = OpeningExecutionConfig(**{**asdict(get_config(uid)), **values}).validate()
    db.set_config(_config_key(uid), json.dumps(asdict(config), separators=(",", ":")))
    return config


def _trade_state(uid: str, today: str) -> dict[str, Any]:
    from app.services import db

    key = f"opening_volume_execution_state:{uid}"
    try:
        raw = db.get_config(key)
        state = json.loads(raw) if raw else {}
    except Exception as exc:
        raise RuntimeError(f"opening-leader trade state unavailable: {exc}") from exc
    if state.get("date") != today:
        state = {"date": today, "count": 0, "signals": []}
    state.setdefault("signals", [])
    return state


def _save_trade_state(uid: str, state: dict[str, Any]) -> None:
    from app.services import db

    db.set_config(
        f"opening_volume_execution_state:{uid}",
        json.dumps(state, separators=(",", ":")),
    )


def eligible_candidates(
    scan: dict[str, Any],
    config: OpeningExecutionConfig,
) -> list[dict[str, Any]]:
    """Pure final gate. Unknown evidence and replay quotes fail closed."""

    config.validate()
    eligible: list[dict[str, Any]] = []
    for row in scan.get("leaders") or []:
        decision = row.get("decision") or {}
        score = decision.get("score") or {}
        conviction = decision.get("conviction") or {}
        option = row.get("option") or {}
        if not decision.get("execution_eligible"):
            continue
        if float(score.get("lower_bound") or 0.0) < config.min_score:
            continue
        if int(conviction.get("passed") or 0) < config.min_conviction:
            continue
        if row.get("option_status") != "quoted" or not option:
            continue
        if bool(option.get("beginner_expiry_warning")):
            continue
        if int(option.get("dte") or -1) < config.min_dte:
            continue
        eligible.append(row)
    return eligible


async def execute_opening_scan(
    uid: str,
    *,
    scan: dict[str, Any],
    config: OpeningExecutionConfig | None = None,
) -> dict[str, Any]:
    """Submit and protect eligible option buys; never bypass a failed gate."""

    from app.services import live_safety
    from app.services.exchanges.kite import accounts
    from app.services.kite_engine import positions, protection, state as engine_state
    from app.services.kite_engine.service import available_fo_capital
    from app.services.kite_engine.sizing import size_position

    config = (config or get_config(uid)).validate()
    if not config.enabled:
        return {"status": "disabled", "executed": []}
    universal = engine_state.get_config(uid)
    if not universal.auto_execute:
        return {"status": "blocked", "reason": "Kite auto-execute is off", "executed": []}
    account = accounts.get_active(uid)
    if not account:
        return {"status": "blocked", "reason": "no active Kite account", "executed": []}
    execution_mode = "paper" if account.is_paper else "live"

    now = datetime.now(IST)
    entry_start = datetime.strptime("09:25", "%H:%M").time()
    entry_end = datetime.strptime("10:30", "%H:%M").time()
    if now.weekday() >= 5 or not (entry_start <= now.time() <= entry_end):
        return {"status": "outside_entry_window", "executed": []}
    if scan.get("enrichment", {}).get("historical_quotes_omitted"):
        return {"status": "blocked", "reason": "replay scans cannot execute", "executed": []}

    state = _trade_state(uid, now.date().isoformat())
    if int(state["count"]) >= config.max_trades_per_day:
        return {"status": "daily_limit", "count": state["count"], "executed": []}
    client = await accounts.acquire_client(account)
    capital = await available_fo_capital(client)
    if capital <= 0:
        return {
            "status": "blocked",
            "reason": "available F&O capital is unavailable",
            "executed": [],
        }

    open_underlyings = {
        str(position.underlying).upper()
        for position in positions.open_positions(uid)
        if position.status in (positions.OPEN, positions.PENDING)
    }
    results: list[dict[str, Any]] = []
    for row in eligible_candidates(scan, config):
        if int(state["count"]) >= config.max_trades_per_day:
            break
        underlying = str(row.get("symbol") or "").upper()
        option = row.get("option") or {}
        symbol = str(option.get("tradingsymbol") or "").upper()
        direction = str(row.get("direction") or "").upper()
        expected_type = "CE" if direction == "UP" else "PE" if direction == "DOWN" else ""
        if not underlying or underlying in open_underlyings or not symbol:
            continue
        if str(option.get("option_type") or "").upper() != expected_type:
            results.append(
                {
                    "status": "blocked",
                    "symbol": symbol,
                    "reason": "option direction mismatch",
                }
            )
            continue
        signal_key = str(row.get("signal_key") or "")
        if not signal_key or signal_key in state["signals"]:
            continue
        idempotency = live_safety.make_idempotency_key(uid, signal_key, "BUY")
        # Kite truncates tags to 20 characters. Broker reconciliation must use
        # the exact transmitted tag while the local safety registry retains the
        # full idempotency key.
        broker_tag = idempotency[:20]
        safety = live_safety.assert_safe_to_trade(
            positions.open_positions(uid), idempotency, check_daily_loss=True, uid=uid
        )
        if not safety.allowed:
            results.append(
                {
                    "status": "blocked",
                    "symbol": symbol,
                    "reason": safety.reason,
                    "code": safety.code,
                }
            )
            continue
        exchange, instrument = await _find_contract(client, symbol, underlying)
        if not exchange or not instrument:
            results.append(
                {
                    "status": "blocked",
                    "symbol": symbol,
                    "reason": "contract no longer exists",
                }
            )
            continue
        if str(instrument.get("instrument_type") or "").upper() != expected_type:
            results.append(
                {
                    "status": "blocked",
                    "symbol": symbol,
                    "reason": "broker option type mismatch",
                }
            )
            continue
        try:
            broker_strike = float(instrument.get("strike") or 0.0)
            displayed_strike = float(option.get("strike") or 0.0)
            broker_expiry = datetime.strptime(
                str(instrument.get("expiry"))[:10], "%Y-%m-%d"
            ).date()
        except (TypeError, ValueError):
            results.append(
                {
                    "status": "blocked",
                    "symbol": symbol,
                    "reason": "invalid broker contract metadata",
                }
            )
            continue
        if displayed_strike <= 0 or abs(broker_strike - displayed_strike) > 0.001:
            results.append(
                {"status": "blocked", "symbol": symbol, "reason": "broker strike mismatch"}
            )
            continue
        if str(option.get("expiry") or "")[:10] != broker_expiry.isoformat():
            results.append(
                {"status": "blocked", "symbol": symbol, "reason": "broker expiry mismatch"}
            )
            continue
        if (broker_expiry - now.date()).days < config.min_dte:
            results.append(
                {
                    "status": "blocked",
                    "symbol": symbol,
                    "reason": "contract inside minimum DTE",
                }
            )
            continue
        try:
            quote = await _fresh_quote(
                client,
                exchange,
                symbol,
                config.max_quote_staleness_s,
                config.max_spread_pct,
            )
        except Exception as exc:
            results.append(
                {
                    "status": "blocked",
                    "symbol": symbol,
                    "reason": f"quote validation failed: {exc}",
                }
            )
            continue
        observed_at = _parse_timestamp(row.get("observed_at"))
        snapshot_age = (
            None
            if observed_at is None
            else (now - observed_at.astimezone(IST)).total_seconds()
        )
        if snapshot_age is None or snapshot_age < 0 or snapshot_age > 120:
            results.append(
                {"status": "blocked", "symbol": symbol, "reason": "scan snapshot is stale"}
            )
            continue
        reference_spot = float(row.get("live_price") or row.get("current_price") or 0.0)
        try:
            spot_payload = await client.get_ltp([f"NSE:{underlying}"])
            current_spot = float(
                ((spot_payload or {}).get(f"NSE:{underlying}") or {}).get("last_price")
                or 0.0
            )
        except Exception as exc:
            results.append(
                {
                    "status": "blocked",
                    "symbol": symbol,
                    "reason": f"underlying quote failed: {exc}",
                }
            )
            continue
        if reference_spot <= 0 or current_spot <= 0:
            results.append(
                {
                    "status": "blocked",
                    "symbol": symbol,
                    "reason": "underlying price unavailable",
                }
            )
            continue
        drift_pct = abs(current_spot - reference_spot) / reference_spot * 100.0
        if drift_pct > config.max_underlying_drift_pct:
            results.append(
                {
                    "status": "blocked",
                    "symbol": symbol,
                    "reason": "underlying moved beyond execution tolerance",
                }
            )
            continue
        orb_level = float(row.get("orb_break_level") or 0.0)
        if (
            orb_level <= 0
            or direction == "UP" and current_spot < orb_level
            or direction == "DOWN" and current_spot > orb_level
        ):
            results.append(
                {
                    "status": "blocked",
                    "symbol": symbol,
                    "reason": "underlying no longer holds the ORB trigger",
                }
            )
            continue
        lot_size = int(instrument.get("lot_size") or option.get("lot_size") or 0)
        stop = quote["ask"] * 0.70
        recommended_risk = float(
            (row.get("playbook") or {}).get("recommended_risk_pct") or 0.0
        )
        risk_pct = min(config.risk_pct, recommended_risk)
        if risk_pct <= 0:
            results.append(
                {
                    "status": "blocked",
                    "symbol": symbol,
                    "reason": "playbook risk gate is zero",
                }
            )
            continue
        sizing = size_position(
            entry_premium=quote["ask"],
            stop_premium=stop,
            lot_size=lot_size,
            available_capital=capital,
            risk_pct=risk_pct,
            max_lots=config.max_lots,
            allow_min_lot_over_risk=False,
        )
        if sizing.blocked or sizing.qty <= 0:
            results.append({"status": "blocked", "symbol": symbol, "reason": sizing.reason})
            continue
        broker_ok, existing = await _existing_order_by_tag(client, broker_tag)
        if not broker_ok:
            results.append(
                {
                    "status": "blocked",
                    "symbol": symbol,
                    "reason": "broker order state unavailable",
                }
            )
            continue
        if existing:
            order_id = str(existing.get("order_id") or existing.get("orderId") or "")
            existing_symbol = str(
                existing.get("tradingsymbol") or existing.get("symbol") or ""
            ).upper()
            existing_side = str(
                existing.get("transaction_type") or existing.get("side") or ""
            ).upper()
            if not order_id or existing_symbol != symbol or existing_side != "BUY":
                live_safety.set_kill_switch(True, "Opening Leader broker tag collision")
                results.append({"status": "critical_tag_collision", "symbol": symbol})
                continue
        else:
            try:
                response = await client.place_order_option(
                    symbol,
                    "buy",
                    sizing.qty,
                    exchange=exchange,
                    tag=broker_tag,
                )
            except Exception as exc:
                results.append({"status": "error", "symbol": symbol, "reason": str(exc)})
                continue
            order_id = str(
                (response or {}).get("order_id")
                or (response or {}).get("orderId")
                or ""
            )
            if not order_id:
                live_safety.set_kill_switch(True, "Opening Leader submission outcome unknown")
                results.append({"status": "critical_unknown_order", "symbol": symbol})
                continue
            live_safety.record_idempotency(idempotency, order_id)

        if account.is_paper or order_id.startswith("PAPER-"):
            filled, fill_price, broker_status = sizing.qty, quote["ask"], "COMPLETE"
        else:
            filled, fill_price, broker_status = await _resolve_fill(client, order_id)
            if filled < sizing.qty and broker_status not in {"CANCELLED", "REJECTED", "EXPIRED"}:
                filled, fill_price, broker_status, safe = await _cancel_and_reconcile(
                    client, order_id, sizing.qty
                )
                if not safe:
                    live_safety.set_kill_switch(True, "Opening Leader partial-fill state uncertain")
                    results.append(
                        {
                            "status": "critical_unknown_position",
                            "symbol": symbol,
                            "order_id": order_id,
                        }
                    )
                    continue
        if filled <= 0:
            results.append(
                {
                    "status": "unfilled",
                    "symbol": symbol,
                    "order_id": order_id,
                    "broker_status": broker_status,
                }
            )
            continue

        armed = await protection.arm_position(
            client,
            uid,
            symbol=symbol,
            exchange=exchange,
            token=int(instrument.get("instrument_token") or 0),
            qty=filled,
            lot_size=lot_size,
            entry_premium=fill_price or quote["ask"],
            stop_premium=(fill_price or quote["ask"]) * 0.70,
            order_id=order_id,
            stop_mode=universal.stop_mode,
            direction="long",
            signal_direction="long" if direction == "UP" else "short",
            vehicle="otm_options",
            underlying=underlying,
            exit_mode=universal.exit_mode,
            entry_spot=current_spot,
            entry_delta=0.5,
            strike=float(option.get("strike") or 0.0),
            expiry=str(option.get("expiry") or "")[:10],
            target_premium=(fill_price or quote["ask"]) * 1.50,
        )
        if not armed.protected:
            closed, note = await _sell_and_verify(client, symbol, exchange, filled)
            if not closed:
                live_safety.set_kill_switch(
                    True, f"unprotected Opening Leader position {symbol}: {note}"
                )
            results.append(
                {
                    "status": "protection_failed",
                    "symbol": symbol,
                    "order_id": order_id,
                    "closed": closed,
                    "reason": armed.describe(),
                }
            )
            continue

        state["count"] = int(state["count"]) + 1
        state["signals"].append(signal_key)
        try:
            _save_trade_state(uid, state)
        except Exception as exc:
            live_safety.set_kill_switch(
                True, f"Opening Leader trade count persistence failed: {exc}"
            )
            results.append(
                {
                    "status": "executed_count_not_persisted",
                    "symbol": symbol,
                    "order_id": order_id,
                }
            )
            open_underlyings.add(underlying)
            continue
        open_underlyings.add(underlying)
        capital = max(0.0, capital - (fill_price or quote["ask"]) * filled)
        results.append(
            {
                "status": "executed",
                "mode": execution_mode,
                "underlying": underlying,
                "symbol": symbol,
                "quantity": filled,
                "fill_price": fill_price or quote["ask"],
                "order_id": order_id,
                "protected": True,
                "estimated_risk_inr": sizing.est_risk,
                "score": (row.get("decision") or {}).get("score", {}).get("lower_bound"),
            }
        )
    status = (
        "executed"
        if any(row.get("status") == "executed" for row in results)
        else "no_trade"
    )
    return {"status": status, "executed": results, "count": state["count"]}
