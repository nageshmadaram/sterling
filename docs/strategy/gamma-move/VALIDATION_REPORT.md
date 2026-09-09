# Gamma Move — validation report

**Run** 2026-08-26 · **Engine** `gamma_move` A310.2 · **Verdict** NOT VALIDATED
**Source-aligned** 2026-09-09 · **Scripts** `backend/study/gamma_move/` · **Result file** `study/gamma_move/out/CALIBRATION.json`

The 26 Aug finding stands. Matching the podcast is not a new calibration.
See §7 for what landed in code on 2026-09-09 and why every measured default is unchanged.

---

## 0. The finding, first

> **The entry trigger alone has no measurable edge. The level filter does.**

`validated` stays **false** until an in-window (DTE ≤ 14) rerun exists.

---

## 7. Source alignment, 2026-09-09

Code now matches W88GygpXZWI 46:22–1:00. Thresholds stay at the 26 Aug values
because that sample has no chain-wide OI series and sits at DTE 34–103.

Unchanged: `level_proximity_pct=1.0`, `min_oi_drop_pct=3.0`, `volume_spike_mult=2.5`,
`min_price_gain_pct=2.0`, SuperTrend 10/2.0, `min_option_premium=10`, `stop_percent=30`,
`max_premium_at_risk_inr=60000`, `max_hold_days=2`, descale 3 / 0.5.

`descriptor().validated` remains `False`. `descriptor().source_aligned` is `True`.
