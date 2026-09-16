#!/usr/bin/env python3
"""Same-day shadow replay of the frozen Snapback pipeline — a dress rehearsal.

This is NOT prospective evidence. Today's data is already known, so nothing produced
here can count toward the authoritative gate. The replay writes to its own database
and stamps every row `source = SAME_DAY_SHADOW_REPLAY`, `authoritative = 0`.

What it exercises, end to end, with the frozen rules:

    signal scan -> contract selection -> executable bid/ask fill logic ->
    futures hedge -> intraday stop / EXIT_PENDING -> EOD MTM -> costs ->
    outcome -> report -> authoritative gate adapter

The acceptance question is not whether the simulated P&L is positive. It is whether
Sterling can reconstruct the session using only information available at each decision
time, with no look-ahead and no fabricated prices.

Usage:
    STERLING_OBSERVATIONS_DB_PATH=.../replay_2026-09-16.db python3 study/snapback_shadow_replay.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_IST = timezone(timedelta(hours=5, minutes=30))

REPLAY_SOURCE = "SAME_DAY_SHADOW_REPLAY"

TABLES = (
    "opportunities",
    "contract_candidates",
    "option_quotes",
    "futures_quotes",
    "decisions",
    "paper_fills",
    "hedge_rebalances",
    "daily_mtm",
    "margin_snapshots",
    "costs",
    "outcomes",
    "paper_positions",
)


def _guard_not_the_evidence_db(db_path: str) -> None:
    """Refuse to run against the clean prospective evidence database."""
    if "prospective_freeze" in str(db_path):
        raise SystemExit(
            f"REFUSING to replay into the authoritative evidence database: {db_path}"
        )


def _stamp_replay_rows(db_path: str) -> Dict[str, int]:
    """Mark every row as shadow replay and non-authoritative."""
    counts: Dict[str, int] = {}
    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        with conn:
            for table in TABLES:
                try:
                    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                except Exception:
                    continue
                if not cols:
                    continue
                if "authoritative" not in cols:
                    conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN authoritative INTEGER NOT NULL DEFAULT 0"
                    )
                if "source" in cols:
                    conn.execute(f"UPDATE {table} SET source = ?", (REPLAY_SOURCE,))
                conn.execute(f"UPDATE {table} SET authoritative = 0")
                counts[table] = int(
                    conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
    finally:
        conn.close()
    return counts


def _backdate_signals_to_previous_session(db_path: str, today: date) -> int:
    """Move recorded signals to the previous trading session.

    The entry phase deliberately refuses to fill on Day T; it fills on Day T+1. To
    exercise the T+1 mechanics inside a single day, the replay presents today's
    signals as if they had been recorded at the previous session's close. This is a
    replay device and is reported as such — it does not change any decision rule.
    """
    from app.services.navigator.calendar import is_trading_day

    prev = today - timedelta(days=1)
    for _ in range(10):
        try:
            if is_trading_day(prev):
                break
        except Exception:
            break
        prev = prev - timedelta(days=1)

    ts = datetime.combine(prev, datetime.min.time()).replace(hour=15, minute=30, tzinfo=_IST)
    iso = ts.isoformat()

    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        with conn:
            # scan_once runs the collector cycle itself, which may already have
            # aged a same-day signal out to INCONCLUSIVE ("missed T+1 window").
            # For the rehearsal those rows are re-presented as previous-session
            # signals so the entry mechanics can run. Decision rules are untouched.
            cur = conn.execute(
                "UPDATE opportunities SET signal_timestamp = ?, observed_at = ?, "
                "status = 'PENDING_ENTRY', processing_token = '', "
                "processing_started_at_ms = 0 "
                "WHERE status IN ('PENDING_ENTRY', 'INCONCLUSIVE')",
                (iso, iso),
            )
            # scan_once already recorded a "missed T+1 window" decision for those
            # rows, and decisions are unique per opportunity. Clearing them lets the
            # rehearsal record the decision the entry phase would really have made.
            conn.execute("DELETE FROM decisions")
            return int(cur.rowcount or 0)
    finally:
        conn.close()


class _FixedClock:
    """Presents a fixed IST wall clock to the frozen runtime.

    The entry phase only fills inside 09:15-09:45 IST, so after the close the
    rehearsal cannot reach the fill logic at all without presenting that window.
    Only the clock is moved: every price, book and timestamp still comes from the
    broker exactly as it is now, so a quote that is stale stays stale and must be
    refused by the unchanged quote-quality gate.
    """

    def __init__(self, target: datetime):
        self.target = target

    def install(self, module) -> Any:
        target = self.target
        real = module.datetime

        class Fixed(real):
            @classmethod
            def now(cls, tz=None):
                return target.astimezone(tz) if tz is not None else target

        previous = module.datetime
        module.datetime = Fixed
        return previous


async def run_replay(uid: str = "default") -> Dict[str, Any]:
    from app.services import snapback as sb
    from app.services.exchanges.kite import accounts
    from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse
    from app.services.snapback_prospective_collector import SnapbackProspectiveCollector

    db_path = os.environ.get("STERLING_OBSERVATIONS_DB_PATH", "snapback_observations.db")
    _guard_not_the_evidence_db(db_path)

    today = datetime.now(_IST).date()
    report: Dict[str, Any] = {
        "replay_kind": REPLAY_SOURCE,
        "authoritative": False,
        "session_date": today.isoformat(),
        "database": db_path,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "stages": {},
        "notes": [],
    }

    # The config store must be initialized explicitly in a standalone process; the
    # API server does this during startup. Without it every config read falls back to
    # "defaults OFF" — the same failure mode that silently disabled the live engine.
    from app.services import db as db_mod

    if not db_mod.is_available():
        db_mod.init()
    if not db_mod.is_available():
        report["classification"] = "REPLAY_FAILED"
        report["notes"].append("Config store unavailable; refusing to replay with defaults.")
        return report

    cfg = sb.get_config(uid)
    report["config"] = {
        "enabled": bool(getattr(cfg, "enabled", False)),
        "lookback_days": getattr(cfg, "lookback_days", None),
        "min_stretch_atr": getattr(cfg, "min_stretch_atr", None),
        "max_rv_pct": getattr(cfg, "max_rv_pct", None),
        "market_filter": getattr(cfg, "market_filter", None),
        "market_ema": getattr(cfg, "market_ema", None),
        "target_delta": getattr(cfg, "target_delta", None),
        "min_dte": getattr(cfg, "min_dte", None),
        "max_dte": getattr(cfg, "max_dte", None),
        "hedge_mode": getattr(cfg, "hedge_mode", None),
    }
    if not cfg.enabled:
        report["classification"] = "REPLAY_FAILED"
        report["notes"].append("Snapback config is disabled; the frozen scan cannot run.")
        return report

    # The API server hydrates accounts during startup; a standalone run must do the
    # same after db.init(), or every account looks disconnected.
    try:
        accounts.bootstrap()
    except Exception as exc:
        report["notes"].append(f"kite accounts bootstrap: {exc}")

    acct = accounts.get_active(uid)
    if not acct:
        all_accts = accounts.all_accounts()
        acct = all_accts[0] if all_accts else None
    if not acct or not acct.connected:
        report["classification"] = "REPLAY_INCOMPLETE"
        report["notes"].append("Kite account not connected; no executable quotes available.")
        return report

    client = await accounts.acquire_client(acct)

    # 1. Frozen signal scan over today's daily tape.
    if os.environ.get("STERLING_REPLAY_SKIP_SCAN") == "1":
        snap = sb.snapshot(uid)
        report["notes"].append("Scan skipped (STERLING_REPLAY_SKIP_SCAN=1); reusing recorded signals.")
    else:
        snap = await sb.scan_once(uid)
    rows = snap.get("rows") or []
    report["stages"]["scan"] = {
        "universe_scanned": snap.get("scanned"),
        "rows": len(rows),
        "armed": len([r for r in rows if r.get("state") == "armed"]),
        "failures": (snap.get("failures") or [])[:20],
        "failure_count": len(snap.get("failures") or []),
        "last_error": snap.get("last_error"),
    }
    report["signals"] = [
        {
            "symbol": r.get("symbol"),
            "signal_time": r.get("signal_ts") or r.get("bar_ts") or r.get("ts"),
            "side": r.get("side"),
            "direction": r.get("direction"),
            "option_type": r.get("option_type"),
            "stretch_atr": r.get("stretch"),
            "state": r.get("state"),
            "reason": r.get("reason"),
            "entry": r.get("entry"),
            "mean_target": r.get("mean_target"),
            "realized_vol": r.get("realized_vol") or r.get("rv"),
            "assumed_iv": r.get("assumed_iv"),
            "contract": r.get("tradingsymbol") or r.get("contract"),
            "expiry": r.get("expiry"),
            "dte": r.get("dte"),
            "strike": r.get("strike"),
            "delta": r.get("delta"),
            "oi": r.get("oi"),
            "lot_size": r.get("lot_size"),
            "bid": r.get("bid"),
            "ask": r.get("ask"),
            "spread_pct": r.get("spread_pct"),
            "execution_eligible": r.get("execution_eligible"),
        }
        for r in rows
    ]

    warehouse = SnapbackObservationWarehouse()
    collector = SnapbackProspectiveCollector(warehouse=warehouse)

    opportunities = warehouse.get_records_by_table("opportunities")
    report["stages"]["signals_recorded"] = len(opportunities)

    # 2. T+1 entry mechanics against today's executable quotes.
    backdated = _backdate_signals_to_previous_session(db_path, today)
    report["stages"]["backdated_for_t1"] = backdated
    report["notes"].append(
        "Signals were presented as previous-session closes so the frozen T+1 entry "
        "phase would run inside one day. No decision rule was modified."
    )

    fake_clock = os.environ.get("STERLING_REPLAY_ENTRY_CLOCK")
    restore = None
    restore_time = None
    if fake_clock:
        hh, mm = (int(x) for x in fake_clock.split(":"))
        target = datetime.now(_IST).replace(hour=hh, minute=mm, second=0, microsecond=0)
        restore = _FixedClock(target).install(sb)

        # The collector derives its execution time from the epoch clock the entry
        # phase passes down, so that must present the same instant.
        import time as _time_mod

        class _FixedTime:
            def __getattr__(self, name):
                return getattr(_time_mod, name)

            @staticmethod
            def time():
                return target.timestamp()

        restore_time = sb.time
        sb.time = _FixedTime()
        report["notes"].append(
            f"Entry phase was run against a fixed clock of {target.isoformat()} so the "
            "09:15-09:45 IST entry window could be reached after the close. Prices, "
            "books and provider timestamps are unmodified."
        )

    try:
        entries = await sb.process_prospective_pending_entries(client, cfg)
    finally:
        if restore is not None:
            sb.datetime = restore
            sb.time = restore_time
    report["stages"]["entries_processed"] = entries

    # 3. Intraday risk monitor (premium stop / runner trail / EXIT_PENDING).
    risk = await sb.process_prospective_intraday_risk(client, cfg)
    report["stages"]["intraday_risk_processed"] = risk

    # 4. End-of-day MTM, hedge rebalance and horizon handling.
    eod = await sb.process_prospective_daily_mtm_and_exits(client, cfg)
    report["stages"]["eod_processed"] = eod

    report["record_counts"] = _stamp_replay_rows(db_path)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    return report


def _classify(report: Dict[str, Any], records: Dict[str, List[Dict[str, Any]]]) -> str:
    """REPLAY_VALID / REPLAY_INCOMPLETE / REPLAY_FAILED.

    VALID means the pipeline ran end to end and every stage either produced evidence
    or refused with a recorded reason. INCOMPLETE means a stage could not be exercised
    (typically no signal, or no executable quote after the close). FAILED means a
    stage raised or produced evidence it should not have.
    """
    if report.get("classification") in {"REPLAY_FAILED", "REPLAY_INCOMPLETE"}:
        return report["classification"]

    if report["stages"].get("scan", {}).get("last_error"):
        return "REPLAY_FAILED"

    # Any fill without a recorded executable quote would be fabricated evidence.
    fills = records.get("paper_fills") or []
    quotes = records.get("option_quotes") or []
    if fills and not quotes:
        return "REPLAY_FAILED"

    if not records.get("opportunities"):
        return "REPLAY_INCOMPLETE"

    if not fills:
        return "REPLAY_INCOMPLETE"

    return "REPLAY_VALID"


def build_replay_report(report: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse
    from study.snapback_forward_gate import evaluate_forward_gate
    from study.snapback_forward_report import build_forward_summary, load_forward_records

    warehouse = SnapbackObservationWarehouse()
    records = load_forward_records(warehouse)

    summary = build_forward_summary(
        records=records,
        runtime_sha=os.environ.get("STERLING_RUNTIME_SHA", "UNKNOWN"),
        strategy_manifest="SAME_DAY_SHADOW_REPLAY",
    )

    gate = evaluate_forward_gate(records=records)
    gate["authoritative"] = False
    gate["note"] = (
        "Shadow replay of a known session. This verdict measures pipeline behaviour "
        "only and can never promote Snapback."
    )

    report["evidence_summary"] = summary
    report["authoritative_gate_adapter"] = gate
    report["contract_candidates"] = records.get("contract_candidates") or []
    report["fills"] = records.get("paper_fills") or []
    report["hedges"] = records.get("hedge_rebalances") or []
    report["positions"] = records.get("paper_positions") or []
    report["decisions"] = records.get("decisions") or []
    report["outcomes"] = records.get("outcomes") or []
    report["costs"] = records.get("costs") or []
    report["classification"] = _classify(report, records)
    return report


def main() -> int:
    report = asyncio.run(run_replay())
    report = build_replay_report(report)

    out_dir = Path(
        os.environ.get(
            "STERLING_REPLAY_REPORT_ROOT",
            "/home/nageshmadaram/Sterling/artifacts/snapback_shadow_replay",
        )
    ) / report["session_date"]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "shadow_replay.json"
    out_file.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print(json.dumps({
        "classification": report.get("classification"),
        "stages": report.get("stages"),
        "signals": len(report.get("signals") or []),
        "report": str(out_file),
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
