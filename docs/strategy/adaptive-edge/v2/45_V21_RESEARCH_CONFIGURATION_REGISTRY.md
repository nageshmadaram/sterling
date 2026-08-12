# Adaptive Edge V2.1 — Research Configuration Registry

**Status:** PRE-REGISTERED / RESEARCH ONLY
**Live authorization:** FALSE

## Purpose

This registry freezes the candidate search space before empirical evaluation. It does not select a winner.

The target family is the A26-ND volatility-normalized directional target:

```text
R(t,h) = P(t+h) / P(t) - 1
Z(t,h) = R(t,h) / sigma_t

UP       if Z > theta
DOWN     if Z < -theta
NEUTRAL  otherwise
```

## Candidate horizons

```text
5 bars
10 bars
15 bars
30 bars
```

The bar interval must be fixed by the dataset manifest before evaluation. The initial research dataset is expected to use completed 1-minute TrueData bars, subject to entitlement verification.

## Candidate movement thresholds

```text
0.25 sigma
0.50 sigma
0.75 sigma
1.00 sigma
```

These are candidate `theta` values, not production constants.

## Candidate volatility estimators

All estimators are causal and use only completed reference-bar returns available at decision time.

### RV-15

Population standard deviation of the previous 15 one-minute log returns.

### RV-30

Population standard deviation of the previous 30 one-minute log returns.

### RV-60

Population standard deviation of the previous 60 one-minute log returns.

The window excludes the current future outcome interval. The exact warm-up and minimum-evidence policy must be enforced by the dataset/replay contract rather than by silently backfilling values.

## Candidate family

```text
4 horizons
x 4 thresholds
x 3 volatility estimators
= 48 target configurations
```

Every configuration receives a stable research identifier:

```text
AE21-H{h}-T{theta}-V{window}
```

Example:

```text
AE21-H15-T050-V30
```

## Selection boundary

The 48 candidates are a single research-selection family.

Candidate selection may inspect TRAIN/VALIDATION evidence only.

The final HOLDOUT is opened only after:

```text
feature set
volatility estimator
horizon
theta
model configuration
calibration configuration
economic policy
```

are frozen.

## Required per-cycle evidence

For every candidate and every walk-forward cycle, record:

```text
candidate_id
training_cutoff
validation_start
validation_end
test_start
test_end
sample counts by class
training loss
validation log loss
validation Brier score
calibration error
class recall/precision
prediction coverage
selected feature_set_version
model_version
calibration_version
source_dataset_version
```

## Selection objective

The primary predictive selection metric is validation log loss because the downstream system consumes probabilities rather than only hard classes.

Secondary diagnostics must include:

```text
Brier score
class-wise recall/precision
calibration error
neutral-class coverage
probability concentration
stability across walk-forward cycles
```

Economic selection is downstream and must not retroactively redefine the predictive target.

## Statistical controls

The 48-target candidate family is subject to the repository's research-selection and multiple-testing contracts.

A candidate that wins only because the candidate family was searched cannot be treated as an independently validated discovery.

## Holdout rule

No final-holdout result may alter:

```text
candidate set
feature set
volatility estimator
horizon
theta
model hyperparameters
calibration
entry threshold
option selection
cost assumptions
```

If any of these are changed after holdout inspection, the holdout is contaminated and a new untouched holdout is required.

## Current state

```text
Candidate registry     = FROZEN
Actual dataset         = NOT YET INGESTED
TrueData entitlement   = MUST VERIFY
Model selection        = NOT STARTED
Holdout                = LOCKED
Promotion              = BLOCKED
Live execution         = BLOCKED
```
