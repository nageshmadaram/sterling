# Snapback Prospective Freeze Record

Runtime tag:
snapback-prospective-freeze-1.0

Runtime SHA:
9e989dd910995deb5e77385b983e5992c58883c0

Frozen strategy:
Snapback 1.0.5

Strategy manifest:
version snapback_reality_v1.2
manifest commit_sha (FROZEN_COMMIT_SHA) 5a1354202e2c960c66b7003fce9cb80abd152008
config_hash 6ecbeb53e9768a91
rule_hash e03ddf75f29463a8
cost_model_hash 630a4de9ad143e2c
trial_registry_hash 186d4dba8b341d66

Frozen parameters:
lookback_days 20
min_stretch_atr 1.5
max_rv_pct 70.0
market_filter bearish
market_ema 50
target_delta 0.70
hold_days 15
runner_mult 1.5
hedge_mode index_futures

Dataset begins:
2026-09-16T09:11:20Z
2026-09-16T14:41:20+05:30 IST

Database:
data/snapback/prospective_freeze_1.db
(absolute: /home/nageshmadaram/Sterling/data/snapback/prospective_freeze_1.db)
Configured via STERLING_OBSERVATIONS_DB_PATH in backend/.env.
No pre-freeze database existed on this host, so no archive copy was required.

Mode:
PAPER / OBSERVATION ONLY

Broker order submission:
DISABLED

Parameter changes permitted:
NONE

Authoritative sample rule:
Only observations created after this timestamp, using this runtime tag and this
frozen manifest, qualify. Pre-freeze paper outcomes are never retroactively
included.
