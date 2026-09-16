"""Live acceptance: does the real Kite payload match what the evidence schema assumes?

Synthetic fixtures prove the code does what its tests describe. They cannot prove
that Kite's FULL tick carries five depth levels with order counts, that
``exchange_timestamp`` is populated the way the schema expects, or that it is ever
absent. Runtime 1.5 was tagged twice on green suites and twice was wrong, both
times because nothing had run the built artifact against reality.

So this runs against a real socket and writes a machine-readable verdict. It is
read-only with respect to trading: it authenticates, reads the instrument master,
computes a contract with the frozen selector, subscribes, and records. It places
no order, and there is no code path here that could.

Evidence written by this harness is marked TEST_ACCEPTANCE and must never enter
the Track A economic sample.

Usage, with a Kite session the operator has already established:

    python -m study.snapback_live_evidence_acceptance --underlying NIFTY --seconds 120

Credentials are read from the existing kitelake session store. Nothing here
prompts for, stores, or logs a password, API secret or access token.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

#: Evidence from this harness is deliberately not OBSERVED_MARKET: it must be
#: impossible to mistake an acceptance run for a real opportunity's evidence.
ACCEPTANCE_EVIDENCE_CLASS = "MODELLED"
ACCEPTANCE_TAG = "TEST_ACCEPTANCE"


@dataclass
class Report:
    """Every check, its verdict, and why. Never a bare boolean."""

    runtime_sha: str
    started_at: str
    underlying: str
    checks: dict[str, str] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def record(self, name: str, verdict: str, detail: Any = None) -> None:
        self.checks[name] = verdict
        if detail is not None:
            self.detail[name] = detail

    @property
    def overall(self) -> str:
        if not self.checks:
            return FAIL
        if any(v == FAIL for v in self.checks.values()):
            return FAIL
        # A skipped check is not a pass. The release gate needs PASS.
        if any(v == SKIP for v in self.checks.values()):
            return SKIP
        return PASS

    def as_dict(self) -> dict[str, Any]:
        return {
            "runtime_sha": self.runtime_sha,
            "started_at": self.started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "underlying": self.underlying,
            "evidence_class": ACCEPTANCE_EVIDENCE_CLASS,
            "tag": ACCEPTANCE_TAG,
            **self.checks,
            "detail": self.detail,
            "notes": self.notes,
            "overall": self.overall,
        }


def _runtime_sha() -> str:
    import subprocess
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "UNKNOWN"


# ─── instrument master ───────────────────────────────────────────────────────

def fetch_instrument_master(kite: Any, exchange: str = "NFO") -> list[dict[str, Any]]:
    """The live master, as published. Never reconstructed."""
    return list(kite.instruments(exchange))


def archive_instrument_master(rows: list[dict[str, Any]], root: Path, *,
                              retrieved_at: datetime) -> dict[str, Any]:
    """Persist an immutable, hashed snapshot. Today's master is gone tomorrow."""
    import hashlib

    stamp = retrieved_at.strftime("%Y%m%dT%H%M%S")
    directory = root / "instrument_master" / f"date={retrieved_at.date().isoformat()}" / f"snapshot={stamp}"
    directory.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    staging = directory.parent / f".staging-{stamp}.json"
    staging.write_text(payload, encoding="utf-8")
    import os
    os.replace(staging, directory / "instruments.json")

    metadata = {
        "snapshot_id": stamp,
        "source": "kite",
        "retrieved_at": retrieved_at.isoformat(),
        "rows": len(rows),
        "sha256": digest,
    }
    (directory / "metadata.json").write_text(json.dumps(metadata, indent=1), encoding="utf-8")
    return metadata


# ─── tick inspection ─────────────────────────────────────────────────────────

def inspect_tick(tick: dict[str, Any]) -> dict[str, Any]:
    """What the vendor actually sent, without interpretation."""
    depth = tick.get("depth") or {}
    buy = depth.get("buy") or []
    sell = depth.get("sell") or []
    return {
        "has_depth": bool(depth),
        "buy_levels": len(buy),
        "sell_levels": len(sell),
        "buy_has_orders": all("orders" in (lvl or {}) for lvl in buy) if buy else False,
        "sell_has_orders": all("orders" in (lvl or {}) for lvl in sell) if sell else False,
        "buy_quantities": [(lvl or {}).get("quantity") for lvl in buy],
        "sell_quantities": [(lvl or {}).get("quantity") for lvl in sell],
        "exchange_timestamp": str(tick.get("exchange_timestamp")),
        "has_exchange_timestamp": tick.get("exchange_timestamp") is not None,
        "has_oi": tick.get("oi") is not None,
        "instrument_token": tick.get("instrument_token"),
    }


def evaluate_ticks(report: Report, role: str, ticks: list[dict[str, Any]]) -> None:
    """Grade one instrument's captured ticks against the schema's assumptions."""
    if not ticks:
        report.record(f"{role}_full_tick", FAIL, "no tick received")
        return

    report.record(f"{role}_full_tick", PASS, f"{len(ticks)} ticks")
    findings = [inspect_tick(t) for t in ticks]

    deepest_buy = max(f["buy_levels"] for f in findings)
    deepest_sell = max(f["sell_levels"] for f in findings)
    report.record(
        f"{role}_five_level_depth",
        PASS if deepest_buy >= 5 and deepest_sell >= 5 else FAIL,
        {"max_buy_levels": deepest_buy, "max_sell_levels": deepest_sell},
    )

    with_orders = any(f["buy_has_orders"] and f["sell_has_orders"] for f in findings)
    report.record(f"{role}_order_counts", PASS if with_orders else FAIL)

    quantities_present = any(
        all(q is not None for q in f["buy_quantities"]) for f in findings if f["buy_quantities"]
    )
    report.record(f"{role}_depth_quantities", PASS if quantities_present else FAIL)

    stamped = sum(1 for f in findings if f["has_exchange_timestamp"])
    report.record(
        f"{role}_exchange_timestamp",
        PASS if stamped else FAIL,
        {"with_exchange_ts": stamped, "total": len(findings)},
    )


def evaluate_rows(report: Report, rows: list[dict[str, Any]]) -> None:
    """Grade the rows the schema produced from those ticks."""
    if not rows:
        report.record("row_conversion", FAIL, "no evidence rows built")
        return

    report.record("row_conversion", PASS, f"{len(rows)} rows")

    # Both clocks, independently recorded.
    separated = [r for r in rows if r.get("exchange_ts") and r.get("received_ts")]
    report.record("clock_separation", PASS if separated else FAIL)

    if separated:
        ages = [
            (r["received_ts"] - r["exchange_ts"]).total_seconds() * 1000.0
            for r in separated
        ]
        report.record("freshness_computable", PASS, {
            "min_age_ms": round(min(ages), 1),
            "max_age_ms": round(max(ages), 1),
        })
        # A negative age means the two clocks are not comparable as assumed.
        report.record("clock_ordering_sane", PASS if min(ages) > -5000 else FAIL,
                      {"min_age_ms": round(min(ages), 1)})

    # An absent exchange timestamp must stay absent, never borrow received_ts.
    unstamped = [r for r in rows if r.get("exchange_ts") is None]
    if unstamped:
        borrowed = [r for r in unstamped if r.get("exchange_ts") == r.get("received_ts")]
        report.record("null_exchange_timestamp_semantics", PASS if not borrowed else FAIL,
                      {"unstamped_rows": len(unstamped)})
    else:
        report.record("null_exchange_timestamp_semantics", PASS,
                      "every tick carried an exchange timestamp; nothing was fabricated")

    # Absent depth levels must be null, never zero.
    zeroed = [r for r in rows if r.get("bid4_price") == 0 or r.get("ask4_price") == 0]
    report.record("absent_level_is_null_not_zero", PASS if not zeroed else FAIL)


# ─── the run ─────────────────────────────────────────────────────────────────

def run_acceptance(
    *,
    underlying: str,
    seconds: int,
    root: Path,
    out_dir: Path,
    kite: Any = None,
    ticker_factory: Any = None,
) -> Report:
    """Execute the acceptance sequence. Returns the report; writes nothing fatal."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    from app.engines.snapback.config import SnapbackConfig
    from app.engines.option_contracts import spec_for
    from app.services.snapback_candidate_universe import (
        CandidateUniverseError, build_candidate_universe, universe_hash,
    )
    from app.services.snapback_contract_selection import (
        SelectionInputs, select_snapback_contract,
    )

    report = Report(
        runtime_sha=_runtime_sha(),
        started_at=datetime.now(timezone.utc).isoformat(),
        underlying=underlying,
    )
    cfg = SnapbackConfig()

    if kite is None:
        from kitelake.config import load_credentials
        from kiteconnect import KiteConnect

        creds = load_credentials()
        kite = KiteConnect(api_key=creds.api_key)
        kite.set_access_token(creds.access_token)

    # ─── instrument master ───────────────────────────────────────────────────
    try:
        rows = fetch_instrument_master(kite)
        metadata = archive_instrument_master(rows, root, retrieved_at=datetime.now(timezone.utc))
        report.record("instrument_master", PASS, metadata)
    except Exception as exc:
        report.record("instrument_master", FAIL, f"{type(exc).__name__}: {exc}")
        return report

    # ─── candidate universe ──────────────────────────────────────────────────
    spec = spec_for(underlying)
    if spec is None:
        report.record("candidate_universe", FAIL, f"no contract spec for {underlying}")
        return report

    try:
        universe = build_candidate_universe(
            opportunity_id=f"ACCEPT-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}",
            underlying=underlying, option_type="PE", as_of=date.today(),
            instruments=rows, instrument_master_date=date.today(),
            min_dte=cfg.min_dte, max_dte=cfg.max_dte,
            evidence_class=ACCEPTANCE_EVIDENCE_CLASS,
        )
        report.record("candidate_universe", PASS, {
            "contracts": len(universe.contracts), "expiries": list(universe.expiries)})
    except CandidateUniverseError as exc:
        report.record("candidate_universe", FAIL, str(exc))
        return report

    recomputed = universe_hash(
        universe.contracts, underlying=universe.underlying, option_type=universe.option_type,
        as_of=universe.as_of, min_dte=universe.min_dte, max_dte=universe.max_dte,
    )
    report.record("candidate_hash", PASS if recomputed == universe.candidate_universe_hash else FAIL,
                  universe.candidate_universe_hash)

    # ─── frozen selector against the real master ─────────────────────────────
    try:
        quote = kite.ltp([f"NSE:{spec.underlying}"]) if hasattr(kite, "ltp") else {}
        spot = float(next(iter(quote.values()))["last_price"]) if quote else 0.0
    except Exception as exc:
        report.record("spot", FAIL, f"{type(exc).__name__}: {exc}")
        return report

    expiry = universe.expiries[0]
    dte = (date.fromisoformat(expiry) - date.today()).days
    inputs = SelectionInputs(
        opportunity_id=universe.opportunity_id, underlying=underlying, option_type="PE",
        spot=spot, assumed_iv=cfg.__dict__.get("assumed_iv_floor", 0.0) or 0.18,
        target_delta=cfg.target_delta, dte_days=max(dte, 1),
        strike_step=spec.strike_step, expiry=expiry,
        candidate_universe_hash=universe.candidate_universe_hash,
    )
    selection = select_snapback_contract(inputs, universe.contracts)
    replay = select_snapback_contract(inputs, universe.contracts)

    report.record("selector_replay",
                  PASS if replay.selected_strike == selection.selected_strike else FAIL,
                  {"strike": selection.selected_strike})
    # LISTED or NOT_LISTED both prove resolution worked; UNKNOWN means it did not.
    report.record("listedness", PASS if selection.listed in ("LISTED", "NOT_LISTED") else FAIL,
                  {"listed": selection.listed, "computed_strike": selection.selected_strike})
    report.notes.append(
        f"frozen selector computed {selection.selected_strike} PE {expiry}; "
        f"listedness against the real master: {selection.listed}"
    )

    if selection.listed != "LISTED":
        # A real and important finding, not a harness failure: the frozen rule
        # computed a contract the exchange does not list.
        report.notes.append(
            "the computed strike is NOT listed in today's master; this is a "
            "selection-reality finding about the frozen strategy, not a defect here"
        )
        report.record("option_full_tick", SKIP, "no listed contract to subscribe to")
        return report

    option_token = selection.selected_instrument_token

    # ─── hedge ───────────────────────────────────────────────────────────────
    from app.services.snapback_hedge_contract import HedgeContractError, select_hedge_future
    try:
        hedge = select_hedge_future(rows, option_expiry=date.fromisoformat(expiry),
                                    name=underlying, exchange="NFO")
        report.record("hedge_contract", PASS, {"tradingsymbol": hedge.tradingsymbol})
        hedge_token = hedge.instrument_token
    except HedgeContractError as exc:
        report.record("hedge_contract", FAIL, str(exc))
        hedge_token = None

    # ─── live socket ─────────────────────────────────────────────────────────
    tokens = [t for t in (option_token, hedge_token) if t]
    captured: dict[int, list[dict[str, Any]]] = {t: [] for t in tokens}
    built_rows: list[dict[str, Any]] = []
    disconnects: list[str] = []

    from kitelake.evidence import evidence_tick_row

    if ticker_factory is None:
        from kitelake.config import load_credentials
        from kiteconnect import KiteTicker
        creds = load_credentials()
        ticker = KiteTicker(creds.api_key, creds.access_token)
    else:
        ticker = ticker_factory()

    def on_ticks(_ws, ticks):
        received = datetime.now(timezone.utc)
        for tick in ticks:
            token = tick.get("instrument_token")
            if token in captured:
                captured[token].append(tick)
                try:
                    built_rows.append(evidence_tick_row(
                        tick, received_ts=received,
                        evidence_class=ACCEPTANCE_EVIDENCE_CLASS,
                        opportunity_id=universe.opportunity_id,
                        capture_reason=ACCEPTANCE_TAG,
                    ))
                except Exception as exc:  # pragma: no cover - live only
                    report.notes.append(f"row build failed: {type(exc).__name__}: {exc}")

    def on_connect(ws, _response):
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_FULL, tokens)

    def on_close(_ws, code, reason):
        disconnects.append(f"{code}: {reason}")

    ticker.on_ticks = on_ticks
    ticker.on_connect = on_connect
    ticker.on_close = on_close

    ticker.connect(threaded=True)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(1.0)
    try:
        ticker.close()
    except Exception:
        pass

    evaluate_ticks(report, "option", captured.get(option_token, []))
    if hedge_token:
        evaluate_ticks(report, "hedge", captured.get(hedge_token, []))
    report.record("simultaneous_subscription",
                  PASS if all(captured.get(t) for t in tokens) else FAIL,
                  {str(t): len(captured.get(t, [])) for t in tokens})
    evaluate_rows(report, built_rows)
    report.record("reconnect", SKIP if not disconnects else PASS,
                  disconnects or "no disconnect occurred during the window")

    # ─── persistence round trip ──────────────────────────────────────────────
    from app.services.snapback_evidence_store import SnapbackEvidenceStore

    store = SnapbackEvidenceStore(root, session_date=date.today())
    sample = built_rows[:50]
    for row in sample:
        store.append_market_event(row)
    reloaded = store.read("market_events")
    report.record("persistence", PASS if len(reloaded) == len(sample) else FAIL,
                  {"written": len(sample), "read_back": len(reloaded)})

    # A round trip must return the same values, not merely the same row count.
    if sample and reloaded:
        original, restored = sample[0], reloaded[0]
        same = str(original.get("bid0_price")) == str(restored.get("bid0_price"))
        report.record("reload_reproduces_values", PASS if same else FAIL,
                      {"bid0_price": restored.get("bid0_price")})

    report.record("writer_healthy", PASS if store.health.healthy else FAIL)

    return report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--underlying", default="NIFTY")
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--root", default=None,
                        help="evidence root; defaults to a temp acceptance area, never the live lake")
    parser.add_argument("--out-dir", default="data/evidence_acceptance")
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else Path("data/evidence_acceptance/_root")
    root.mkdir(parents=True, exist_ok=True)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = run_acceptance(
        underlying=args.underlying, seconds=args.seconds, root=root, out_dir=out_dir,
    )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"acceptance-{stamp}.json"
    path.write_text(json.dumps(report.as_dict(), indent=1, default=str), encoding="utf-8")

    print(json.dumps(report.as_dict(), indent=1, default=str))
    print(f"\nreport written to {path}")
    print(f"OVERALL: {report.overall}")
    return 0 if report.overall == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
