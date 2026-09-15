# Specification 03 — Durable execution, accounting, API and UI

Status: proposed implementation specification, PLAN-1.0. Parent: [implementation plan](../IMPLEMENTATION_PLAN.md).

## 1. One execution authority

Reuse the existing Kite order journal, execution lease, broker observation handler, positions registry, protective-stop adapter and fill ledger. Add a strategy policy adapter, not a second broker client, order store or P&L calculator.

Proposed services:
- backend/app/services/snapback_execution.py: admission and conversion from an accepted TradePlan to the shared durable order intent;
- backend/app/services/snapback_lifecycle.py: route market/fill/timer/reconciliation events to the pure position policy and persist resulting intents;
- backend/app/services/snapback_readiness.py: configuration/evidence/capability eligibility;
- shared kite_engine modules extended through explicit strategy-policy interfaces.

The generic execution router's current SHADOW mode sends broker orders. Define OBSERVE as a new non-submitting capability or implement it upstream with no execution client. Never map observation mode to existing SHADOW. This requirement is enforced by transport spies and integration tests.

## 2. Orthogonal state machines

Order lifecycle:
RESERVED → SUBMITTING → ACKNOWLEDGED → PARTIAL → FILLED.
Terminal no-fill outcomes: REJECTED or CANCELLED.
A timeout after submission means UNKNOWN, not rejection or permission to submit again.

Position inventory:
NO_POSITION → PARTIAL_OPEN/OPEN → PARTIAL_CLOSING/CLOSING → CLOSED.
Only fill events change inventory.

Protection lifecycle:
NONE → REQUIRED → SUBMITTING → ACTIVE → MODIFY_PENDING → ACTIVE.
Unknown submission/modification becomes RECONCILIATION_REQUIRED. It never becomes ACTIVE from an optimistic local update.

Strategy policy:
OPEN_SCALP → LOCKED → RUNNER → EXIT_REQUIRED.
This state cannot override actual order or protection state.

Operational readiness:
OBSERVE, PAPER, LIVE_ELIGIBLE, HALTED, RECONCILING.
A UI toggle does not manufacture LIVE_ELIGIBLE status.

## 3. Entry transaction

1. Resolve the server-owned plan by id and exact account. Reject browser-supplied authoritative prices/quantity.
2. Recheck config generation, plan expiry, fresh quote/depth, contract metadata, session, current funds/inventory and promotion scope.
3. Atomically reserve cash and risk and write a durable intent with a unique signal/account/contract identity.
4. Acquire the existing account/instrument execution ownership lease.
5. Persist SUBMITTING before the network operation.
6. Submit a bounded, tick-valid limit order according to verified broker capabilities. Expire/reprice only within the original price, time and risk limits.
7. Persist broker acknowledgment; keep filled_quantity=0 until observed fills establish otherwise.
8. On partial fill: protect confirmed quantity, re-evaluate total entry risk, cancel stale/unaffordable remainder, and retain unresolved orders for reconciliation.
9. Release reservation only as actual fills, terminal cancellations/rejections and inventory accounting justify it.

Kite explicitly distinguishes order registration from execution; an order id alone is not proof of a fill. Persist trade observations and reconcile status using the broker's documented order/trade interfaces. [Official order documentation](https://kite.trade/docs/connect/v3/orders/).

No exactly-once broker guarantee is assumed. Achieve idempotent local intents and reconciliation before retry. Unknown placement cannot be repaired by generating a new id and resubmitting.

## 4. Protection and exits

Broker protection and application monitoring must have one shared owner for a position. A broker-native stop remains the disaster backstop where supported; the application manages target/runner policy.

Before live admission, the capability record must state:
- supported order/stop varieties for this segment, product and quantity;
- target/stop relationship and cancellation semantics;
- modification/placement limits and observed latency;
- what survives process/network failure;
- whether the intended exit policy can be implemented without unprotected gaps.

Do not assume an attached hard target and a soft runner trigger can coexist: a resting take-profit may sell before the strategy decides to extend. For a runner policy, use compatible broker protection without an independently firing full-position profit order, unless the broker adapter proves a safe alternative.

Existing generic protection may use GTT. Validate its applicability and latency for the selected intraday product; do not rename a GTT as an exchange stop or claim guaranteed execution.

On fill, protection covers confirmed remaining quantity. If protection cannot be established within its measured deadline:
- stop further entries;
- cancel unfilled entry remainder;
- reconcile any uncertain protection request before issuing a conflicting order;
- execute the bounded emergency policy only against confirmed residual inventory.

Proposed operating budgets for initial evaluation: 2 seconds quote freshness; 5 seconds entry working lifetime; 2 seconds protection-ack objective after a fill. These are SLO candidates, not broker promises. Failure to achieve them means the scalping mode is ineligible, not permission to weaken reporting.

Exit transaction:
1. Persist exit reason, target residual quantity and expected position revision.
2. Coordinate any working broker stop and pending sells under the protection/exit lease.
3. If cancellation or trigger state is unknown, reconcile before submitting a rival sell.
4. Submit only the residual quantity not already sold or committed by a known working exit.
5. Apply partial fills, resize protection for remaining inventory, and retain EXIT_REQUIRED until inventory is zero.
6. Mark CLOSED and release residual reservations only after reconciliation.

No UI disappearance, target touch, submit acknowledgment or elapsed timer closes a position.

## 5. Timers, disconnects and recovery

Backend timers operate without browser activity. Session cutoff uses a versioned exchange calendar; start square-off before the broker's applicable product cutoff. A requested exit is not a completed square-off.

On market-data disconnect:
- reject new entries;
- preserve working broker protection;
- report stale mark/protection status;
- reconnect and reconcile before resuming;
- use only fresh executable evidence or the preauthorized bounded emergency policy for any discretionary exit.

On restart:
1. Read unresolved intents, open inventory, position policy and protection state.
2. Acquire account ownership.
3. Fetch orders/trades/positions/protective orders.
4. Journal and deduplicate observations.
5. Rebuild ledger and policy projection.
6. Resume protection/exit work for actual exposure.
7. Admit new entries only after conflicts and unknown outcomes are resolved.

A policy parameter update does not rewrite an open trade's history. New settings apply to new entries. Halting entries must continue managing existing exposure.

Kill switch semantics are separate actions:
- stop new entries;
- cancel pending unfilled entries;
- flatten confirmed positions using the coordinated exit path.
An emergency control must state which action was requested and which has actually completed.

## 6. Accounting and account risk

The shared fill_ledger is authoritative. Each fill records broker order/trade identity, side, quantity, price, exchange/receive times and correction lineage. Fee events remain separate and reference orders/trades.

Trade summary:
- intended vs actual entry and exit;
- partial fill sequence and remaining inventory;
- gross P&L;
- broker-confirmed fees;
- estimated fees still reserved;
- provisional net and finalized net P&L;
- MFE/MAE measured from accepted executable values;
- slippage, time in market, runner upgrade/exit reason;
- missing-data intervals and unresolved status.

Do not show provisional gross P&L as final net profit. Use conservative fee reserves in the daily loss gate while actual charges are incomplete.

Session risk includes realized net loss, mark-to-liquidation losses on open positions, remaining downside to protective stops and risk reserved for pending orders. Avoid double counting by documenting each reservation component and rebuilding it from inventory. Profit already earned does not silently authorize increasing maximum loss.

A broker position changed outside Snapback is still account exposure. Reconcile it and halt incompatible strategy actions rather than assuming exclusive ownership.

## 7. API contracts

Existing endpoints remain compatible:
- GET/PUT /api/v1/config/snapback: settings, defaults, vocabularies and generation; updates use expected_generation and return 409 on conflict.
- GET /snapshot: current decisions/plans and freshness, never an executable promise inferred from a UI state string.
- POST /scan: queue or perform a generation-bound scan.
- GET /history: filter by strategy/mode/policy and price-data basis.
- GET /validation: return applicable evidence only.
- POST /replay-intraday: research-only, immutable settings override, no brokerage side effects.

Planned additive endpoints:
- GET /api/v1/snapback/readiness;
- GET /api/v1/snapback/positions and /positions/{id}/events;
- GET /api/v1/snapback/runs/{run_id};
- POST /api/v1/snapback/plans/{plan_id}/arm, requiring live eligibility or explicit paper mode;
- POST /api/v1/snapback/positions/{id}/exit;
- POST /api/v1/snapback/controls/halt.

All account-scoped requests enforce authenticated account ownership. Command bodies carry expected_revision/idempotency_key. Response codes distinguish queued/submitted/filled/rejected/unknown. Browser retry cannot create another order.

Move heavy replay to bounded jobs if the synchronous draft endpoint exceeds the declared duration budget. Research jobs have bar/event/size limits, immutable dataset/config hashes and cancellation state; cancellation retains partial/unresolved results.

Stream typed projections through the existing app event transport:
setup, plan_updated, position_changed, protection_changed, execution_unknown,
ledger_updated, readiness_changed. Every event has a monotonically increasing projection revision and last-updated timestamp.

## 8. Product workflow

Settings:
- mode, timeframe, warmup consequence, sides and universe;
- desired target, cost floor and feasibility ceiling;
- risk/cash/depth limits, fees, trading window and runner policy;
- research parameters visibly distinguished from validated operating scope;
- no mode change silently enables live trading.

Opportunity row:
- fresh setup and contract identity;
- observed ask/bid/spread and age;
- requested/effective target, estimated net gain/loss and feasible-room status;
- quantity caps and rejection reasons;
- status: research, ready for paper, eligible for live, expired or blocked.

Position panel:
- confirmed filled/remaining quantity and average fill;
- desired stop versus broker-confirmed active stop;
- current strategy phase, target/runner trigger and absolute timeout;
- gross/provisional net/final net values;
- working orders, partial fills, unknown outcomes, stale quotes and next action;
- a timeline from signal through fills, protection changes and exit.

Evidence panel:
- dataset coverage, train/holdout dates, costs, confidence intervals;
- scope of promotion and reasons a particular contract/quantity/mode is ineligible;
- no imported daily metrics labeled as intraday results.

Use server-calculated monetary values and enum contracts. The frontend must not recompute trading eligibility, P&L, risk sizing or stops.

## 9. Key integration tests

- A settings update during await cannot republish an old scan or arm its plan.
- NaN timestamp is rejected even when bid/ask are otherwise valid.
- Mixed legacy replay preserves the skipped intraday reason and never invokes daily seeding for it.
- A broker acknowledgment produces no filled inventory.
- Fill duplication/out-of-order delivery causes no duplicate P&L or exit.
- Timeout plus restart reconciles, never blindly resubmits.
- Partial fills protect only actual quantity; entry remainder remains tracked.
- Target/stop/explicit exit races cannot oversell the long option.
- Desired trail never masquerades as active protection while modification is pending.
- OBSERVE and PAPER cannot call broker write methods, including the existing router SHADOW path.
- Browser closure does not stop management; server restart restores it.
- Unrelated strategies preserve their behavior when shared lifecycle interfaces change.

