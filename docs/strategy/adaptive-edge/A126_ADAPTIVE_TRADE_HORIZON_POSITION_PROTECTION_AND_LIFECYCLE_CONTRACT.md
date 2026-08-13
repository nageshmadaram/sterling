# A126 — Adaptive Trade Horizon, Position Protection & Lifecycle Contract

**Status:** CANONICAL / IMPLEMENTATION SOURCE OF TRUTH
**Version:** 1.0
**Scope:** Adaptive Edge trade lifecycle after an entry decision and while exposure exists.
**Depends on:** A75 Canonical Market Event Contract and preceding canonical strategy, state, economic and execution contracts.

## 1. Purpose

A126 defines how a position can persist, graduate to a longer management horizon, downgrade when persistence weakens, protect capital/profit, and terminate before normal session close.

Core invariant:

```text
initial_horizon != current_horizon != exit_condition
```

Horizon, thesis and protection are independent state dimensions.

```text
POSITION
  +-- HORIZON
  +-- THESIS
  +-- PROTECTION
  +-- OVERLAYS
```

No dimension may silently mutate another.

---

## 2. Adaptive horizon taxonomy

The four rough time buckets are replaced by five overlapping management states.

| ID | State | Intended objective | Initial research window |
|---|---|---|---|
| H0 | IMPULSE | Immediate displacement | seconds to ~5 min |
| H1 | TACTICAL | Short directional continuation | ~3–20 min |
| H2 | INTRADAY_SWING | Developing structural move | ~15–60 min |
| H3 | SESSION_TREND | Persistent session direction | ~45 min–2.5 hr |
| H4 | SESSION_EXTENSION | Exceptional persistence toward cutoff | ~2–5+ hr |

The windows are **semantic research ranges, not frozen numerical strategy parameters**. They overlap deliberately. Time alone must never trigger promotion or downgrade.

The actual policy is:

```text
TIME
+ MARKET STATE
+ THESIS PERSISTENCE
+ ECONOMIC EDGE
+ RISK
+ LIQUIDITY
        -> CURRENT HORIZON
```

### H0 — IMPULSE
Immediate displacement. Highest marginal value from TBT/quote/flow information. Strictest entry and shortest expected persistence.

### H1 — TACTICAL
Short continuation after an initial displacement. Flow, volume, VWAP, opening structure, value interaction and short-horizon volatility become important.

### H2 — INTRADAY_SWING
Developing structural move. Market/Volume Profile, POC/value migration, VWAP, HVN/LVN, opening structure, flow, volatility and option state become important.

### H3 — SESSION_TREND
Persistent session regime. Structural/value migration, profile, volatility regime, underlying persistence, derivatives state and continuing economics dominate; individual ticks must not automatically dominate.

### H4 — SESSION_EXTENSION
An exceptional position that has demonstrated persistence and still has sufficient economic value and acceptable risk to remain open toward the normal cutoff. H4 is not an initial prediction that a trade will last hours.

---

## 3. Initial vs current horizon

Every position records:

```text
initial_horizon
current_horizon
horizon_transition_history[]
```

Example:

```text
initial_horizon = IMPULSE
current_horizon = SESSION_TREND
history = IMPULSE -> TACTICAL -> INTRADAY_SWING -> SESSION_TREND
```

The transition history is immutable except through an auditable correction mechanism.

---

## 4. Thesis state

Canonical states:

```text
THESIS_STRONG
THESIS_VALID
THESIS_WEAKENING
THESIS_INVALID
```

`THESIS_INVALID` forbids normal continuation and requires exit unless an operational emergency prevents immediate execution.

Supervisory `trade_health` may additionally be:

```text
RISK_BREACH
ECONOMICS_INVALID
DATA_UNCERTAIN
LIQUIDITY_STRESS
```

These are supervisory states, not predictive features.

---

## 5. Protection state

Protection is orthogonal to horizon.

```text
P0 RISK_CONTROLLED
P1 BREAKEVEN_PROTECTED
P2 PROFIT_PROTECTED
P3 AGGRESSIVE_TRAIL
```

Meaning:

- `P0`: initial downside bounded by the approved risk contract.
- `P1`: protection approximately reaches entry economics, subject to costs/slippage policy.
- `P2`: a positive portion of accumulated economic profit is protected.
- `P3`: aggressive protection is applied as continued exposure becomes less attractive relative to giving back gains.

Exact thresholds/formulas are **UNFROZEN**.

Mandatory principles:

```text
PROFIT != PERMISSION TO HOLD
LOSS   != AUTOMATIC EXIT
HARD_RISK_BREACH -> EXIT
```

Continued holding requires continued thesis/economic justification.

---

## 6. Option-specific protection

Option premium is not a pure proxy for the underlying thesis. It is affected by underlying movement, IV, Delta, Gamma, Theta, Vega, spread and liquidity.

Therefore the implementation must not reduce all risk management to:

```text
option_price <= fixed_stop
```

The system must be capable of distinguishing:

```text
underlying thesis failure
```
from:

```text
option-specific repricing/noise
```

while retaining an absolute risk boundary and an economic-exit path.

---

## 7. Horizon promotion

Promotion is evidence-driven, never timer-driven.

```text
CURRENT HORIZON
 -> persistence evidence
 -> thesis state
 -> economic edge
 -> risk/liquidity gates
 -> NEXT HORIZON ELIGIBLE
```

Valid conceptual transitions:

```text
IMPULSE -> TACTICAL
TACTICAL -> INTRADAY_SWING
INTRADAY_SWING -> SESSION_TREND
SESSION_TREND -> SESSION_EXTENSION
```

Promotion is forbidden when the thesis is invalid, hard risk is breached, required data is temporally unsafe, or economics have collapsed.

Profit by itself cannot promote a position.

---

## 8. Horizon downgrade

A position may downgrade when persistence weakens while the thesis remains valid.

```text
SESSION_EXTENSION -> SESSION_TREND
SESSION_TREND -> INTRADAY_SWING
INTRADAY_SWING -> TACTICAL
TACTICAL -> IMPULSE
```

Downgrade means shorter expected persistence and stricter management. It does not automatically mean exit.

If thesis is invalid:

```text
ANY_HORIZON -> EXIT
```

Forbidden transitions:

```text
EXIT -> OPEN without a new entry lifecycle
profit -> automatic promotion
loss -> automatic downgrade
elapsed_time -> automatic transition
THESIS_INVALID -> longer horizon
HARD_RISK_BREACH -> continue normal management
```

---

## 9. Entry lifecycle

Signal, order, fill and position are different states.

```text
SIGNAL_CREATED
 -> ENTRY_ELIGIBLE
 -> ENTRY_PENDING
 -> FILLED | EXPIRED | INVALIDATED | REJECTED
```

Before an order is submitted, current economics, risk and execution eligibility must be revalidated.

```text
SIGNAL_VALID != EXECUTION_VALID
```

For a long option, the executable buy-side quote, not historical LTP or an assumed midpoint, governs current entry economics.

Partial fills create actual exposure and therefore immediate position supervision.

---

## 10. Position lifecycle

```text
FLAT
 -> ENTRY_PENDING
 -> OPEN
 -> SUPERVISED
 -> EXIT_PENDING
 -> EXIT_FILLED
 -> FLAT
```

Partial exits are architecturally supported but percentages and thresholds remain research parameters.

---

## 11. Exit hierarchy

At least three independent normal exit mechanisms exist:

### A. Hard risk stop
Absolute risk boundary. Model conviction cannot override it.

### B. Thesis invalidation
Underlying/market thesis becomes invalid.

### C. Profit protection / trailing
Protect accumulated gains using validated option/underlying/volatility/liquidity-aware policy.

Additional exit causes may include:

```text
ECONOMICS_INVALID
TIME_INVALID
SESSION_CUTOFF
DATA_UNCERTAIN
LIQUIDITY_STRESS
EMERGENCY
```

An economic exit may occur before a hard stop when expected remaining value is no longer sufficient.

---

## 12. End-of-day cutoff

Normal strategy trading stops **45 minutes before the authoritative exchange session close**.

```text
NORMAL_TRADING_CUTOFF = SESSION_CLOSE - 45 minutes
```

At cutoff:

```text
new entries            = FORBIDDEN
new additions          = FORBIDDEN
horizon upgrades       = FORBIDDEN
session extension      = FORBIDDEN
normal strategy state  = FLAT
```

The session close must come from the authoritative exchange/session calendar; it must not be globally hard-coded.

The 45-minute offset is currently frozen as an operational requirement.

The phrase "do not exit in the last 45 minutes" cannot mean that an exposed position becomes impossible to close. Positions must be normally flattened **by** the cutoff. Emergency/risk exits remain permitted afterward.

---

## 13. Overlays

These are not additional horizons:

```text
BURST
LIQUIDITY_STRESS
DATA_UNCERTAINTY
ECONOMIC_COLLAPSE
EMERGENCY
```

They modify eligibility, supervision or exit behavior.

If required data is stale, contradictory, malformed or temporally unsafe, the system must raise `DATA_UNCERTAIN`; it must never fabricate missing state.

---

## 14. Transition record

Every horizon/protection transition must record:

```text
transition_id
position_id
as_of
from_state
to_state
trigger
preconditions
supporting_evidence
risk_state
economic_state
model_version
configuration_version
reason_code
```

The record must be auditable and causally reproducible.

---

## 15. Causal invariant

For decision time `t`:

```text
Every input used by lifecycle management must have been observable by t.
```

No future price, completed future bar/profile, future option-chain state, future OI, future Greek, future volume, future fill or future outcome may influence an earlier transition.

Outcome data is permitted only for later labeling/evaluation.

---

## 16. Learned/configurable parameters

The following are NOT frozen:

```text
exact horizon boundaries
promotion thresholds
demotion thresholds
hard-stop distance
trailing formula
profit-lock thresholds
partial-exit fractions
maximum holding period
minimum continuation edge
persistence thresholds
```

They must be selected through causal historical walk-forward validation, with explicit training/validation/test boundaries and multiple-testing controls.

No parameter is valid merely because it appears reasonable.

---

## 17. Frozen architecture

```text
five adaptive horizon states
orthogonal horizon/thesis/protection state
promotion is evidence-driven
downgrade is allowed without mandatory exit
hard risk overrides model conviction
thesis invalidation can exit before hard stop
profit protection is independent of horizon
signal/order/fill/position remain distinct
normal cutoff = session close - 45m
normal positions flattened by cutoff
emergency exit remains available
all transitions auditable
all transitions causally ordered
```

## 18. External dependencies

```text
TODO/UNKNOWN:
authoritative exchange session calendar
TrueData freshness/update semantics for each required field
provider-specific option/Greek timing semantics
broker order/fill semantics
instrument-specific lot/tick/expiry rules
```

These must be resolved by documentation/evidence before implementation claims them as facts.

---

## 19. Hostile review requirements

Before declaring any implementation complete, attack for:

```text
look-ahead bias
profit-induced overholding
loss-induced averaging
arbitrary time-boundary effects
option-price-only stop failure
underlying-only stop failure
EOD deadlock
state explosion
partial-fill exposure errors
execution mismatch
multiple testing
parameter fragility
liquidity failure
data staleness
```

Synthetic adversarial cases must include:

```text
fast failure
fast winner -> multi-horizon graduation
large profit + thesis weakening
loss + thesis still valid
hard-risk breach
cutoff with open position
data failure
liquidity collapse
partial fill
```

---

## 20. Implementation source-of-truth rule

A126 is the canonical source of truth for adaptive trade horizon, position protection and lifecycle semantics.

If implementation, configuration, or another document contradicts A126, implementation must stop at the contradiction until the canonical specification is reconciled.

No hidden lifecycle business logic may be added in code/configuration.

Any new lifecycle requirement must update A126 before implementation changes are accepted.

---

# Architecture Status

```text
ARCHITECTURE STATUS:
COMPLETE

FROZEN:
- five adaptive horizon states
- orthogonal thesis/protection/horizon model
- evidence-based promotion/demotion
- hard-risk override
- thesis invalidation
- profit protection separation
- signal/order/fill/position separation
- 45-minute normal EOD cutoff
- emergency exit path
- causal transition/audit requirements

UNRESOLVED:
- numerical horizon boundaries
- promotion/demotion thresholds
- SL/TSL formulas
- profit-lock thresholds
- partial-exit policy
- maximum holding-period parameters
- exact option-specific protection model
- provider/broker/session external semantics not yet verified

BLOCKERS:
None at architectural level.

NEXT ARTIFACT:
A127 — Execution Lifecycle and Broker Adapter Contract
```
