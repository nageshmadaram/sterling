# Gamma Move — validation report

**Run** 2026-08-26 · **Engine** `gamma_move` A310.2 · **Verdict** NOT VALIDATED
**Source-aligned** 2026-09-09 · **Scripts** `backend/study/gamma_move/` · **Result file** `study/gamma_move/out/CALIBRATION.json`

---

## 0. The finding, first

> **The entry trigger alone has no measurable edge. The level filter does.**

Bars passing all three of the source's entry conditions reached a 30% favourable
excursion within two sessions **24.7%** of the time [20.9, 28.9], against an
unconditional baseline of **21.7%** [21.5, 21.9]. Those intervals overlap: on
this sample, the triple that the whole strategy is named for does not separate
from simply picking a bar at random.

The same triple, restricted to bars where spot sat within 1% of a confirmed
support or resistance level, reached **46.2%** [31.6, 61.4] — a lower bound above
the baseline's upper bound.

So the strategy's distinctive claim, that open-interest unwinding predicts a
gamma move, is **not** what the data supports. What the data supports is a much
older idea: things happen at levels. The engine ships with the trigger intact,
because it is the source's rule and it is cheap, but the setting that actually
matters is `level_proximity_pct`, and the config docstring says so.

`validated` stays **false**. Matching the podcast is not the same as beating the
baseline on in-window data.

---

## 1. What was measured against

| | |
|---|---|
| Contracts | **598** NSE stock options (top 104 F&O underlyings by futures OI **notional**) |
| Option bars | **193,135** fifteen-minute candles carrying real open interest |
| Evaluable bars | **167,253** (after the session-slice and prior-bar guards) |
| Spot bars | **35,020** daily candles across 103 underlyings |
| Expiry | 2026-09-29 monthly · **DTE 34–103** |
| Window | 2026-06-18 → 2026-08-26 |
| Source | live Kite Connect session, `get_historical(..., oi=1)` |

**Forward measure.** For every bar, the maximum favourable excursion (MFE) of the
option premium over the next two trading sessions, expressed as a percent of that
bar's close. Reported as the rate of bars reaching ≥30%, with a Wilson 95%
interval. The *rate of large moves* is the right statistic here and the mean is
not: a single 400% bar carries a mean on its own, and this strategy is explicitly
hunting the tail.

---

## 2. Results

### 2.1 The stack

| Stage | n | MFE ≥30% | 95% CI |
|---|---:|---:|---|
| Baseline — every bar | 174,592 | 21.7% | [21.5, 21.9] |
| Trigger only (OI ≥3%, vol ≥2.5×, px ≥2%) | 450 | 24.7% | [20.9, 28.9] |
| **+ level filter (spot within 1%)** | **39** | **46.2%** | **[31.6, 61.4]** |
| + SuperTrend(10, 2.0) agreeing | 32 | 46.9% | [30.9, 63.6] |

Only the level row clears the baseline with a non-overlapping interval.

### 2.2 Level proximity — the U1 answer

| Band | n | MFE ≥30% | 95% CI |
|---|---:|---:|---|
| ≤ 0.75% | 25 | 28.0% | [14.3, 47.6] |
| **≤ 1.0%** | **39** | **46.2%** | **[31.6, 61.4]** |
| ≤ 1.5% | 56 | 37.5% | [26.0, 50.6] |
| ≤ 2.0% | 70 | 31.4% | [21.8, 43.0] |
| ≤ 3.0% | 96 | 31.2% | [22.9, 41.1] |
| > 3% (far) | 253 | 22.5% | [17.8, 28.1] |

The effect decays monotonically from 1% outward and is indistinguishable from
baseline past 3%. The ≤0.75% cell is worse, but n=25 and its interval spans the
whole range — that is noise, not a reversal. **`level_proximity_pct = 1.0`.**

### 2.3 Trigger thresholds — the U2 answer

Percentiles of the three metrics across all 167,253 evaluable bars:

| Metric | p90 | p95 | p99 | p99.5 | shipped | ≈ percentile |
|---|---:|---:|---:|---:|---:|---|
| OI drop % | 0.31 | 1.06 | 4.49 | 7.31 | **3.0** | p98.6 |
| Volume ratio | 3.12 | 5.46 | 20.0 | 27.7 | **2.5** | p87 |
| Premium gain % | 3.28 | 5.22 | 10.8 | 13.9 | **2.0** | p93 |

Chosen to keep the joint rate genuinely rare — about 0.012 signals per contract
per day, matching the source's own "you get very few trades in this" — while
leaving enough sample inside the level band to measure at all. Stricter settings
(5.0 / 3.0 / 3.0) gave a marginally better rate on n=21 and were rejected as
fitting the smallest cell.

### 2.4 SuperTrend — the U3 answer, and a trap

Percentage-point lift in the MFE ≥30% rate of triggers where the gate **agreed**
with the trade direction, against those where it disagreed:

| multiplier | period 7 | period 10 | period 14 | period 21 |
|---|---:|---:|---:|---:|
| 2.0 | **+7.0** | **+5.1** | **+6.5** | **+6.5** |
| 2.5 | +3.6 | +3.3 | +3.6 | +3.6 |
| 3.0 | +1.3 | **−3.3** | −1.6 | −1.0 |
| 4.0 | +0.1 | −0.7 | −2.5 | −3.3 |

**At the conventional multiplier of 3.0 the gate is inverted at three of four
periods**: agreeing with it was worse than fighting it. At 2.0 the sign is
positive at every period tested and the magnitude is stable. Shipped as
`regime_multiplier = 2.0`, `regime_period = 10` — period 10 because all four
periods agree within noise and 10 is the platform's existing default; taking
period 7 for its +7.0 would be selecting the largest cell.

### 2.5 The session-boundary guard, quantified

Bar-on-bar OI drops, across a session boundary versus within one session:

| threshold | across boundary | within session | inflation |
|---|---:|---:|---:|
| ≥ 5% | 2.95% | 0.85% | 3.5× |
| ≥ 10% | 1.34% | 0.32% | 4.2× |
| ≥ 20% | 0.57% | 0.11% | 5.2× |

Differencing open interest across the boundary would fire a phantom unwind at
the first bar of a large fraction of trading days. `trigger.slice_session`
exists for this and `test_trigger.py` locks it.

### 2.6 Adverse excursion, and the stop

Over the fully-filtered signal set: median MAE **−12.8%**, 10th percentile
−30.9%, worst −47.5%.

| stop | hit rate |
|---|---:|
| 20% | 41% |
| **30%** | **16%** |
| 40% | 3% |

`stop_percent = 30` survives 84% of signals while still capping the loss well
inside the premium.

---

## 3. Two findings that only appeared on contact with real data

1. **Ranking by open-interest *share count* selects penny options.** IDEA carries
   400 million shares of OI because the share is ₹15; its ATM call trades at
   ₹0.73, where the 0.05 tick is a **7% price quantum**. `min_price_gain_pct`
   would have been measuring tick rounding. Fixed by ranking on notional and
   adding `min_option_premium = 10.0` — the source's own entries were at 75, 540
   and 600.
2. **Swing pivots computed with `>=` on both sides make every bar of a plateau a
   pivot**, so a quiet range manufactured a level with 53 "touches". Caught by a
   unit test, fixed to be strictly greater on one side, and the level number in
   §2.2 was then **re-measured through the shipped code** rather than the study's
   copy.

---

## 4. Engine replay

The shipped engine was run end to end over the calibration window
(`step9_engine_replay.py`): 592 contracts considered, 66 passed the level gate,
32 entries, 25 closed, **7 winners (28%) against a 47.9% break-even**, gross
−₹234,960.

**This is not a verdict on the strategy.** It is evidence that the machinery
runs. Four reasons it cannot be read as a backtest:

- the whole sample sits at **DTE 34–103, outside the strategy's own expiry
  window** — the source is explicit that open interest does not behave this way
  early in the cycle, and the window had to be widened for the replay to enter
  at all;
- the level was **today's, held fixed** across the window, rather than
  rediscovered bar by bar;
- exits were `TIME_STOP` at two sessions, the only exit the source supports —
  its own examples exit discretionarily on a 2–3×;
- fills were at the bar close, with **no spread, slippage or brokerage**. Real
  results would be worse, not better.

---

## 5. Live readiness

There is **no paper-only lock and no `live_ready` flag**. Paper/live for Kite is
`account.is_paper`, set from the Trading Mode panel, and a second switch inside a
strategy config is how an engine ends up believing it is papering while the
broker is not — which is exactly what happened here on 2026-08-26.

What this engine owes instead is that the case against trading it is impossible
to miss: the board carries the finding above every row, the settings page repeats
it, and the snapshot returns it as a warning. Whether to trade an unproven edge
is the operator's call; making it an informed one is ours.

The engineering guards are unconditional rather than mode-gated — a rule that
only holds when the money is real is a rule the paper results were never measured
under. Every entry carries a stop, `validate()` refuses a config without one, and
under the default `stop_mode = both` a broker-side GTT goes on at entry.

The list below is therefore a **research** checklist, not a lock:

- [ ] Re-run this calibration on data **inside** the expiry window (DTE ≤ 14).
      First window for the Sep-29 monthly is mid-September 2026. Until that run
      exists, `validated` stays false and the 26 Aug numbers stay the defaults.
- [ ] A clean walk-forward that rediscovers levels bar by bar
- [ ] A win rate stated **with** its break-even threshold
- [ ] A structure-aware cost model, and the edge surviving it
- [ ] The regime gate shown to help on in-window data, not just out-of-window
- [x] Stop required and `stop_basis == PERCENT` (enforced in code)
- [x] Daily-loss breaker receives this engine's `uid=` (`check_daily_loss=True`)
- [ ] Paper-traded through a full expiry cycle with the record reconciling

---

## 6. Reproducing this

```bash
cd backend
python3 study/gamma_move/step1_candidates.py   # highest-OI strikes on liquid names
python3 study/gamma_move/step2_bars.py         # 15m OI candles (~27MB, gitignored)
python3 study/gamma_move/step5_levels.py       # daily spot
python3 study/gamma_move/step3_calibrate.py    # distributions + joint rates
python3 study/gamma_move/step4_forward.py      # forward excursion vs baseline
python3 study/gamma_move/step7_grid.py         # U1 sweep + U3 grid
python3 study/gamma_move/step10_reverify.py    # U1 through the SHIPPED find_levels
python3 study/gamma_move/step9_engine_replay.py
```

Needs a live Kite session. The raw pulls are gitignored and re-fetchable; the
results (`CALIBRATION.json`, `triggers.json`, `candidates.json`) are tracked.

---

## 7. Source alignment, 2026-09-09

Code now matches the W88GygpXZWI 46:22–1:00 walkthrough. That is **not** a new
calibration. The 26 Aug numbers stay because the study sample has no chain-wide
OI series and sits at DTE 34–103, so wall / spot-through / two-step descale
cannot be re-measured on it.

Shipped and left unchanged:

| field | value | why it does not move |
|---|---:|---|
| `level_proximity_pct` | 1.0 | only cell whose CI clears baseline |
| `min_oi_drop_pct` | 3.0 | p98.6 of evaluable bars |
| `volume_spike_mult` | 2.5 | p87 |
| `min_price_gain_pct` | 2.0 | p93 |
| `regime_period` / `regime_multiplier` | 10 / 2.0 | 3.0 inverts the gate |
| `min_option_premium` | 10.0 | tick quantum below this |
| `stop_percent` | 30.0 | 16% hit rate vs 41% at 20 |
| `max_premium_at_risk_inr` | 60_000 | one-lot floor on liquid names |
| `max_hold_days` | 2 | source hold |
| `descale_after_losses` / `descale_factor` | 3 / 0.5 | source two-step ladder |

Landed in code, not in the 26 Aug sample:

- chain-max OI wall and spot-through-or-at-strike
- two-step size ladder 1.0 → 0.5 → 0.25, persisted on `TradeRecord`
- weekday session hold from dates
- forming 15m bar dropped live; kept in replay
- live NSE spot, spread filter, closed daily bars
- buy limits floor, exits ceil, PnL vs fill

`descriptor().validated` remains `False`. `descriptor().source_aligned` is `True`.
Flipping the first without the mid-September in-window rerun would be a claim
the 26 Aug study already falsified.
