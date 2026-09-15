"""Targeted TrueData Historical Retention Probe Runner 1.1 for Snapback.

Probes TrueData API using exact provider-resolved symbols across a targeted 5-horizon
matrix for futures and options. Implements early stopping after two consecutive empty windows.
"""
import argparse
import asyncio
import json
import os
import sys
from typing import List, Tuple

from study.snapback_truedata_probe import TrueDataEntitlementProbe, resolve_truedata_credentials


async def main():
    parser = argparse.ArgumentParser(description="Run targeted TrueData retention matrix probe.")
    parser.add_argument("--user-id", default=None, help="Sterling user ID for credential lookup")
    args = parser.parse_args()

    print("=" * 80)
    print("TRUEDATA HISTORICAL F&O RETENTION & ENTITLEMENT PROBE (Runner 1.1)")
    print("=" * 80)

    user_id = args.user_id
    username, password = resolve_truedata_credentials(user_id)
    if username:
        print(f"Loaded credentials for user_id='{user_id or 'any'}': username={username}")
    else:
        print(f"WARNING: No active TrueData credentials found in DB or environment for user_id='{user_id or 'any'}'.")
        print("Results will be classified as CONFIG_MISSING rather than NOT_ENTITLED.\n")

    probe = TrueDataEntitlementProbe(username=username, password=password, user_id=user_id)

    # 1. Discover exact provider symbols via get_all_symbols()
    nifty_fo_symbols = await probe.resolve_provider_symbols("NIFTY", segment="fo", allexpiry=True)
    reliance_fo_symbols = await probe.resolve_provider_symbols("RELIANCE", segment="fo", allexpiry=True)

    print(f"Discovered {len(nifty_fo_symbols)} NIFTY provider symbols and {len(reliance_fo_symbols)} RELIANCE provider symbols.")

    # Provider symbol resolution logic (Micro-Fix 4: NEVER substitute guessed option symbols on discovery failure)
    current_nifty_fut = "SYMBOL_UNRESOLVED"
    recent_expired_nifty_fut = "SYMBOL_UNRESOLVED"
    current_nifty_opt = "SYMBOL_UNRESOLVED"
    expired_nifty_opt = "SYMBOL_UNRESOLVED"
    recent_reliance_opt = "SYMBOL_UNRESOLVED"
    expired_reliance_opt = "SYMBOL_UNRESOLVED"

    if nifty_fo_symbols:
        futs = [s for s in nifty_fo_symbols if "-I" in s or "FUT" in s]
        opts = [s for s in nifty_fo_symbols if "PE" in s or "CE" in s]
        if futs:
            current_nifty_fut = futs[0]
            recent_expired_nifty_fut = futs[1] if len(futs) > 1 else futs[0]
        if opts:
            current_nifty_opt = opts[0]
            expired_nifty_opt = opts[-1] if len(opts) > 1 else opts[0]

    if reliance_fo_symbols:
        rel_opts = [s for s in reliance_fo_symbols if "PE" in s or "CE" in s]
        if rel_opts:
            recent_reliance_opt = rel_opts[0]
            expired_reliance_opt = rel_opts[-1] if len(rel_opts) > 1 else rel_opts[0]

    # Target test matrix (6 specified targets)
    targets: List[Tuple[str, str]] = [
        (current_nifty_fut, "1. Current NIFTY Future"),
        (recent_expired_nifty_fut, "2. Recent Expired NIFTY Future"),
        (current_nifty_opt, "3. Current/Recent NIFTY Option"),
        (expired_nifty_opt, "4. Most Recent Expired NIFTY Option"),
        (recent_reliance_opt, "5. One Recent RELIANCE Option"),
        (expired_reliance_opt, "6. One Expired RELIANCE Option"),
    ]

    # Time depth horizons (5 specified windows)
    time_windows = [
        ("Today / Yesterday", "2026-09-15", "2026-09-16"),
        ("5 Trading Days Ago", "2026-09-08", "2026-09-09"),
        ("10 Trading Days Ago", "2026-09-01", "2026-09-02"),
        ("1 Month Ago", "2026-08-15", "2026-08-16"),
        ("3 Months Ago", "2026-06-15", "2026-06-16"),
    ]

    summary_rows = []

    for c_symbol, c_label in targets:
        print(f"\nProbing Target: {c_symbol} ({c_label})")
        print("-" * 75)

        if c_symbol == "SYMBOL_UNRESOLVED":
            print("  [ALL HORIZONS          ] SKIPPED: Provider symbol discovery failed (SYMBOL_UNRESOLVED).")
            summary_rows.append({
                "contract": c_symbol,
                "label": c_label,
                "horizon": "ALL",
                "entitlement_status": "SYMBOL_UNRESOLVED",
                "ticks_available": False,
                "bars_available": False,
                "error": "Symbol resolution failed; provider symbols unavailable",
            })
            continue

        consecutive_empty = 0

        for w_label, start_d, end_d in time_windows:
            if consecutive_empty >= 2:
                print(f"  [{w_label:22s}] SKIPPED (2 consecutive older windows empty)")
                continue

            try:
                res = await probe.probe_symbol(c_symbol, start_d, end_d)
                d = res.as_dict()
                status = d.get("entitlement_status")

                row = {
                    "contract": c_symbol,
                    "label": c_label,
                    "horizon": w_label,
                    "requested": f"{start_d} to {end_d}",
                    "tick_status": d.get("tick_status"),
                    "bar_status": d.get("bar_status"),
                    "entitlement_status": status,
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

                # Micro-Fix 5: 401 / NOT_ENTITLED / CONFIG_MISSING aborts probe immediately and NEVER counts toward empty retention windows
                if status in ("NOT_ENTITLED", "CONFIG_MISSING", "ERROR") and ("401" in str(row["error"]) or "credentials unavailable" in str(row["error"]).lower()):
                    print(f"  [{w_label:22s}] Status={status:14s} | PROBE ABORTED (Auth/Credential failure - Retention depth UNKNOWN) | Err={str(row['error'])[:40]}")
                    break

                is_empty = not d.get("ticks_available") and not d.get("bars_available")
                if is_empty:
                    consecutive_empty += 1
                else:
                    consecutive_empty = 0

                print(f"  [{w_label:22s}] Status={row['entitlement_status']:14s} | Ticks={row['tick_count']:6d} ({row['bid_ask_coverage_pct']:5.1f}% bid/ask) | Bars={row['bar_count']:5d} | Err={str(row['error'])[:40]}")
            except Exception as e:
                print(f"  [{w_label:22s}] ERROR: {e}")
                break

    output_dir = "research/snapback_reality_v1"
    os.makedirs(output_dir, exist_ok=True)
    out_file = os.path.join(output_dir, "truedata_retention_probe_results.json")
    with open(out_file, "w") as f:
        json.dump({"probed_at": "2026-09-16", "user_id": user_id, "results": summary_rows}, f, indent=2)

    print("\n" + "=" * 80)
    print(f"PROBE COMPLETED. Saved full evidence report to: {out_file}")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(main())


