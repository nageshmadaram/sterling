# Specification 01 — Data, lineage and artifact contracts

Status: proposed implementation specification, PLAN-1.0. Parent: [implementation plan](../IMPLEMENTATION_PLAN.md).

## 1. Common artifact envelope

Every durable strategy artifact carries:
- schema_version and engine_version;
- artifact_id, parent_artifact_ids, created_at_ms and available_at_ms;
- strategy_id, trading_mode, config_hash and config_generation;
- account_id/user_id only for private account-scoped artifacts;
- dataset_id/run_id for research;
- source_kind: broker_stream, broker_rest, historical_vendor, imported or simulated;
- quality_status, quality_reason_codes and input/source hashes.

IDs are deterministic where replay/idempotency requires them. An emitted artifact is immutable. A correction is a new version referencing the superseded artifact. Price values in durable execution/accounting contracts use integer ticks or decimal strings, never binary-float equality. Analysis arrays may use floats, with finite-value checks at boundaries.

Only decision-time-available inputs can be parents of a decision. An earlier exchange timestamp does not make a quote received later usable earlier.

## 2. ContractRegistry

Identity:
- exchange, segment, underlying_id, exchange_token and exact tradingsymbol;
- option_type, strike, expiry_date, settlement/product constraints;
- lot_size, tick_size, quantity/freeze limits where available;
- effective_from, effective_to, source_version and captured_at_ms.

Never use token alone as permanent identity: the full dated contract identity accompanies it. Never reconstruct historical contracts or lot sizes from the current instrument dump. Unknown tick, lot, expiry or eligible order capability blocks live planning/submission.

Existing contract resolvers remain the source for listed contracts. Add historical registry snapshots rather than overwriting prior metadata.

## 3. RawQuoteEvent

Required fields:
- contract_id/source connection id/local ingestion sequence;
- exchange_timestamp_ms, received_at_ms, persisted_at_ms;
- best_bid, best_ask, bid_quantity, ask_quantity;
- optional ordered depth levels, last_trade_price/time, cumulative_volume, open_interest;
- original payload hash, reconnect/gap indicator and subscription mode.

Spot/index events use their own instrument identity. Underlying candles and option quotes stay separate. Index OHLC volume of zero is missing participation evidence, not proof of low trading interest.

Capture existing WebSocket full quote/depth events into an append-only sink, with explicit bounded subscriptions. Use REST for startup/reconciliation, not a 200-symbol sequential polling loop as a low-latency execution clock. Kite documents full quote fields and depth in its [WebSocket specification](https://kite.trade/docs/connect/v3/websocket/).

A repeated quote can be a health observation; it cannot create another market movement, trade count, OI change or zero-volatility candle. For sources without an event sequence, deduplication uses contract, exchange timestamp and stable payload identity while preserving genuinely distinct payloads with equal timestamps.

## 4. QualityDecision and SessionTape

QualityDecision fields:
- raw_event_id, decision_at_ms, accepted_for_context, accepted_for_execution;
- quote_age_ms, receive_delay_ms, last_trade_age_ms and clock_skew_ms;
- two_sided, non_crossed, finite_values, contract_valid, depth_available;
- reason_codes and policy_version.

Mandatory rejection cases: non-finite/missing timestamps; future events beyond explicit clock tolerance; invalid/crossed/one-sided books; non-positive prices; negative/non-integer quantities; expired/unknown contracts; wrong account/provider; missing session membership.

Research and live freshness thresholds are different policies. Proposal: live entry requires exchange quote age and local availability within a 2-second ceiling, with clock tolerance 250ms, initially rejecting rather than silently widening the threshold. These are provisional operational budgets, not measured guarantees; calibrate against actual feed timestamps/latency before live acceptance. The existing 60-second archive audit is only a coarse data-quality filter.

SessionTape identifies trading date/calendar version and interval. OHLC timestamps mean bar open. A completed bar becomes available after its end and the recorded processing delay. Sessions and special sessions come from the exchange calendar; do not infer them from weekday alone. Unknown calendar coverage blocks live decisions.

Store:
- completed underlying bars with missing-interval flags;
- observed option quote events separately;
- true vendor/trade option OHLC only when provenance supports that interpretation;
- sample-derived quote bars explicitly marked sampled_quotes, never true OHLC.

No forward/backfill across missing market intervals. Reset signal warmup after gaps. If a gap occurs during an open position, replay flags an unresolved/uncertain execution interval. It cannot drop that trade or invent a favorable exit.

## 5. Dataset manifest

Fields:
- dataset_id, file/table partitions, checksums, immutable snapshot time;
- contract registry version, calendar version, source/provider;
- symbols, dates, intervals, raw/accepted/rejected counts by reason;
- coverage per contract-day, gap distributions, first/last observation;
- quote delay, spread/depth distributions;
- trade eligibility versus context-only classification;
- train/validation/test date partitions and split policy;
- known survivorship, selection and missing-data limitations.

Counts must reconcile. Dataset availability is part of the research result. Current data are sufficient for the existing coverage/cost audit, not for a profitable scalping claim. The requirements are described in [the stored-data assessment](../SCALPING_RESEARCH.md).

## 6. Decision artifacts

| Artifact | Required content | Consumer |
|---|---|---|
| FeatureSnapshot | completed bar id; EMA/Bollinger/ADX/ATR; optional VWAP/rvol/OI context; value availability and lookback counts | Entry rule |
| SetupDecision | setup_id; CE/PE bias; trigger/invalidation; bar-close and decision timestamps; acceptance or reasons | Contract selection |
| ContractCandidateSet | all eligible candidate ids; quote ids; selection constraints; rejected candidates/reasons; deterministic rank | Cost planner |
| CostEstimate | quantity, executable price assumptions, spread/impact, fee components, entry/exit latency/slippage assumptions, model version | Trade plan and risk |
| TradePlan | selected contract; requested/effective target; feasible target ceiling; initial stop; quantity; cash/risk; expiry; validity/expiry time; policy hash | Risk reservation |
| RiskReservation | account, plan id, reserved cash/risk, session id, expected inventory revision, state/expiration | Execution journal |
| PositionState | confirmed inventory; remaining quantity; filled VWAP; active protection; desired stop; policy state/revision | Exit manager/UI |
| TradeLedger | fill ids; fees; gross/net P&L; fee completeness; unresolved exposure | Risk/research/UI |
| ValidationReport | exact code/data/config/cost hashes; tested scope; results/uncertainty; limitations | Promotion gate |

Unavailable fields are null with a reason, not zeros. Displayed confidence/probability must carry estimator version, sample count and calibration evidence, otherwise remain unavailable.

## 7. Persistence and concurrency

Add migrations under backend/alembic/versions with names assigned at implementation time. Prefer existing market capture tables when their semantics match; introduce separate versioned tables for Snapback feature decisions, plans, lifecycle events and validation manifests. Do not duplicate the existing kite_order_intents or fill ledger.

Minimum new logical tables:
- snapback_decisions, keyed by deterministic setup/decision id;
- snapback_plans, immutable per plan revision;
- snapback_position_events, append-only policy transitions linked to shared fill/protection ids;
- snapback_validation_runs and snapback_dataset_manifests.

Risk reservations should extend/reuse the shared durable account transaction boundary. Store ownership and expiration; reserve before network submission; release only after terminal broker truth, not a timeout.

Use compare-and-swap on configuration generation and position revision. Before publishing a scan or reserving/submitting an order, recheck the captured generation against current persisted settings. Running positions retain their entry-policy version; a settings edit affects new decisions unless a specific audited position policy change is requested.

## 8. Data acceptance tests

- Non-finite timestamps, prices and sizes always fail quality checks.
- Late-arriving data never alter a previously emitted decision in the same replay.
- Appending future candles/events leaves every earlier artifact unchanged.
- Out-of-order input does not mutate historical state; duplicates do not double-count.
- Session/timeframe changes cannot hit another mode's candle cache.
- Missing required feature or contract metadata rejects the dependent operation.
- Two concurrent workers cannot publish or execute a stale-generation plan.
- Dataset counts and all rejected-row categories reconcile.
- Imported candles are labeled provenance-unverified unless separately audited.

Implementation locations: reuse navigator capture/repository and ohlcv_store; add a focused backend/app/services/snapback_market_data.py for Snapback quality/subscription policy, and backend/app/engines/snapback/intraday_models.py for typed artifacts. Avoid embedding broker client calls in pure feature code.

