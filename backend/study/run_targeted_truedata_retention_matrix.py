"""Targeted TrueData Historical Retention Probe Runner for Snapback.

Probes TrueData API across a progressive time matrix (recent, 1m, 3m, 6m, 1y, 2y, 3y)
for Index Futures, Index Options, Stock Futures, and Stock Options to determine
the exact depth of executable bid/ask tick data and bar data available.
"""
import asyncio
import json
import os
import sys

from study.snapback_truedata_probe import TrueDataEntitlementProbe


async def main():
    print("=" * 80)
    print("TRUEDATA HISTORICAL F&O RETENTION & ENTITLEMENT PROBE")
    print("=" * 80)

    probe = TrueDataEntitlementProbe()

    # Target test contracts across derivative types
    contracts = [
        ("NIFTY_FUT", "NIFTY Index Futures"),
        ("NIFTY26SEP24000PE", "NIFTY Index Option (Recent Expired)"),
        ("NIFTY24JUL22500PE", "NIFTY Index Option (2024 Expired)"),
        ("RELIANCE_FUT", "Stock Futures (RELIANCE)"),
        ("RELIANCE24JUL3000CE", "Stock Option (RELIANCE 2024 Expired)"),
    ]

    # Time depth horizons
    time_windows = [
        ("Recent (1 month ago)", "2026-08-01", "2026-08-15"),
        ("3 Months ago", "2026-05-01", "2026-05-15"),
        ("6 Months ago", "2026-02-01", "2026-02-15"),
        ("1 Year ago", "2025-08-01", "2025-08-15"),
        ("2 Years ago", "2024-08-01", "2024-08-15"),
        ("3 Years ago", "2023-08-01", "2023-08-15"),
    ]

    summary_rows = []

    for c_symbol, c_label in contracts:
        print(f"\nProbing Target: {c_symbol} ({c_label})")
        print("-" * 75)
        for w_label, start_d, end_d in time_windows:
            try:
                res = await probe.probe_symbol(c_symbol, start_d, end_d)
                d = res.as_dict()
                row = {
                    "contract": c_symbol,
                    "label": c_label,
                    "horizon": w_label,
                    "requested": f"{start_d} to {end_d}",
                    "tick_status": d.get("tick_status"),
                    "bar_status": d.get("bar_status"),
                    "entitlement_status": d.get("entitlement_status"),
                    "ticks_available": d.get("ticks_available"),
                    "bars_available": d.get("bars_available"),
                    "tick_count": d.get("tick_count"),
                    "bid_ask_coverage_pct": d.get("bid_ask_coverage_pct"),
                    "bar_count": d.get("bar_count"),
                    "first_ts": d.get("first_tick_timestamp"),
                    "last_ts": d.get("last_tick_timestamp"),
                    "error": d.get("provider_tick_error") or d.get("provider_bar_error") or d.get("error_message"),
                }
                summary_rows.append(row)
                print(f"  [{w_label:22s}] Status={row['entitlement_status']:12s} | Ticks={row['tick_count']:6d} ({row['bid_ask_coverage_pct']:5.1f}% bid/ask) | Bars={row['bar_count']:5d} | Err={str(row['error'])[:40]}")
            except Exception as e:
                print(f"  [{w_label:22s}] ERROR: {e}")

    output_dir = "research/snapback_reality_v1"
    os.makedirs(output_dir, exist_ok=True)
    out_file = os.path.join(output_dir, "truedata_retention_probe_results.json")
    with open(out_file, "w") as f:
        json.dump({"probed_at": "2026-09-16", "results": summary_rows}, f, indent=2)

    print("\n" + "=" * 80)
    print(f"PROBE COMPLETED. Saved full evidence report to: {out_file}")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(main())
