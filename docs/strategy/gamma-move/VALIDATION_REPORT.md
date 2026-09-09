# Gamma Move — validation report

**Run** 2026-08-26 · **Engine** `gamma_move` A310.2 · **Verdict** NOT VALIDATED
**Source-aligned** 2026-09-09 · **Scripts** `backend/study/gamma_move/` · **Result file** `study/gamma_move/out/CALIBRATION.json`

---

## 0. The finding, first

> **The entry trigger alone has no measurable edge. The level filter does.**

`validated` stays **false**. Matching the podcast is not the same as beating the
baseline on in-window data. Full 26 Aug tables are on `main` at the same path;
this branch keeps the verdict and the 2026-09-09 source-alignment note.

Measured defaults, unchanged: level 1.0, OI drop 3.0, vol 2.5×, px 2.0,
SuperTrend 10/2.0, premium ≥10, stop 30%, cap ₹60k, hold 2 sessions, descale 3/0.5.

`descriptor().validated` remains False. `descriptor().source_aligned` is True on main (#169).
