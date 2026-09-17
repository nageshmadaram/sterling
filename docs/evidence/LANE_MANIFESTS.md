# Sterling — The Ten Lane Manifests

A lane is one `(strategy, mode)` experiment. It carries its own rules, its own
evidence and its own verdict, and none of that leaks sideways.

**This page is a human-readable mirror. The authoritative artifacts are:**

- `data/manifests/release.json` — the frozen release and all ten identities
- `data/manifests/strategies/<strategy>__<mode>.json` — one file per lane

Regenerate them with `./sterlingctl freeze`, read them with
`./sterlingctl manifest`, and check for drift with `./sterlingctl verify`.

---

## 1. The mode contract

Modes differ only in time budget. The budget is a *budget*, not a target: a
trade may exit far earlier for a stop, a reversal, a risk limit or a broker
problem. `hard_hold_limit` is the latest permitted holding time.

| Mode | Signal TF | Expected hold | Hard exit | Overnight | Square-off | Max entries/session | Max concurrent |
|---|---|---|---|---|---|---|---|
| `ultra_scalping` | 1m | 1–10 min | 20 min | no | 15:20 IST | 12 | 1 |
| `scalping` | 5m | 10–45 min | 90 min | no | 15:20 IST | 6 | 2 |
| `intraday` | 15m | 45 min – 4 h | same session | no | 15:20 IST | 3 | 2 |
| `overnight` | 60m | 1–3 sessions | 5 sessions | yes | — | 2 | 3 |
| `swing` | 60m | 3–10 sessions | 15 sessions | yes | — | 2 | 4 |

Every open position carries an immutable holding-horizon plan, computed at
admission and frozen at entry. A position whose horizon cannot be computed does
not open.

## 2. Lane states

| State | Meaning | May open new exposure |
|---|---|---|
| `disabled` | Switched off | no |
| `research` | Exists as a plan or awaits its own sample | no |
| `paper` | Collecting forward paper evidence | yes, paper only |
| `shadow` | Priced against the real book, orders never sent | yes, shadow only |
| `live_minimum` | Promoted; minimum executable size | yes, real money |
| `live_scaled` | Promoted and scaled | yes, real money |
| `blocked` | Held back by an operational problem | no |

Three independent conditions must **all** hold before a lane originates: the
strategy is focused, the lane's rules are frozen and hashable, and the state
permits it. Each reports its own refusal code, because collapsing them into one
boolean is how "why did nothing trade today?" becomes unanswerable.

Live states additionally require the global live switch, which is
`LIVE_EXECUTION_ENABLED = False` in code.

## 3. The ten lanes

### Snapback

| Lane | State | Rules frozen | Note |
|---|---|---|---|
| `snapback:ultra_scalping` | research | no | 1m reversal mechanics are sketched, not frozen; no rule hash yet |
| `snapback:scalping` | paper | yes | Canonicalised from the legacy `scalp` path; forward sample starts at zero |
| `snapback:intraday` | research | no | No distinct rules yet: shares the `scalp_*` path and knobs with scalping |
| `snapback:overnight` | research | no | Gap, DTE, theta, stop and hedge policy are undefined; cannot be hashed |
| `snapback:swing` | paper | yes | The frozen daily lineage; the core must not change once forward collection runs |

**Why `intraday` is not a lane yet.** The engine branches on
`trading_mode in ("scalp", "intraday")` and reads the same `scalp_*` knobs for
both, so "intraday" is the scalping rule wearing a second label. Treating them
as two lanes would demand two 300-trade samples from one rule and let the same
edge be counted twice. The lane opens when it has rules of its own: a 5–15m
signal timeframe and a 45 min – 4 h budget.

### Triple SuperTrend

| Lane | State | Rules frozen | Note |
|---|---|---|---|
| `supertrend:ultra_scalping` | research | yes | 1m Heikin-Ashi, same triple-ST alignment rule; no evidence yet |
| `supertrend:scalping` | research | yes | 5m Heikin-Ashi, same triple-ST alignment rule; no evidence yet |
| `supertrend:intraday` | research | yes | 15m Heikin-Ashi; hard square-off 15:20 IST; no evidence yet |
| `supertrend:overnight` | research | yes | 60m Heikin-Ashi; gap, theta and weekend exposure evidence still missing |
| `supertrend:swing` | research | yes | 1H engine is the candidate; awaits the parity audit and a fresh sample |

**Why `supertrend:swing` is not PAPER.** The 1H engine has years of history, but
that history belongs to no canonical lane. Promoting on the strength of it would
import a sample this lane never collected.

The five SuperTrend lanes differ only by signal timeframe: they are five entries
beside one frozen core (`supertrend_core_v1`), not five engines.

## 4. Identity

Every authoritative evidence row carries:

```
strategy_id + strategy_version + mode + mode_version
runtime_sha + release_tag + config_hash + rule_hash
evidence_schema_version + universe identity
```

Two hashes are kept apart on purpose:

- **`rule_hash`** — hashed from the *frozen record*, not the loaded config, so
  the identity cannot move because somebody edited a default.
- **`config_hash`** — hashed from the config actually loaded, drift included, so
  a drifted runtime can still record what it really ran.

A lane whose rules are not frozen has **no identity at all** in the manifest:
`identity: null`, with `identity_unavailable: "rules_not_frozen"`. Manufacturing
a hash would produce something that looks exactly as authoritative as a real
one.

### One correction worth knowing

`snapback/manifest.compute_rule_hash` hashes only the daily swing rules and no
mode field, so scalping and swing would otherwise produce the *same* rule hash
from genuinely different rules. `snapback.lanes.lane_rule_hash` covers the core
rules, the canonical mode and the mode's own knobs, so two lanes can never
collide. The manifest hash itself is left untouched: it is bound to a frozen
commit and verified against stored evidence.

## 5. Legacy modes

| Legacy spelling | Canonical mode |
|---|---|
| `scalp` | `scalping` |
| `swing` | `swing` |
| `intraday` | `intraday` |
| `positional` | **refused** |

`positional` predates the overnight/swing split and the old config does not say
which it meant. Mapping it either way would fabricate the very distinction the
five-mode model exists to measure, so it raises rather than defaults. An
unmappable mode means the row is not recorded — which is better than recording
it against the wrong experiment.

## 6. Reading a lane manifest file

```json
{
  "lane_key": "snapback:swing",
  "state": "paper",
  "rules_defined": true,
  "horizon": { "expected_hold_min": 3, "hard_hold_limit": 15, "...": "..." },
  "identity": {
    "strategy_version": "snapback_core_v1",
    "mode_version": "snapback_swing_v1",
    "rule_hash": "...",
    "config_hash": "...",
    "identity_hash": "..."
  },
  "build": { "runtime_sha": "...", "release_tag": "..." }
}
```

If `identity_hash` differs from the one attached to a stored evidence row, that
row belongs to a **different experiment**. It is not this lane's evidence, and
no report may count it as such.
