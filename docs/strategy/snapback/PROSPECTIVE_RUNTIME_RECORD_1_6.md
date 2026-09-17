# Prospective runtime record — 1.6

**Economic verdict: INCONCLUSIVE.**

Runtime 1.6 changes what Sterling can prove about its own execution. It changes
nothing about whether Snapback makes money, and no result in this document
should be read as evidence that it does.

---

## Why this release exists

On 2026-09-17 the SterlingLake inventory was run against the mounted drive. It
found no historical option price data at all behind the 746 frozen Snapback
signals:

```
real historical option bars      0 / 746
real historical option depth     0 / 746
dated contract masters           2 days (2026-08-13, 2026-08-14), both
                                 current-universe snapshots
historical tick archive          empty
NFO-FUT day bars                 427 files, 598,441 bars, 2018-01-01 to 2026-09-03
```

No `NFO-OPT` or `BFO-OPT` segment exists in the lake at any interval. The vendor
constraint was confirmed independently: Kite serves intraday history only for
currently active options and continuous daily data for expired futures, so
expired option history cannot be retrieved at any price.

The consequence is permanent. The historical Snapback result (+3.04% OOS,
p=0.016) rests on modelled option prices and can never be upgraded to
execution evidence for the 2017-2026 window. Forward recording is the only route
to real option data, which makes every unrecorded session evidence destroyed
rather than deferred.

The historical replay engine was quarantined rather than repaired.

---

## Identity

A release record cannot contain the SHA of the commit that contains it: writing
the SHA in changes the commit, which changes the SHA. The immutable tag is the
canonical release-to-commit binding, and this file names the tag rather than
chasing its own hash.

```
release identity               tag: snapback-prospective-runtime-1.6
                               exact commit:
                                 git rev-parse snapback-prospective-runtime-1.6^{commit}
release-candidate source       branch runtime-1.6-evidence
parent runtime-1.5 SHA         9a706f5848ed087b947b690161c02208a249955e

strategy SHA                   5a1354202e2c960c66b7003fce9cb80abd152008
config hash                    6ecbeb53e9768a91
rule hash                      e03ddf75f29463a8
manifest                       snapback_reality_v1.2
```

No strategy logic, parameter, filter, or hash changed in this release.

## Schema versions

```
EvidenceClass                  MODELLED < OBSERVED_MARKET < BROKER_SHADOW < BROKER_EXECUTED
candidate-universe schema      v2  (strike/tick_size float, expiry ISO string;
                                    incomplete/non-positive contract metadata rejected)
candidate hash algorithm       sha256 over canonical JSON, numerics as f"{v:.4f}",
                               sorted, eligibility bounds included in the payload
selector version               snapback_delta_closed_form_v1
selection record               implicit v1
hedge evidence schema          1
tick evidence schema           kitelake EVIDENCE_TICK_SCHEMA, 53 columns
broker evidence schema         1
lifecycle schema               1
evidence store schema          1
```

Candidate-universe v1 was deterministic, but the pre-release live-path audit
found one violation of the release's own evidence rule: a missing/zero exchange
`tick_size` was silently repaired to `0.05`, and blank/non-positive identity
fields could survive into the candidate set. No authoritative prospective sample
had been accepted yet, so runtime 1.6 advances the universe contract to v2 rather
than freezing a known fabrication. v2 keeps the same stored field shapes but
rejects incomplete contract metadata instead of repairing it. v1 hashes remain
reproducible, but are not runtime-1.6 authoritative evidence.

The same hardening pass made prospective tick rows refuse a missing/zero
`instrument_token` and a naive `received_ts`; neither may be silently rewritten
as token `0` or interpreted in the machine's local timezone.

## Selector fixtures

```
file                           backend/tests/fixtures/snapback_selector_parity.json
sha256                         a2c81c975b87f8971cea3d2084cad78bcff97c696e3c51afd69ef8b46a1ae536
fixtures                       840
```

Captured from `pick_for` before any edit, across 7 symbols x 3 spot levels x
5 IVs x 4 DTEs x CE/PE. Parity is asserted in both directions — the shared
selector reproduces every fixture, and `pick_for` still matches them — and the
file's own hash is pinned, so moving the selector and regenerating the fixtures
to match fails the suite.

---

## Two selections, recorded separately

Snapback selects twice, and an earlier draft of this record described only the
first — claiming broadly that "the production selector does not consult a chain".
That is true of the frozen theoretical selector and false of the path that
actually buys.

**The frozen theoretical selector** (`pick_for`) is closed-form. It solves for a
strike via `strike_for_delta`, rounds it to the instrument's published strike
step, and consults no option chain.

**The T+1 prospective execution path** separately evaluates the real listed
chain under the frozen executability constraints — monthly expiry inside the DTE
window, quote present, spread within `max_spread_pct`, premium at or above
`min_option_premium`, open interest at or above `min_option_oi` — and selects the
eligible contract closest to `target_delta`.

Runtime 1.6 records these as two separate facts:

```
theoretical SelectionRecord        what the frozen rule targeted
actual ExecutionContractRecord     what the real chain would sell
```

Neither may overwrite or masquerade as the other. Collapsing them would credit
the closed-form rule with a chain-aware choice it never makes, and would erase
`strike_gap` and `delta_gap` — the distance between intention and reality, which
is the execution quantity this release exists to measure.

Making the *theoretical* selection depend on candidates would change which
contract Snapback buys — a strategy change during a frozen prospective
experiment. So that behaviour is preserved exactly, and the candidate universe
answers only the question it honestly can:

```
LISTED       the computed contract exists in the observed master
NOT_LISTED   the master exists and the contract is absent
UNKNOWN      no authoritative master was available
```

Only `LISTED` is authoritative. Both failures block entry and are recorded
distinctly, because "the rule chose a contract that does not exist" and "we could
not see the exchange" mean different things to an operator. Neither is ever
repaired by snapping to a nearby strike.

**How often the computed strike is actually listed is still unmeasured.** Every
Snapback trade to date has assumed it. This release makes the question
answerable; it does not answer it.

---

## What was built

| Commit | Slice |
|---|---|
| `dd332db8e` | quarantine the historical observed replay |
| `31b26e28a` | EvidenceClass + full-depth prospective tick schema |
| `1d353c452` | candidate universe capture + deterministic hash |
| `9f6ba7ff9` | shared canonical selector + 840-fixture parity |
| `fb6a44a92` | selection provenance + reality check |
| `60ca3a1a5` | hedge evidence parity |
| `a2fc2ea02` | evidence recorder + broker lifecycle |
| `d51b5c4a1` | live evidence acceptance harness |
| `b586ca34d` | kitelake fno-fut expectation |
| `b8badac0a` | evidence auditor + fail-closed operator controls |
| `830a215b3`, `74e0aae49` | release record, bound to its tag rather than its own SHA |
| `b51203be1` | make the acceptance harness runnable |
| `fbda6efe5`, `4ceb6fe76` | restore fail-closed prospective evidence hardening |
| `e1a5f41b9` | execution-contract evidence + production recorder wiring |

The fail-closed contract/tick hardening was first written on a sibling commit
(`8c49034a5`) that is not in this branch's history; its changes reached the
release through `4ceb6fe76`. The lineage above is the branch as it actually
stands, which is what the tag will point at.

### Two un-backfillable gaps closed

The raw tick path kept `depth["buy"][0]` and discarded levels 2-5, so it could
record that a quote existed but never whether a given quantity would have
filled. It also collapsed both clocks into one field and substituted local
wall-clock when the exchange timestamp was absent, making a fabricated stamp
indistinguishable from a real one. Neither is repairable in rows already
written.

`EVIDENCE_TICK_SCHEMA` keeps all five levels per side with order counts, records
`exchange_ts` and `received_ts` separately, never substitutes a clock, and
distinguishes an absent level (null) from one quoting zero.

### Lifecycle

State is derived by folding an append-only journal, never assigned. Every legal
edge is enumerated; an exhaustive test drives ~400 non-declared pairs to prove
they are refused. Terminal states cannot be reanimated — a retry requires a new
`opportunity_id`, so the refusal stays in the record. States carrying exposure
keep exiting reachable.

Every opportunity leaves a trace, including those that never reach a broker.
That is the denominator: without it, "84 completed trades" cannot be
distinguished from 100 signals or 400.

### Broker truth

Fills advance on cumulative broker quantity, never status text: a broker
reporting COMPLETE with 75 of 150 is reporting a partial fill. Event ids derive
from the broker's own facts rather than receive time, so a retried callback
returns `ALREADY_RECORDED` instead of becoming a second fill.
`PROTECTION_SUBMITTED` is not `PROTECTION_ACTIVE`.

### Durability

Immutable part files via `_staging` + `os.replace`. A crash may leave an orphan
staging file; it cannot leave a truncated file that reads as valid evidence.
Writer failure is deliberately asymmetric — it blocks new exposure and never
blocks managing existing exposure, and it records rather than raises, so a
storage fault cannot propagate into a trading path.

### Safe mode

One durable state that survives a restart, because restarting is the first thing
anyone does when something looks wrong. It blocks opening risk and nothing else.
An unreadable or unrecognised state file reads as SAFE_MODE, not NORMAL.

---

## Verification

```
backend unit + integration     1995 pass
kitelake                        294 pass
frontend vitest                1539 pass
frontend typecheck             clean
frontend production build      succeeds
worktree                       clean
service                        /api/v1/health/live   200 alive
                               /api/v1/health/ready  200 READY, 14/14, failed: none
operator scripts               sterling-status and sterling-safe-mode verified
                               against the running service
evidence auditor CLI           verified, text and --json
backup manifest                sha256 -c VERIFIED against the mounted lake
```

Remote CI: PR #179, 13/13 checks SUCCESS at the code-complete head
`e1a5f41b9`. This record is the only change after it, and CI must be green again
at the resulting head before the live acceptance run certifies that SHA.

## Backups

```
working    /run/media/nageshmadaram/3f36ac07-.../SterlingLake
copy 1     /mnt/OS/SterlingLakeBackup                      18483 files, verified
copy 2     SD card 3331-6535                               18483 files, verified
manifest   41f1f2c398f45a901bbce598b5305d172bd81dd4ca49839ca83d924b43b49c09
```

---

## Known limitations

1. **The live acceptance run has not passed.** A pre-open invocation reached the
   real Kite instrument master and reproduced a real candidate hash, then stopped
   at the spot quote because the daily access token had expired. FULL-mode depth
   and timestamp semantics therefore remain unconfirmed against the vendor until
   `acceptance-*.json` reports `overall: PASS` using a current token during market
   hours.
2. **The recorder has not observed a natural live opportunity.** The
   recorder/lifecycle journal is wired into the real
   `SnapbackProspectiveCollector` production path and proven by production-path
   integration tests that drive `execute_pending_entry` itself rather than the
   helpers in isolation. What remains is prospective evidence still to be
   collected, not an implementation gap.
3. **Listedness is unmeasured for real Snapback opportunities.** The acceptance
   master proves the universe can be built from reality; an acceptance probe is
   not a frozen strategy signal with its real `assumed_iv`.
4. **CI has no kitelake job**, so `kitelake/tests` must still be verified locally.
5. **`OPTION_PARTIALLY_FILLED` is not a distinct state**; partials re-enter
   `OPTION_FILL_PENDING` with cumulative quantity in the payload.
6. **The state machine lives in `snapback_evidence_recorder.py`**, not a separate
   `snapback_evidence_state.py`.

## What this release does not prove

That Snapback will be profitable. That it has durable forward expectancy. That
the modelled historical option prices resembled reality. That future drawdown
will be tolerable. That the system can safely support meaningful family capital.

Those are economic questions. Runtime 1.6 builds the instrument that can answer
them honestly, and the answer needs 300 completed authoritative trades across 60
sessions at roughly 0.33 trades per session. That is years. Nothing about the
deadline changes it, and increasing trade frequency to reach it faster would
destroy the experiment it is meant to serve.

## After this release

Stop changing Snapback. Collect, reconcile, audit, measure, wait. Change code
only for a real P0-CAPITAL, P0-EVIDENCE or P0-OPERABILITY defect exposed by
prospective operation, and treat that as a new runtime and a new experiment.
