# Specification 04 — Validation, implementation sequence and release

Status: proposed implementation specification, PLAN-1.0. Parent: [implementation plan](../IMPLEMENTATION_PLAN.md).

## 1. Two independent acceptance questions

Engineering correctness: does the system implement causal decisions, risk limits, broker truth, protection and accounting correctly?

Economic validity: does a frozen strategy show positive net expectancy on genuinely held-out, execution-appropriate data at the intended quantity?

Unit tests can establish the former, not the latter. A high win rate does not establish net profitability. Small samples, sparse quote archives and a positive arithmetic target are insufficient.

The present archive audit is a completed data-quality artifact. It is not a completed profitability backtest. Further implementation must not overwrite that distinction.

## 2. Research artifacts and reproducibility

Each run produces an immutable directory under a configurable research-artifact root, with a compact committed report and large data files outside source control:

- run_manifest.json: engine commit/dirty-source hash, rule hash, execution-policy hash, environment/dependency versions, random seeds and trial id;
- dataset_manifest.json: source hashes, dated contract registry, calendar, quality counts, sampling limits and frozen splits;
- configurations.json: exact baseline and candidate settings, all variants attempted, intended quantity scopes;
- decisions.parquet: every evaluated setup and rejection, including cost/room/quality failures;
- orders_and_fills.parquet: simulated or observed execution events with data basis and timing;
- trades.parquet: full round trips and unresolved exposure;
- session_equity.parquet: cash, realized/provisional net, liquidation marks, fee reserves and drawdown;
- comparisons.json: baseline, ablations, costs, size, regime and sensitivity results;
- validation_report.md: interpretable outcome, limitations and next decision;
- promotion_record.json: eligible scope or explicit failure/inconclusive reasons.

Retention policy preserves the source partitions used by a run; a raw-data cleanup cannot invalidate a published result invisibly. Logs and exported reports exclude broker credentials and unnecessary account identity.

## 3. Frozen comparisons

Register the comparison design before looking at the final test set:
1. Reversal alone, fixed small target and initial stop.
2. Same entries with realistic fees, spread, latency and fill behavior.
3. Same with cost-feasible contract/quantity selection.
4. Same with cost-aware lock.
5. Same with runner/trailing policy.
6. EMA/ADX/volume and optional context ablations, one declared change at a time.

The runner comparison uses identical entry opportunities when testing exits. Filter comparisons report the opportunities they exclude; improvement from fewer trades must be visible. Keep fixed-target and soft-continuation policies distinct: they are different execution decisions.

Scalp and intraday results are separate, as are CE/PE, indices/stocks, expiry buckets and quantities. Daily swing results remain a different experiment.

Measure both strategy contribution and simple matched baselines, with appropriate exposure/cost alignment. Do not judge a relative-value daily result against an unhedged minute trade and call the difference an indicator improvement.

## 4. Data splitting and statistics

Use chronological train, validation and a final untouched test partition. Keep complete exchange sessions together; options of the same underlying/day must not leak between partitions. Purge overlapping position/label horizons at boundaries. Preserve warmup as context without allowing future labels or test outcomes into fitting.

All feature thresholds, target-feasibility estimators, conditional distributions and contract-ranking weights are selected on training/validation only. Log the total number of trials. Retuning after final-test failure makes that test development data; acquire a new final holdout.

Report:
- number of opportunities, rejected setups, fills, completed and unresolved trades;
- independent trading-day count and effective sample concerns;
- net expectancy per trade and per deployed/at-risk capital, with day-clustered uncertainty;
- net win rate with its definition, average gain/loss and profit factor;
- after-cost time-exit and runner contribution, tail concentration;
- mark-to-liquidation and realized drawdown separately;
- spread/slippage/fees and fill ratio by quantity;
- MFE/MAE and target-before-stop/time outcomes with censoring acknowledged;
- regime, side, symbol and expiry consistency.

Missing performance is null, not zero. No-trade configurations cannot pass. Incomplete trades cannot silently disappear from the dataset. Report outcome bounds or label the comparison inconclusive when execution uncertainty is material.

Use moving-block/day bootstrap for uncertainty with resampling settings frozen before the final test. A multiple-testing correction or deflated statistic must account for the actual trial registry; do not reuse a single-strategy p-value after searching many configurations.

## 5. Proposed economic gates

These are proposed acceptance criteria, not results already achieved:
- At least 60 distinct held-out trading sessions and 300 completed trades for the initial decision, with full relevant entry-to-exit coverage. This is a minimum screening floor, not proof or a target trade quota.
- Positive lower 95% day-clustered confidence bound for net expectancy at baseline executable-cost assumptions.
- Candidate improvement over its predeclared baseline remains positive after the appropriate multiple-comparison treatment; if uncertain, retain the simpler policy.
- Net results remain positive under a 2× variable execution-cost stress. Report 3× as a severe scenario regardless of pass/fail.
- Drawdown remains within the predeclared account allocation limit and no exposure/ledger invariant fails.
- No material unbounded dependency on one contract/day or a few extreme trades; disclose and stress concentration rather than hiding it.
- No unresolved positions or missing intervals that can reverse the profitability conclusion.
- Capacity evidence supports the exact approved quantity; one-lot results do not promote ten-lot trading.

Initial account drawdown ceiling for evaluation: 10% of allocated capital; session limit 2%, with gap exceedances separately reported. These are budget policies, not maximum-loss guarantees. If these thresholds are changed after seeing test outcomes, the revised criteria require a new holdout.

Economic failure leads to a documented rejection or a new separately registered hypothesis. It never triggers live averaging down, size escalation or loosening stops.

## 6. Execution realism

Maintain two identified replay levels:
- BAR_RESEARCH: true observed candle references, next-bar entry, old stop first on ambiguity, close-confirmed target/runner policy and next-bar-effective trailing.
- QUOTE_EVENT: only observations available at simulated time, intended latency, price/depth limits, partial fill/rejection/cancel models, and the same target decision policy intended for runtime.

BAR_RESEARCH is useful for logic and exploratory evidence; it alone cannot validate rapid quote-level stop/target behavior. Sampled quote extrema are not upgraded into true candle extrema.

For quote replay, a marketable buy fills against available asks after submission latency, and a sell against bids; cap usable size by the fill model and visible depth. Displayed depth is not guaranteed retained liquidity. Stress cancellation/latency/hidden movement, and report sensitivity to quote gaps. Resting-limit fills require an explicit queue/participation assumption; a touched price alone is insufficient.

Retain a fee/slippage ledger. Do not charge spread twice when quotes already encode it. Child orders, partial exits and retries affect fees according to the dated model. Observed live charges later reconcile provisional paper assumptions rather than rewriting the historical rules.

## 7. Test matrix

| Layer | Required tests | Evidence |
|---|---|---|
| Config/schema | strict booleans/integers, NaN/Inf, bounds, mode-specific fields, invalid schedules, version conflicts | Validation tests |
| Data | timestamps, deduplication, delayed arrival, missing/session bars, metadata rollover, quote staleness and depth | Quality/count reconciliation |
| Features | prefix invariance, ADX warmup, gap reset, upper/lower mirror, optional-feature absence | Causal unit/property tests |
| Economics | tick-aware observed fill, lot/cash/risk/depth caps, child fees, attainable target ceiling, no double-counted spread | Boundary/property tests |
| Portfolio | concurrent reservations, other-strategy exposure, remaining daily loss, fees pending, partial entry cash | Transaction/concurrency tests |
| Policy | lock then runner, delayed monotone trails, gaps, ambiguous bars, target/stop races, absolute timeout | Deterministic event sequences |
| Execution | ACK not fill, partial fills, rejection, timeout/unknown, late order update, order-id reuse collision, lease contention | Fake-broker tests |
| Recovery | crash at each persist/network boundary, restart from journal, pending protection, external broker position changes | Fault-injection/restart tests |
| Accounting | duplicate/corrected fills, charges late, fee reconciliation, partial exits, zero inventory closure | Ledger reconciliation |
| API | account ownership, stale generation/revision, replay limits, no settings persistence in research, idempotent commands | Integration tests |
| UI | requested/effective/feasible targets, desired/active stop, research/filled states, minute vs daily evidence, unknown outcomes | Component/integration tests |
| Full path | raw event → decision → plan → reservation → order → fill → protection → exit → ledger → report | End-to-end fixture with artifact ids |
| Regression | daily Snapback, mixed replay, shared Kite lifecycle, other strategy consumers | Existing affected suites |

Useful current commands from repository root:
- backend/.venv/bin/python -m pytest backend/tests/engines/snapback backend/tests/services/test_snapback_intraday.py backend/tests/services/test_snapback_sessions.py backend/tests/services/test_snapback_validation.py -q
- backend/.venv/bin/python -m pytest backend/tests/api/test_simulation_snapback.py backend/tests/api/test_simulation_replay_api.py -q
- frontend targeted Vitest suites for Snapback settings, board and adapter; npm run build from frontend.

Add new shared lifecycle/risk tests to the affected suite as those interfaces change. Do not invent fixed final test counts before implementation.

## 8. Ordered implementation slices

| Slice | Files/artifacts | Depends on | Exact exit condition |
|---|---|---|---|
| A0 — stabilize draft | snapback.py freshness/generation; simulation.py mode guards; baseline source manifest | Approved specification | Three identified defects reproduced then fixed; scans cannot republish after config changes |
| A1 — data lineage | intraday_models.py; snapback_market_data.py; existing navigator/ohlcv adapters; migration | A0/schema freeze | Quality counts reconcile; historical/live inputs distinguish context vs execution validity |
| A2 — causal strategy | intraday_features.py; config.py; intraday.py compatibility facade | A1 | Rules above produce deterministic causal artifacts; initial experimental configs registered |
| A3 — trade economics | intraday_economics.py; contracts.py adapters; candidate/cost/plan records | A1–A2 | Plan respects true tick/lot/cash/depth, cost floor and feasibility ceiling |
| A4 — account admission | shared journal/reservation boundary; snapback_execution.py admission | A3 | Concurrent intents cannot overspend or bypass total account/day limits |
| A5 — management | intraday_lifecycle.py; snapback_lifecycle.py; position policy projection | A3–A4 | Locks/runners/exits obey exact event sequence and confirmed inventory/protection |
| A6 — replay/research | replay_intraday.py or evolved facade; study/snapback_scalp_research.py; new comparison runner | A1–A5 | Full artifact trail, known dataset scope, fixed holdout and stress reports |
| A7 — broker adapter | shared execution_lifecycle/monitor/protective_stop extensions; snapback_execution.py | A4–A5 | Recovery/unknown/partial-fill/stop race tests pass with broker writes mocked |
| A8 — product | config.py API plus dedicated Snapback operations endpoints; hooks/settings/board/position evidence | Frozen contracts; A3/A5/A7 | UI presents server truth and all live eligibility reasons |
| A9 — observation/paper | readiness service; non-submitting adapters; forward comparison report | A6–A8 | No real order submissions; forward decision/fill-model/ledger behavior audited |
| A10 — release scope | snapback_validation.py; signed/versioned release manifest; operator runbook | A9 plus economic/operational gates | Eligible scope explicit; rollback and one-lot deployment controls proven |

Use focused reviewable changes per slice. Code may be parallelized only after shared contracts are frozen. A negative research result does not block completing engineering tests, but it blocks live promotion.

## 9. Readiness and promotion contract

Use separate hashes:
- strategy_rule_hash: signal, feature, estimator, contract-selection, horizon and exit parameters;
- execution_policy_hash: event ordering, fill/cost/latency rules, broker adapter capabilities;
- dataset_manifest_hash and code/environment hashes;
- capacity/risk scope: approved contract families, DTE, lot/quantity, cost regime, account constraints.

Transient UI preferences, notification settings and enabled flags are not evidence inputs. Enabling a previously validated configuration must not invalidate its evidence merely because an enabled boolean changed. Conversely, changing a stop, runner, estimator, fee model, universe, execution policy or supported quantity must not reuse incompatible evidence.

Promotion states:
UNVALIDATED → DATA_READY → RESEARCH_PASSED → PAPER_READY → LIVE_ELIGIBLE.
FAILED and INCONCLUSIVE retain reasons and artifacts. HALTED/RECONCILING override entry eligibility at runtime.

A promotion record is not a toggle. It contains exact scope, evidence, measured dates, limitations and compatibility checks. The existing A501.0 prototype change invalidates previous implementation records; legacy reports remain historical artifacts.

## 10. Forward observation, paper and live

OBSERVE:
- consume live data and generate all non-execution artifacts;
- no order submission, modification or cancellation;
- compare event timing and data availability against replay assumptions;
- verify the existing router SHADOW mode is unreachable.

PAPER:
- same signal, economics, risk and policy code;
- simulated execution clearly labeled;
- collect rejected/missed/partial fill estimates and mark provisional accounting;
- proposed minimum 20 complete sessions and 50 closed trades for operational review; no forced trades and no implication that this alone proves profitability.

LIVE_ELIGIBLE:
- only after both economic and operational gates and actual broker capability verification;
- start at one permitted, affordable lot, never at the user's hoped-for high quantity;
- runtime rechecks funds, quotes, scope and protective-order health;
- independent account caps and stop-new-entry controls remain active;
- increasing quantity requires capacity evidence and an explicitly scoped release.

Implementation of live support does not itself send an order or enable the account. Any future live action follows the user's instruction for that deployment, not a background assumption from this planning document.

Rollback:
- disable new admissions first;
- continue managing/reconciling existing positions using their pinned policy;
- do not delete pending intents, protection or ledger state;
- preserve compatible readers/migrations until exposure and unresolved broker state are zero;
- export the failure/run manifest for diagnosis.

## 11. Definition of done

A delivered implementation includes the source changes, migrations, configuration schemas, fake-broker and replay fixtures, targeted regression results, frontend build, reproducible research artifacts, promotion/readiness behavior and operator recovery runbook.

The closing report must state separately:
1. what was engineered and tested;
2. what economic performance was actually measured;
3. what cannot be concluded from available data;
4. whether live execution is connected, enabled and eligible;
5. the exact quantities/contracts/policies supported by evidence.

Do not declare the strategy profitable because all tests pass, because a target exceeds fees, or because a selected backtest is positive.

