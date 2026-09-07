import numpy as np, pandas as pd, math, json
OUT="/tmp/claude-1000/-home-nageshmadaram-Sterling/c7ae8747-06d7-4b0d-9c29-49192c37c2c2/scratchpad"
R=pd.read_parquet(f"{OUT}/f_0.12_0.06_1.15.parquet")   # realistic smile + VRP
E=R[R.is_exp].copy()

def ci(df,B=4000,seed=11,key="ret"):
    rng=np.random.default_rng(seed); days=df.date.unique()
    by={d:df.loc[df.date==d,key].values for d in days}
    m=np.array([np.concatenate([by[d] for d in rng.choice(days,len(days),replace=True)]).mean() for _ in range(B)])
    return float(np.quantile(m,.025)),float(np.quantile(m,.975))

print("### G. DOES AN OPENING-RANGE-BREAKOUT FILTER RESCUE IT?")
print("    aligned = spot above OR-high and buying CE, or below OR-low and buying PE\n")
rows=[]
for sym in ["NIFTY","SENSEX"]:
    for al in (True,False):
        s=E[(E.sym==sym)&(E.aligned==al)]
        if len(s)<30: continue
        lo,hi=ci(s)
        rows.append(dict(sym=sym,aligned=al,n=len(s),days=s.date.nunique(),hit=round((s.term>0).mean(),3),
                         mean_ret=round(s.ret.mean(),3),ci_lo=round(lo,3),ci_hi=round(hi,3)))
print(pd.DataFrame(rows).to_string(index=False))

print("\n  best single (sym,off,entry) cell among ALIGNED trades, ranked by mean return:")
a=E[E.aligned]
g=a.groupby(["sym","off","entry"]).agg(n=("ret","size"),days=("date","nunique"),
                                       hit=("term",lambda x:(x>0).mean()),mean_ret=("ret","mean"))
g=g[g.n>=15].sort_values("mean_ret",ascending=False).head(6)
best=[]
for idx,row in g.iterrows():
    sub=a[(a.sym==idx[0])&(a.off==idx[1])&(a.entry==idx[2])]
    lo,hi=ci(sub); best.append(dict(sym=idx[0],off=idx[1],entry=idx[2],n=int(row.n),days=int(row.days),
              hit=round(row.hit,3),mean_ret=round(row.mean_ret,3),ci_lo=round(lo,3),ci_hi=round(hi,3)))
print(pd.DataFrame(best).to_string(index=False))
print("  NOTE: these are the TOP cells out of 98 searched. Selecting the max of 98 noisy")
print("  estimates biases the mean upward; the CI shown is NOT multiplicity-corrected.")

print("\n\n### H. MONTE CARLO: Rs1,00,000 -> Rs20,00,000, resampling REAL observed trade returns")
print("    2 expiry days/week (NIFTY Tue + SENSEX Thu) = the only weekly 0DTE products that exist.\n")
def sim(pool,f,n_trades,N=20000,seed=3,start=1e5,target=2e6,ruin=1e4):
    rng=np.random.default_rng(seed); pool=np.asarray(pool)
    eq=np.full(N,start); hit=np.zeros(N,bool); dead=np.zeros(N,bool); peak=np.full(N,start)
    for _ in range(n_trades):
        r=rng.choice(pool,N)
        live=~dead
        eq[live]=eq[live]*(1+f*r[live])
        peak=np.maximum(peak,eq)
        hit |= eq>=target
        dead |= eq<=ruin
    return dict(p_target=float(hit.mean()),p_ruin=float(dead.mean()),
                median=float(np.median(eq)),p90=float(np.quantile(eq,.90)),
                p99=float(np.quantile(eq,.99)),mean=float(eq.mean()))

pool_all=E.ret.values
pool_atm=E[E.off==0.0].ret.values
pool_wing=E[E.off>=0.010].ret.values
for name,pool in [("all strikes",pool_all),("ATM only",pool_atm),("wing >=1% OTM",pool_wing)]:
    print(f"-- pool: {name}  (n={len(pool)}, mean per-trade return {pool.mean():+.3f})")
    for f in (0.05,0.20,0.50,1.00):
        for nt in (100,400):
            r=sim(pool,f,nt)
            print(f"   risk {int(f*100):>3}% of equity/trade, {nt:>3} trades (~{nt/2/4.3:.0f} months): "
                  f"P(reach 20L)={r['p_target']*100:6.3f}%  P(ruin)={r['p_ruin']*100:5.1f}%  "
                  f"median=Rs{r['median']:,.0f}  p99=Rs{r['p99']:,.0f}")
    print()

print("### I. WHAT WOULD BE REQUIRED (arithmetic, not a market claim)")
print("    2 expiry days/week -> ~104 0DTE sessions per year.")
for nt in (40,104,208):
    print(f"    {nt:>3} trades to 20x needs {(20**(1/nt)-1)*100:.3f}% net compounded per trade")
obs=pool_all.mean()
print(f"    observed mean net return per trade (realistic pricing): {obs*100:+.2f}%")
print(f"    a strategy with a {obs*100:+.2f}% per-trade edge has NO trade count that reaches 20x.")
