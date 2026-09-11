# Does any of this have an edge?

Measured, not argued. Nine symbols, 173 sessions of real 5-minute bars,
2026-01-01 to 2026-09-11. Every number below is reproducible from
`study/intraday_walkforward.py` and the engine's own functions.

## The entries do predict — a little, and slowly

Forward move in the signal's own direction, **non-overlapping** windows
(overlapping ones reuse the same price path and inflate the t-statistic):

| | horizon | mean | t | n |
|---|---|---|---|---|
| `pivot_break` | 60 min | +3.54 bp | **3.62** | 3330 |
| `vwap_supertrend` | 120 min | +14.92 bp | **3.43** | 746 |
| `ma_ribbon` | 120 min | +6.96 bp | **2.57** | 968 |

That is a real directional signal. It is also **3 to 15 basis points**, and it
takes one to two hours to arrive.

## Why trading it still loses

A round trip costs about **2.3 bp** on the underlying at futures rates
(slippage dominates; brokerage and taxes are ~0.3 bp). So the edge clears costs
on paper.

It does not clear the **stop**. The structural stops these strategies use — a
candle's low, the slow EMA, VWAP — sit 10 to 20 bp away. That is two to four
times the entire edge, so noise reaches the stop long before the signal has had
time to be right, and the strategy books the noise.

## Six things tried, all measured

| | result |
|---|---|
| Remove the rule exits (they averaged -0.6R) | **worse** — they were cutting trades that went on to lose more |
| Slower timeframes (15m, 30m) | worse at every step |
| Fixed-time exits matched to the edge (60/120 min) | no better |
| Widen the stop 2x, 3x, 4x | no better |
| Remove the stop entirely | positive gross, still negative net |
| Let the hold span the session close | **full sample: PF 1.20, Sharpe 1.37, p = 0.003** |

That last one looked like the answer — the edge is a two-hour move and closing
at 15:15 was cutting it in half. On the FULL SAMPLE both surviving strategies
reached PF ≈ 1.2.

Walk-forward, it evaporates:

```
ma_ribbon         OOS 511 trades  PF 0.979  Sharpe -0.19  DSR 0.000
vwap_supertrend   OOS 476 trades  PF 1.009  Sharpe +0.07  DSR 0.019
```

The gross edge is still there out-of-sample — `ma_ribbon` made 121,401 gross —
and **116.9% of it went to costs**.

## The honest state

The signal is real and too small. The gap is not a threshold that needs tuning;
it is roughly an order of magnitude between a 3-15 bp edge and what it costs to
act on one this often.

Three things would change the answer, in descending order of how much:

1. **Fewer trades on the same edge.** The edge is per-signal; the cost is
   per-trade. A filter that keeps the top decile of signals and discards the
   rest pays 1/10th the cost for a fraction of the edge — worth it only if the
   filter is selective on something real.
2. **A cheaper instrument.** These trade OPTIONS live, where costs are roughly
   an order of magnitude worse than the futures rates measured here. Everything
   above is the OPTIMISTIC lens.
3. **More data.** 173 sessions is about six folds. A 3 bp effect needs far more
   than that to separate from a 2.3 bp cost with any confidence.

What would NOT change it: another threshold grid. Every variant tried raises
the bar the deflated Sharpe sets, so a wider search does not find an edge — it
buys a worse one at a higher price.
