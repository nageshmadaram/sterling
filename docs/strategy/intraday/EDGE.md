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

## Six things tried before the one that worked

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

## What fixed it: trade a quarter as often, in the window that pays

Costs are per TRADE. The edge is per SIGNAL. So the only direction that helps is
fewer, better signals — and the measurement says which ones.

Forward move by time of day, 1714 non-overlapping signals:

| window | n | forward move | t |
|---|---|---|---|
| 09:18–09:48 | 414 | +6.84 bp | 1.99 |
| 09:48–11:48 | 432 | +5.57 bp | 1.95 |
| 11:48–13:30 | 435 | +7.44 bp | 1.99 |
| **13:30–14:54** | **433** | **+21.70 bp** | **2.81** |

Three times the edge, same cost per trade. Restricting entries to the afternoon
cuts trade count ~75% and keeps the part that pays.

**Why it works matters before you trust it.** A two-hour hold from 13:30 runs
into and past the close, so the captured return includes the overnight gap.
That is real money and a DIFFERENT risk from the intraday one these rules were
written for — a stop cannot protect against a gap.

### Shipped configuration

```
session_start          13:30      (was 09:20)
no_entry_after         14:55      (was 15:00)
exit_after_bars        24         (~2 hours, was: no time exit)
close_at_session_end   False      (was True)
stop_widen_mult        4.0        (was 1.0)
```

### Out of sample

| | trades | PF | net | Sharpe | maxDD | p | symbols + |
|---|---|---|---|---|---|---|---|
| `ma_ribbon` | 124 | **1.25** | +89,628 | +0.84 | -7.24R | 0.041 | 6/9 |
| `vwap_supertrend` | 150 | 1.13 | +51,781 | +0.44 | -5.31R | 0.083 | 5/9 |
| `pivot_break` | 343 | 1.17 | +198,257 | +0.82 | -63.19R | 0.010 | 3/9 |

All three are profitable out of sample. `ma_ribbon` passes six of the seven
gates.

### Why none is PROMOTED

The deflated Sharpe. `ma_ribbon` scores 0.350 against a 0.5 bar, and that is
correct: this window was chosen by looking at a four-way split of the same
data, after roughly sixty other configurations had been tried. A search that
size can produce a result this good by luck, and the statistic says so.

That is not a reason to discard the result. It is a reason to want the ONE
thing that would settle it — out-of-period data this search has never touched.
Until then auto-execution stays gated and the board says why.

`pivot_break` should be treated as the weakest of the three whatever its net
says: 3 of 9 symbols profitable and a 63R drawdown mean one or two instruments
carried it.

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
