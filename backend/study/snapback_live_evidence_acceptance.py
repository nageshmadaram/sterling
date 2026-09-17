"""Live acceptance: does the real Kite payload match what the evidence schema assumes?

Synthetic fixtures prove code behavior, not vendor reality. This harness reads the
live instrument master and FULL websocket feed, exercises one forced transport
failure without disabling KiteTicker retry, verifies resubscription with fresh
post-reconnect ticks, and round-trips the captured evidence. It never places an
order.

The frozen selector's listedness is reported separately from the vendor-schema
probe. If the theoretical target is NOT_LISTED, that is a strategy-reality finding;
the harness then subscribes to a clearly labelled real listed probe contract from
the captured universe so depth/timestamp/reconnect assumptions can still be tested.
The probe must never be mistaken for a Snapback execution choice.

Evidence written by this harness is TEST_ACCEPTANCE/MODELLED and must never enter
Track A economics.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

ACCEPTANCE_EVIDENCE_CLASS = "MODELLED"
ACCEPTANCE_TAG = "TEST_ACCEPTANCE"

#: Ticks older than this are not a live market. On connect, Kite replays the last
#: trade of the previous session, so a run after the close receives well-formed
#: five-level books whose data is hours old. Every schema check then passes while
#: proving nothing about live behaviour, and the artifact reads like an
#: acceptance. Generous on purpose: this separates "the market is trading" from
#: "this is yesterday's close", not a production freshness TTL.
MAX_LIVE_TICK_AGE_MS = 15 * 60 * 1000

#: Levels per side in Kite FULL mode. Mirrors kitelake.evidence.DEPTH_LEVELS and
#: is asserted equal to it by the harness tests, so the two cannot drift.
DEPTH_LEVELS = 5

#: How each underlying's SPOT quotes, which differs from the derivatives
#: underlying name. Tried in order; the first positive last_price wins.
_SPOT_QUOTE_KEYS = {
    "NIFTY": ("NSE:NIFTY 50", "NSE:NIFTY"),
    "BANKNIFTY": ("NSE:NIFTY BANK", "NSE:BANKNIFTY"),
    "FINNIFTY": ("NSE:NIFTY FIN SERVICE", "NSE:FINNIFTY"),
    "MIDCPNIFTY": ("NSE:NIFTY MID SELECT", "NSE:MIDCPNIFTY"),
    "SENSEX": ("BSE:SENSEX",),
    "BANKEX": ("BSE:BANKEX",),
}


class CredentialsUnavailable(RuntimeError):
    """No Kite session this harness can use. Never carries the token itself."""


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


# ─── credentials ─────────────────────────────────────────────────────────────

def resolve_acceptance_credentials() -> tuple[str, str, str]:
    """The Kite session this machine actually has, whichever flow created it.

    Two independent stores exist and they held different applications: Sterling's
    own login flow writes an encrypted token into ``kite_accounts``, while the
    kitelake data-lake tooling keeps its own ``session.json``. The harness
    originally read only the second, so an operator who refreshed the session the
    documented way — through Sterling — still hit
    ``TokenException: Incorrect api_key or access_token``, with no indication
    that two different apps were involved.

    A release gate the operator cannot run after following the runbook is not a
    gate, so this prefers Sterling's store and falls back to kitelake's.

    Returns ``(api_key, access_token, source)``. The token is returned for
    immediate use and is never logged, printed or persisted; the caller must not
    put it in the acceptance report.
    """
    # 1. Sterling's own account store, which the documented login flow writes.
    try:
        import app.services.exchanges.kite.accounts as kite_accounts

        for account in kite_accounts._load_from_db():
            if not account.is_active:
                continue
            # ``access_token`` is a property that decrypts on read. Calling a
            # non-existent ``token()`` here silently yielded "" through the
            # handler below and looked exactly like a logged-out account.
            token = account.access_token or ""
            if account.api_key and token:
                return account.api_key, token, "sterling_kite_accounts"
            if account.api_key and not token:
                # Present but undecryptable: almost always STERLING_SECRET_KEY
                # missing from this process. Say so, because decrypt() returns ""
                # and an absent key is otherwise indistinguishable from an
                # account that was never logged in.
                raise CredentialsUnavailable(
                    "Sterling has an active Kite account "
                    f"(api_key {account.api_key[:4]}…) whose access token could not be "
                    "decrypted. STERLING_SECRET_KEY is probably not set for this "
                    "process; export it, or run the harness the same way the backend "
                    "service runs."
                )
    except CredentialsUnavailable:
        raise
    except Exception:
        # No store, no schema, no rows — fall through to kitelake.
        pass

    # 2. The data-lake session, for a machine that only ever used kitelake.
    try:
        from kitelake.config import load_credentials

        creds = load_credentials()
        if creds.api_key and creds.access_token:
            return creds.api_key, creds.access_token, "kitelake_session"
    except Exception:
        pass

    raise CredentialsUnavailable(
        "no usable Kite session found. Either complete Sterling's Kite login "
        "(and make STERLING_SECRET_KEY available to this process), or set "
        "KITE_API_KEY and KITE_ACCESS_TOKEN in the environment."
    )


# ─── instrument master ───────────────────────────────────────────────────────

def fetch_instrument_master(kite: Any, exchange: str = "NFO") -> list[dict[str, Any]]:
    return list(kite.instruments(exchange))


def archive_instrument_master(rows: list[dict[str, Any]], root: Path, *,
                              retrieved_at: datetime) -> dict[str, Any]:
    """Persist an immutable, hashed snapshot. Today's master is gone tomorrow."""
    import hashlib
    import os

    stamp = retrieved_at.strftime("%Y%m%dT%H%M%S")
    directory = root / "instrument_master" / f"date={retrieved_at.date().isoformat()}" / f"snapshot={stamp}"
    directory.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    staging = directory.parent / f".staging-{stamp}.json"
    staging.write_text(payload, encoding="utf-8")
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
        {
            "max_buy_levels": deepest_buy,
            "max_sell_levels": deepest_sell,
            "interpretation": "observed book depth; a thin market and a decoder defect require separate diagnosis",
        },
    )

    with_orders = any(f["buy_has_orders"] and f["sell_has_orders"] for f in findings)
    report.record(f"{role}_order_counts", PASS if with_orders else FAIL)

    quantities_present = any(
        all(q is not None for q in f["buy_quantities"])
        for f in findings if f["buy_quantities"]
    )
    report.record(f"{role}_depth_quantities", PASS if quantities_present else FAIL)

    stamped = sum(1 for f in findings if f["has_exchange_timestamp"])
    report.record(
        f"{role}_exchange_timestamp",
        PASS if stamped else FAIL,
        {"with_exchange_ts": stamped, "total": len(findings)},
    )


def evaluate_rows(report: Report, rows: list[dict[str, Any]]) -> None:
    if not rows:
        report.record("row_conversion", FAIL, "no evidence rows built")
        return

    report.record("row_conversion", PASS, f"{len(rows)} rows")
    separated = [r for r in rows if r.get("exchange_ts") and r.get("received_ts")]
    report.record("clock_separation", PASS if separated else FAIL)

    if separated:
        # Is this a live market at all? See MAX_LIVE_TICK_AGE_MS.
        all_ages = [
            (r["received_ts"] - r["exchange_ts"]).total_seconds() * 1000.0
            for r in separated
        ]
        freshest = min(all_ages)
        live = freshest <= MAX_LIVE_TICK_AGE_MS
        report.record(
            "market_data_live",
            PASS if live else FAIL,
            {
                "freshest_tick_age_ms": round(freshest, 1),
                "threshold_ms": MAX_LIVE_TICK_AGE_MS,
                "interpretation": (
                    "ticks are current; the book was moving during the run"
                    if live
                    else "NOT_A_LIVE_MARKET: every tick predates the threshold, so "
                         "these are replayed closing-book values. Schema and decode "
                         "checks remain valid; depth turnover, freshness and live "
                         "behaviour are NOT exercised by this run."
                ),
            },
        )

        ages = [
            (r["received_ts"] - r["exchange_ts"]).total_seconds() * 1000.0
            for r in separated
        ]
        report.record("freshness_computable", PASS, {
            "min_age_ms": round(min(ages), 1),
            "max_age_ms": round(max(ages), 1),
        })
        report.record("clock_ordering_sane", PASS if min(ages) > -5000 else FAIL,
                      {"min_age_ms": round(min(ages), 1)})

    unstamped = [r for r in rows if r.get("exchange_ts") is None]
    if unstamped:
        borrowed = [r for r in unstamped if r.get("exchange_ts") == r.get("received_ts")]
        report.record(
            "null_exchange_timestamp_semantics",
            PASS if not borrowed else FAIL,
            {"unstamped_rows": len(unstamped), "exercised": True},
        )
    else:
        # This is deliberately a PASS for the release fold: no timestamp was
        # fabricated. The detail makes explicit that the nullable live path was
        # not exercised, so an auditor must not overclaim what this run proved.
        report.record(
            "null_exchange_timestamp_semantics",
            PASS,
            {
                "unstamped_rows": 0,
                "exercised": False,
                "interpretation": "NOT_EXERCISED: every observed tick was stamped",
            },
        )

    # Absent levels must be null, never zero — an empty rung and a rung quoting
    # zero are different facts. On a full five-deep book nothing is absent, so
    # this passes without testing anything; say which case occurred rather than
    # letting a vacuous pass read like a proven one.
    zeroed = [r for r in rows if r.get("bid4_price") == 0 or r.get("ask4_price") == 0]
    exercised = any(
        r.get(f"{side}{i}_price") is None
        for r in rows for side in ("bid", "ask") for i in range(DEPTH_LEVELS)
    )
    report.record(
        "absent_level_is_null_not_zero",
        PASS if not zeroed else FAIL,
        {
            "rows_with_a_zeroed_level": len(zeroed),
            "exercised": exercised,
            "interpretation": (
                "an absent level was observed and stored as null"
                if exercised and not zeroed
                else "NOT_EXERCISED: every observed book was fully populated, so "
                     "the absent-level path never ran"
                if not exercised
                else "a level was stored as zero rather than null"
            ),
        },
    )


# ─── reconnect exercise ──────────────────────────────────────────────────────

def _force_transport_disconnect(ticker: Any, *, schedule: Any = None) -> tuple[bool, str]:
    """Abort only the TCP transport, leaving KiteTicker auto-retry enabled."""
    ws = getattr(ticker, "ws", None)
    transport = getattr(ws, "transport", None)
    abort = getattr(transport, "abortConnection", None)
    if not callable(abort):
        return False, "ticker websocket transport has no abortConnection()"

    if schedule is None:
        from twisted.internet import reactor
        schedule = reactor.callFromThread

    try:
        schedule(abort)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, "scheduled transport.abortConnection() without stopping auto-retry"


def evaluate_forced_reconnect(
    report: Report,
    *,
    tokens: list[int],
    force_succeeded: bool,
    force_detail: str,
    disconnects: list[str],
    reconnect_attempts: list[int],
    connection_count: int,
    post_reconnect: dict[int, list[dict[str, Any]]],
) -> None:
    report.record("forced_disconnect", PASS if force_succeeded else FAIL, force_detail)
    report.record(
        "disconnect_observed",
        PASS if disconnects else FAIL,
        disconnects or "forced transport loss was not observed by KiteTicker",
    )
    # Grade the outcome, not the instrumentation. The requirement is that the
    # socket comes back and resumes delivering evidence after a loss; whether
    # kiteconnect happens to invoke on_reconnect while doing so is its own
    # implementation detail. A run where the transport was aborted, the loss was
    # observed, a second connection succeeded and fresh FULL ticks arrived has
    # demonstrably exercised recovery, and failing it because a callback stayed
    # silent would report a working system as broken.
    #
    # The counter is still recorded, so a future kiteconnect that does fire it
    # leaves that visible rather than the fact being lost.
    recovered = connection_count >= 2 and bool(disconnects)
    report.record(
        "reconnect_attempted",
        PASS if (reconnect_attempts or recovered) else FAIL,
        {
            "on_reconnect_callbacks": reconnect_attempts,
            "recovered_without_callback": bool(recovered and not reconnect_attempts),
            "interpretation": (
                "recovery evidenced by reconnection and post-loss ticks; the "
                "on_reconnect callback did not fire"
                if recovered and not reconnect_attempts
                else "on_reconnect callback observed"
                if reconnect_attempts
                else "no retry path entered and no successful reconnection"
            ),
        },
    )
    report.record(
        "reconnect_connected",
        PASS if connection_count >= 2 else FAIL,
        {"successful_connections": connection_count},
    )

    fresh_counts = {str(t): len(post_reconnect.get(t, [])) for t in tokens}
    report.record(
        "post_reconnect_fresh_ticks",
        PASS if tokens and all(fresh_counts[str(t)] > 0 for t in tokens) else FAIL,
        fresh_counts,
    )

    full_counts = {
        str(t): sum(1 for tick in post_reconnect.get(t, []) if inspect_tick(tick)["has_depth"])
        for t in tokens
    }
    report.record(
        "post_reconnect_full_mode",
        PASS if tokens and all(full_counts[str(t)] > 0 for t in tokens) else FAIL,
        full_counts,
    )


def _vendor_probe(selection: Any, universe: Any, expiry: str) -> tuple[Optional[int], dict[str, Any]]:
    """Choose the socket probe without changing or hiding the frozen selection.

    LISTED uses the actual frozen theoretical target. NOT_LISTED uses the nearest
    genuinely listed contract from the same observed expiry (falling back to the
    universe only if that expiry has no rows) solely for vendor-schema testing.
    """
    if selection.listed == "LISTED" and selection.selected_instrument_token:
        return int(selection.selected_instrument_token), {
            "source": "FROZEN_THEORETICAL_SELECTION",
            "instrument_token": int(selection.selected_instrument_token),
            "computed_strike": selection.selected_strike,
            "not_strategy_substitute": False,
        }

    contracts = list(universe.contracts or ())
    same_expiry = [c for c in contracts if c.expiry == expiry]
    pool = same_expiry or contracts
    if not pool:
        return None, {
            "source": "NONE",
            "reason": "candidate universe contains no listed contract for vendor probe",
            "not_strategy_substitute": True,
        }

    probe = min(
        pool,
        key=lambda c: (
            abs(float(c.strike) - float(selection.selected_strike)),
            c.expiry,
            float(c.strike),
            int(c.instrument_token),
        ),
    )
    return int(probe.instrument_token), {
        "source": "LISTED_VENDOR_SCHEMA_PROBE",
        "instrument_token": int(probe.instrument_token),
        "tradingsymbol": probe.tradingsymbol,
        "strike": float(probe.strike),
        "expiry": probe.expiry,
        "theoretical_target_listedness": selection.listed,
        "computed_strike": selection.selected_strike,
        "not_strategy_substitute": True,
    }


def _roundtrip_root(root: Path) -> Path:
    """One evidence store per acceptance invocation; reruns cannot see old parts."""
    return root / "roundtrip" / f"run={uuid.uuid4().hex}"


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
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    from app.engines.snapback.config import SnapbackConfig
    from app.engines.option_contracts import spec_for
    from app.services.snapback_candidate_universe import (
        CandidateUniverseError, build_candidate_universe, universe_hash,
    )
    from app.services.snapback_contract_selection import (
        SelectionError, SelectionInputs, select_snapback_contract,
    )

    report = Report(
        runtime_sha=_runtime_sha(),
        started_at=datetime.now(timezone.utc).isoformat(),
        underlying=underlying,
    )
    cfg = SnapbackConfig()

    if kite is None:
        from kiteconnect import KiteConnect

        api_key, access_token, creds_source = resolve_acceptance_credentials()
        report.notes.append(f"kite session source: {creds_source}")
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)

    try:
        rows = fetch_instrument_master(kite)
        metadata = archive_instrument_master(rows, root, retrieved_at=datetime.now(timezone.utc))
        report.record("instrument_master", PASS, metadata)
    except Exception as exc:
        report.record("instrument_master", FAIL, f"{type(exc).__name__}: {exc}")
        return report

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
    report.record(
        "candidate_hash",
        PASS if recomputed == universe.candidate_universe_hash else FAIL,
        universe.candidate_universe_hash,
    )

    # The tradingsymbol an index quotes under is not its derivatives underlying:
    # NIFTY options are written on "NIFTY", but the spot quotes as "NSE:NIFTY 50".
    # Asking for NSE:NIFTY returns nothing, and an empty result previously became
    # spot 0.0 and then a raised SelectionError rather than a recorded failure.
    spot_keys = _SPOT_QUOTE_KEYS.get(underlying.upper(), (f"NSE:{spec.underlying}",))
    spot, spot_key, spot_error = 0.0, "", ""
    for key in spot_keys:
        try:
            quote = kite.ltp([key]) if hasattr(kite, "ltp") else {}
        except Exception as exc:
            spot_error = f"{type(exc).__name__}: {exc}"
            continue
        row = (quote or {}).get(key) or {}
        price = float(row.get("last_price") or 0.0)
        if price > 0:
            spot, spot_key = price, key
            break

    if spot <= 0:
        report.record(
            "spot", FAIL,
            spot_error or f"no positive last_price from any of {list(spot_keys)}",
        )
        return report
    report.record("spot", PASS, {"key": spot_key, "last_price": spot})

    expiry = universe.expiries[0]
    dte = (date.fromisoformat(expiry) - date.today()).days
    inputs = SelectionInputs(
        opportunity_id=universe.opportunity_id, underlying=underlying, option_type="PE",
        spot=spot, assumed_iv=cfg.__dict__.get("assumed_iv_floor", 0.0) or 0.18,
        target_delta=cfg.target_delta, dte_days=max(dte, 1),
        strike_step=spec.strike_step, expiry=expiry,
        candidate_universe_hash=universe.candidate_universe_hash,
    )
    try:
        selection = select_snapback_contract(inputs, universe.contracts)
        replay = select_snapback_contract(inputs, universe.contracts)
    except SelectionError as exc:
        # A refusal is a result, not a crash. Recording it keeps the artifact
        # machine-readable instead of leaving a traceback and no report.
        report.record("selector_replay", FAIL, f"SelectionError: {exc}")
        return report

    report.record(
        "selector_replay",
        PASS if replay.selected_strike == selection.selected_strike else FAIL,
        {"strike": selection.selected_strike},
    )
    report.record(
        "listedness",
        PASS if selection.listed in ("LISTED", "NOT_LISTED") else FAIL,
        {
            "listed": selection.listed,
            "computed_strike": selection.selected_strike,
            "interpretation": "strategy-reality finding; NOT_LISTED is not a decoder failure",
        },
    )
    report.notes.append(
        f"frozen selector computed {selection.selected_strike} PE {expiry}; "
        f"listedness against the real master: {selection.listed}"
    )

    option_token, probe_detail = _vendor_probe(selection, universe, expiry)
    report.record("vendor_probe_contract", PASS if option_token else FAIL, probe_detail)
    if not option_token:
        return report
    if probe_detail.get("not_strategy_substitute"):
        report.notes.append(
            "NOT_LISTED theoretical target retained as a strategy finding; live socket "
            "checks use a labelled listed vendor-schema probe and do not treat it as Snapback's choice"
        )

    from app.services.snapback_hedge_contract import HedgeContractError, select_hedge_future
    try:
        hedge = select_hedge_future(
            rows, option_expiry=date.fromisoformat(expiry), name=underlying, exchange="NFO"
        )
        report.record("hedge_contract", PASS, {"tradingsymbol": hedge.tradingsymbol})
        hedge_token = hedge.instrument_token
    except HedgeContractError as exc:
        report.record("hedge_contract", FAIL, str(exc))
        hedge_token = None

    tokens = [t for t in (option_token, hedge_token) if t]
    captured: dict[int, list[dict[str, Any]]] = {t: [] for t in tokens}
    post_reconnect: dict[int, list[dict[str, Any]]] = {t: [] for t in tokens}
    built_rows: list[dict[str, Any]] = []
    disconnects: list[str] = []
    reconnect_attempts: list[int] = []
    connection_count = 0
    shutting_down = False

    from kitelake.evidence import DEPTH_LEVELS, evidence_tick_row

    if ticker_factory is None:
        from kiteconnect import KiteTicker

        api_key, access_token, _ = resolve_acceptance_credentials()
        ticker = KiteTicker(api_key, access_token)
    else:
        ticker = ticker_factory()

    def on_ticks(_ws, ticks):
        received = datetime.now(timezone.utc)
        generation = connection_count
        for tick in ticks:
            token = tick.get("instrument_token")
            if token in captured:
                captured[token].append(tick)
                if generation >= 2:
                    post_reconnect[token].append(tick)
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
        nonlocal connection_count
        connection_count += 1
        if connection_count == 1:
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_FULL, tokens)

    def on_close(_ws, code, reason):
        if not shutting_down:
            disconnects.append(f"{code}: {reason}")

    def on_reconnect(_ws, attempts_count):
        reconnect_attempts.append(int(attempts_count))

    ticker.on_ticks = on_ticks
    ticker.on_connect = on_connect
    ticker.on_close = on_close
    ticker.on_reconnect = on_reconnect

    ticker.connect(threaded=True)
    started = time.monotonic()
    deadline = started + seconds
    force_not_before = started + min(5.0, max(1.0, seconds * 0.1))
    force_succeeded = False
    force_detail = "forced disconnect was never attempted"

    while time.monotonic() < deadline:
        now = time.monotonic()
        if (
            not force_succeeded
            and now >= force_not_before
            and tokens
            and all(captured.get(t) for t in tokens)
        ):
            force_succeeded, force_detail = _force_transport_disconnect(ticker)
            if not force_succeeded:
                break
        time.sleep(0.25)

    evaluate_ticks(report, "option", captured.get(option_token, []))
    if hedge_token:
        evaluate_ticks(report, "hedge", captured.get(hedge_token, []))
    report.record(
        "simultaneous_subscription",
        PASS if all(captured.get(t) for t in tokens) else FAIL,
        {str(t): len(captured.get(t, [])) for t in tokens},
    )
    evaluate_rows(report, built_rows)
    evaluate_forced_reconnect(
        report,
        tokens=tokens,
        force_succeeded=force_succeeded,
        force_detail=force_detail,
        disconnects=disconnects,
        reconnect_attempts=reconnect_attempts,
        connection_count=connection_count,
        post_reconnect=post_reconnect,
    )

    shutting_down = True
    try:
        ticker.close()
    except Exception:
        pass

    from app.services.snapback_evidence_store import SnapbackEvidenceStore

    # Isolate every invocation. read(kind) intentionally reads every part in its
    # store, so sharing one date root made a valid second run fail because it saw
    # the first run's rows too.
    persistence_root = _roundtrip_root(root)
    store = SnapbackEvidenceStore(persistence_root, session_date=date.today())
    sample = built_rows[:50]
    for row in sample:
        store.append_market_event(row)
    reloaded = store.read("market_events")
    report.record(
        "persistence",
        PASS if len(reloaded) == len(sample) else FAIL,
        {
            "written": len(sample),
            "read_back": len(reloaded),
            "isolated_store": str(persistence_root),
        },
    )

    if sample and reloaded:
        original, restored = sample[0], reloaded[0]
        same = str(original.get("bid0_price")) == str(restored.get("bid0_price"))
        report.record(
            "reload_reproduces_values",
            PASS if same else FAIL,
            {"bid0_price": restored.get("bid0_price")},
        )

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
