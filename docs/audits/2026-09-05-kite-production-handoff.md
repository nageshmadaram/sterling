# Sterling Kite Engine: production audit and AI implementation handoff

Date: 2026-09-05. Scope: Sterling Kite Engine, including spot, derivatives and confluence signals, option/futures execution, protection, replay and operator UI. Navigator remains an optional integration, not a replacement strategy.

## 1. Decision and evidence boundary

**NO-GO for live promotion.** This audit implemented safety changes; it did not establish positive net expectancy or complete production certification. No live order, account-mode change, deployment or parameter optimization was performed. Do not interpret this document, passing unit tests, historical comments saying “validated”, or the existing auto-exec toggle as permission/evidence to activate capital.

Concrete release blockers: atomic fill/PnL/capital accounting; recovery of a confirmed entry when the process dies before registry/protection setup; manual-entry journal parity; unverified current executable contract data; incomplete quote freshness, capital reservation and mandatory portfolio risk gates. Live **auto-entry** intent reservation and ambiguous-timeout duplicate prevention are now implemented. The connected lake's registered removable-drive path was unavailable during inspection. The available index cache ends **2026-07-16**, before CAS commenced.

Labels used below:

- **EXCHANGE-VERIFIED:** facts checked against exchange/regulator publications.
- **IMPLEMENTED:** code changes present in this checkout; verification limits still apply.
- **STERLING-DESIGNED:** engineering policy, not an exchange mandate or measured alpha.
- **CALIBRATION-REQUIRED:** profitability, liquidity or execution assumptions not demonstrated by available data.
- **REMAINING:** work required before promotion; never represent it as completed.

Original review base: `7ff055e1e30d6d26b33aafb07788e7ad982231aa`. A concurrent workspace process created commits during this audit, including `ba1996dc7`, `91551a47a`, `cf5cb66b5` and `c33fd3338`. Review the entire delta from the original base plus later edits. Graph results were initially stale, then refreshed; their coverage numbers are navigation hints, not test evidence. Exact verification is recorded in [validation JSON](2026-09-05-kite-validation.json): **505 core tests + 352 affected integration/adapter tests passed**, with three existing FastAPI deprecation warnings per suite; TypeScript compilation and whitespace checks passed. These tests verify mechanics, not investment returns. Production broker execution was not certified.

## 2. Verified exchange and broker rules

All times IST; intervals end exclusively for strategy eligibility. Effective dates must remain part of every replay, session decision and expiry calculation.

| Rule | Verified behavior | Engine policy |
|---|---|---|
| Pre-open from 2026-09-07 | 09:00–09:05 market/limit; 09:05 onward limit only; market modification/cancellation unavailable; entry closes randomly during 09:08–09:10; matching starts when entry ends; buffer 09:12–09:15 | No strategy orders in any pre-open phase. Do not infer order acceptance from clock during randomized closure. Futures pre-open eligibility must not be applied to options. |
| CAS from 2026-08-03 | Eligible cash stocks stop continuous trading at 15:15. Transition/reference calculation until 15:20; market/limit entry 15:20–15:25; limit-only phase then random closure during 15:28–15:30; matching finishes by 15:35 | Auctions excluded from continuous signal candles. No auction execution implementation. |
| Other NSE cash | Continuous trading ends 15:30 | Explicit eligibility flag required to distinguish the cash schedules. |
| NSE equity derivatives | Continuous session 09:15–15:40 from 2026-08-03 | NFO monitoring can continue after cash continuous trading ends. Cash-derived origination stops at 15:15. |
| Zerodha MIS | Current published square-off: CAS cash 15:12; other cash 15:25; equity/index derivatives 15:26 | Broker product cutoff overrides exchange close. A 15:40 exchange close does not authorize MIS holding until then. Existing engine futures margin request is NRML. Verify the actual order product end to end. |
| Holidays | 2026 annual NSE list plus January 15 election closure | New-entry calendar covers 2026; unknown years close entry eligibility. Special sessions are not assumed regular sessions. |

Primary references, checked 2026-09-05:

1. [SEBI January 16 circular: CAS and pre-open](https://www.sebi.gov.in/legal/circulars/jan-2026/introduction-of-closing-auction-session-cas-in-the-equity-cash-segment-and-certain-modifications-in-the-pre-open-auction-session_99122.html).
2. [NSE CAS operating page](https://www.nseindia.com/static/products-services/closing-auction-session), including circular references CMTR/74466 and FAOP/74467. Some direct archive fetches failed; indexed primary content and the operating page supported the rules.
3. [NSE derivatives pre-open](https://www.nseindia.com/static/products-services/equity-derivatives-pre-open-session), updated September 4.
4. [NSE current timings and holidays](https://www.nseindia.com/resources/exchange-communication-holidays). This page spans multiple segments; do not merge settlement/banking holidays into the equity trading list.
5. [NSE annual equity holidays CMTR/71775](https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf) and [January 15 addition CMTR/72260](https://nsearchives.nseindia.com/content/circulars/CMTR72260.pdf).
6. [Zerodha MIS cutoffs](https://zerodha.com/marketintel/bulletin/249809/latest-intraday-leverages-mis-bo-co).
7. [Zerodha April 2026 STT revision](https://zerodha.com/marketintel/bulletin/445377/revision-in-stt-securities-transaction-tax-from-1st-april-2026) and [STT calculation](https://support.zerodha.com/category/account-opening/resident-individual/ri-charges/articles/how-is-the-securities-transaction-tax-stt-calculated).

**BSE limitation:** the implemented timing module retains the old BSE/BFO 15:30 bound. Its separate notices, CAS eligibility and holiday coverage are not independently certified here. Do not promote BSE/BFO execution using this NSE audit. The calendar's shared holiday assumptions also need separation before BSE certification.

The actual option/futures adapter uses **NRML**, confirmed in `client.py`; MIS cutoffs are therefore relevant to future product changes, not the current engine order product. The adapter now sends automatic market protection for MARKET/SL-M payloads, validates protection values, and refuses to convert strategy derivatives into next-day AMOs. Protected orders can remain pending or be rejected; protection is not a fill guarantee. See [Kite order parameters](https://kite.trade/docs/connect/v3/orders/). Only explicit HTTP 429 responses trigger the adapter's rate-limit retry; arbitrary error text containing “rate” does not.

Before live release, verify the actual account's static-IP/API setup against the [retail algo framework implementation timeline](https://www.sebi.gov.in/sebi_data/attachdocs/sep-2025/1759232056254.pdf) and [Zerodha's operating explanation](https://zerodha.com/z-connect/updates/nses-new-algo-trading-circular). This audit did not inspect or certify account registration/network permissions. Kite's [sandbox](https://kite.trade/docs/connect/v3/sandbox/) has different capabilities, including absent GTT/margin support; sandbox success cannot certify those production paths.

## 3. Actual data audit

Machine-readable results: [2026-09-05-kite-real-data-audit.json](2026-09-05-kite-real-data-audit.json). Reproducer: `backend/study/kite_production_audit.py`. Input arrays were read without modification; hashes record the exact bytes examined. No generated premiums or manufactured market series were used in this evidence run.

| Instrument | Raw bars | Invalid OHLC/volume bars | Signal prefix violations | Exit indices changed by raw versus HA extrema |
|---|---:|---:|---:|---:|
| NIFTY 50 | 13,029 | 0 | 0 | 3 |
| NIFTY FIN SERVICE | 13,029 | 0 | 0 | 4 |
| NIFTY BANK | 13,029 | 0 | 0 | 2 |
| SENSEX | 12,976 | 0 | 0 | 5 |

Total: **52,063 bars**, **404 sampled prefix checks**, **2,275 entry/exit comparisons**, **14 changed exit indices**. Exit comparison horizon: at most 240 following bars. It isolates the extrema change while keeping the current exit implementation otherwise constant; it is not a full comparison against the original commit. No prefix failures in a sample is not proof of every possible input.

All caches end July 16, 2026. Post-CAS bars: **0**. Read-only acquisition attempts did not repair this gap: the configured broker quote probe returned `ConnectTimeout`; a September 4 NSE FO UDiFF archive request returned `ReadTimeout`. These are unavailable responses, not empty markets, expired credentials or proof that the archive is absent. Index volume can legitimately be zero; it must not be treated as executed option volume. Files were produced by the existing Kite history-cache workflow, but lack original response receipts, acquisition manifests and independent vendor validation. They establish observed-cache behavior, not independently certified market provenance.

**No Sharpe, winning strategy selection, option profitability or go-live return claim is supported by this dataset.** An index is not an option or futures fill. Earlier Black–Scholes “options lens” sweeps are modeled research and cannot satisfy the user's real-data-only promotion requirement.

## 4. Final strategy contract

Preserve current three SuperTrend parameters `(21,1)`, `(14,2)`, `(7,3)` and configurable HA/raw indicator basis until a valid chronological study demonstrates an improvement. These parameters are existing choices, not proven optima. Entry requires a fresh full alignment transition after warmup. Options remain long premium: bull cash signal selects CE; bear cash signal selects PE. Futures alone carry short exposure directly. Contract-premium derivative signals have their own direction in premium space.

Decisions use finalized candles. HA may define indicators; raw traded prices establish order execution/stop touches. Check the previous completed bar's stop against the next bar's raw extrema; a same-bar revised stop is lookahead. Stops may tighten but must not widen after entry. Closed-bar counter exits and resting price stops are separate event types.

STERLING-DESIGNED operating policies implemented: no auctions; no unknown-year entries; no previous-day or unfinished signal entries; fresh signal age within ten minutes after a full one-hour bar closes; serialize account entry callbacks in one process; unknown capital or invalid stop sizes to zero; fixed-lot mode still requires fresh available capital and valid finite execution inputs; unresolved exits/PnL or failed book-health checks halt further origination. Ten minutes reflects scan/cache operations, not a measured trading advantage. Final shortened session bars are finalized for analytics but cannot authorize entries after the market has closed.

REMAINING: unify every consumer on the same session-aware bar-end resolver, including partial final bars, chart preparation, historical premium attribution, index close/CAS observations and expiry-day stops. The scanner applies its new session clipping from the CAS effective date; earlier historical records retain the old duration path. Date-effective symbol eligibility and separate CAS datasets remain necessary.

## 5. Artifact-by-artifact implementation inventory

Paths below are repository-relative. Treat this table as a precise work map; inspect the current diff before editing because other tasks share the checkout.

| Artifact | Implemented behavior | Remaining acceptance requirement |
|---|---|---|
| `backend/app/services/kite_engine/market_hours.py` | Versioned NSE policy, 2026 holidays, aware timestamps, exclusive close, pre-open/CAS phase labels, date-effective NFO close, cash-source entry cutoff | Primary-source refresh workflow; 2027 and special sessions; instrument eligibility snapshots; separate BSE policy; broker product cutoff resolver; operational observability |
| `backend/app/services/kite_engine/scanner.py` | Removed execution uses of `allow_forming=True`; common scan snapshot time; rejects nonfinite/nonincreasing/impossible OHLC; short final bars and new-session auction clipping | Full interval continuity/gap checks, stale data reasons in response, true candle revision/finality contract, cash-index CAS semantics, short-bar entry premium attribution |
| `backend/app/engines/sterling_kite_engine/regime.py` | Carries raw high/low separately from indicator-basis prices | Raw OHLC validation at every direct engine/replay entrypoint; freeze hashes of indicator implementation in evidence |
| `backend/app/engines/sterling_kite_engine/exits.py` | Raw extrema for stop touches; monotonic stop; shared ratchet for displayed levels, exit reasons, engine management and premium replay | Special cash-to-premium translation remains approximate; prove full incremental/batch parity |
| `backend/app/engines/sterling_kite_engine/engine.py` | Tracks entry timestamp; management delegates to shared exit resolver | Incremental versus batch parity after skipped calls/lookback eviction; same-bar reprocessing; stock theta-stop scope parity |
| `backend/app/services/kite_engine/sizing.py` | Invalid/missing/nonfinite inputs block; no default lot on unknown capital or invalid stop; affordability cannot be bypassed by min-lot risk override | Remove futures 15% estimate entirely in favor of per-order broker margin input; exact decimals/tick/lot rules; fee and gap risk; portfolio capital reservation |
| `backend/app/services/kite_engine/service.py` | Entry data/session gate before work and before send; account callback lock; enabled liquidity failures block; fixed-lot affordability and finite stop/lot checks; exact final futures margin; pending exit polling and unique-tag recovery; book-health/PnL/exit entry block | Mandatory quote age/depth and portfolio risk independent of optional filters; multi-process durable entry intents; account generation checks; ambiguous entry ACK recovery; no assumed IV for live premium stops; complete manual-entry parity |
| `backend/app/services/exchanges/kite/client.py` | Automatic market protection for MARKET/SL-M; valid protection required; derivatives refuse AMO fallback; retries limited to explicit HTTP 429 | Fresh marketable-limit/tick policy; per-slice freeze limits; durable intent recovery; account/product permissions; uncertain transport outcomes must never auto-resend |
| `backend/app/services/kite_engine/order_journal.py` | SQLite `BEGIN IMMEDIATE` auto-entry reservation keyed by account/strategy/generation/signal/contract/side; deterministic 20-character broker tag; strict intent transitions; unresolved lookup; immutable fill-event uniqueness primitive | Wire manual entries and exit intents; atomically combine fill ledger, signed position, PnL, fees and capital reservation; recover filled entries before registry/protection setup; multi-process crash harness |
| `backend/app/services/db.py` | Creates versioned-compatible intent and fill-ledger tables with unique broker execution identity | Move strategy financial state out of JSON config; explicit migration/version checks; fail startup when live durability cannot be established |
| `backend/app/services/exchanges/kite/accounts.py` | Attaches stable account identity to warm clients for intent ownership | Prove account-generation rotation and prevent stale-client order attribution |
| `backend/app/services/kite_engine/positions.py` | Persists exit order ID, unique tag, request time, cumulative exit fills and PnL-reconciliation flag | Transactional durable journal; persistence failures currently swallowed; generation identity; per-order cumulative fill accounting and schema migration |
| `backend/app/services/kite_engine/monitor.py` | ACK no longer closes/books PnL; pending/unknown exit prevents resend and can recover by unique tag; attributable confirmed fills consume quantity; duplicates ignored; partial exit retains remainder; all intents defer on active/unverified GTT cancellation; opposite broker direction blocks exit; external changes flag reconciliation | Partial-fill financial accounting; legacy/ambiguous tag recovery; external/GTT child-order attribution; protection re-arm on rejection; fill corrections; crash atomicity; fresh venue/product/account-specific holdings; unknown holdings and pre-cancel sequencing |
| `backend/app/services/kite_engine/detail.py` | Date/venue-dependent expiry close for fractional DTE | Scanner/service helpers still contain day floors/defaults; unify DTE everywhere; remove stale 15:30 comments; certify BFO expiry rules |
| `backend/app/services/kite_engine/backtest.py` | Raw next-open premium entry; next-open close-derived exit; gap-aware stop estimate; raw input checks; cash affordability; dated STT; GST includes SEBI fee; removed spurious daily annualization of trade returns | Effective dates for all fees; IPFT/exercise/physical settlement; expiry identity; unresolved end-of-series holdings; quote-based execution; bid/ask and fee stress; portfolio replay; invalidate older synthetic evidence |
| `backend/app/api/v1/endpoints/kite_engine.py` | Registry deletion requires broker-confirmed flatness; returns pending-exit/PnL flags; legacy force cannot bypass book-health preflight | Full release-readiness API beyond current registry checks; expose policy/config/data version; manual-order parity |
| `backend/app/engines/sterling_kite_engine/schemas.py` | New `exit_pending` and `pnl_reconciliation_required` response fields | Explicit submitted/part-filled/unknown/reconciliation states; strict finite config validation; stop-mode and product constraints |
| `frontend/src/types/kiteEngine.ts` | Mirrors optional exit/PnL flags | Durable readiness response types and migration coverage |
| `frontend/src/components/kite/EnginePositionsPane.tsx` | Shows “Exit awaiting fill” and “P&L reconciliation required” | UI interaction test: pending remains visible; repeated exit disabled; no claim that ACK means flat |
| `frontend/src/components/kite/BacktestPane.tsx` | Renamed old “Sharpe” output to “Trade return ratio” | Daily mark-to-market equity and correctly defined Sharpe before restoring that label; visibly non-promotable synthetic runs |
| `backend/tests/engines/sterling_kite_engine/test_production_audit.py` | Session boundaries, malformed bars, no HA-only fills, ratchet/report parity, next-open replay, sizing failures, exit ACK/partial/duplicate/tag recovery, opposite exposure, STT dates, liquidity evidence | Broader state-machine/crash/restart/time-boundary suites; actual broker sandbox/captured postback contracts |
| `backend/tests/test_kite_orders.py` | Mock HTTP payload assertions for protection; no strategy AMO; no retry from arbitrary error text | Captured real broker contracts and rejected/pending protected-order lifecycle |
| `backend/tests/engines/sterling_kite_engine/test_autoexec_gate_http.py` | Real local HTTP checks retain 409 despite `force=true`; disabling remains allowed | Full readiness reasons and manual-entry parity |
| `backend/tests/engines/sterling_kite_engine/test_order_journal.py` | Concurrent reservation idempotency, strict terminal transitions, broker-order/tag lookup and duplicate fill rejection | Process-level crash/restart and multiple worker tests against production-equivalent database settings |
| `backend/tests/engines/sterling_kite_engine/execution_fixtures.py` | Shared explicit broker COMPLETE event helper; requires a submitted order ID | Use only for order mechanics, never for market-performance evidence |
| `backend/tests/engines/sterling_kite_engine/test_directional.py` | Unknown-capital sizing expects zero exposure | Preserve the funding requirement across vehicle configuration changes |
| `backend/tests/engines/sterling_kite_engine/test_directional_exec.py` | Freshness dependency isolated in routing fixtures; explicit margin evidence; fixed-lot capital/stop failures tested | Session boundary tests remain separate; fixture balances are not real account evidence |
| `backend/tests/engines/sterling_kite_engine/test_engine.py` | Exit expectations use the earliest shared raw stop event | Extend skipped-management and lookback-eviction parity |
| `backend/tests/engines/sterling_kite_engine/test_protection.py` | ACK/fill separation, cancelled/unknown exit safety, real-event quantity fields and reconciliation expectations | Extend durable crash, partial-value and actual broker-postback contracts |
| `backend/tests/engines/sterling_kite_engine/test_risk_and_monitor.py` | Unknown capital and unaffordable lots block; exit completion requires a broker fill | Extend cross-position reservations and portfolio risk constraints |
| `backend/tests/engines/sterling_kite_engine/test_scanner.py` | Fixture OHLC low remains positive | Add session gaps/revision/finality tests |
| `backend/tests/engines/sterling_kite_engine/test_state_service.py` | Execution fixtures isolate session time and provide explicit available capital | Exercise current session gate separately |
| `backend/tests/engines/sterling_kite_engine/test_zz_probe.py` | Legacy plumbing isolates the new entry-data gate | Do not read fixture signals as real market evidence |
| `backend/tests/engines/sterling_kite_engine/test_zz_probe_realized.py` | Realized PnL assertions follow explicit exit fill confirmation | Add partial fill value corrections and transactional restart tests |
| `backend/study/kite_production_audit.py` | Read-only hash/integrity/prefix/stop-impact report from actual caches | Acquire immutable post-CAS contract datasets with manifests; independent data-source comparison; replay actual tradable vehicles |
| `docs/audits/2026-09-05-kite-real-data-audit.json` | Machine-readable source/code hashes and measured audit counts | Regenerate after relevant strategy code or input data changes; retain prior versions for provenance |
| `docs/audits/2026-09-05-kite-validation.json` | Exact verification snapshot, commands, source hashes, warnings and test outcomes | Release requires zero unexplained failures across core and affected integrations |

## 6. Required production lifecycle

Implement one durable state machine per account, instrument, strategy and position generation:

`INTENT_RESERVED → ENTRY_SUBMITTED/ENTRY_UNKNOWN → PARTIALLY_FILLED → OPEN_PROTECTED → EXIT_SUBMITTED/EXIT_UNKNOWN → PARTIALLY_EXITED → FLAT_RECONCILED`.

Rejection/cancellation changes order status; it does not prove position quantity is zero. Timeout means unknown outcome, not permission to retry. Broker net position is evidence of exposure, not attribution to one strategy or trade. Resolve order IDs using strategy/client intent tags and the broker order book before sending again.

In one database transaction, ingest `(account, order_id, exchange_trade_id)` once, update cumulative quantity/value, update position quantity/average cost, book exact realized PnL/fees, update risk reservations, and enqueue state/event delivery. Keep exchange and receive timestamps. Reject stale-generation fills. For order updates with cumulative averages, derive incremental value from cumulative value differences; never multiply the latest cumulative average by just the newly filled quantity.

Crash tests must cover every boundary between reservation, send, ACK, persistence, fill delivery, protection placement/cancellation and exit. A duplicated/out-of-order postback must not double-book PnL or create an opposite exposure. Broker-protection rejection requires confirmed replacement or explicit unprotected-exposure handling; never silently forget the position. One process lock does not solve multiple workers, duplicate service instances or a restart.

## 7. Real-data acquisition and research protocol

1. Restore the registered lake or select an accessible account-authorized storage destination. Fetch read-only broker history and current instrument masters. Store raw payloads, request parameters, server/exchange timestamps, retrieval time, hashes and source identifiers. Do not expose credentials in artifacts.
2. Capture NSE post-August-3 cash continuous bars, separate auction events, NFO options/futures OHLC and timestamped bid/ask depth. Capture September-7 pre-open under the new policy when those events exist; they do not exist as historical evidence on September 5. Preserve absence as absence.
3. Build date-effective universes with listed tokens, expiry, option type/strike, lot/tick size, corporate actions and CAS eligibility. Never select historical strikes from today's instrument master. Respect expired-contract data availability; no Black–Scholes replacement in a real-only run.
4. Split chronologically with a final untouched holdout. Purge/embargo overlapping trade horizons across folds. Freeze the hypothesis/parameter grid before seeing holdout results; log all tested candidates and reject retrospective “best” selection.
5. Replay the actual vehicle using closed-bar decision times and first available subsequent executable quote. Model order type, tick rounding, spread, queue uncertainty, partial fills, gaps, broker RMS rejection and dated costs. Report any OHLC approximation explicitly; OHLC cannot establish intrabar ordering or queue fills.
6. Build daily mark-to-market portfolio equity, including open positions, costs, funding/margin and cash reservations. Report net expectancy, confidence intervals using session blocks, drawdown, loss tails, concentration, turnover, fill/rejection statistics and cost sensitivity. Do not annualize trade returns as daily returns.
7. Compare the unchanged baseline against one justified change at a time. Declare acceptance thresholds and permitted risk budget before calibration. There is no pre-approved profit-factor/sample-size threshold in this audit; do not fabricate one.
8. Use shadow execution on real market events to validate decision and order-intent parity. Paper fills are operational simulations, not proof of live fill quality or profitability.

## 8. Release gates and rollback

All gates required: current primary-source session/calendar coverage; verified contract dataset and untouched holdout; positive robust net edge under predeclared acceptance rules; no unresolved core/affected test failures; transactional fill/risk accounting; startup reconciliation against broker; fresh quote/depth and margin evidence; validated protected partial-fill paths; broker/product/account permissions; operational alerting and tested kill switch.

No autoexec `force` flag can override missing data, unknown exposure, stale policy, unverified margin, failed protection or missing release evidence. On any such condition, block new exposure while maintaining monitoring and confirmed risk-reducing exits.

Deploy only after gates pass. Start with explicitly approved limited capital/position limits and continuously compare expected versus observed fills. On rollback: stop new entries, reconcile and protect existing exposure, preserve order/fill journal and idempotency records, then revert code. Never reset the registry or cancel protection merely to obtain a clean UI.

## 9. Remaining implementation tickets for the next AI

These are **not implemented** by this audit. Do not infer completion from the preventive halts above.

| Priority / work unit | Artifacts to create or extend | Required contract and acceptance |
|---|---|---|
| P0 durable entry intents — partial | `order_journal.py`, database schema, `service.py`, `monitor.py`, account client identity | Implemented: atomic reservation, tag before network send, SUBMITTING/SUBMITTED/UNKNOWN states, exact duplicate blocking and broker-event terminal transitions. Remaining: reconstruct and protect a confirmed fill after crash between ACK and registry creation; resolve safe pre-send RESERVED cleanup; process-level two-worker crash proof. |
| P0 fill ledger | Same journal plus `state.py` financial accounting and protection adapters | Unique `(account_id, order_id, trade_id)` executions; cumulative quantity/value reconciliation; exact signed position and realized ledger in one transaction. Entry/exit corrections, partial cancel, duplicate postback, reordered postbacks, scale-in and restart must preserve quantity and PnL. Current JSON `set_config` failures can be swallowed and are insufficient durability. |
| P0 mandatory execution evidence | New `execution_policy.py`; service/manual order paths; instrument master adapter | `ExecutionContext` carries account, product, exchange, contract, tick/lot/freeze size, quote timestamp, bid/ask sizes, available/reserved capital, broker margin and policy version. Reject missing/stale/nonfinite/ambiguous data before reservation. Fixed quantity may skip sizing optimization, never mandatory budget/exposure limits. Reserve capital across simultaneous orders and strategies. |
| P0 protection and holdings | `monitor.py`, `protection.py`, `protective_stop.py` | Key holdings by account/venue/product, use signed fresh quantities, cancel/reconcile entry remainder before exiting partial fills, attribute GTT child IDs, verify cancellation before rival exit, re-arm residual protection after rejection. Current unknown holdings and cached/symbol-only snapshots need explicit resolution; never use absence of an API answer as flatness. |
| P1 canonical bars and instruments | `market_hours.py`, scanner/detail/backtest helpers; new versioned exchange calendars/universe manifests | `bar_end(start, interval, instrument, policy_version)` shared everywhere. Maintain auctions separately; date-effective CAS eligibility, roll/expiry, corporate actions, special sessions and separate BSE policy. Replays at every boundary must match online finalized-bar decisions without revisions/lookahead. |
| P1 real execution research | New immutable acquisition manifests, executable-contract replay, chronological study report | Store original requests/responses and hashes. Actual instrument prices/depth/fills only; no synthetic option replacement. Include costs and open-position equity; predeclare folds/candidates/thresholds. Holdout and post-CAS coverage must be actual acquired events. September 7 evidence cannot exist before that date. |
| P1 release readiness | New `readiness.py`; schemas/API; settings and positions UI | Return `ready`, structured `reasons[]`, strategy/data/policy/code hashes and evidence IDs. Evaluate at startup, enable and each entry reservation. Full release checks must cover more than current registry preflight; no force override. Missing evidence halts entries while exits/protection stay available. |
| P1 operations | New runbook, metrics and shadow reports | Alerts for unknown orders, stale feed/calendar, naked exposure, failed cancellation, ledger mismatch and clock drift. Exercise kill switch, reconnect, cold restart and rollback against actual event captures. Broker GTT and margin capabilities require appropriate production validation because the documented sandbox lacks them. |

## 10. Instructions to the next AI

Read root AGENTS.md; use code-review-graph before source exploration. Read this handoff, validation JSON and real-data JSON. Inspect the delta from the original base and preserve concurrent user changes. Use isolated test databases: existing global fixtures clear stored account/config records.

Priority order: (1) implement and prove transactional lifecycle/ambiguous entry ACK recovery while preserving passing regressions; (2) unify mandatory execution inputs, premium/DTE/stop domains and policy metadata; (3) acquire missing real contract/post-CAS data and complete chronological research; (4) wire release readiness through API/UI; (5) operational shadow validation and only then controlled rollout.

Do not call this strategy “production ready” while any release gate is open. Report implemented artifacts, exact tests, real measurements and unresolved dependencies separately. Do not tune on generated prices, purchase data, place live orders, deploy, or send external messages merely because this handoff exists.
