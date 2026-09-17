# Reading the evidence report

```bash
cd ~/Sterling/backend && .venv/bin/python -m study.snapback_evidence_audit --date 2026-09-17
```

## What the numbers mean

**Opportunities** — how many times the strategy fired today. This is the number
everything else is measured against, and it is the number most systems fail to
record. Without it, "three trades today" could mean the strategy fired three
times or thirty.

**Selection: LISTED / NOT_LISTED / UNKNOWN**

Sterling calculates which contract to buy, then checks whether the exchange
actually lists it.

- `LISTED` — it exists; the trade may proceed.
- `NOT_LISTED` — the exchange does not list that contract, so the trade is
  skipped. This is a real finding about the strategy, not a bug.
- `UNKNOWN` — Sterling could not read the exchange's contract list, so it does
  not know. Also skipped, for a different reason.

`NOT_LISTED` and `UNKNOWN` both block, and they are deliberately reported
separately: one says the rule chose an impossible contract, the other says we
could not see.

**Hedge: authoritative / waived / unknown** — Snapback buys an option and hedges
it with a futures contract. `waived` means the rule deliberately did not require
one. `unknown` means one was required and could not be found, which blocks.

**Blocked** — opportunities that never reached the broker. A high number is not
necessarily bad, but it should be explainable.

**Economic-authoritative trades** — trades where the *entire* chain is provable:
real contract, real prices, real broker fills, protection confirmed, exit
confirmed, reconciled. Only these count toward the 300.

**Inconclusive** — Sterling traded, but cannot fully prove what happened. These
are excluded from the count. Worth investigating; they usually mean something
was not recorded properly.

## What the report will never do

It never looks at whether a trade made money. Evidence quality is judged before
and independently of the outcome. An audit that could see profit would
eventually start treating losses as bad data, which is how honest systems become
dishonest ones.

## The verdict

The report always says INCONCLUSIVE, because one day cannot answer the question.
Only the full sample — 300 trades, 60 sessions — produces a verdict, and the
correct expectation is that this takes years.
