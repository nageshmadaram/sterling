"""Mirror test: defined-risk credit spreads (sell near strike, buy wing as a hard cap)."""
from __future__ import annotations
import math, numpy as np, pandas as pd
from scipy.stats import norm
LAKE="/run/media/nageshmadaram/3f36ac07-fdbe-48c1-9514-ecf65c6619b0/SterlingLake"
SCALE=10_000; IST="Asia/Kolkata"
SPECS={"NIFTY":dict(p=f"{LAKE}/bars/interval=minute/exchange=NSE/segment=INDICES/256265__NIFTY_50.parquet",
                    lot=65,tick=.05,step=50.,dow=1),
       "SENSEX":dict(p=f"{LAKE}/bars/interval=minute/exchange=BSE/segment=INDICES/265__SENSEX.parquet",
                     lot=20,tick=.05,step=100.,dow=3)}
ENTRIES=[9*60+30,10*60,11*60,12*60,13*60]; SETTLE_FROM=15*60
C1,C2,VRP=0.12,0.06,1.15
def bs(S,K,v,c):
    if v<=1e-12: return max(S-K,0.) if c else max(K-S,0.)
    d1=(math.log(S/K)+.5*v*v)/v; d2=d1-v
    return S*norm.cdf(d1)-K*norm.cdf(d2) if c else K*norm.cdf(-d2)-S*norm.cdf(-d1)
def sm(v,S,K):
    if v<=0: return v
    z=abs(math.log(K/S))/v; return v*(1+C1*z+C2*z*z)
rows=[]
for sym,s in SPECS.items():
    d=pd.read_parquet(s["p"],columns=["ts","close"]); d["close"]/=SCALE
    d["ts"]=pd.to_datetime(d["ts"],utc=True).dt.tz_convert(IST)
    d["date"]=d["ts"].dt.date; d["mins"]=d["ts"].dt.hour*60+d["ts"].dt.minute; d["dow"]=d["ts"].dt.dayofweek
    hist={m:[] for m in ENTRIES}; ses=[]
    for dt,g in d.groupby("date",sort=True):
        if len(g)<300: continue
        t=g[g["mins"]>=SETTLE_FROM]
        if len(t)<20: continue
        ses.append((dt,int(g.dow.iloc[0]),g.set_index("mins")["close"],float(t["close"].mean())))
    for dt,dow,ser,settle in ses:
        for m in ENTRIES:
            if m not in ser.index: continue
            S=float(ser.loc[m]); r=math.log(settle/S)
            if len(hist[m])>=20 and dow==s["dow"]:
                v=float(np.std(hist[m][-60:],ddof=1))
                for shortoff in (0.002,0.004,0.006):
                    for width in (1,2,3):
                        for cp in ("CE","PE"):
                            raw=S*(1+shortoff) if cp=="CE" else S*(1-shortoff)
                            Ks=round(raw/s["step"])*s["step"]
                            Kl=Ks+width*s["step"] if cp=="CE" else Ks-width*s["step"]
                            if Kl<=0: continue
                            ps=max(round(bs(S,Ks,sm(v*VRP,S,Ks),cp=="CE")/s["tick"])*s["tick"],s["tick"])
                            pl=max(round(bs(S,Kl,sm(v*VRP,S,Kl),cp=="CE")/s["tick"])*s["tick"],s["tick"])
                            credit=ps-pl-2*s["tick"]                       # pay spread both legs
                            if credit<=0: continue
                            ts_=max(settle-Ks,0.) if cp=="CE" else max(Ks-settle,0.)
                            tl_=max(settle-Kl,0.) if cp=="CE" else max(Kl-settle,0.)
                            payoff=credit-(ts_-tl_)
                            maxloss=width*s["step"]-credit
                            q=s["lot"]
                            gross=payoff*q
                            cost=40+.001*ps*q+5e-4*((ps+pl)*q*2)+.18*(40+5e-4*((ps+pl)*q*2))
                            rows.append((sym,str(dt),m,shortoff,width,cp,credit,maxloss,
                                         (gross-cost)/(maxloss*q)))
            hist[m].append(r)
X=pd.DataFrame(rows,columns=["sym","date","entry","soff","w","cp","credit","maxloss","ret_on_risk"])
def ci(g,B=3000,seed=9):
    rng=np.random.default_rng(seed); ds=g.date.unique()
    by={x:g.loc[g.date==x,"ret_on_risk"].values for x in ds}
    mm=np.array([np.concatenate([by[x] for x in rng.choice(ds,len(ds),True)]).mean() for _ in range(B)])
    return round(float(np.quantile(mm,.025)),4),round(float(np.quantile(mm,.975)),4)
print("### K. DEFINED-RISK CREDIT SPREADS, expiry days. ret_on_risk = net PnL / max loss")
o=[]
for (sym,so,w),g in X.groupby(["sym","soff","w"]):
    lo,hi=ci(g)
    o.append(dict(sym=sym,short_off=so,width_strikes=w,n=len(g),days=g.date.nunique(),
                  win=round((g.ret_on_risk>0).mean(),3),mean=round(g.ret_on_risk.mean(),4),
                  ci_lo=lo,ci_hi=hi,worst=round(g.ret_on_risk.min(),3)))
print(pd.DataFrame(o).sort_values("mean",ascending=False).to_string(index=False))
print("\npooled:",round(X.ret_on_risk.mean(),4),"ci",ci(X),"win",round((X.ret_on_risk>0).mean(),3),
      "worst",round(X.ret_on_risk.min(),3))
