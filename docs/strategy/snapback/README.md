# Snapback

Buys premium into an over-extension and hedges the market out of it.
**`VALIDATION_REPORT.md` is the authority on what it is worth, and it is the
first thing to read** — the directional version of this was falsified by its own
rerun on nine years of data.

## The idea in three sentences

An instrument that closes through its own 20-session high while stretched at
least 1.5 ATR above its 20-session mean tends, over the next fifteen sessions, to
under-perform *the market* — the edge is RELATIVE, about +1.5 percentage points
against a day-matched baseline. The trade is a bought put, roughly 40 days out,
at a 0.70 delta, with the put's own market delta hedged away by an index future.
Without that hedge the book is short a nine-year bull market and loses.

The return distribution is the other half of the design: the top 1% of trades
carry 148% of the P&L and the median trade loses 15.5%, so the engine refuses
anything that caps the payoff (no spreads) and lets a trade already worth 1.5x
its cost run past the horizon. See `AUDIT.md`.

## Why each piece is what it is

**Why buy an option at all.** The loss is capped at the premium, and fading
strength is precisely the bet whose tail is a breakout that keeps going — the
one risk a reversion book cannot survive. The delta is high because what is
being monetised is a drift rather than a gamma: for a drift you want delta per
rupee of theta, which is an in-the-money contract.

**Why puts and not calls.** Realised vol after an upside break runs at 0.86–0.92×
the trailing vol that prices the option, so the put is bought cheap; after a
downside break it runs at 1.25×, so the call is bought dear and every result on
that side is overstated by an unknown amount. That is a PRICING reason, and it is
why `allow_fade_down` ships off. Measured separately, the fade-down side also has
a negative excess in every regime tested.

**Why daily bars.** The effect is a multi-session reversion. The same
instruments at a 5-minute timeframe lost essentially all of their gross edge to
costs in this repository's own harness — one strategy there spent 238% of its
gross on fees.

**Why the strike is picked by delta.** Every other engine here picks by a
moneyness rung. A rung is a different amount of leverage at every vol level and
tenor, so a sweep over rungs measures the vol regime and reports it as a strike
effect. Delta is the leverage, so delta is the control.

**Why the market is hedged out.** The signal's edge is RELATIVE — about +1.5
percentage points against a day-matched unconditional put — while a bought put
is a large SHORT position in the market. Over nine rising years the second cost
more than the first earned, and that gap is the entire reason the directional
book lost. `hedge_mode` shorts the put's own market delta, beta-weighted, with
an index FUTURE.

A future and not a bought index call, and the difference is not academic: the
call version beat an exact hedge by almost a point, which is the tell that it
had stopped hedging. A bought call is convex, so over a rising sample it gains
MORE than the put's market loss and quietly becomes a second long-market,
long-vol position. A future offsets by construction and cannot flatter the
result. It does need MARGIN rather than premium, which the premium budget does
not cover.

**Why the break-even VRP is the headline.** No store in this repository holds
option price history, so a premium has to be modelled. Modelling it as a LEVEL
invites the flat-vol trap this codebase has already paid for — a far-OTM wing
priced at ATM vol looked like a +455% edge and became −79.5% under a realistic
smile. Modelling it as a RATIO to realised vol does not have that failure mode,
and the honest output is not one return but the ratio at which the trade stops
paying.

## Layout

```
app/engines/snapback/
  pricing.py      Black-Scholes, delta-targeted strikes, the break-even search
  regime.py       the market gate — fade only while the index is weak
  hedge.py        causal beta, and the index-futures hedge
  config.py       every knob, and which defaults are measured
  models.py       Bars and the signal
  strategy.py     the rule, and entry_indices — the ONE definition of an entry
  contracts.py    which contract a signal buys, and what it is modelled to cost
  backtest.py     the replay, the cost model, the day-clustering
  walkforward.py  purged folds, the permutation, the gate

app/services/
  snapback.py             config, the daily candle fetch, the scan
  snapback_validation.py  the stored verdict, and what it permits

study/snapback_research.py   reproduces every number in the report
study/snapback_backfill.py   nine years of DAILY bars for the whole F&O list,
                             plus the instrument spec file. Kite serves 2000
                             days per request, so this is minutes, not hours.
```

The live scan and the replay call the SAME `entry_indices` and the same contract
picker. A backtest that reimplements the rules measures the reimplementation.

## Running it

```bash
# get the data first — the store ships with 19 names and 3 years, which is not
# enough to measure anything (see the report's falsification section)
.venv/bin/python -m study.snapback_backfill --years 9

# the whole study
.venv/bin/python -m study.snapback_research

# just the verdict, and store it where the engine reads it
.venv/bin/python -m study.snapback_research --part gate --universe all \
    --rounds 800 --record --measured-at 2026-09-12
```

`--measured-at` is passed in rather than read from the clock: re-running against
a stale dataset would otherwise stamp an old measurement as today's.

## Operating it

`auto_execute` is off and blocked by the validation record until the gate
passes. Manual arming is open — taking a measured-but-unproven setup with your
eyes open is an operator's call, and the board and the settings page both render
the gate's scorecard rather than a slogan so that call is an informed one.

The scan reads daily closes, so it is worth running once after the close and
once before the open. It looks back over the last three closed sessions, because
a rule that fires on one bar loses that signal forever if nothing ran that day.

**One sizing trap worth knowing.** At the default 2% premium budget on ₹1 lakh of
capital, one NIFTY lot of a 35-day 0.70-delta put costs more than the whole
budget, so index signals will show as `watching` with that reason rather than
arming. That is the sizer refusing to squeeze in an oversized position, not a
fault. Raise the capital, raise the budget, or switch to fixed lots.

## Related

* `docs/audits/2026-09-07-options-20x-challenge-verdict.md` — why unconditional
  long premium loses, and the three traps that measurement exposed. Snapback
  hits the same wall from a different direction: conditioning the entry is not
  enough on its own.
* `docs/strategy/gamma-move/VALIDATION_REPORT.md` — the other options-buying
  engine here, and a negative finding stated in the same shape.
