# Snapback — validation report

**This file is the authority.** Anything about Snapback stated anywhere else and
contradicted here is stale.

## Verdict: the market-neutral version works; the directional one did not

Measured 2026-09-12 on **202 F&O underlyings over nine years** (2017-09 to
2026-09, 2,232 sessions, 430,223 daily bars), with a 20-position portfolio cap,
the put's market delta hedged out with an index future, a realistic asymmetric
volatility skew, and futures carry charged.

| out-of-sample | **shipped** | no runner | unhedged (previous) |
|---|---|---|---|
| trades / entry days | 1133 / 409 | 1168 / 411 | 487 / 282 |
| mean per entry day | **+4.03%** | +3.46% | −1.08% |
| entry-timing permutation | **p = 0.0164** | p = 0.0164 | p = 0.37 |
| break-even VRP (market charges 1.15–1.30) | **2.012** | 1.754 | 1.00 |
| Sharpe | 0.610 | 0.550 | −0.90 |
| max drawdown at 2% per position | −29.45% | −26.1% | −32.6% |
| gate checks passed | **6 of 9** | 6 of 9 | 3 of 9 |

**Still NOT promoted.** Three checks fail and they are stated below. But the
entry-timing permutation — the test every other strategy in this repository has
failed, and the one the directional version failed at p=0.37 — passes at the
floor the round count allows, and the break-even vol multiple is now **1.55×
the top of the band the market actually charges**. That is the first time either
has happened here.

Reproduce:

```bash
.venv/bin/python -m study.snapback_backfill --years 9
.venv/bin/python -m study.snapback_research --part gate --universe fno --rounds 60
```

Any single field can be varied without editing the defaults:

```bash
.venv/bin/python -m study.snapback_research --part gate --universe fno --set market_ema=100
```

## Why hedging was the fix

The signal's edge is **relative**, not directional. Against a day-matched
unconditional put — same contract rule, same sessions, so the market's own move
cancels — it has a real excess of about **+1.5 percentage points**: the names it
picks do under-perform. A bought put, though, is a large SHORT position in the
market, and over nine rising years that cost far more than 1.5 points earned.
The edge was real and the expression was wrong.

`hedge_mode = index_futures` shorts the put's own market delta, beta-weighted on
a causal 60-session regression, with an index future, rebalanced each session
and charged carry at `(r − q) ≈ 5.2%` a year.

**A future, not a bought index call.** The options-only version was tried first
and beat an exact hedge by almost a point — which is the tell that it had
stopped being a hedge. A bought call is convex, so over a rising sample it gains
MORE than the put's market loss and quietly becomes a second long-market,
long-vol position that the bull run paid for. A future offsets by construction.
It needs MARGIN rather than premium, which the premium budget does not cover and
this engine does not model.

## Why the runner is the only return-adding change kept

The return distribution is the finding, and everything else follows from it:

```
skew +1.95   kurtosis +7.4   win rate 39%   median trade −15.5%
top  1% of trades → 148% of total P&L
the other 80%     → −890%
```

The edge is **entirely in the right tail**. A 15-session horizon therefore closes
the few trades that pay for all the rest, on a session count that knows nothing
about the trade. `runner_mult = 1.5` holds a position past the horizon only while
it is already worth 1.5× what it cost, under a 25% give-back ratchet, never past
five days before expiry. A trade below the multiple closes on its horizon
exactly as before.

Out of sample it moved the book from **+3.46% to +4.03%** per entry day and the
break-even VRP from **1.754 to 2.012**, on 409 entry days against 411 — it
changes the exit, not the sample. The cost is drawdown, −26.1% to −29.4%.

The same distribution explains two things that look like contradictions:
vertical spreads all lose (they cap the tail; break-even VRP undefined), and the
premium stop is KEPT despite cutting the mean from +3.93% to +2.01%, because it
takes the COMPOUNDED return from +56% to +74% and the drawdown from −58% to
−46%. On a tail book the arithmetic and geometric answers disagree, and only one
of them is what an account earns.

## The three books

| book | question | trades | entry days | mean/day | Sharpe | max DD |
|---|---|---|---|---|---|---|
| **fixed, out of sample** | does the RULE generalise? | 1133 | 409 | **+4.03%** | 0.610 | −29.45% |
| selected, out of sample | does PARAMETER CHOICE generalise? | 1050 | 391 | +2.63% | 0.048 | −39.22% |
| full sample (optimistic) | what the research produced | 1161 | 455 | +3.63% | 0.898 | −25.80% |

Out-of-sample years, fixed book: 2018 −3.76, 2019 +0.38, 2020 +16.20,
2021 −0.62, 2022 +2.72, 2023 +2.65, 2024 +2.93, 2025 +0.74, 2026 −0.15.

Selecting parameters per fold is **worse** than leaving them fixed (+2.63%
against +4.03%, Sharpe 0.05 against 0.61), which is the usual answer on a sample
this size and the reason the shipped defaults are fixed.

## Gate scorecard

| check | result |
|---|---|
| enough trades | PASS |
| enough days | PASS |
| profitable | PASS |
| beats random timing | PASS |
| deflated sharpe | **FAIL** |
| priced edge | PASS |
| consistent across years | **FAIL** |
| survivable drawdown | PASS |
| mean proven | **FAIL** |

The three failures, verbatim from the harness:

* deflated Sharpe 0.000 < 0.5 — the result does not survive how many variants were tried
* only 67% of calendar years profitable, needs 100%
* day-clustered 95% interval [−1.52%, +8.58%] includes zero — the DIRECTION is established, the SIZE is not

On the deflated Sharpe specifically: it is computed against **396 variants** —
33 folds times a 12-configuration grid — and the FIXED book does not use that
selector at all. The number is honest for the selected book and over-penalises
the fixed one. It is reported unadjusted rather than quietly recomputed on a
smaller denominator, because picking the denominator after seeing the answer is
how a gate stops being one.

## What happened to the earlier results

Two earlier headlines are now superseded, in opposite directions, and both are
worth keeping.

**+6.28% per entry day, p = 0.029, seven of nine checks.** Measured on **19
instruments over 3 years**. It was period and universe selection. The same 19
names over nine years average **−0.38%**; widening to 202 names takes that to
**−1.27%**; and the permutation moved from p = 0.029 to **p = 0.37**. A p-value
that moves like that under more data was never evidence of timing.

**+4.78% per entry day, break-even 1.728.** Measured five hours before this run,
with a gate that could open itself — see finding 4 in `AUDIT.md`. Sessions where
the index EMA had not yet formed were stamped "not above the EMA", which under a
`bearish` filter is the OPEN state. Fixing it took the shipped configuration's
out-of-sample mean from +4.78% down to **+3.46%**, before the runner took it
back to +4.03%.

## Why the DIRECTIONAL version lost, in three parts

1. **Beta.** A bought put is a large short position in the market. Over nine
   years of rising Indian equity that cost far more than the 1.5 points of
   relative edge earned. **This is what the hedge removes.**
2. **Cost.** A round trip is roughly 1.6% of premium. Unhedged, the break-even
   VRP was 1.00 — the trade stopped paying at a vol multiple BELOW what the
   market charges, so the edge sat inside the modelling error.
3. **Variance drag.** With a 39% win rate and a hard floor at −100% of premium,
   a book can have a positive arithmetic mean and a negative geometric one.
   Unhedged, every capped configuration compounded negative (cap 5 → −95%,
   cap 20 → −34%, uncapped → −24%). Hedged, the same 20-position book compounds
   positive — removing the market removes most of the variance as well as the
   drift, which is why the cap could be loosened from 5 to 20.

## The market gate

Gating entries to sessions where NIFTY sits **below its own 50-session EMA**
moves the ungated directional book from −1.32% to +1.08% per entry day and
roughly doubles the excess over the day-matched baseline. It is kept alongside
the hedge: the two address the same problem from opposite ends — the gate avoids
the exposure, the hedge removes it — and measured, keeping both is better than
either alone.

**The 50 is not the best number on this sample and is kept anyway.** EMAs of 100
to 200 all read better on the full sample (Sharpe 1.0–1.4 against 0.72), but the
response is not a curve — 75 sessions reads worse than either neighbour — and
slower gates cut the sample from 455 entry days to 324. Gated out of sample at
100, the slower version won the mean and lost the Sharpe, the compounded return,
the drawdown and the year count, with its fold-selected book going negative.
`AUDIT.md` lists the four other sweeps whose best cell was refused for the same
reason.

## What was tried and rejected

Each was motivated before it was run, and each is reported whichever way it went.

| idea | result |
|---|---|
| Index-futures hedge | **KEPT — the change that turned the verdict.** OOS −1.08% → +3.04% at the time, permutation 0.37 → 0.016 |
| Runner past the horizon | **KEPT — confirmed out of sample.** +3.46% → +4.03%, break-even 1.754 → 2.012 |
| Market-regime gate | KEPT — ungated −1.32%, gated +1.08% per entry day |
| Hold 15 sessions rather than 10 | KEPT — +2.01% → +3.40%, and under two different pricing models |
| Bought index CALL as the hedge | **REJECTED** — beat an exact hedge by ~1pp, which means it was a second long-market bet |
| Vertical debit spreads | **REJECTED** — every variant lost and the break-even VRP was undefined; they cap the tail that carries the book |
| Slower market EMA (100–200) | **REJECTED** — better full-sample number, non-monotone surface, 29% less sample, worse out of sample on four of five measures |
| `cooldown_days = 3` | **REJECTED** — +4.51% against +3.40%, but both neighbours are lower; a single-point spike |
| More stretch = stronger signal | **REJECTED** — stretch is monotonically WORSE: 1.5–2 ATR −0.97%, 3–4 ATR −5.88%, 4+ ATR −6.98% |
| Relative stretch vs the index as a ranker | **REJECTED** — ranking by the MOST relative stretch is worse (+0.71%) than the least (+2.86%) |
| Buying calls into the same extension (momentum) | **REJECTED** — excess −4.87pp |
| `fade_down` into calls | **REJECTED** — negative excess in every regime (−0.78 to −4.85pp) |
| Shorter tenor / lower delta | **REJECTED** — mean return flatters cheap options, and the skew reverses the ranking entirely |

## Bugs this measurement exposed

Six, all found by widening the sample or by attacking the model, and four of
which produced normal-looking numbers. `AUDIT.md` carries the arithmetic.

1. **An unformed EMA answered the gate's question.** Sessions before the index
   EMA filled were stamped "not above", which under `bearish` is the OPEN state,
   and `warmup_bars()` did not count `market_ema` at all. It surfaced only
   because a STRICTER gate produced twice the trades of a looser one. Worth
   −1.32 points per entry day to the shipped configuration.
2. **Futures carry was never charged** — about 40% of the reported edge.
3. **Every strike was priced at one ATM vol.** The flat-vol trap, both wings: it
   reversed which delta looks cheap, and a symmetric skew then handed the
   in-the-money put a discount no market gives.
4. **`to_bars` did not understand the repo's own `Candle` shape.** Every live
   candle was unparseable, so the scan built an empty tape for all 200
   instruments and reported "no signals" — indistinguishable from a quiet
   market. `evaluate_symbol` now raises rather than returning `[]`.
5. **`chain_rows_for` returns dicts spelled `call`/`put`,** not objects spelled
   `CE`/`PE`. Every contract lookup missed and reported "no listed contract".
6. **The replay never enforced `max_open_positions`,** so its equity curve
   compounded a book nobody could hold. Plus `build_universe`'s fourteen-name
   registry silently capping the universe at 18 instruments, and a hardcoded
   instrument table with the wrong lot size for NIFTY, TATASTEEL and TCS.

## Concentration

Out of sample, **ADANIGREEN and YESBANK produce 49% of the net rupees** across
191 names. That matters for sizing. It is not the return edge: dropping both
takes the mean from +3.40% to +3.26% per entry day, the 21st percentile of 200
random two-name removals. The rupee concentration is a lot-size effect.

## Capital

A 0.70-delta monthly option on this universe is **₹30,000 to ₹200,000 of premium
per lot**, and a bought option cannot be sized below one lot. That is why sizing
ships as `LOTS × 1` rather than a percentage budget: at 2% of ₹1 lakh, nothing
ever armed. Every row states its own per-lot cost.

## Status

`auto_execute` is OFF and blocked by the validation record — six of nine checks
is not a promotion. Manual arming is open, and the board and the settings page
both render the scorecard rather than a slogan so that is an informed choice.

## What would change the verdict

* **The interval.** 409 out-of-sample entry days is not enough to pin a mean
  with this much variance. More history and more instruments both help.
* **Year consistency.** Three of nine years are negative. Requiring all nine is
  a deliberately hard bar; whether it is the right bar for a relative-value book
  is worth arguing, but not by lowering it after seeing the result.
* **The deflated Sharpe.** Driven by the fold selector's 396 variants, which the
  fixed book does not use. The honest fix is a smaller grid, not a smaller
  denominator.

And the standing blocker for every option strategy here: **capture real option
quotes.** Everything rests on a modelled premium. The break-even VRP is the
defence and it now says 2.01 against a 1.15–1.30 market — a real margin, but a
margin measured against a model with two skew slopes in it. This is Phase 0 of
`docs/audits/2026-09-07-options-20x-challenge-verdict.md`.

## Files

| what | where |
|---|---|
| the rule | `backend/app/engines/snapback/strategy.py` |
| the market gate | `backend/app/engines/snapback/regime.py` |
| the maths and the skew | `backend/app/engines/snapback/pricing.py` |
| the hedge | `backend/app/engines/snapback/hedge.py` |
| the replay (one portfolio) | `backend/app/engines/snapback/backtest.py` |
| the harness and the gate | `backend/app/engines/snapback/walkforward.py` |
| the daily backfill | `backend/study/snapback_backfill.py` |
| the study | `backend/study/snapback_research.py` |
| the model audit | `docs/strategy/snapback/AUDIT.md` |
| this run | `backend/study/snapback_walkforward_fno.json` |
| the same rule without the runner | `backend/study/snapback_walkforward_audited.json` |
| the rejected slower-gate variant | `backend/study/snapback_walkforward_candidate.json` |
