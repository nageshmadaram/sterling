# Adaptive Edge V2 — Research Resolution Selection and Information-Preservation Protocol

**Artifact:** A66  
**Status:** SPECIFICATION  
**Version:** 2.1.0

## 1. Purpose

A66 defines how temporal resolution may be compared without discarding information before research begins and without selecting a resolution after observing holdout performance.

The raw research authority remains TrueData. Trading/execution remains Zerodha Kite. This artifact does not change either ownership boundary.

## 2. Canonical hierarchy

```text
TrueData raw observations
        |
        v
immutable source dataset
        |
        +--> canonical event representation
        |
        +--> deterministic derived resolutions
                    |
                    +--> feature construction
                    +--> label construction
                    +--> execution simulation
```

Raw observations must be preserved whenever the provider entitlement permits their lawful retention.

## 3. Resolution is a research factor

Temporal resolution is not a strategy constant unless the canonical strategy specification explicitly freezes it.

Candidate resolution is therefore represented as:

```text
r ∈ R
```

where `R` is a preregistered research set.

No resolution may be added to `R` after inspecting holdout results.

No resolution may be selected because it produces the best in-sample result.

## 4. Information preservation

A coarser representation may be derived from a finer representation only through deterministic aggregation.

```text
fine -> coarse       ALLOWED
coarse -> fine       FORBIDDEN
```

The system must never reconstruct missing intra-bar observations from OHLC data and treat those reconstructions as observed market events.

## 5. Aggregation boundary

For each derived resolution, the aggregation function must define:

- interval origin;
- interval inclusion/exclusion rule;
- timestamp assigned to the derived observation;
- OHLC construction;
- volume construction;
- open-interest handling;
- bid/ask handling where available;
- missing-event behavior;
- session-boundary behavior;
- timezone;
- source revision policy.

These are implementation contracts, not learned parameters.

## 6. Causality

For a decision at timestamp `t`, only source observations with provider-defined availability time `<= t` may influence the decision.

A completed bar ending at `t` is usable only if its availability semantics establish that the complete bar was available by `t`.

If availability cannot be established, the observation is not silently treated as causal.

## 7. Resolution comparison protocol

Resolution comparison must occur inside the declared walk-forward experiment.

For each candidate resolution:

```text
same source population
same chronological folds
same embargo/purge policy
same label definition
same cost model
same promotion criteria
```

Only the representation resolution changes.

The following must not change silently between resolution experiments:

- target definition;
- execution-cost source;
- risk semantics;
- holdout population;
- session calendar;
- evaluation metric definitions.

## 8. Multiple testing

If multiple resolutions are evaluated, resolution is part of the research-selection family.

The selected resolution must therefore be recorded together with:

- candidate family;
- number of candidates evaluated;
- selection criterion;
- validation observations;
- dependence-adjustment procedure where required;
- final holdout result.

Holdout data may not be used to choose the resolution.

## 9. Resolution and feature leakage

A higher-resolution source does not authorize higher-resolution future information.

For example, if a 1-minute decision is defined at `10:15:00`, observations occurring after `10:15:00` cannot be incorporated merely because they belong to the same eventual 1-minute bar.

Feature construction must operate on the event availability timeline, not only on bar labels.

## 10. Resolution and execution

Execution simulation may require finer information than prediction.

This is permitted provided the execution simulation uses only information that would have been available after the decision and follows the declared order/fill model.

Prediction resolution and execution-simulation resolution are therefore separate variables:

```text
prediction_resolution
execution_simulation_resolution
```

Neither is assumed equal to the other.

## 11. Research candidates

The currently registered candidate set must be read from the research configuration registry.

No new numerical resolution is frozen by A66.

If tick-level data are available, tick-derived resolutions may be candidates only after the raw tick semantics and entitlement have been verified.

## 12. Failure conditions

A resolution experiment must fail closed when:

1. required source observations are unavailable;
2. timestamp semantics are unresolved;
3. aggregation creates duplicate timestamps;
4. aggregation is non-deterministic;
5. source data are silently repaired;
6. a feature uses observations after decision availability time;
7. the candidate was introduced after holdout inspection;
8. candidate-specific costs or labels are changed without preregistration.

## 13. Frozen versus learned

### Frozen architecture

- raw-source preservation;
- deterministic aggregation;
- causal availability boundary;
- preregistration requirement;
- identical fold/evaluation protocol;
- holdout isolation;
- resolution-selection audit trail.

### Learned / validated

- final prediction resolution;
- final feature resolution where multiple representations are tested;
- any resolution-specific hyperparameters.

### Configuration requiring validation

- candidate resolution set;
- aggregation calendar;
- fold lengths;
- embargo duration;
- computational limits.

### External dependencies

```text
TrueData raw tick entitlement          = UNKNOWN
TrueData tick payload semantics        = UNKNOWN until provider evidence is recorded
TrueData historical retention          = entitlement-dependent / UNKNOWN for this account
Provider availability timestamp        = UNKNOWN until documented/verified
```

## 14. Adversarial review

### Look-ahead

A coarse bar must not expose intra-bar events that occurred after the decision timestamp.

### Overfitting

A finer resolution can increase the effective hypothesis space. Resolution selection therefore belongs to the declared research family.

### Survivorship

Instrument universes must be fixed using the canonical instrument-selection policy. A resolution experiment cannot silently remove difficult observations.

### Execution impossibility

A backtest cannot use a finer observation stream to claim a fill that the live Kite execution boundary could not have observed or achieved.

### Data substitution

Kite market data must not replace missing TrueData research observations.

## 15. Canonical audit chain

```text
TrueData raw observation
    -> canonical event
    -> derived resolution
    -> causal feature snapshot
    -> prediction
    -> economic decision
    -> Kite execution
    -> confirmed fill
    -> position
    -> outcome
    -> mature label
    -> research evaluation
```

## ARCHITECTURE STATUS

Frozen: information-preserving multi-resolution architecture, causal availability, preregistered resolution selection, holdout isolation.

## UNRESOLVED

Actual TrueData tick entitlement, tick payload semantics, historical retention, and provider availability semantics.

## BLOCKERS

A real tick-level research experiment cannot begin until the actual TrueData source contract and entitlement are verified.

## NEXT ARTIFACT

A67 — TrueData Tick/Event Semantic Verification and Source Adapter Contract.
