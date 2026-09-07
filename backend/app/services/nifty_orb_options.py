"""Runtime orchestration for NIFTY ORB + VWAP options."""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from app.engines.nifty_orb_options import Bar, OptionContract, StrategyConfig, build_trade_plan, generate_signal, is_monthly_expiry, select_option, summarize_pnl
from app.core.config import settings
from app.core.logging import get_logger

log=get_logger(__name__)
_IST=timezone(timedelta(hours=5,minutes=30)); _CONFIG_KEY="nifty_orb_options_config"; _TRADE_STATE_PREFIX="nifty_orb_options_trade_state:"

def get_config()->StrategyConfig:
    """The persisted config, or the defaults when nothing has been stored.

    Two different fallbacks, deliberately not the same one:

    * **Nothing stored** -> the real defaults (engine OFF on a fresh install).
    * **Stored but unreadable or invalid** -> defaults with the engine OFF. A row
      persisted before validation existed, or edited straight in the database,
      must never become a trading config: the invalid value would otherwise
      surface as an exception deep inside the engine mid-session.

    A stored value always wins over a default, so changing a default cannot
    overrule an operator who deliberately set something.
    """
    off=StrategyConfig(enabled=False)
    try:
        from app.services import db
        raw=db.get_config(_CONFIG_KEY)
    except Exception:
        log.warning("NIFTY ORB config store unavailable; running with defaults OFF")
        return off
    if not raw:return StrategyConfig()
    try:
        x=json.loads(raw) if isinstance(raw,str) else raw
        base=StrategyConfig()
        merged = {**base.__dict__, **x}
        for key in ("scan_stocks", "scan_indices", "strike_moneyness",
                    "scan_expiries_indices",
                    "scan_weekly_series_indices", "scan_monthly_series_indices",
                    "scan_monthly_series_stocks"):
            if isinstance(merged.get(key), list):
                merged[key] = tuple(merged[key])
        loaded=StrategyConfig(**{k: v for k, v in merged.items() if k in StrategyConfig.__dataclass_fields__})
        return loaded.validate()
    except (ValueError,TypeError,json.JSONDecodeError) as exc:
        log.error("Stored NIFTY ORB config is invalid (%s); running with defaults OFF",exc)
        return off

def set_config(values:dict[str,Any])->StrategyConfig:
    c=get_config().__dict__.copy(); bad=sorted(set(values)-set(c))
    if bad: raise ValueError(f"Unknown NIFTY ORB config fields: {', '.join(bad)}")
    c.update(values)
    for key in ("scan_stocks", "scan_indices", "strike_moneyness",
                "scan_expiries_indices",
                "scan_weekly_series_indices", "scan_monthly_series_indices",
                "scan_monthly_series_stocks"):
        if isinstance(c.get(key), list):
            c[key] = tuple(c[key])
    if c["data_source"] not in {"kite","truedata"}: raise ValueError("data_source must be 'kite' or 'truedata'")
    if c["execution_broker"]!="kite": raise ValueError("execution_broker is fixed to 'kite'")
    if c["interval_minutes"] not in {1,3,5,10,15}: raise ValueError("interval_minutes must be one of 1, 3, 5, 10, 15")
    if c["opening_range_minutes"] not in {5,10,15,20,30}: raise ValueError("opening_range_minutes must be one of 5, 10, 15, 20, 30")
    if c["entry_start"]>=c["entry_end"]: raise ValueError("entry_start must be before entry_end")
    if c["max_trades_per_day"]<1: raise ValueError("max_trades_per_day must be >= 1")
    if c["max_risk_inr"]<=0: raise ValueError("max_risk_inr must be > 0")
    if not 0<c["max_spread_pct"]<=10: raise ValueError("max_spread_pct must be > 0 and <= 10")
    if c["min_option_volume"]<0 or c["min_open_interest"]<0: raise ValueError("option liquidity thresholds cannot be negative")
    # The engine also accepts "any", but an operator-facing options strategy must
    # state an expiry preference, so the API deliberately offers only the three
    # meaningful ones.
    if c["expiry_selection"] not in {"nearest","weekly","monthly"}: raise ValueError("expiry_selection must be nearest, weekly or monthly")
    if c["avoid_expiry_day"] and c["expiry_dte_max"]==0: raise ValueError("avoid_expiry_day cannot be enabled with expiry_dte_max=0")
    # Shared invariants live on StrategyConfig so the API boundary and the engine
    # cannot drift apart -- an operator must never be able to persist a config
    # the engine will later reject mid-session.
    cfg=StrategyConfig(**c).validate()
    from app.services import db
    db.set_config(_CONFIG_KEY,json.dumps(cfg.__dict__,separators=(",",":"))); return cfg

def _bar(r:Any)->Bar:
    ts=r.get("timestamp") or r.get("time") or r.get("timestamp_ms")
    if isinstance(ts,(int,float)): dt=datetime.fromtimestamp(float(ts)/1000,tz=_IST)
    elif isinstance(ts,datetime): dt=ts if ts.tzinfo else ts.replace(tzinfo=_IST); dt=dt.astimezone(_IST)
    else:
        s=str(ts).replace("Z","+00:00"); dt=datetime.fromisoformat(s) if "T" in s or "+" in s[10:] else datetime.strptime(s,"%Y-%m-%d %H:%M:%S").replace(tzinfo=_IST); dt=dt.astimezone(_IST)
    return Bar(dt,float(r["open"]),float(r["high"]),float(r["low"]),float(r["close"]),float(r.get("volume") or 0))

def normalize_option_chain(rows:Any,expiry:str|None=None)->list[OptionContract]:
    if isinstance(rows,dict): rows=rows.get("Records") or rows.get("records") or rows.get("data") or rows.get("options") or []
    out=[]
    for r in rows or []:
        if not isinstance(r,dict): continue
        raw=str(r.get("option_type") or r.get("type") or r.get("opttype") or "").upper(); typ={"CALL":"CE","C":"CE","PUT":"PE","P":"PE"}.get(raw,raw)
        if typ not in {"CE","PE"}: continue
        try: strike=float(r.get("strike") or r.get("strike_price"))
        except (TypeError,ValueError): continue
        out.append(OptionContract(str(r.get("symbol") or r.get("tradingsymbol") or r.get("instrument") or ""),strike,str(r.get("expiry") or r.get("expiry_date") or expiry or "")[:10],typ,float(r.get("ltp") or r.get("last_price") or r.get("close") or 0),float(r.get("bid") or r.get("bid_price") or 0),float(r.get("ask") or r.get("ask_price") or 0),int(r.get("lot_size") or r.get("lotsize") or 75),float(r["delta"]) if r.get("delta") not in (None,"") else None,float(r.get("volume") or 0),float(r.get("oi") or r.get("open_interest") or 0)))
    return out

async def _kite_bars(uid:str,interval:str)->list[Bar]:
    from app.services.exchanges.kite import accounts as ka
    from app.services.exchanges import instrument_registry as reg
    acct=ka.get_active(uid)
    if not acct: raise RuntimeError("No active Kite account")
    client=await ka.acquire_client(acct); inst=reg.get_instrument("NIFTY") or reg.get_instrument("NIFTY 50")
    if not inst: raise RuntimeError("NIFTY instrument is not registered")
    rows=await client.get_candles(inst,interval,limit=240); now=datetime.now(_IST); minutes=int(interval.rstrip("m"))
    return [b for b in [_bar({"timestamp_ms":r.timestamp_ms,"open":r.open,"high":r.high,"low":r.low,"close":r.close,"volume":r.volume}) for r in rows] if b.timestamp+timedelta(minutes=minutes)<=now]

async def _kite_options(uid:str,direction:str)->list[OptionContract]:
    from app.services.exchanges.kite import accounts as ka
    acct=ka.get_active(uid)
    if not acct: raise RuntimeError("No active Kite account")
    cfg=get_config(); client=await ka.acquire_client(acct); rows=await client.search_instruments("NIFTY","NFO",limit=5000); today=datetime.now(_IST).date(); wanted="CE" if direction=="LONG" else "PE"; candidates=[]
    for r in rows:
        if str(r.get("name") or "").upper()!="NIFTY" or str(r.get("instrument_type") or "").upper()!=wanted: continue
        try: exp=datetime.strptime(str(r.get("expiry"))[:10],"%Y-%m-%d").date()
        except (TypeError,ValueError): continue
        dte=(exp-today).days
        if cfg.expiry_dte_min<=dte<=cfg.expiry_dte_max and not (cfg.avoid_expiry_day and dte==0): candidates.append((exp,r))
    if not candidates:return []
    # One expiry rule for the whole strategy: the engine's. A local
    # reimplementation here previously resolved "weekly" to the nearest expiry,
    # which silently bought a monthly contract on a weekly mandate.
    selection=cfg.expiry_selection.strip().lower(); expiries={e for e,_ in candidates}
    if selection in {"nearest","any"}: expiry=min(expiries)
    else:
        want_monthly=selection=="monthly"
        matching=[e for e in expiries if is_monthly_expiry(e) is want_monthly]
        if not matching:
            log.warning("No eligible %s NIFTY expiry in the Kite chain; refusing to substitute another bucket",selection)
            return []
        expiry=min(matching)
    out=[]
    for exp,r in candidates:
        if exp!=expiry:continue
        sym=str(r.get("tradingsymbol") or "")
        try:
            q=await client.get_quote([f"NFO:{sym}"]); d=q.get(f"NFO:{sym}",{}) or {}; dep=d.get("depth") or {}; buy=(dep.get("buy") or [{}])[0]; sell=(dep.get("sell") or [{}])[0]
            out.append(OptionContract(sym,float(r.get("strike") or 0),exp.isoformat(),wanted,float(d.get("last_price") or 0),float(buy.get("price") or 0),float(sell.get("price") or 0),int(r.get("lot_size") or 1),None,float(d.get("volume") or 0),float(d.get("oi") or 0)))
        except Exception: continue
    return out

async def snapshot(uid:str)->dict[str,Any]:
    """Same tickets Auto uses. Not a NIFTY-only second planner.

    Kept so anything still posting ``/snapshot`` cannot silently diverge from
    ``scan_user``. The universe is the payload; ``signal``/``plan`` are the
    NIFTY row when one exists, for older callers.
    """
    from app.services.nifty_orb_scanner import scan_user
    cfg=get_config()
    if not cfg.enabled:
        return {"enabled":False,"signal":None,"plan":None,"signals":[],"data_source":cfg.data_source}
    scan=await scan_user(uid, cfg)
    rows=list(scan.get("signals") or [])
    nifty=next((r for r in rows if str(r.get("underlying") or "").upper() in {"NIFTY","NIFTY 50"} and r.get("status")=="signal"), None)
    if nifty is None:
        nifty=next((r for r in rows if r.get("status")=="signal"), None)
    out={"enabled":True,"data_source":cfg.data_source,"execution_broker":cfg.execution_broker,"signals":rows,"signal":None,"plan":None}
    if nifty is not None:
        out["signal"]=nifty.get("signal")
        out["plan"]=nifty.get("trade")
        out["ticket"]=nifty.get("ticket")
        out["ticket_fingerprint"]=nifty.get("ticket_fingerprint")
    return out

def _trade_state(uid:str)->dict:
    from app.services import db
    try:
        raw=db.get_config(_TRADE_STATE_PREFIX+uid); return json.loads(raw) if raw else {"date":"","count":0,"signals":[]}
    except Exception:return {"date":"","count":0,"signals":[]}

def _save_trade_state(uid:str,state:dict)->None:
    from app.services import db; db.set_config(_TRADE_STATE_PREFIX+uid,json.dumps(state,separators=(",",":")))

def _today_trade_state(uid:str)->dict:
    state=_trade_state(uid); today=datetime.now(_IST).date().isoformat()
    if state.get("date")!=today: state={"date":today,"count":0,"signals":[]}
    return state

def backtest_from_bars(rows:list[dict[str,Any]],cfg:StrategyConfig|None=None)->dict[str,Any]:
    """Underlying baseline. Positions are strictly non-overlapping and reset per session."""
    cfg=cfg or get_config(); bars=[_bar(r) for r in rows]
    if len(bars)<100:return {"metrics":summarize_pnl([]),"warning":"At least 100 bars are required"}
    pnls=[]; i=60; day_counts={}
    while i<len(bars):
        day=bars[i].timestamp.date(); count=day_counts.get(day,0)
        if count>=cfg.max_trades_per_day: i+=1; continue
        sig=generate_signal(bars[:i+1],cfg)
        if sig.direction=="NONE" or sig.atr<=0: i+=1; continue
        entry=bars[i].close; risk=max(sig.atr*cfg.stop_buffer_atr,1e-9); stop=entry-risk if sig.direction=="LONG" else entry+risk; target=entry+risk*cfg.target_r if sig.direction=="LONG" else entry-risk*cfg.target_r; outcome=None; j=i+1
        while j<len(bars) and bars[j].timestamp.date()==day:
            b=bars[j]
            if sig.direction=="LONG":
                if b.low<=stop: outcome=-risk; break
                if b.high>=target: outcome=target-entry; break
            else:
                if b.high>=stop: outcome=-risk; break
                if b.low<=target: outcome=entry-target; break
            j+=1
        if outcome is not None:
            pnls.append(outcome); day_counts[day]=count+1; i=j+1
        else:
            i=max(i+1,j)
    return {"metrics":{**summarize_pnl(pnls),"model":"underlying-point baseline","costs_included":False,"option_pnl":False},"warning":"Underlying baseline only. Option-level replay requires historical option premiums, contracts, costs and slippage."}

async def execute_manual(uid:str)->dict[str,Any]:
    """Manual does not place from this endpoint.

    The board Buy button (shared order window) is the Manual path. Return the
    same universe tickets Auto would consume from ``scan_user`` — not the
    NIFTY-only snapshot, which is a different planner.
    """
    from app.services.nifty_orb_lifecycle import attach_ticket, manual_mode_response
    from app.services.nifty_orb_scanner import scan_user
    cfg=get_config()
    out=manual_mode_response()
    if not cfg.enabled:
        out["enabled"]=False
        return out
    scan=await scan_user(uid, cfg)
    rows=list(scan.get("signals") or [])
    armed=next((r for r in rows if r.get("status")=="signal" and (r.get("trade") or r.get("ticket"))), None)
    if armed is None:
        out.update({"enabled":True,"ticket":None,"ticket_fingerprint":None,"plan":None,"signal":None,"signals":rows})
        return out
    attach_ticket(armed)
    out.update({
        "enabled":True,
        "ticket":armed.get("ticket"),
        "ticket_fingerprint":armed.get("ticket_fingerprint"),
        "plan":armed.get("trade"),
        "signal":armed.get("signal"),
        "signals":rows,
    })
    return out
