# Snapback — audit of the model, 2026-09-12

What was wrong with the measurement, what it was worth, and what survived.
`VALIDATION_REPORT.md` carries the verdict; this file carries the reasoning.

Everything here was found by attacking the engine's own numbers rather than by
looking for a better configuration. **Five of the eight findings made the
strategy look worse**, and those are the valuable ones. Only one change was
kept for making it look better, and that one was confirmed out of sample before
it shipped.

---

## 1. The hedge earned spot returns but a future pays carry — MATERIAL

**The bug.** The hedge is long index futures (the put is short the market, so
the offset is long it). The harness priced its P&L off **spot** index returns,
which contain the full equity return. A future trades above spot by the cost of
carry `(r − q)` and converges down to it, so a long position gives that up. The
model was collecting a return the position does not earn.

**What it was worth.** The hedge notional runs about **six times** the option's
premium outlay, so 5.2% a year over a ten-session hold is ~0.85% of the outlay
per trade — against a mean trade return of +1.4%. Charging it took the full
sample from **+2.16% to +1.54%** per entry day. It was roughly 40% of the
reported edge.

**Fixed.** `FuturesCost.carry`, charged per session on the notional actually
carried.

## 2. Every strike was priced at one ATM vol — REVERSED A CONCLUSION

**The bug.** The flat-vol trap, which this repository has documented before: a
far-OTM wing priced at ATM vol once looked like a +455% edge and became −79.5%
under a realistic smile. Snapback had no smile at all.

**What it was worth.** It reversed which strike looks good:

| delta | flat vol | realistic skew |
|---|---|---|
| 0.20 | +8.31% per entry day, 6/9 years | **−5.97%, 0/9** |
| 0.30 | +2.67%, 7/9 | **−2.46%, 3/9** |
| 0.70 | +1.54%, 3/9 | **+4.70%, 7/9** |

A cheap out-of-the-money put is cheap only in a model without skew. The shipped
0.70 delta was chosen for an unrelated reason — delta per rupee of theta — and
turns out to be right for a second, independent one: an in-the-money put sits on
the *low* side of the equity skew.

**Fixed.** `pricing.smile_vol`, additive in log-moneyness, clamped to a band
around ATM because a linear skew extrapolated far prices contracts no market
quotes.

## 3. The skew was symmetric, and real skew is not — CAUGHT BEFORE SHIPPING

**The bug.** Applying the out-of-the-money steepness to strikes ABOVE spot hands
an in-the-money put a discount the market does not give. This engine buys
in-the-money puts, so a symmetric slope is a flattering assumption.

**What it was worth.** At 0.70 delta, the whole difference between a headline
and a result:

| in-the-money slope | mean/day | Sharpe | compounded | years + |
|---|---|---|---|---|
| 1.6 (symmetric — wrong) | +4.70% | 1.05 | +313% | 7/9 |
| 0.8 | +2.67% | 0.66 | +97% | 5/9 |
| **0.5 (realistic)** | **+2.01%** | **0.59** | **+74%** | **5/9** |
| 0.0 (flat above spot) | +1.53% | 0.27 | +9% | 3/9 |

I had the +4.70% written into the report before running this. It was the same
trap as finding 2, on the other wing.

**Fixed.** `smile_itm_slope`, defaulting to 0.5 — roughly a third of the
out-of-the-money steepness, which is the shape NIFTY quotes. Both wings are
swept, neither assumed.

## 4. An EMA that had not formed ANSWERED the gate's question — MATERIAL

**The bug.** `market_state` stamped every bar the EMA had not filled on with
`False` — "not above the EMA". Under the shipped `bearish` filter, *not above*
is the **open** state. So every session before the index EMA warmed up waved the
gate through, silently, and `warmup_bars()` did not count `market_ema` at all —
it warmed the per-instrument windows and left the gate's own tape short.

**How it surfaced.** A *stricter* gate produced **twice** the trades of a looser
one: at `market_ema = 100`, past the 80-bar warm-up, every walk-forward fold
opened with an unfilled EMA and took everything. 2,077 out-of-sample trades
against 1,034 in the same rule's full sample — an impossible ordering, which is
the only reason it was caught.

**What it was worth.** It was inflating the shipped configuration too. The
audited out-of-sample mean fell from **+4.78% to +3.46%** per entry day once the
gate could no longer open itself.

**Fixed.** An unfilled session is now OMITTED from the gate map, and `allowed`
already refuses a day it has no entry for — the reading the rest of the module
always used. `warmup_bars()` includes `market_ema`.

## 5. The hedge was sized once at entry — SMALL, AND MY AUDIT OF IT WAS WRONG

**The suspicion.** A put moving into the money has a larger |delta|, so the
trade's market sensitivity grows as it wins and a hedge frozen at entry covers
less of it. An audit holding the notional at the entry spot put the residual at
**27% of the edge**, correlated −0.42 with the market.

**What it was actually worth: 2%.** That audit was wrong. It varied the delta
while holding the spot fixed, and the two move together — when the put wins,
spot has fallen by the same move that raised the delta. The full calculation
gives +2.16% rebalanced against +2.21% static.

**Fixed anyway.** `hedge.rebalanced` sizes on each session's own delta and
spot. A static hedge that happens to be right is still an assumption; this one
is a calculation.

## 6. The replay ignored its own position cap — MATERIAL, FIXED EARLIER

It opened as many concurrent positions as the tape offered, so its equity curve
compounded a book nobody could hold. Now one time-ordered portfolio; a signal
arriving with the book full is dropped and counted, not queued.

## 7. Vertical spreads — TESTED AND REJECTED, WITH A MECHANISM

A debit spread halves the premium and most of the vol-model sensitivity, which
is this engine's weakest input. Every variant lost, and the break-even VRP was
undefined — they lose even at a nearly-free option.

The reason is in the return distribution, and it matters beyond spreads:

```
skew +1.95   kurtosis +7.4   win rate 41%   median trade −9.8%
top  1% of trades (13 of 1373) → 148% of total P&L
top  5% of trades (68)         → 446% of total P&L
the other 80% of trades        → −890%
```

**The edge is entirely in the right tail.** Anything that caps the payoff
removes the part that pays for everything else. That is what a vertical does.

It is also why the premium stop survives despite cutting the mean: removing it
takes the mean from +2.01% to +3.93% but the COMPOUNDED return from +74% to
+56%, and the drawdown from −46% to −58%. A tail-driven book is exactly the one
where the arithmetic and geometric answers disagree.

## 8. A fixed horizon closed the tail — THE ONE CHANGE THAT ADDED RETURN

Finding 7 states the mechanism and this acts on it. If the top 1% of trades
carry 148% of the P&L, a 15-session horizon closes the few trades that pay for
all the rest, on a session count that knows nothing about the trade.

`runner_mult` holds a position past the horizon **only while it is already worth
1.5× what it cost**, under a 25% give-back ratchet, and never past five days
before expiry. A trade below the multiple closes on its horizon exactly as
before — the rule reaches the tail without holding every loser longer.

It is the only change here proposed for a reason and then confirmed out of
sample rather than chosen from a sweep:

| | entry days | mean/day | Sharpe | break-even VRP | max DD |
|---|---|---|---|---|---|
| horizon only (out of sample) | 411 | +3.46% | 0.55 | 1.754 | −26.1% |
| **with the runner (out of sample)** | **409** | **+4.03%** | **0.61** | **2.012** | **−29.4%** |

The entry days barely move, which is the point: it changes the exit, not the
sample. The give-back matters as much as the trigger — at 1.5×, a 15% ratchet
gives +4.09% and 25% gives +3.48%, but 40% collapses to +1.86% and *no* ratchet
to +2.35%. The tail is given back faster than it accrues.

---

## What the audit did NOT find

* **Look-ahead.** The gate, the beta, the realised vol and the prior-session
  extremes are each tested for causality; the fills are next-session opens.
* **A cheaper expression.** Spreads lose, lower delta was an artefact, and a
  bought index call as the hedge is a second long-market bet rather than a hedge.
* **A better parameter.** 0.70 delta is an interior optimum under the realistic
  skew. Nothing here was re-tuned to a better number — see the next section for
  the ones that looked better and were refused.

## Sweeps that produced a better number and were REFUSED

The full sample has 455 entry days and a Sharpe under 1. On a surface that
noisy, the best cell of a sweep is a number, not a finding. Each of these was
kept at its existing value:

| swept | best cell | why it was refused |
|---|---|---|
| `market_ema` | 150: +5.11%/day, Sharpe 1.37 against 0.72 | The response is not a curve — 75 sessions reads WORSE than either neighbour (0.50). Slower gates also cut the sample from 455 entry days to 324. Gated out of sample at 100, it won the mean and lost the Sharpe, the compounded return, the drawdown and the year count, with its fold-selected book going NEGATIVE (−0.92%). |
| `cooldown_days` | 3: +4.51%/day against +3.40% | A single-point spike: 0 gives +3.45%, 5 gives +3.40%, 10 gives +1.90%. Both neighbours are lower. |
| `max_open_positions` | 30: +3.79% against +3.40% | Monotone and mechanically sensible, but the drawdown goes −47.6% to −56.1%. The cap is a capital constraint, not a strategy parameter. |
| `one_position_per_underlying` | off: +3.84% against +3.40% | Two fades of one instrument are one bet with two tickets. The gain is inside the noise and the risk argument is not. |
| `min_dte` | 50: Sharpe 0.73, DD −38.2% | Better geometry, worse mean, and it interacts with the runner — gated together with it, 50 was worse than 40 on every axis. |

## Concentration: two names, half the rupees

Out of sample, **ADANIGREEN and YESBANK produce 49% of the net P&L** across 191
names. That is worth knowing before sizing anything in rupees.

It is not the return edge, though, and the control says which: dropping those
two names takes the mean from +3.40% to +3.26% per entry day, which sits at the
**21st percentile** of 200 random two-name removals. The rupee concentration is
a lot-size effect — those names' contracts are large — not two crashes carrying
a strategy.

## Known and unfixed

* **Dividend yield is omitted** from Black-Scholes (`q = 0`), which understates a
  put slightly. Entry and exit are priced the same way so most of it cancels in
  the return ratio; it is second-order against the carry and the skew.
* **The hedge needs margin**, which this engine does not model at all. The
  premium budget covers the option leg only.
* **Survivorship.** The 202-name universe is today's F&O list applied to 2017.
  The entry-timing permutation controls for it — the null draws from the same
  instruments — but the level does not.
* **The smile is a parameterisation, not a measurement.** No store here holds
  option prices. The break-even VRP is what bounds the damage, and it now
  carries two slopes' worth of uncertainty rather than none.
* **The fold books restart.** Each walk-forward window begins with an empty
  portfolio, so `max_open_positions` binds less there than in a continuous book.
  It is a small effect at the shipped hold length and a large one for any rule
  that lengthens holds — which is how finding 4 was first mistaken for it.
