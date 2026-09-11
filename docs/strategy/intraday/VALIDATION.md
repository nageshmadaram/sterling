# The walk-forward harness

```bash
python -m study.intraday_walkforward                 # report only
python -m study.intraday_walkforward --record        # report AND gate on it
python -m study.intraday_walkforward --slippage 0.5  # what an option book pays
```

Reads the OHLCV store. No broker session, no network.

## What it is for

Three strategies were shipped with every threshold a judgement call. This
decides which of them — if any — has earned the right to trade while nobody is
watching. It is deliberately hard to pass. Nothing in this repository's history
has cleared a deflated Sharpe of 0.5, and a gate that waved through the first
thing tried would be worse than no gate.

## The one rule

**The window a configuration is chosen on is never the window it is judged on.**

Everything else exists to make that impossible to break by accident.

- Each fold selects on its in-sample window only. The out-of-sample window is
  not read and not scored during selection; passing it to the selector raises
  `SelectionContaminated` rather than producing a number that looks clean.
- A **purge** gap (default one session) sits between the windows, so a trade
  opened at the end of in-sample cannot still be running inside out-of-sample.
  Without it the two windows share outcomes and the split is decorative.
- Out-of-sample windows are contiguous and non-overlapping, so concatenating
  them counts every bar once.
- **The concatenated out-of-sample book is the result.** Per-fold numbers are
  published for auditing the selector, never as findings. A per-fold best quoted
  as the headline is the overfitting the split was supposed to prevent, wearing
  a rigorous hat.

## What the replay refuses to assume

| | |
|---|---|
| Signals come off **closed** bars | The close is the last print of a bar |
| Fills at the **next bar's open** | Filling a close-derived signal at that close is the most common way an intraday backtest invents money |
| A bar through **both** stop and target is a **loss** | Nothing in the data says which came first |
| Costs and slippage on **both** legs | At 5 minutes these are not a rounding error — an earlier sweep here found sub-hour timeframes losing essentially all of their gross edge to fees |
| The evaluation window is **bounded** | The live scanner fetches a fixed lookback, so an unbounded replay would be a backtest of something that does not exist |
| The default lens is the **underlying** | Flat-vol option pricing has already faked a wing edge in this repo once. Measure the signal first; an option overlay can amplify an edge but cannot create one |

The replay calls the same `evaluate_all` / `thesis_broken` the live scanner
calls. A backtest that reimplements the rules measures the reimplementation, and
the first thing to diverge is the thing you were trying to measure.

## The two numbers that matter

Both are about the second question, not the first.

**Deflated Sharpe** — what survives once you account for how many variants were
tried. Computed per-period throughout (annualising inside the formula is a
common, quiet error, because the skew and kurtosis terms are per-period), with
**non-excess** kurtosis (passing excess kurtosis makes the correction negative
for well-behaved returns, so the deflation inflates), and with `SR0` built from
the **measured** spread of Sharpes across the variants actually tried rather
than an approximation of it.

**Permutation p-value** — does the entries' *timing* beat random entries with
identical exposure? The null is not "no position"; it is the same trade count
and the same holding periods, entered at random in the same tape. Without that,
a strategy passes simply by having been long a market that went up.

## The gate

```
min_oos_trades              50
min_oos_net                 > 0        after costs
min_oos_sharpe              0.5
min_deflated_sharpe         0.5
max_permutation_p           0.05
min_symbols_positive_pct    60%
max_drawdown_pct            -35%
```

A missing p-value is a **failed** check, never a passed one: "could not test"
and "tested and passed" must not look the same from the gate.

## What a verdict changes

`--record` writes the verdicts to the engine's validation record, and that is
what gates **unattended** execution. A strategy that has not cleared the harness
is skipped by auto-entry with the harness's own reason attached — not "not
promoted", which tells an operator nothing, but the sentence naming what would
have to change.

**Manual arming is not gated on it.** An operator taking an unproven setup with
their eyes open is their call; refusing that would be paternalism rather than
safety. Trading it while nobody is watching is a different thing, and that is
what the harness has to earn.

The board and the settings page read the same record, per strategy. The three do
not stand or fall together, and a banner that keeps saying "not validated" after
one of them passes is the same kind of lie as one that claims the reverse.

## Honest limits

- The store currently holds about **8.5 months** of 5-minute bars. That is
  enough for roughly six folds and is the binding constraint on every number
  this produces. More data is the single highest-value improvement available.
- The grid is **three values of one knob per strategy**, on purpose. Every extra
  variant raises the bar the deflated Sharpe sets, so a wide grid does not find
  an edge — it buys a worse one at a higher price.
- The default lens is the underlying, so the numbers measure the **signal**, not
  the option book that would express it. Slippage is a parameter precisely
  because the answer is sensitive to it.
