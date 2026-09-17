# Sterling — Lane Promotion Policy

**Declared before the evidence is inspected. Not adjustable afterwards.**

This document is the contract for how a strategy-mode lane earns real money.
Its whole purpose is to be written down before anybody knows the answer, so
that a disappointing result cannot quietly become a weaker rule.

> If a lane fails, preserve the failure and create a challenger.
> Never weaken the gate.

---

## 1. Ten lanes, ten verdicts

Two strategies × five modes = ten lanes. Each earns promotion **independently**.

- 299 Snapback-Swing trades plus 1 Snapback-Scalping trade is **not** 300 Swing
  trades.
- SuperTrend results cannot satisfy a Snapback gate.
- There is deliberately no combined "is Snapback profitable?" verdict. Swing and
  Scalping are different experiments; averaging them describes neither.

This is enforced in code: `app.core.lane_promotion.evaluate_all_lanes` filters
evidence to exactly one lane before the gate sees it, and reports what it
excluded rather than dropping it. "This lane has 47 eligible trades out of 900
rows" is a very different answer from "47 trades", and both are printed.

## 2. The economic gate

Every row must pass. A single failure is a failure.

| Gate | Minimum rule |
|---|---|
| Independent sessions | ≥ 60 fully observed sessions |
| Completed trades | ≥ 300 authoritative completed trades |
| Confidence | Day-clustered 95% CI lower bound > 0 |
| Baseline costs | Expectancy > 0 |
| 2× costs | Expectancy > 0 |
| 3× costs | Expectancy > 0 |
| Tail robustness | Expectancy still > 0 after removing the best ~1% of trades |
| Drawdown | MTM drawdown ≤ 10% of allocated strategy capital |
| Quote/evidence coverage | ≥ 95% |
| Unresolved exposure | 0 |
| Identity | Every promoted outcome belongs to the exact frozen strategy-mode identity |

### What counts as a trade

Only rows that are **all** of:

- attributed to this lane (`lane_key` matches exactly — never inferred);
- marked authoritative;
- of a promotable evidence class: `paper`, `shadow` or `broker`.

Modelled and replay rows are research inputs. They cannot show whether a trade
was fillable, so a gate built on them measures the model, not the market.
Acceptance rows certify the runtime and say nothing about profitability.

### What counts as a session

A session counts only when the daily completeness report reads `COMPLETE`:
scan complete, entry and EOD phases complete, no evidence gap codes, coverage at
or above the floor, zero unresolved positions.

**A gap is permanent.** A later clean rescan does not restore an earlier
incomplete session. This is deliberate: the alternative is a 60-session
denominator that can be inflated by re-running the scanner.

## 3. Slow lanes

Overnight and Swing may take an impractically long time to reach 300 trades.
A different design is permitted **only** if:

1. it is specified in writing **before** collection starts;
2. it is of statistician quality — not "fewer trades because it is slow";
3. it is recorded here, with the date and who approved it.

Never lower the requirement after seeing disappointing or slow evidence. That
is the one move this document exists to prevent.

### Approved alternative designs

| Lane | Alternative design | Declared on | Approved by |
|---|---|---|---|
| _(none yet)_ | | | |

## 4. The state machine

```
RESEARCH -> PAPER -> SHADOW -> PROMOTION_REVIEW
         -> LIVE_MINIMUM -> SMALL_FIXED_CAPITAL -> CONTROLLED_SCALING
```

| Transition | Required evidence |
|---|---|
| RESEARCH → PAPER | Rule identity frozen; parity tests pass; evidence schema complete |
| PAPER → SHADOW | Operationally stable; no unresolved evidence defects; enough signals to exercise the whole lifecycle |
| SHADOW → PROMOTION_REVIEW | Execution assumptions observed against the real book; reconciliation, protection and restart proven |
| PROMOTION_REVIEW → LIVE_MINIMUM | Economic gate PASS **and** safety gate PASS **and** explicit operator approval |
| LIVE_MINIMUM → SMALL_FIXED | Observed live fills, slippage and protection consistent with the shadow model |
| SMALL_FIXED → SCALE | Sufficient live history; drawdown and execution inside predeclared limits |

No transition is automatic. Real-money execution is disabled at code level
(`LIVE_EXECUTION_ENABLED = False` in `app/core/lane_registry.py`); no
environment variable and no lane state lifts it alone.

## 5. The shadow requirement

Paper profitability is not enough. Before any lane is eligible for capital, its
decisions must be replayed against live broker reality **without sending the
entry order**:

```
signal -> selected contract -> observed bid/ask/depth -> intended quantity
-> broker-observed margin -> hypothetical order intent -> protection plan
-> observed subsequent book -> hypothetical fill/partial/no-fill -> exit reality
```

Required metrics, per mode: fillability and no-fill rate, expected vs observed
spread and slippage, depth available at the requested quantity, broker margin
availability and capacity-refusal rate, protection feasibility, contract
availability by mode, timing drift between signal and intended order, and the
effect of restart/reconnect on open hypothetical positions.

**A no-fill is a result.** Converting one into a synthetic fill at the last
quote deletes exactly the evidence the shadow phase exists to gather, and
`app.core.shadow_record.ShadowRecord` refuses to construct such a row.

## 6. Minimum live capital

When a lane qualifies, it is deliberately under-sized first. The goal is to test
reality, not to earn.

- Minimum executable quantity supported by the instrument and the broker.
- Daily loss and gross exposure capped at a small fixed amount, independent of
  any reported strategy confidence.
- No automatic capital increase on a winning streak.
- Every live fill, rejection, partial fill, slippage and protection event
  becomes authoritative execution evidence.
- If live execution contradicts the shadow model, the lane returns to SHADOW or
  SAFE_MODE.

Capital limits are layered: global, per strategy, per mode, per underlying, per
position. The per-underlying layer exists because two different lane labels can
still be the same market bet — ten lanes must never become one oversized NIFTY
position.

## 7. When a lane fails

Failure is evidence. Do not repair a losing frozen lane by changing its
parameters and continuing the same sample.

- **Track A** — the frozen lane. Keep collecting, or evaluate the predeclared
  gate and record the verdict.
- **Track B** — execution diagnostics. Improve infrastructure without touching
  strategy identity.
- **Track C** — a challenger. New rule identity, independent research, its own
  evidence, starting at zero.

Specifically:

- If costs destroy expectancy, investigate a challenger with different execution
  or horizon. Do not erase costs.
- If the top 1% of winners explain all the profit, record that concentration and
  challenge the design separately.
- If a mode rarely finds executable contracts, that is an operability result,
  not missing data to ignore.

## 8. Weekly review

Descriptive reporting is not promotion. Reading a good week moves nothing.

| Area | Questions |
|---|---|
| Data quality | Sessions complete? Persistent gaps? Quote coverage? Timestamp health? |
| Execution reality | No-fill rate? Spread and slippage? Margin refusals? Protection failures? |
| Strategy behaviour | Signals per lane? Holding-time distribution? Timeouts? Exit reasons? |
| Economics | Expectancy, CI, cost stress, drawdown, top-tail dependence — descriptive until the gate sample is reached |
| Operations | SAFE_MODE incidents? Restarts? Reconciliation mismatches? Backup failures? |
| Change control | Did any production behaviour change? If yes, was a new identity created? |

## 9. Change control

Any change that can alter entries, exits, instrument selection, risk, holding
time or fills creates a **new identity**. Existing forward evidence is never
silently reassigned to it.

`./sterlingctl verify` compares the running build against the frozen manifest
and names every moved hash. A moved `rule_hash`, `config_hash`,
`strategy_version`, `mode_version` or `evidence_schema_version` is identity
drift: the old sample belongs to the old identity, and the new one starts at
zero.

---

**The central rule.** Do not try to make Sterling look profitable. Make Sterling
impossible to fool. If an edge is real, the evidence system will eventually show
it. If it is not, the system protects capital by refusing to pretend otherwise.
