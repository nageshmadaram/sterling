# The 1L → 20L options-buying challenge: measured verdict and the strategy that survives

Date: 2026-09-07. Author: automated study over the attached SterlingLake.
Status: **the challenge as posed is not achievable by buying options. This is now measured, not asserted.**

## 1. Summary

Buying Indian index options intraday has a large, statistically significant negative
expectancy at every strike distance, every entry time, and under every exit rule tested.
The best exit discipline found still loses 16.5% of capital per trade. The compounding
target of 20x is therefore not reachable by long premium: no trade count and no position
size produces it, because the per-trade edge is negative and negative edges compound
toward zero rather than toward a target.

The single most useful number in this document: across 20 real expiry days, 3,803
realistically-priced long-option trades, **the best single trade returned 19.9x capital
and not one trade returned 20x or more.** The screenshots that motivate this challenge
describe 150x and 1000x outcomes. Those did not occur in the sample, at any strike, on
any day.

One structure has a non-negative point estimate: a defined-risk credit spread, which is
the mirror of the losing trade. It earns roughly 2.05% of capital-at-risk per trade with
an 80.8% win rate, but its 95% confidence interval straddles zero. Even taken at face
value, it reaches 20x in about seven years at aggressive sizing, not in a month.

## 2. Why this could be measured without option price history

The attached lake holds 231,143,717 minute bars across 12,246 symbols, and **zero
derivative bars**. It has an instrument master with 35,845 NFO options and 4,532 BFO
options, but no prices for any of them.

That would normally block the study. It does not, because of one property of expiry day:
the terminal value of an index option at expiry is its intrinsic value against the
settlement price. That is a deterministic function of the underlying, and the underlying
is exactly what the lake has at minute resolution.

So the terminal leg of every trade is real observed data, not a model. Only the entry
premium is modelled, and it is modelled from a strictly causal estimate of the index's own
prior realised move over the identical remaining-day window, never from the day being
tested. Settlement is the mean of spot over 15:00–15:30 IST, matching how NSE and BSE
settle index options.

The model layer is then stress-tested three ways, from a case that is impossibly generous
to the buyer to a realistic one:

| Pricing assumption | What it means |
| --- | --- |
| flat vol, no variance-risk premium | options sold at exactly realised vol with zero seller margin, no smile. Impossible in practice; an upper bound on the buyer's case. |
| flat vol, VRP 1.15 | seller charges 15% over realised, still no smile |
| smile + VRP 1.15 | wing strikes priced above ATM vol, as real 0DTE books quote them |

The buy-side conclusion holds in all three, including the impossible one. That is what
makes it robust to the modelling.

## 3. Ground truth pulled from the instrument master

Read from `instruments/latest.parquet` dated 2026-08-14, not assumed:

| Index | Exchange | Lot | Tick | Strike step | Expiry cadence |
| --- | --- | --- | --- | --- | --- |
| NIFTY | NFO | 65 | 0.05 | 50 | weekly, Tuesday |
| SENSEX | BFO | 20 | 0.05 | 100 | weekly, Thursday |
| BANKNIFTY | NFO | 30 | 0.05 | 100 | monthly only |
| BANKEX | BFO | 30 | 0.05 | 100 | monthly only |
| FINNIFTY | NFO | 60 | 0.05 | 50 | monthly only |
| MIDCPNIFTY | NFO | 120 | 0.05 | 25 | monthly only |

This alone constrains the challenge. Only two weekly zero-days-to-expiry products exist:
NIFTY on Tuesday and SENSEX on Thursday. That is **two expiry sessions per week, about 104
per year.** Any plan premised on a daily expiry lottery is premised on instruments that do
not exist. BANKEX, the index in the motivating screenshot, expires monthly.

Note also that the screenshot's own chart contradicts its headline. It shows
`BANKEX 27 Aug 64500 Put` opening at 187.95 and closing at 186.55, a 0.93% decline. The
caption claims 1 lakh became 1.5 crore. The chart shows a flat-to-losing option.

## 4. Result: long options lose, significantly

Net return per trade, sized to roughly ₹5,000 of premium so the flat ₹20-per-order
brokerage is properly amortised. All costs applied: brokerage, STT at 0.1% on the sell
side, exchange turnover, SEBI fee, stamp duty, GST. Confidence intervals are day-clustered
bootstraps, because every strike and entry time on one day shares a single market shock,
so the effective sample size is 20 days, not 3,803 rows.

Realistic pricing, expiry days only:

| Index | Strike | Hit rate | Mean return | 95% CI |
| --- | --- | --- | --- | --- |
| NIFTY | ATM | 48.7% | −38.3% | −53.8% to −20.5% |
| NIFTY | 0.5% OTM | 12.5% | −74.9% | −89.5% to −59.1% |
| NIFTY | 1.0% OTM | 2.5% | −96.5% | −100.7% to −90.5% |
| NIFTY | 1.5% OTM | 0.0% | −101.1% | −101.1% to −101.1% |
| SENSEX | ATM | 48.8% | −30.0% | −52.6% to −3.1% |
| SENSEX | 1.0% OTM | 3.2% | −72.0% | −101.1% to −17.1% |
| SENSEX | 2.0% OTM | 1.4% | −95.5% | −101.1% to −84.5% |

Every confidence interval lies entirely below zero. A NIFTY strike 1.5% out of the money
at any entry time finished in the money **zero times in 20 expiry days**.

### The apparent far-OTM edge is a pricing artifact

Under flat vol with no variance-risk premium, SENSEX at 1.5–2.0% OTM appears to return
+455% and +488% per trade. That looks like the loophole. It is not one, for two reasons
that the data itself shows.

First, the confidence interval on that +455% runs from −101% to +1567%. The entire result
rests on roughly five lucky days out of twenty.

Second, and decisively: flat ATM vol structurally underprices the wing. Real distributions
have fatter tails than lognormal, market makers know this, and that is precisely why real
option books quote a smile that lifts far-OTM prices above flat-vol theoretical. Apply a
realistic smile and the same cell goes from +455% to −79.5%. The "edge" was never in the
market. It was in pricing the wing as if the smile did not exist.

### Scalping with stops does not rescue it

Marking the option to market minute-by-minute along the real spot path, decaying time
value, exiting at first touch of target or stop, paying one tick of spread each way:

| Exit rule | NIFTY mean | NIFTY 95% CI | SENSEX mean | SENSEX 95% CI |
| --- | --- | --- | --- | --- |
| +50% / −30% | −16.5% | −20.5% to −12.4% | −16.6% | −20.2% to −12.9% |
| +100% / −50% | −25.3% | −34.1% to −16.0% | −27.0% | −35.9% to −17.4% |
| +200% / −50% | −21.6% | −33.6% to −8.8% | −25.8% | −38.2% to −11.2% |
| +300% / −50% | −20.8% | −33.8% to −6.4% | −23.3% | −39.4% to −2.8% |
| hold to 15:20 | −40.0% | −58.3% to −19.5% | −31.3% | −66.4% to +27.0% |

Tight risk management is the largest single improvement measured: it cuts the bleed from
−40% to −16.5%. It never crosses zero. This is the expected result and it is worth stating
as a principle, because it kills a whole family of hoped-for fixes: **exit rules reshape
the return distribution but cannot add drift.** A negative-expectancy position remains
negative under every stop and target, and each additional exit pays more spread and more
fees. Position management is not an edge. It is damage control on an edge you already have.

An opening-range-breakout filter also fails. Aligned trades (spot above the opening-range
high while buying calls, or below the low while buying puts) returned −75.5% on NIFTY and
−94.8% on SENSEX, both worse than the unaligned set on SENSEX.

## 5. What this does to the 20x target

Monte Carlo over 20,000 paths, resampling the **real observed** trade returns rather than
a fitted distribution, at two expiry sessions per week:

| Pool | Sizing | P(ever touch ₹20L) | P(ruin) | Median outcome |
| --- | --- | --- | --- | --- |
| ATM only | 5% per trade | 0.000% | 24.1% | ₹14,937 |
| ATM only | 50% per trade | 0.125% | 100% | ₹7,256 |
| ATM only | 100% per trade | 0.515% | 100% | ₹-1,240 |
| all strikes | 100% per trade | 0.100% | 100% | ₹-1,085 |
| wing ≥1% OTM | 50% per trade | 0.010% | 100% | ₹5,987 |

The best configuration for reaching the target gives roughly a **1-in-200 chance of ever
touching ₹20 lakh and a 100% chance of ruin**. Increasing risk per trade raises the chance
of touching the target and raises the chance of ruin faster. That is the structural
signature of a negative-edge bet: aggression buys lottery tickets, not expectancy.

The required arithmetic, for contrast:

| Trades | Net compounded return needed per trade to reach 20x |
| --- | --- |
| 40 | 7.777% |
| 104 (one year) | 2.922% |
| 208 (two years) | 1.451% |

Measured mean per-trade return under realistic pricing: **−73.76%**.

## 6. The one structure with a non-negative estimate

If buying loses 25–40% of premium in expectation, the mirror should collect it. Testing
defined-risk credit spreads on identical data, selling a near strike and buying a further
strike as a hard cap, return measured against maximum loss:

| Index | Short strike | Width | Win rate | Mean on risk | 95% CI |
| --- | --- | --- | --- | --- | --- |
| SENSEX | 0.4% OTM | 3 strikes | 84.0% | +5.48% | −3.31% to +12.98% |
| SENSEX | 0.6% OTM | 2 strikes | 90.5% | +4.44% | −3.79% to +10.79% |
| NIFTY | 0.6% OTM | 3 strikes | 87.5% | +2.75% | −1.92% to +6.77% |
| pooled | — | — | 80.8% | +2.05% | −3.39% to +7.11% |

Two honest caveats, both important.

The confidence interval straddles zero. With 20 expiry days you can prove the buyer loses,
because the loss is large relative to the noise, but you cannot prove the seller wins,
because the gain is small relative to the same noise. The statistical power is asymmetric.

And this side of the test is partly circular. The buy-side conclusion survives even the
impossible no-premium no-smile case, so it does not depend on the pricing model. The
sell-side result is the mirror of that same model: if the model overprices options, the
seller looks good by construction. It needs real quotes to confirm.

Taken at face value, at 2.05% on risk and two trades per week:

| Risk per trade | Per-trade equity growth | Trades to 20x | Time |
| --- | --- | --- | --- |
| 5% | 0.103% | 2,924 | 28.1 years |
| 10% | 0.205% | 1,463 | 14.1 years |
| 20% | 0.410% | 732 | 7.0 years |

That is the honest ceiling of the best structure found: roughly 4% a month at aggressive
sizing, with real drawdowns. It is a business. It is not a 20x month.

## 7. Why the motivating examples do not transfer

The DIXON 14750 CE arithmetic is internally correct and operationally impossible.
₹1,00,000 at ₹0.05 with a lot size of 50 is 40,000 lots, or 2,000,000 underlying shares.
A contract trading at the ₹0.05 tick floor has a resting book measured in hundreds of lots,
not tens of thousands. Buying that size moves the contract to its price band; there is no
bid for 40,000 lots at ₹500 on the other side; and the position exceeds client-level
position limits by a wide margin. The number is real. The fill is not.

The tick floor itself was measured directly. Across all 102 sessions and 23,704
observations of contracts priced at exactly ₹0.05, **15 finished in the money, a rate of
0.063%**. On expiry days specifically, 1 out of 4,680, a rate of 0.021%. The largest
multiple observed anywhere in the study was 2,072x, so these outcomes are genuinely real.
Their mean terminal value was ₹0.0168 against a ₹0.05 purchase price, an expected value of
**one third of the price paid.**

That is the complete answer to the question of whether mathematics can be made to win here.
It can, and it already has: the market prices these contracts at three times their expected
value, and that pricing is why they exist to be bought. The 2,072x outcome is not evidence
against the pricing. It is the thing the pricing is charging for.

Add the ₹20-per-order flat brokerage and it gets worse. A single lot of a ₹0.05 SENSEX
option costs ₹1.00 in premium and ₹40 in round-trip brokerage. The fee is forty times the
position. Lottery tickets are the one trade structure where flat fees dominate completely.

## 8. Strategy specification, if you build it anyway

This is the spec worth coding, in dependency order. Note that step one is the only one that
can change the verdict, and none of the later steps are worth trusting until it exists.

**Phase 0 — capture real option data. This is the blocker and the highest-value build.**
Everything above prices options from a model. The model is stress-tested and the buy-side
conclusion survives its most generous setting, but no conclusion here is worth live capital
until it is re-run against executable quotes. Subscribe the NIFTY and SENSEX 0DTE chains
from 09:15 to 15:30 on Tuesdays and Thursdays. Persist best bid, best ask, quoted size, and
depth with both exchange event time and local receipt time. Retain the daily instrument
master so strike, lot, and expiry are historical facts and not today's values applied
backwards. Two months of this makes every table above re-runnable against reality.

**Phase 1 — replay harness.** Replay strictly on receipt time so no record can influence a
decision before it arrived. Every entry and exit must price against that specific contract's
own book, never against the index and never against a mid you could not have hit. Re-run
sections 4 through 6 against real quotes. If the buy-side result inverts, the model was
wrong and this document is void. If it holds, proceed.

**Phase 2 — the candidate.** SENSEX 0DTE defined-risk credit spread, because SENSEX showed
the better estimate on both sides and its strike step of 100 against a spot near 80,000
gives 0.125% granularity, finer than NIFTY's 0.205%, with a lot size of 20 that fits a
small account.

- Trade only Thursdays for SENSEX and Tuesdays for NIFTY, verified against the instrument
  master's expiry field rather than the weekday.
- Entry window 10:00 to 13:00. Short leg 0.4% to 0.6% out of the money. Long leg two to
  three strikes further out as a hard cap.
- Require a positive uncrossed book on both legs, book age under one second, and combined
  spread under 1.0% of the credit. Skip otherwise. Missing depth is a skip, not an assumed
  zero cost.
- Risk per trade is maximum loss, which is width minus credit, times lot size. Cap it at
  1% of reconciled equity while unvalidated. The 20% figure in section 6 is what the
  arithmetic permits, not what an unvalidated strategy earns.
- Exit at 15:20 or on the short leg going in the money, whichever comes first. Never carry
  to settlement: STT on an exercised in-the-money index option is 0.125% of intrinsic
  settlement value, not of premium, which is a materially worse cost than closing.
- One position at a time. Daily loss breaker at 3% of starting equity. Campaign pause at
  10% below the high-water mark.

**Phase 3 — falsification before capital.** Chronological splits only, never random.
Require the one-sided 95% lower bound on mean return, clustered by trading day, to sit
above zero on a validation window that was never inspected during development. The pooled
lower bound today is −3.39%. Until that number is positive on untouched data, the correct
position size is zero.

## 9. What would change this verdict

Real executable option quotes, and nothing else. Every limitation of this study traces
back to their absence: the twenty-day sample, the modelled entry premium, the assumed
smile, the asymmetric statistical power between the two sides. The finding that survives
regardless is section 4's, because it holds even when options are priced at zero seller
margin with no smile, which is better than any real market has ever offered a buyer.

The finding that does not survive without real data is section 6's. Build Phase 0 first.
