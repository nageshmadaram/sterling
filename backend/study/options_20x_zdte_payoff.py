"""Final 0DTE study: smile-aware pricing, budget sizing, day-clustered bootstrap,
signal conditioning, and the 1L->20L Monte Carlo."""
from __future__ import annotations
import math, json
import numpy as np, pandas as pd
from scipy.stats import norm

LAKE="/run/media/nageshmadaram/3f36ac07-fdbe-48c1-9514-ecf65c6619b0/SterlingLake"
OUT="/tmp/claude-1000/-home-nageshmadaram-Sterling/c7ae8747-06d7-4b0d-9c29-49192c37c2c2/scratchpad"
SCALE=10_000; IST="Asia/Kolkata"
SPECS={"NIFTY":dict(path=f"{LAKE}/bars/interval=minute/exchange=NSE/segment=INDICES/256265__NIFTY_50.parquet",
                    lot=65,tick=0.05,step=50.0,dow=1),
       "SENSEX":dict(path=f"{LAKE}/bars/interval=minute/exchange=BSE/segment=INDICES/265__SENSEX.parquet",
                     lot=20,tick=0.05,step=100.0,dow=3)}
ENTRIES=[9*60+30,10*60,11*60,12*60,13*60,14*60,14*60+30]
OFFSETS=[0.0,0.003,0.005,0.0075,0.010,0.015,0.020]
SETTLE_FROM=15*60; ORB_END=9*60+20
BUDGET=5000.0   # user's own framing: risk ~5k per attempt

def load(sym):
    s=SPECS[sym]; d=pd.read_parquet(s["path"],columns=["ts","open","high","low","close"])
    for c in ("open","high","low","close"): d[c]=d[c]/SCALE
    d["ts"]=pd.to_datetime(d["ts"],utc=True).dt.tz_convert(IST)
    d["date"]=d["ts"].dt.date; d["mins"]=d["ts"].dt.hour*60+d["ts"].dt.minute
    d["dow"]=d["ts"].dt.dayofweek; return d.sort_values("ts").reset_index(drop=True)

def bs(S,K,v,call):
    if v<=1e-12: return max(S-K,0.0) if call else max(K-S,0.0)
    d1=(math.log(S/K)+.5*v*v)/v; d2=d1-v
    return S*norm.cdf(d1)-K*norm.cdf(d2) if call else K*norm.cdf(-d2)-S*norm.cdf(-d1)

def smile(v_atm,S,K,c1,c2):
    """Wing vol uplift. z = strike distance in ATM standard deviations.
    Flat vol structurally UNDERPRICES the wing; real 0DTE books quote a smile."""
    if v_atm<=0: return v_atm
    z=abs(math.log(K/S))/v_atm
    return v_atm*(1.0+c1*z+c2*z*z)

def net_pnl(prem,term,lots,lot,budget):
    q=lots*lot; bt,st=prem*q,term*q
    brok=40.0; stt=.001*st; txn=5e-4*(bt+st); sebi=1e-6*(bt+st); stamp=3e-5*bt
    gst=.18*(brok+txn+sebi)
    return st-bt-(brok+stt+txn+sebi+stamp+gst)

def build(c1,c2,vrp):
    rows=[]
    for sym,spec in SPECS.items():
        d=load(sym); hist={m:[] for m in ENTRIES}
        ses=[]
        for dt,g in d.groupby("date",sort=True):
            if len(g)<300: continue
            tail=g[g["mins"]>=SETTLE_FROM]
            if len(tail)<20: continue
            orb=g[g["mins"]<=ORB_END]
            ses.append((dt,int(g["dow"].iloc[0]),g.set_index("mins")["close"],
                        float(tail["close"].mean()),float(orb["high"].max()),float(orb["low"].min())))
        for dt,dow,ser,settle,orh,orl in ses:
            for m in ENTRIES:
                if m not in ser.index: continue
                S=float(ser.loc[m]); r=math.log(settle/S); past=hist[m]
                if len(past)>=20:
                    v=float(np.std(past[-60:],ddof=1))
                    up=S>orh; dn=S<orl                      # opening-range breakout state at entry
                    for off in OFFSETS:
                        for cp in ("CE","PE"):
                            raw=S*(1+off) if cp=="CE" else S*(1-off)
                            K=round(raw/spec["step"])*spec["step"]
                            if off>0 and cp=="CE" and K<=S: K+=spec["step"]
                            if off>0 and cp=="PE" and K>=S: K-=spec["step"]
                            if K<=0: continue
                            term=max(settle-K,0.) if cp=="CE" else max(K-settle,0.)
                            p=bs(S,K,smile(v*vrp,S,K,c1,c2),cp=="CE")
                            p=max(round(p/spec["tick"])*spec["tick"],spec["tick"])
                            lots=int(BUDGET//(p*spec["lot"]))
                            if lots<1: continue
                            cap=p*spec["lot"]*lots
                            pnl=net_pnl(p,term,lots,spec["lot"],BUDGET)
                            aligned=(up and cp=="CE") or (dn and cp=="PE")
                            rows.append((sym,str(dt),dow==spec["dow"],m,off,cp,S,K,settle,p,term,
                                         lots,cap,pnl,pnl/cap,aligned))
                hist[m].append(r)
    return pd.DataFrame(rows,columns=["sym","date","is_exp","entry","off","cp","S","K","settle",
                                      "prem","term","lots","cap","pnl","ret","aligned"])

def boot_ci(df,key="ret",B=4000,seed=7):
    """Day-clustered bootstrap. All strikes/times on one day share one shock,
    so the effective sample size is DAYS, not rows."""
    rng=np.random.default_rng(seed)
    days=df["date"].unique(); by={d:df.loc[df.date==d,key].values for d in days}
    means=np.empty(B)
    for b in range(B):
        pick=rng.choice(days,len(days),replace=True)
        means[b]=np.concatenate([by[d] for d in pick]).mean()
    return float(np.quantile(means,.025)),float(np.quantile(means,.975))

print("### F. NET RETURN PER TRADE (budget-sized ~Rs5,000), EXPIRY DAYS, by smile strength")
print("    ret = net INR / capital deployed.  ci = 95% day-clustered bootstrap on the mean.\n")
for label,(c1,c2,vrp) in {"no smile, no VRP (fantasy)":(0.0,0.0,1.00),
                          "no smile, VRP 1.15":(0.0,0.0,1.15),
                          "realistic smile + VRP 1.15":(0.12,0.06,1.15)}.items():
    R=build(c1,c2,vrp); R.to_parquet(f"{OUT}/f_{c1}_{c2}_{vrp}.parquet")
    E=R[R.is_exp]
    print(f"-- {label}")
    for sym in SPECS:
        s=E[E.sym==sym]
        out=[]
        for off,g in s.groupby("off"):
            lo,hi=boot_ci(g)
            out.append(dict(off=off,n=len(g),days=g.date.nunique(),hit=round((g.term>0).mean(),3),
                            mean_ret=round(g.ret.mean(),3),ci_lo=round(lo,3),ci_hi=round(hi,3),
                            best=round(g.ret.max(),1)))
        print(f"   {sym}"); print(pd.DataFrame(out).to_string(index=False))
    print()
