"""Measure the source's wall + spot-through gates on the REAL 2026-08-26 snapshot.

No synthetic bars. Reads study/gamma_move/out/candidates.json — the Kite pull
that produced CALIBRATION.json. Reports filter rates, not forward edge: that
file is one afternoon's chain, DTE ~34, outside the strategy window.
"""
from __future__ import annotations
import json
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).parent / "out"
data = json.loads((OUT / "candidates.json").read_text())
cands = data["candidates"]


def through(spot: float, strike: float, opt: str, band: float = 1.0) -> bool:
    b = band / 100.0
    if opt == "CE":
        return float(spot) >= float(strike) * (1.0 - b)
    return float(spot) <= float(strike) * (1.0 + b)


mx: dict[tuple[str, str], int] = defaultdict(int)
for c in cands:
    key = (c["underlying"], c["option_type"])
    mx[key] = max(mx[key], int(c["oi"]))

n = len(cands)
n_wall = sum(1 for c in cands if int(c["oi"]) >= mx[(c["underlying"], c["option_type"])])
n_thr = sum(1 for c in cands if through(c["spot"], c["strike"], c["option_type"]))
n_both = sum(
    1 for c in cands
    if int(c["oi"]) >= mx[(c["underlying"], c["option_type"])]
    and through(c["spot"], c["strike"], c["option_type"])
)
report = {
    "source": "backend/study/gamma_move/out/candidates.json",
    "pulled": "2026-08-26 Kite Connect",
    "expiry": data.get("expiry"),
    "contracts": n,
    "underlyings": len({c["underlying"] for c in cands}),
    "wall_of_sampled_leg": {"n": n_wall, "pct": round(100.0 * n_wall / n, 1)},
    "spot_through_or_at_1pct": {"n": n_thr, "pct": round(100.0 * n_thr / n, 1)},
    "both": {"n": n_both, "pct": round(100.0 * n_both / n, 1)},
    "note": (
        "Filter rates on one snapshot, not a forward MFE. The sample keeps the "
        "top-3 OI strikes per (name, leg) inside ±8% of spot, so 'wall' here is "
        "the max of that sample, not the unseen rest of the chain."
    ),
}
(OUT / "SOURCE_GATES.json").write_text(json.dumps(report, indent=1) + "\n")
print(json.dumps(report, indent=2))
