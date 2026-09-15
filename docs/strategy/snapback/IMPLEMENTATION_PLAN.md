# Snapback: complete implementation plan
Version: PLAN-1.0 · Date: 2026-09-15 · Status: planning; implementation paused

## Objective and definition of success

Build an options scalping/intraday strategy that can capture small moves after costs, preserve earned gains, and extend a winning position when continuation is supported. Optimize net expectancy and capital survival, not the percentage of winning entries. Every entry being profitable is not an implementable guarantee.

A five-point move means five points in the purchased option premium. Quantity amplifies both gains and losses and affects spread, market impact and fees. The system must decline an uneconomic or unaffordable trade. It must not invent a larger attainable target simply to make a reward/risk ratio pass.

The user's $1,000 example is a capital constraint, not permission to infer an INR balance, add leverage, or fund an account. Production sizing uses the smaller of the operator's configured INR allocation and reconciled broker buying power, after reservations. There is no dependency on an assistant earning money for itself.

## Planning boundary and current saved work

This plan follows the request to finish specifications before more code. Existing edits remain in the working tree; no source edits are part of producing this document.

Drafts already exist for an intraday signal/planner/lifecycle, minute scanning, a research replay endpoint, mode settings and board presentation, and daily runner gap/expiry corrections. These are research prototypes, not a completed live trading system. Targeted tests and the frontend build passed in earlier work, but there has not been a final combined release check.

Three known review findings remain unfixed at this planning boundary:
1. A non-finite numeric quote timestamp can escape the freshness comparison.
2. Mixed-strategy legacy simulation can overwrite the intraday-skipped message and run daily setup work.
3. A scan started before a settings change can publish stale rows after the change.

A fourth integration hazard is architectural: the generic router's existing SHADOW mode submits real broker orders. It must not be used for observation-only rollout. The draft also hardcodes a premium tick and uses approximate intraday volatility for strike selection; live trading must require actual contract metadata and verified quote inputs.

The local audit found 1,596,586 option snapshots but only eight dates of usable fresh quotes, two contract-days with every five-minute bucket represented, and no contract-day passing its cadence screen. That is insufficient for a held-out small-target fill comparison. See [the recorded data audit](SCALPING_RESEARCH.md). Existing daily performance figures cannot validate the new strategy.

## Scope decisions

- Preserve the daily swing strategy as a separate strategy contract.
- First intraday version buys one CE or PE contract per position; no naked option selling, futures hedge, spreads, averaging down or martingale.
- Start research with a small explicit liquid universe. No dynamic historical universe built from today's listings.
- Both scalp and intraday use the same causal reversal rules with different timeframes and time allowances. A trend-following strategy is a separate future experiment.
- Use Bollinger re-entry, EMA confirmation and ADX as testable candidate filters. OI and depth primarily establish whether the contract is tradeable.
- PCR, change in OI, VWAP and other context only become active after availability checks and held-out ablation evidence.
- Automatic upgrades modify the remaining position's exit policy, never its quantity.
- Build the complete data-to-accounting loop before enabling broker submissions.

## End-to-end artifact flow

~~~mermaid
flowchart LR
    A[ContractRegistry and RawQuoteEvents] --> B[QualityDecision and SessionTape]
    B --> C[FeatureSnapshot]
    C --> D[SetupDecision]
    D --> E[ContractCandidateSet]
    E --> F[CostEstimate and TradePlan]
    F --> G[RiskReservation]
    G --> H[OrderIntent]
    H --> I[BrokerObservation and FillEvent]
    I --> J[PositionState]
    J --> K[ProtectionIntent and ExitIntent]
    K --> I
    I --> L[TradeLedger and SessionLedger]
    L --> M[ReplayRun and ValidationReport]
    M --> N[PromotionRecord]
    N --> G
    B --> O[API and UI projections]
    D --> O
    F --> O
    J --> O
    L --> O
~~~

Each arrow has a contract, version, provenance and failure behavior. Missing information generates a reasoned rejection or unresolved state; it never silently becomes zero, a synthetic quote, a successful order or a closed trade.

## Specification package

| Artifact | Contents |
|---|---|
| [Data and contracts](specs/01_DATA_AND_CONTRACTS.md) | Feed capture, timestamps, contract identity, data quality, storage, schemas and rejection reasons |
| [Strategy and trade economics](specs/02_STRATEGY_AND_ECONOMICS.md) | Exact entry rules, indicator decisions, target feasibility, costs, sizing, scalping and runner policies |
| [Execution and product](specs/03_EXECUTION_AND_PRODUCT.md) | Durable state machine, broker boundary, partial fills, protection, recovery, accounting, API and UI |
| [Validation and delivery](specs/04_VALIDATION_AND_DELIVERY.md) | Datasets, comparisons, statistical gates, test matrix, implementation order, rollout and acceptance evidence |

These documents form one specification. Changing a rule requires updating its dependent contracts, replay policy, configuration hash and validation scope.

## Existing components to reuse

| Responsibility | Existing artifact | Planned use |
|---|---|---|
| Swing strategy | backend/app/engines/snapback/strategy.py, backtest.py, walkforward.py | Preserve daily semantics; rerun corrected daily research |
| Intraday prototype | backend/app/engines/snapback/intraday.py | Refactor into stable feature, economics and lifecycle boundaries with compatibility wrappers |
| Configuration and metadata | backend/app/engines/snapback/config.py, models.py, __init__.py | Versioned mode configuration and typed artifacts |
| Scan and history | backend/app/services/snapback.py | Fresh minute scan, generation checks, plan projection |
| Stored market data | backend/app/services/ohlcv_store.py; navigator/repository.py, chain_sampler.py | Reuse storage and capture infrastructure; add provenance/cadence evidence |
| Contract execution evidence | backend/app/services/kite_engine/execution_evidence.py | Exact current symbol, tick, lot, expiry and quote checks |
| Durable submission and recovery | backend/app/services/kite_engine/order_journal.py, execution_lease.py, execution_lifecycle.py | One account/position owner, persisted intent, recovery without resubmission |
| Broker positions and protection | backend/app/services/kite_engine/positions.py, monitor.py, protective_stop.py, protection.py | Strategy-specific exit policy using shared confirmed inventory |
| Accounting | backend/app/services/kite_engine/fill_ledger.py | Single ledger of fills and costs; no second Snapback P&L authority |
| API and presentation | backend/app/api/v1/endpoints/config.py; frontend/src/hooks/useSnapback.ts; SnapbackSettings.tsx, SnapbackScalpSettings.tsx, board/SnapbackBoard.tsx, board/snapbackAdapter.ts | Shared server contracts and truthful operational state |
| Evidence | backend/app/services/snapback_validation.py; backend/study/snapback_scalp_research.py | Version-bound reports, coverage audit, promotion gate |

Paths in this table are repository-relative implementation locations, not claims that every planned interface already exists.

## Artifact-by-artifact delivery order

| ID | Input | Deliverable | Completion evidence |
|---|---|---|---|
| A0 | Working-tree drafts and review findings | Baseline manifest; fixed generation/freshness/simulation boundaries | Regression tests for all known defects; no promotion flag changes |
| A1 | Feed and historical contract snapshots | ContractRegistry, RawQuoteEvent, QualityDecision, dataset manifest | Counts reconcile; stale/repeated/future/invalid events rejected |
| A2 | Valid completed sessions | FeatureSnapshot, SetupDecision and ablation switches | Prefix causality, no cross-session leakage, mirrored CE/PE tests |
| A3 | Setup plus candidate quotes | ContractCandidateSet, CostEstimate, attainable TradePlan | Tick/lot/depth/fees/cash tests; reject unattainable target floors |
| A4 | Plans and account inventory | RiskReservation and account/session risk ledger | Concurrent plans cannot overspend cash or remaining loss allowance |
| A5 | Filled inventory and observations | Deterministic PositionState, ProtectionIntent and ExitIntent | Runner, delayed trail, partial fill, gap, timeout and event-order tests |
| A6 | Frozen data, A2–A5 | Causal replay and comparison reports | Same rules/policies as runtime; explicit data gaps; costs and holdout preserved |
| A7 | A4–A5 and existing shared broker infrastructure | Durable Snapback execution adapter and recovery | No submit on ACK confusion, unknown outcome, stale config or missing protection capability |
| A8 | Server artifact projections | Full settings, opportunity, position and evidence UI | UI agrees with server; no plan presented as a filled/protected trade |
| A9 | A6 evidence plus A7–A8 | Observation-only and paper readiness report | Submission spies show zero broker writes; ledger and decision parity |
| A10 | Accepted validation and operational evidence | Restricted live release manifest and runbook | Explicit supported scope; small initial size; rollback tested; no unattended sizing escalation |

A7 can be developed against a fake broker while A6 research runs. It cannot submit live before A9/A10 gates. A8 can proceed once API schemas are frozen. No implementation should wait for an indicator experiment that has not demonstrated value.

## Final acceptance

The feature is complete only when:
- every displayed trade is traceable from valid data to actual or explicitly simulated fills;
- costs, partial fills, unresolved positions and account risk are accounted for;
- a profitable move can upgrade under the specified policy without loosening protection or adding size;
- exits and restart reconciliation work when the UI is closed;
- replay, paper and live share decision rules and clearly identify differing execution models;
- live eligibility is scoped to measured strategy/configuration/data/broker capabilities;
- the final report states whether profitability was demonstrated, failed or remains inconclusive.

A negative or inconclusive validation result completes the research honestly but does not authorize a profitable-strategy claim or a live rollout.

