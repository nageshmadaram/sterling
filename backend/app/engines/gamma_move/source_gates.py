"""Source-rule snapshot rates from the real 2026-08-26 Kite pull.

Not calibrated edge. Filter occupancy on candidates.json.
"""
SOURCE_GATES = {
    "require_chain_max_oi": "208/598 sampled contracts were the max-OI of their (name, leg)",
    "require_spot_through_strike": "369/598 had spot through/at the strike inside the 1% band",
    "both": "119/598 passed wall AND through on that snapshot",
}

REQUIRE_CHAIN_MAX_OI = True
REQUIRE_SPOT_THROUGH_STRIKE = True
