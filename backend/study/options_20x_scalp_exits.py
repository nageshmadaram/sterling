"""Does INTRADAY exit management (scalping) rescue long 0DTE options?
Marks the option to market minute-by-minute along the real spot path, decaying
time value, and exits at first touch of target or stop."""
from __future__ import annotations
import math, numpy as np, pandas as pd
from scipy.stats import norm
LAKE="/run/media/nageshmadaram/3f36ac07-fdbe-48c1-9514-ecf65c6619b0/SterlingLake"
SCALE=10_000; IST="Asia/Kolkata"
SPECS={"NIFTY":dict(path=f"{LAKE}/bars/interval=minute/exchange=NSE/segment=INDICES/256265__NIFTY_50.parquet",
                    lot=65,tick=.05,step=50.,dow=1),
       "SENSEX":dict(path=f"{LAKE}/bars/interval=minute/exchange=BSE/segment=INDICES/265__SENSEX.parquet",
                     lot=20,tick=.05,step=100.,dow=3)}
ENTRIES=[9*60+30,10*60,11*60,12*60,13*60,14*60]; OFFSETS=[0.0,0.003,0.005,0.0075]
CLOSE=15*60+30; FLAT=15*60+20; SETTLE_FROM=15*60
C1,C2,VRP=0.12,0.06,1.15
SPREAD_TICKS=1.0   # cross half-spread each way, in ticks; 0DTE index books are tight but not free

def bs(S,K,v,call):
    if v<=1e-12: return max(S-K,0.) if call else max(K-S,0.)
    d1=(math.log(S/K)+.5*v*v)/v; d2=d1-v
    return S*norm.cdf(d1)-K*norm.cdf(d2) if call else K*norm.cdf(-d2)-S*norm.cdf(-d1)
def sm(v,S,K):
    if v<=0: return v
    z=abs(math.log(K/S))/v; return v*(1+C1*z+C2*z*z)

def load(sym):
    s=SPECS[sym]; d=pd.read_parquet(s["path"],columns=["ts","close"])
    d["close"]=d["close"]/SCALE
    d["ts"]=pd.to_datetime(d["ts"],utc=True).dt.tz_convert(IST)
    d["date"]=d["ts"].dt.date; d["mins"]=d["ts"].dt.hour*60+d["ts"].dt.minute
    d["dow"]=d["ts"].dt.dayofweek; return d

rows=[]
for sym,spec in SPECS.items():
    d=load(sym); hist={m:[] for m in ENTRIES}
    ses=[]
    for dt,g in d.groupby("date",sort=True):
        if len(g)<300: continue
        t=g[g["mins"]>=SETTLE_FROM]
        if len(t)<20: continue
        ses.append((dt,int(g["dow"].iloc[0]),g.set_index("mins")["close"],float(t["close"].mean())))
    for dt,dow,ser,settle in ses:
        is_exp = dow==spec["dow"]
        for m in ENTRIES:
            if m not in ser.index: continue
            S=float(ser.loc[m]); r=math.log(settle/S); past=hist[m]
            if len(past)>=20 and is_exp:
                v0=float(np.std(past[-60:],ddof=1))
                tot_min=CLOSE-m
                for off in OFFSETS:
                    for cp in ("CE","PE"):
                        raw=S*(1+off) if cp=="CE" else S*(1-off)
                        K=round(raw/spec["step"])*spec["step"]
                        if off>0 and cp=="CE" and K<=S: K+=spec["step"]
                        if off>0 and cp=="PE" and K>=S: K-=spec["step"]
                        ent=max(round(bs(S,K,sm(v0*VRP,S,K),cp=="CE")/spec["tick"])*spec["tick"],spec["tick"])
                        ent_fill=ent+SPREAD_TICKS*spec["tick"]     # pay the offer
                        path=ser[(ser.index>m)&(ser.index<=FLAT)]
                        for tgt,stp in [(0.5,0.3),(1.0,0.5),(2.0,0.5),(3.0,0.5),(None,None)]:
                            exit_p=None
                            for mm,Sx in path.items():
                                rem=max(CLOSE-mm,0)/max(tot_min,1)
                                v=v0*VRP*math.sqrt(max(rem,1e-9))
                                mark=bs(Sx,K,sm(v,Sx,K),cp=="CE")
                                mark=max(round(mark/spec["tick"])*spec["tick"],0.0)
                                fill=max(mark-SPREAD_TICKS*spec["tick"],0.0)   # hit the bid
                                if tgt is not None:
                                    if fill>=ent_fill*(1+tgt): exit_p=ent_fill*(1+tgt); break
                                    if fill<=ent_fill*(1-stp): exit_p=ent_fill*(1-stp); break
                            if exit_p is None:
                                last=path.iloc[-1] if len(path) else S
                                rem=max(CLOSE-FLAT,0)/max(tot_min,1)
                                mk=bs(last,K,sm(v0*VRP*math.sqrt(max(rem,1e-9)),last,K),cp=="CE")
                                exit_p=max(max(round(mk/spec["tick"])*spec["tick"],0.)-SPREAD_TICKS*spec["tick"],0.)
                            lots=max(int(5000//(ent_fill*spec["lot"])),1)
                            q=lots*spec["lot"]; bt,st=ent_fill*q,exit_p*q
                            cost=40+.001*st+5e-4*(bt+st)+1e-6*(bt+st)+3e-5*bt+.18*(40+5e-4*(bt+st))
                            rows.append((sym,str(dt),m,off,cp,f"{tgt}/{stp}" if tgt else "hold_to_1520",
                                         ent_fill,exit_p,(st-bt-cost)/bt))
            hist[m].append(r)
X=pd.DataFrame(rows,columns=["sym","date","entry","off","cp","rule","ent","exit","ret"])
print("### J. SCALPING WITH TARGET/STOP EXITS  (expiry days, realistic smile+VRP, 1-tick spread each way)")
def ci(df,B=3000,seed=5):
    rng=np.random.default_rng(seed); days=df.date.unique()
    by={d:df.loc[df.date==d,"ret"].values for d in days}
    m=np.array([np.concatenate([by[x] for x in rng.choice(days,len(days),True)]).mean() for _ in range(B)])
    return round(float(np.quantile(m,.025)),3),round(float(np.quantile(m,.975)),3)
o=[]
for (sym,rule),g in X.groupby(["sym","rule"]):
    lo,hi=ci(g)
    o.append(dict(sym=sym,rule=rule,n=len(g),days=g.date.nunique(),
                  win=round((g.ret>0).mean(),3),mean_ret=round(g.ret.mean(),3),
                  ci_lo=lo,ci_hi=hi,best=round(g.ret.max(),2)))
print(pd.DataFrame(o).sort_values(["sym","mean_ret"],ascending=[True,False]).to_string(index=False))
X.to_parquet("/tmp/claude-1000/-home-nageshmadaram-Sterling/c7ae8747-06d7-4b0d-9c29-49192c37c2c2/scratchpad/scalp.parquet")
print("\n  best rule overall:",X.groupby("rule").ret.mean().idxmax(),
      round(X.groupby("rule").ret.mean().max(),4))
