"""Fail-closed broker execution for ORB option buying."""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo
IST=ZoneInfo("Asia/Kolkata")

def _state(uid:str)->dict[str,Any]:
    import json
    from app.services import db
    try: raw=db.get_config(f"nifty_orb_options_trade_state:{uid}"); state=json.loads(raw) if raw else {}
    except Exception as exc: raise RuntimeError(f"ORB trade-state unavailable: {exc}") from exc
    today=datetime.now(IST).date().isoformat()
    if state.get("date")!=today: state={"date":today,"count":0,"signals":[]}
    state.setdefault("signals",[]); return state

def _save_state(uid:str,state:dict[str,Any])->None:
    import json
    from app.services import db
    db.set_config(f"nifty_orb_options_trade_state:{uid}",json.dumps(state,separators=(",",":")))

async def _find_contract(client,symbol:str,underlying:str):
    for exchange in ("NFO","BFO"):
        try: rows=await client.search_instruments(underlying,exchange,limit=10000)
        except Exception: continue
        for row in rows or []:
            if str(row.get("tradingsymbol") or "").upper()==symbol.upper(): return exchange,row
    return None,None

async def _existing_order_by_tag(client,tag:str):
    try: orders=await client.get_orders()
    except Exception: return False,None
    return True,next((o for o in orders or [] if str(o.get("tag") or "")==tag),None)

def _parse_timestamp(value:Any):
    if value is None:return None
    if isinstance(value,datetime): return value if value.tzinfo else value.replace(tzinfo=IST)
    if isinstance(value,(int,float)):
        x=float(value); x=x/1000 if x>10_000_000_000 else x
        return datetime.fromtimestamp(x,tz=timezone.utc).astimezone(IST)
    s=str(value).strip().replace("Z","+00:00")
    try:
        d=datetime.fromisoformat(s); return d if d.tzinfo else d.replace(tzinfo=IST)
    except ValueError:return None

async def _resolve_fill(client,order_id:str,timeout_s:float=5.0):
    deadline=asyncio.get_running_loop().time()+timeout_s; last_status="UNKNOWN"
    while True:
        latest={}
        try:
            h=await client.get_order_history(order_id); latest=h[-1] if isinstance(h,list) and h else (h if isinstance(h,dict) else {})
        except Exception: pass
        status=str(latest.get("status") or "").upper(); last_status=status or last_status
        filled=int(float(latest.get("filled_quantity") or latest.get("filled_qty") or 0)); avg=float(latest.get("average_price") or latest.get("average_price_filled") or 0)
        try:
            trades=await client.get_order_trades(order_id)
            if trades:
                q=sum(int(float(t.get("quantity") or 0)) for t in trades); v=sum(float(t.get("quantity") or 0)*float(t.get("average_price") or t.get("price") or 0) for t in trades)
                if q: filled,avg=q,v/q
        except Exception: pass
        if status in {"COMPLETE","PARTIALLY FILLED","PARTIAL","CANCELLED","REJECTED","EXPIRED"} or filled>0:return filled,avg,status or last_status
        if asyncio.get_running_loop().time()>=deadline:return filled,avg,last_status
        await asyncio.sleep(.25)

async def _cancel_and_reconcile(client,order_id:str,expected_total:int):
    try: await client.cancel_order(order_id)
    except Exception: return (*await _resolve_fill(client,order_id,2.0),False)
    filled,avg,status=await _resolve_fill(client,order_id,3.0)
    return filled,avg,status,(status in {"CANCELLED","REJECTED","EXPIRED"} and filled<expected_total) or filled>=expected_total

async def _sell_and_verify(client,symbol:str,exchange:str,quantity:int):
    if quantity<=0:return True,"nothing to close"
    try:
        r=await client.place_order_option(symbol,"sell",quantity,exchange=exchange,tag="ORB-PROTECTION-FAIL-CLOSE")
        oid=str((r or {}).get("order_id") or (r or {}).get("orderId") or "")
        if not oid:return False,"emergency close returned no order id"
        filled,_,status=await _resolve_fill(client,oid,5.0)
        return (filled>=quantity,f"closed {filled}/{quantity} ({status})")
    except Exception as exc:return False,f"emergency close failed: {exc}"

def _quote_age(value:Any):
    ts=_parse_timestamp(value)
    return None if ts is None else max(0,(datetime.now(IST)-ts.astimezone(IST)).total_seconds())

async def _fresh_quote(client,exchange,symbol,max_age_s,max_spread_pct):
    key=f"{exchange}:{symbol}"; payload=await client.get_quote([key]); q=(payload or {}).get(key)
    if not q:raise RuntimeError("live option quote unavailable")
    dep=q.get("depth") or {}; buys=dep.get("buy") or []; sells=dep.get("sell") or []
    bid=float((buys[0] if buys else {}).get("price") or 0); ask=float((sells[0] if sells else {}).get("price") or 0); ltp=float(q.get("last_price") or 0)
    if bid<=0 or ask<=0 or ask<bid or ltp<=0:raise RuntimeError("option quote is not executable")
    age=_quote_age(q.get("timestamp") or q.get("last_trade_time"))
    if age is None or age>max_age_s:raise RuntimeError(f"option quote stale/untimestamped: age={age}")
    mid=(bid+ask)/2; spread=(ask-bid)/mid*100 if mid else float("inf")
    if spread>max_spread_pct:raise RuntimeError(f"spread {spread:.2f}% exceeds {max_spread_pct:.2f}%")
    return {"bid":bid,"ask":ask,"ltp":ltp,"spread_pct":spread,"age_s":age,"volume":float(q.get("volume") or 0),"oi":float(q.get("oi") or 0)}

def _conservative_quantity(requested,lot_size,ask,max_risk_inr):
    """Lot-aligned quantity whose *entire premium* fits the INR risk budget.

    A bought option can go to zero, so the conservative ceiling is the full
    premium outlay rather than the modelled stop distance. The name matches
    ``StrategyConfig.max_risk_inr`` so the budget being spent is unambiguous.
    """
    if requested<=0 or lot_size<=0 or ask<=0 or max_risk_inr<=0:return 0
    return min(requested,int(max_risk_inr//(ask*lot_size))*lot_size)

def _signal_age(value):
    ts=_parse_timestamp(value); return None if ts is None else max(0,(datetime.now(IST)-ts.astimezone(IST)).total_seconds())

def _as_ist(now):
    if getattr(now, "tzinfo", None) is None:
        return now.replace(tzinfo=IST)
    return now.astimezone(IST)

def _entry_window_open(now,cfg):
    now=_as_ist(now)
    try:
        start=datetime.strptime(cfg.entry_start,"%H:%M").time(); end=datetime.strptime(cfg.entry_end,"%H:%M").time()
        return start<=now.time()<=end
    except (TypeError,ValueError):
        return False

def _market_open(now):
    """NSE F&O cash session in IST. Same gate ``execute_scan`` uses before any order."""
    now=_as_ist(now)
    start=datetime.strptime("09:15","%H:%M").time(); end=datetime.strptime("15:29","%H:%M").time()
    return now.weekday()<5 and start<=now.time()<=end

async def execute_scan(uid:str,*,scan:dict[str,Any],max_trades:int)->dict[str,Any]:
    from app.services.kite_engine import state as engine_state,positions,protection
    from app.services import live_safety
    from app.services.exchanges.kite import accounts
    from app.services.nifty_orb_options import get_config
    from app.services.nifty_orb_lifecycle import manual_mode_response, ticket_fields, ticket_fingerprint
    universal=engine_state.get_config(uid)
    if not getattr(universal,"auto_execute",False):return manual_mode_response()
    account=accounts.get_active(uid)
    if not account:return {"status":"blocked","reason":"No active Kite account","executed":[]}
    cfg=get_config(); trade_state=_state(uid)
    if int(trade_state.get("count",0))>=max_trades:return {"status":"daily_limit","executed":[],"count":trade_state["count"]}
    client=await accounts.acquire_client(account); executed=[]; open_pos=positions.open_positions(uid)
    seen={str(p.underlying).upper() for p in open_pos if p.status in (positions.OPEN,positions.PENDING)}
    now=datetime.now(IST)
    if not _market_open(now):return {"status":"market_closed","executed":[]}
    if not _entry_window_open(now,cfg):return {"status":"outside_entry_window","executed":[]}
    for row in scan.get("signals",[]):
        if row.get("status")!="signal":continue
        plan=row.get("trade") or {}; contract=plan.get("contract") or {}; symbol=str(contract.get("symbol") or ""); underlying=str(row.get("underlying") or "").upper(); requested=int(plan.get("quantity") or 0); signal=row.get("signal") or {}; direction=str(signal.get("direction") or "")
        expected="CE" if direction=="LONG" else "PE" if direction=="SHORT" else ""
        fingerprint=ticket_fingerprint(plan,signal); ticket=ticket_fields(plan)
        if not symbol or requested<=0 or not underlying:
            executed.append({"status":"blocked","symbol":symbol,"reason":"incomplete ticket"});continue
        if underlying in seen:
            executed.append({"status":"blocked","symbol":symbol,"underlying":underlying,"reason":"already in an open ORB position"});continue
        if expected!=str(contract.get("option_type") or ""):
            executed.append({"status":"blocked","symbol":symbol,"reason":"option direction mismatch"});continue
        age=_signal_age(signal.get("timestamp"))
        if age is None or age>cfg.interval_minutes*60:
            executed.append({"status":"blocked","symbol":symbol,"reason":f"signal stale/invalid age={age}"});continue
        # Only filled entries consume the daily budget. `len(executed)` counted
        # every refusal too, so with the default cap of 2 a pair of illiquid
        # candidates exhausted the day before a tradable third was ever examined.
        if sum(1 for e in executed if e.get("status")=="executed")+int(trade_state.get("count",0))>=max_trades:break
        key=f"{underlying}:{signal.get('timestamp')}:{direction}:{symbol}"; idem=live_safety.make_idempotency_key(uid,key,"BUY")
        decision=live_safety.assert_safe_to_trade(positions.open_positions(uid),idem,check_daily_loss=True,uid=uid)
        if not decision.allowed:
            executed.append({"status":"blocked","symbol":symbol,"reason":decision.reason,"code":decision.code});continue
        exchange,instrument=await _find_contract(client,symbol,underlying)
        if not exchange or not instrument:
            executed.append({"status":"blocked","symbol":symbol,"reason":"contract no longer exists"});continue
        # The broker-resolved contract, not the scanner payload, is authoritative.
        broker_type=str(instrument.get("instrument_type") or instrument.get("option_type") or "").upper()
        if broker_type and broker_type!=expected:
            executed.append({"status":"blocked","symbol":symbol,"reason":"broker contract option type mismatch"});continue
        plan_strike=float(contract.get("strike") or 0); broker_strike=float(instrument.get("strike") or 0)
        if plan_strike>0 and broker_strike>0 and abs(plan_strike-broker_strike)>0.001:
            executed.append({"status":"blocked","symbol":symbol,"reason":"broker contract strike mismatch"});continue
        try: expiry=datetime.strptime(str(instrument.get("expiry"))[:10],"%Y-%m-%d").date()
        except (TypeError,ValueError):
            executed.append({"status":"blocked","symbol":symbol,"reason":"invalid contract expiry"});continue
        plan_expiry=str(contract.get("expiry") or "")[:10]
        if plan_expiry and plan_expiry!=expiry.isoformat():
            executed.append({"status":"blocked","symbol":symbol,"reason":"broker contract expiry mismatch"});continue
        dte=(expiry-now.date()).days
        if dte<cfg.expiry_dte_min or dte>cfg.expiry_dte_max or (cfg.avoid_expiry_day and dte==0):
            executed.append({"status":"blocked","symbol":symbol,"reason":"contract outside configured expiry policy"});continue
        try: quote=await _fresh_quote(client,exchange,symbol,float(cfg.max_quote_staleness_s),float(cfg.max_spread_pct))
        except Exception as exc:
            executed.append({"status":"blocked","symbol":symbol,"reason":f"quote validation failed: {exc}"});continue
        if quote["volume"]<cfg.min_option_volume or quote["oi"]<cfg.min_open_interest:
            executed.append({"status":"blocked","symbol":symbol,"reason":"option liquidity below configured minimum"});continue
        spot=float(plan.get("underlying_entry") or row.get("spot") or 0)
        try:
            uq=await client.get_quote([f"NSE:{underlying}"]); uquote=(uq or {}).get(f"NSE:{underlying}") or {}; current=float(uquote.get("last_price") or 0)
        except Exception as exc:
            executed.append({"status":"blocked","symbol":symbol,"reason":f"underlying quote unavailable: {exc}"});continue
        if spot<=0 or current<=0:
            executed.append({"status":"blocked","symbol":symbol,"reason":"underlying entry/current price unavailable"});continue
        if abs(current-spot)/spot>0.003:
            executed.append({"status":"blocked","symbol":symbol,"reason":"underlying moved >0.30% since signal"});continue
        lot=int(contract.get("lot_size") or 0)
        broker_lot=int(instrument.get("lot_size") or 0)
        if lot<=0 or broker_lot<=0 or lot!=broker_lot:
            executed.append({"status":"blocked","symbol":symbol,"reason":"broker contract lot size mismatch"});continue
        quantity=_conservative_quantity(requested,lot,quote["ask"],float(cfg.max_risk_inr))
        if quantity<=0:
            executed.append({"status":"blocked","symbol":symbol,"reason":"one option lot exceeds conservative premium risk budget"});continue
        if quantity!=requested:
            executed.append({"status":"blocked","symbol":symbol,"reason":"live premium would change the ticket quantity"});continue
        decision=live_safety.assert_safe_to_trade(positions.open_positions(uid),idem,check_daily_loss=True,uid=uid)
        if not decision.allowed:
            executed.append({"status":"blocked","symbol":symbol,"reason":decision.reason,"code":decision.code});continue
        ok,existing=await _existing_order_by_tag(client,idem)
        if not ok:
            executed.append({"status":"blocked","symbol":symbol,"reason":"broker order state unavailable"});continue
        if existing:
            oid=str(existing.get("order_id") or existing.get("orderId") or "")
            exsym=str(existing.get("tradingsymbol") or existing.get("symbol") or symbol).upper(); side=str(existing.get("transaction_type") or existing.get("side") or "BUY").upper()
            if exsym!=symbol.upper() or side!="BUY":
                live_safety.set_kill_switch(True,f"ORB tag mapped to unexpected broker order {oid}");continue
        else:
            try:r=await client.place_order_option(symbol,"buy",quantity,exchange=exchange,tag=idem)
            except Exception as exc:
                executed.append({"status":"error","symbol":symbol,"error":str(exc)});continue
            oid=str((r or {}).get("order_id") or (r or {}).get("orderId") or "")
            if not oid:
                live_safety.set_kill_switch(True,"ORB submission outcome unknown; reconcile broker state");continue
            live_safety.record_idempotency(idem,oid)
        filled,fill_price,status=await _resolve_fill(client,oid)
        if filled<=0:
            if status not in {"CANCELLED","REJECTED","EXPIRED"}:
                _,_,_,safe=await _cancel_and_reconcile(client,oid,quantity)
                if not safe:live_safety.set_kill_switch(True,"ORB unfilled order state remains uncertain")
            executed.append({"status":"pending_or_unfilled","symbol":symbol,"order_id":oid,"broker_status":status});continue
        actual=filled
        if filled<quantity:
            actual,fill_price,status,safe=await _cancel_and_reconcile(client,oid,quantity)
            if not safe:
                live_safety.set_kill_switch(True,"ORB partial-fill remainder state remains uncertain")
                executed.append({"status":"critical_unknown_position","symbol":symbol,"order_id":oid,"quantity":actual});continue
        try:
            armed=await protection.arm_position(client,uid,symbol=symbol,exchange=exchange,token=int(instrument.get("instrument_token") or 0),qty=actual,lot_size=lot,entry_premium=fill_price or quote["ask"],stop_premium=float(plan.get("stop_premium") or 0),order_id=oid,stop_mode=universal.stop_mode,direction="long",signal_direction="long" if direction=="LONG" else "short",vehicle="otm_options",underlying=underlying,exit_mode=universal.exit_mode,entry_spot=spot,entry_delta=float(abs(contract.get("delta") or 0.5)),strike=float(contract.get("strike") or 0),expiry=str(instrument.get("expiry") or "")[:10],target_premium=float(plan.get("target_premium") or 0))
            if not armed.protected:raise RuntimeError(armed.describe())
        except Exception as exc:
            closed,note=await _sell_and_verify(client,symbol,exchange,actual)
            if not closed:
                live_safety.set_kill_switch(True,f"ORB unprotected position {symbol}: {note}")
                executed.append({"status":"critical_unprotected","symbol":symbol,"order_id":oid,"quantity":actual,"reason":str(exc),"close":note})
            else:executed.append({"status":"entry_closed_protection_failure","symbol":symbol,"order_id":oid,"quantity":actual,"reason":str(exc),"close":note})
            continue
        trade_state["count"]=int(trade_state.get("count",0))+1; trade_state["signals"].append(key)
        try:
            _save_state(uid,trade_state)
        except Exception as exc:
            # The position is open and protected, but the day's trade count did
            # not persist. The next tick would read the stale count and could
            # trade past max_trades_per_day, so stop trading instead of silently
            # raising the cap. Same failure class as the daily-PnL persistence gate.
            live_safety.set_kill_switch(True,f"ORB trade count did not persist after {symbol}: {exc}")
            executed.append({"status":"executed_count_not_persisted","underlying":underlying,"symbol":symbol,"quantity":actual,"order_id":oid,"protected":True,"reason":str(exc)})
            seen.add(underlying);continue
        seen.add(underlying)
        executed.append({"status":"executed","underlying":underlying,"symbol":symbol,"quantity":actual,"requested_quantity":quantity,"fill_price":fill_price,"broker_status":status,"order_id":oid,"protected":True,"conservative_max_loss_inr":round(quote["ask"]*actual,2),"plan":plan,"ticket_fingerprint":fingerprint,"ticket":ticket})
    return {"status":"executed" if executed else "no_trade","executed":executed,"count":trade_state["count"]}