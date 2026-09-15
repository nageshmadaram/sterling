# Snapback scalping: stored-data audit

**Result: profitability is unproven.** The available option snapshots do not
support a reliable small-target execution backtest. No return, win rate, or
promotion is reported. Missing results are `null`, not zero-profit trades.

This audit was run on the local database on 2026-09-15. It reads actual stored
bid/ask observations and keeps database access read-only. It does not replace
missing option prices with a model or turn sparse quotes into purported option
candle highs and lows.

## Reproduce

From `backend`:

```bash
.venv/bin/python -m study.snapback_scalp_research --out /tmp/snapback_scalp_quote_audit.json
```

The JSON includes every contract-day's coverage and the exact cost assumptions.
Use `--db` for another database. Timestamp tolerances and the 1/3/5-minute
coverage bucket size are explicit arguments. No strategy parameters are
optimized by this study.

## What the database actually contains

The snapshot table contains **1,596,586 rows**, 568 option contracts, and 15
underlyings: SENSEX and 14 stocks. NIFTY and BANKNIFTY option tapes are absent.
The underlying OHLC store has 1,267,047 five-minute bars across 209 symbols;
its 750 one-minute bars are one session under two NIFTY aliases.

Rows are filtered by availability at receipt, not retrospectively assigned to
an earlier exchange timestamp. Repeated contract/exchange timestamps are counted
only at their first valid receipt. The following rejection counts are mutually
exclusive and reconcile exactly to the original row count.

| Result | Rows |
|---|---:|
| Accepted, unique, regular-session quotes | 211,324 |
| Exchange quote more than 60 seconds old | 1,369,302 |
| Outside regular session | 12,190 |
| Stored quote quality not `ok` | 1,942 |
| Invalid bid/ask, spot, or contract | 1,665 |
| Repeated contract/exchange timestamp | 160 |
| Exchange timestamp in the future | 3 |

Fresh quotes cover eight dates and **1,329 contract-days**. Only **two
contract-days** have at least one quote in every five-minute bucket: SENSEX
77100 CE and PE on 2026-08-28. Both have just 370 observations across the session,
first appear 26.108 seconds after the open, and have a maximum internal gap of
104.314 seconds. Their median quoted spreads are respectively 1.40 and 0.70
option points.

Neither passes the declared five-second observation-gap and session-boundary
screen. There are **zero qualifying contract-days**. Even a cadence pass would
be only a prerequisite: sampled extrema still cannot establish continuous
option OHLC, order-book depth, queue position, or a broker fill.

## Chronological separation

The split is fixed by date before the cost diagnostics:

- Training: 2026-08-14, 08-17, 08-21, 08-27, and 08-28.
- Held out: 2026-08-31, 09-02, and 09-03.

The only two complete sets of five-minute buckets are in training. The held-out
partition contains no full-session bucket coverage and no cadence passes. This
is a reproducible data-coverage split, **not a completed out-of-sample
performance result**.

## Why a requested five-point target can become larger

The script also runs the new entry planner against each contract-day's first
valid observed quote. These are hypothetical sizing checks, **not strategy
signals or executed trades**. It uses the current research defaults:
₹100,000 capital, one-lot maximum, 0.5% stop-risk budget, a four-point stop,
at least 1:1 reward/risk after estimated costs, one variable cost point per
round trip or the observed spread if greater, and ₹40 fixed round-trip costs.
These are configured estimates, not a verified exchange fee schedule.

| Cost estimate | Training plans accepted / checked | Held-out plans accepted / checked | Median required target, accepted plans |
|---|---:|---:|---:|
| 1× variable costs | 70 / 819 | 42 / 510 | 10.075 option points |
| 2× variable costs | 70 / 819 | 42 / 510 | 12.075 option points |
| 3× variable costs | 70 / 819 | 42 / 510 | 14.075 option points |

Most stock option lots exceed the configured cash or stop-risk budget. A bigger
quantity can spread fixed fees over more units, but it also increases rupee
losses and requires executable market depth. The audit provides no evidence
that high quantities would fill at the displayed quotes.

## Required evidence for a performance comparison

A future comparison needs synchronized underlying bars and genuine option
price history for identified contracts, complete position lifetimes through
square-off, sufficiently frequent bid/ask observations, actual lot sizes, and
a broker/exchange-specific cost model. Retain a separate chronological test
period and stress spread/slippage there. Report unresolved positions and missing
data instead of dropping them from the results.

The earlier daily Snapback results concern a different holding period and
modelled option premiums. They do not validate these new scalp/intraday rules.
The daily runner's calendar deadline and gap-fill corrections also change its
backtest semantics; historical daily performance reports require a fresh run
before being attributed to the corrected engine.
