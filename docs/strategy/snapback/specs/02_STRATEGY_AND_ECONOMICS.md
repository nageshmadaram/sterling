# Specification 02 — Signals, economics and trade management

Status: proposed implementation specification, PLAN-1.0. Parent: [implementation plan](../IMPLEMENTATION_PLAN.md).

## 1. Strategy modes and starting research parameters

All numbers below are research starting assumptions, not optimized or validated trading recommendations. Keep the existing swing configuration separate.

| Parameter | Scalp | Intraday |
|---|---:|---:|
| Signal candles | 1 minute | 5 minutes |
| Entry warmup | 30 contiguous same-session bars | 30 contiguous same-session bars |
| Earliest configured entry | 09:35 IST, subject to warmup | 09:35 IST, subject to warmup |
| Last new entry | 14:45 IST | 14:45 IST |
| Scheduled square-off | 15:15 IST | 15:15 IST |
| Initial maximum hold | 20 signal bars | 12 signal bars |
| Absolute runner hold | 60 signal bars from initial fill | 36 signal bars from initial fill |
| Requested target | 5 option-premium points | 5 option-premium points |
| Initial maximum planned stop distance | 4 premium points | 4 premium points |
| Minimum net reward/risk | 1.0 | 1.0 |
| Initial fixed trailing distance | 2 premium points | 2 premium points |
| Net profit-lock objective | 1 premium point per remaining unit | 1 premium point per remaining unit |
| Per-entry modeled risk cap | 0.5% of effective capital | 0.5% |
| Session loss limit | 2% of start-of-session capital | 2% |
| Max completed/attempted entry policy | 6 filled entries/day; bounded separate failed-attempt counter | Same |
| Cooldown after exit | 5 signal bars | 5 signal bars |
| Initial live quantity, if later eligible | One affordable permitted lot | One affordable permitted lot |

The warmup means the first possible 1-minute signal is 09:45 and the first 5-minute signal is 11:45. A 3-minute interval remains a configurable experiment. To trade earlier, define and test a separate warmup/previous-session continuity policy; do not hide the dependency by treating unformed ADX as zero.

Runner hold is an absolute limit measured from initial fill. Promotion must not reset the clock indefinitely. Time exits use elapsed clock/session time so a feed gap does not extend permission to hold.

## 2. Exact initial reversal hypothesis

This is a candidate hypothesis to validate, not established alpha.

At completed underlying bar t:
1. Compute Bollinger mean and sample standard deviation from 20 strictly prior closes. Bands = mean ± 2 standard deviations. The band for t-1 must likewise use its own prior window.
2. Compute causal EMA9 and ADX14. Require enough real observations for indicator warmup.
3. For a bearish setup: t-1 closed above its upper band; t closes back inside its upper band but above its prior-window mean; t is a bearish candle, closes below the previous close and at/below EMA9.
4. For a bullish setup: mirror the conditions below the lower band and require t at/above EMA9. The existing allow_fade_down setting explicitly controls this side.
5. Reject ADX greater than 25 at entry. Missing/non-finite ADX rejects rather than passing.
6. If enabled, relative volume = current completed volume / mean volume of 20 prior bars. Missing reliable volume rejects this optional branch.
7. Require valid session, fresh completed bar, contiguous warmup, and no current position/cooldown conflict.

The setup artifact includes current price, band re-entry, mean objective, invalidation reference, timestamps, indicator values, availability, and every rule pass/fail. The scanner cannot label more stretch as a better probability without evidence.

Signal generation occurs at bar close. A fill at that same historical close is not assumed. Operational exits may act on quotes between signal bars.

## 3. Indicator policy and experiments

| Input | Initial role | Proposed experiment / limitation |
|---|---|---|
| EMA9 | Reversal confirmation | Compare no EMA vs EMA9; trend slope as separately versioned context |
| Bollinger20/2 | Identify extension and re-entry | Compare against ATR extension; use predeclared small parameter grid |
| ADX14 | Refuse strong-trend fades | Compare disabled vs threshold; do not interpret as direction |
| ATR14 | Volatility and movement feasibility context | Volatility-scaled stop/trail only as a distinct candidate |
| Open interest | Contract-liquidity floor | Never a stand-alone buy/sell direction rule |
| Relative volume | Optional participation filter | Exclude index volume proxies that are not comparable |
| VWAP | Optional context for instruments with valid volume | No pseudo-VWAP for an index with missing trade volume |
| PCR | Optional synchronized option-chain context | Fixed comparable strike/expiry universe; minimum coverage; no daily-final PCR in intraday replay |
| Change in OI | Optional price/OI context | Compare same contract and comparable interval, retain units and timestamps; never subtract across expiry rolls |
| Fibonacci | Deferred | No demonstrated incremental evidence in the current task; avoid discretionary anchor selection |

Ablation reports must include trade count, net expectancy, costs, drawdown and uncertainty, not just win rate. Adding all filters together would make attribution and overfitting control impossible.

## 4. Contract selection

Select only an actually listed, unexpired CE/PE matching the setup. Read tick, lot, expiry and quantity rules from dated metadata. Rank deterministically by:
1. valid, fresh two-sided quotes and sufficient usable depth;
2. cash/risk feasibility and estimated net economics;
3. operator-approved delta/expiry bands;
4. spread/impact estimate and stable tiebreakers.

Do not choose the cheapest option solely because more lots fit. Stock options, indices and near-expiry contracts need separate evidence scopes. A realized-volatility Greek proxy can support labeled research ranking; live ranking that requires delta needs a quote-consistent, quality-checked estimate with recorded method or an explicit supported alternative.

The chosen contract is frozen for the trade. Signal refreshes must not silently migrate an open position to a new strike/expiry.

## 5. Cost and quantity model

For each candidate lot count, estimate:
- expected entry fill from ask/depth plus entry impact and latency;
- expected exit execution from bid/depth plus exit impact and latency;
- actual brokerage/transaction/tax components from a dated account/product schedule;
- additional orders and fees for slicing/partial exits;
- conservative fee reserve while broker charges are unknown.

If execution prices already use ask entry and bid exit, the spread is already in gross P&L. Never deduct it twice. For mid-reference research, explicit spread is an additional cost. CostEstimate records its reference-price convention.

Cash and risk are recomputed jointly with quantity, because impact and fixed fees are quantity-dependent. Do not use a single per-unit estimate and then scale to arbitrary size.

Let q be units, L lot size, P expected entry, S stop price:
- modeled stop risk = q × (P − expected_stop_fill) + estimated_round_trip_fees;
- cash reservation = q × P + estimated_entry_fees + explicit reserve;
- per-trade budget = effective_capital × configured_risk_fraction;
- available day risk = day_limit − realized_loss_consumed − open_risk − pending_entry_risk;
- allowed risk = minimum(per-trade budget, remaining day/account/strategy/underlying limits).

Choose the largest allowed multiple of L within all constraints and operator quantity cap. Effective capital is broker-reconciled available funds capped by operator allocation. No new position if even one lot fails.

Positive intraday P&L does not automatically expand the day's authorized loss budget. Separate the fixed session allocation from realized and mark-to-liquidation equity. Portfolio reservations include other strategies in the same account, not just Snapback's local positions.

Initial conservative account policy, to make that rule executable:
- settled_loss_used = sum of max(0, −net trade P&L) across the session; winning trades do not replenish this allowance;
- open_risk_used = sum of max(0, entry cash including costs − minimum(current net liquidation value, conservative net stop liquidation value)) across open positions;
- pending_risk_used = remaining reserved loss on unresolved/unfilled entry quantities;
- day_risk_remaining = max(0, fixed day loss allocation − settled_loss_used − open_risk_used − pending_risk_used).

When an entry fills, transfer its reservation into open risk atomically; do not count both. When an exit fills, transfer realized loss into settled usage and release only the corresponding open quantity. Separately halt admissions if total account mark-to-liquidation equity crosses its daily/drawdown floor. Unknown charges use a conservative estimate until reconciled. Any alternative net-loss rather than cumulative-loss budget is a separately named and tested policy, not an implicit behavior change.

Displayed depth is an upper bound on visible liquidity, not a guaranteed fill. Initial research participation ceiling: at most 10% of displayed usable depth inside the slippage limit, subject to later measurement. Unknown depth blocks any capacity claim and live high-quantity admission. Validate exchange freezing and per-order limits before selecting slices.

## 6. Five-point target, economic floor and feasibility ceiling

In a simplified reference-price model:
- u = variable round-trip cost per unit + fixed cost/q;
- d = tick-rounded stop distance;
- T_required = u + min_net_RR × (d + u);
- T_candidate = max(requested_points, T_required).

Example: P=100, q=50, d=4, variable=1 and fixed=40 gives u=1.8, T_required=7.6, modeled stop loss ₹290 and modeled target gain ₹290. Five points would yield only ₹160 net. Larger size is not an automatic solution.

**New requirement beyond the saved prototype:** a cost-adjusted target cannot grow without an attainability check. Produce a feasible target ceiling using entry-time structural room and a frozen, training-only conditional premium-movement distribution for the allowed horizon. Record its sample size, estimator, uncertainty and price/reference basis. Validate the target-before-stop/time probability on held-out data.

If the floor exceeds feasible room: reduce quantity if doing so improves total economics, select another eligible contract, or reject. Do not move the mean objective farther away, widen the stop, or label a made-up target as expected reward. A delta conversion alone is not proof that an option price will reach a target.

If no calibrated feasibility estimator exists, allow a research plan with feasibility_status=unmeasured; deny promotion/live eligibility. Report probabilities as unavailable rather than inventing percentages.

Reward/risk is a geometric property of a plan. Positive expected net value also depends on hit probability, time exits and execution costs. Use the full observed outcome distribution; never infer profitability from a 1:1 or 2:1 ratio.

## 7. Exit policy: small capture into a runner

Trade management uses executable-side option quotes and completed continuation features. LTP can be displayed, but it cannot establish realizable liquidation value at high quantity.

States of the strategy policy:
OPEN_SCALP → LOCKED → TARGET_PENDING → RUNNER → EXIT_REQUIRED → CLOSED.
LOCKED and TARGET_PENDING may be skipped if events jump across thresholds. Reconciliation/protection status is an independent operational dimension.

Initial stop:
- pinned from actual fill VWAP, quantity and entry policy;
- enforce instrument tick rounding and the authorized maximum risk;
- cannot be widened to keep a losing position alive;
- if the fill makes the plan invalid, cancel remaining entry quantity and enter the controlled exit path.

Pre-target lock:
- activate only after fresh executable value covers accrued plus reserved exit costs and the configured lock objective;
- desired stop cannot exceed the executable bid minus legal tick/buffer;
- if price already crossed the intended stop, request an exit instead of submitting a fictitious last price to legalize modification;
- protection is confirmed only after broker acknowledgment/reconciliation.

Target handling has a deliberate, testable choice:
- Quote/event execution: on an executable target crossing, use only the last completed continuation snapshot. If it qualifies, promote; otherwise issue an exit intent. Fill at subsequent broker-observed prices.
- Bar-only research: existing stop wins ambiguity. At completed candle close, a strong close above the threshold may promote; otherwise a touched target exits at observed close with costs. Never claim both a hindsight target fill and a later continuation decision.

The saved draft uses the bar-only policy. Quote-policy results and bar-policy results must carry different execution-policy versions. Historical validation for live rollout must use the same decision policy as live.

Initial continuation hypothesis:
- premium remains above the cost-aware threshold;
- last completed confirmation supports the original underlying direction;
- option close is in the upper 35% of its completed range;
- quote freshness/spread and remaining time/risk remain acceptable.

Trailing:
- high-water mark derives from accepted executable bids for quote execution, and completed closes for bar research;
- desired stop = max(previous desired stop, permitted cost-aware lock, high_water − configured trail);
- active stop remains the last broker-confirmed level while a modification is pending;
- coalesce updates, honor broker modification limits, prioritize exits over routine updates;
- never loosen the stop and never reset runner duration.

Exit priority: broker fill truth first; existing protective stop/gap; operational emergency/session cutoff; thesis invalidation; time limit; target/continuation decision; then a new trail. An unacknowledged desired stop is not credited as protection or realized profit.

## 8. Partial exits and larger quantities

Version 1 holds or exits the whole remaining position; automatic upgrade never buys more. Partial profit taking is a later isolated experiment:
- integer tradable quantities and remaining minimum lot respected;
- reserve extra fees and update protected remaining inventory after confirmed partial fills;
- no assumed reduction from a submitted sell;
- compare whole-position runner against partial bank-and-run on identical holdout signals.

Scaling stages are evidence stages, not a self-optimizing live multiplier. Require measured depth/impact and risk headroom before moving from one lot to larger size. A profitable week alone is not capacity evidence.

Implementation boundaries: extract pure features to intraday_features.py, economics to intraday_economics.py and policy transitions to intraday_lifecycle.py under backend/app/engines/snapback. Keep intraday.py as a compatibility facade until consumers migrate. Do not maintain separate formulas in the frontend or the broker adapter.
