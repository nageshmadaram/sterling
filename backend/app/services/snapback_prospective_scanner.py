"""Unattended Day-T signal finalisation.

Signals must exist because the market closed, not because somebody opened a browser.
This runs after the official close, evaluates the frozen universe, and writes a
durable session record whether or not any signal fired — so a quiet session and a
broken scanner can never look the same.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))

# The daily bar is only final after the official close.
SCAN_AFTER_CLOSE = time(15, 35)

EXPERIMENT_ENV = "STERLING_EXPERIMENT_ID"


@dataclass
class SessionScanResult:
    session_date: str
    status: str
    universe_expected: int = 0
    universe_scanned: int = 0
    symbol_failures: int = 0
    market_gate_status: str = ""
    signals_authoritative: int = 0
    gap_codes: List[str] = field(default_factory=list)


def _experiment_id() -> str:
    import os

    return os.environ.get(EXPERIMENT_ENV, "prospective_runtime_1_1")


def _calendar_version() -> str:
    try:
        from app.services.navigator.calendar import CALENDAR_VERSION

        return str(CALENDAR_VERSION)
    except Exception:
        return "UNKNOWN"


async def _scan_universe(*, client, cfg, session_date: date, warehouse) -> Dict[str, Any]:
    """Evaluate the frozen universe against post-close daily bars."""
    import asyncio

    from app.engines.option_contracts import canonical
    from app.engines.snapback.models import to_bars
    from app.engines.snapback.regime import MARKET_SYMBOL, gate_for
    from app.services.snapback import (
        LOOKBACK_BARS, _drop_forming, evaluate_symbol, resolve_universe,
    )

    nfo, bfo, nse, bse = await asyncio.gather(
        client.search_instruments("", "NFO", limit=1_000_000),
        client.search_instruments("", "BFO", limit=1_000_000),
        client.search_instruments("", "NSE", limit=1_000_000),
        client.search_instruments("", "BSE", limit=1_000_000),
    )
    universe = resolve_universe(cfg, nfo=nfo, bfo=bfo, equities=nse + bse)

    from app.schemas.instruments import InstrumentMeta

    async def daily(token: int, name: str) -> list:
        meta = InstrumentMeta(
            underlying=name, tick_size=0.05, strike_step=1.0, exchange_currency="INR",
            index_name=name, has_options=True, exchange="zerodha", zerodha_token=int(token),
        )
        # Deliberately not cached: a bar fetched before the close is not a closed bar.
        return await client.get_candles(meta, "1D", LOOKBACK_BARS)

    market_gate = None
    market_gate_status = ""
    index = next((i for i in universe if canonical(i.name) == MARKET_SYMBOL), None)
    if index is not None:
        try:
            bars = to_bars(_drop_forming(await daily(index.token, index.tradingsymbol)))
            market_gate = gate_for(
                {MARKET_SYMBOL: bars},
                market_filter=cfg.market_filter, ema_period=cfg.market_ema,
            )
            market_gate_status = "EVALUATED" if market_gate else "UNAVAILABLE"
        except Exception as exc:
            log.warning("Snapback scanner: market gate failed: %s", exc)
            market_gate_status = ""

    sem = asyncio.Semaphore(3)
    signals: List[Any] = []
    failures = 0
    scanned = 0

    async def one(item) -> None:
        nonlocal failures, scanned
        async with sem:
            try:
                raw = _drop_forming(await daily(item.token, item.tradingsymbol))
                if not raw:
                    failures += 1
                    return
                scanned += 1
                emitted = list(evaluate_symbol(raw, cfg, item.name, market_gate=market_gate))
                for sig in emitted:
                    signals.append((item, sig))

                # Every scanned symbol leaves a decision, signal or not: a quiet
                # market and a symbol nobody looked at must not look identical.
                try:
                    from app.services.snapback_instrument_identity import identity_from_instrument
                    from app.services.snapback_scan_evidence import (
                        build_symbol_decision, record_symbol_decision,
                    )

                    identity = identity_from_instrument(
                        {
                            "tradingsymbol": getattr(item, "tradingsymbol", ""),
                            "instrument_token": getattr(item, "token", 0),
                            "exchange": "BSE" if str(getattr(item, "option_exchange", "")) == "BFO" else "NSE",
                            "name": getattr(item, "name", ""),
                        },
                        canonical_symbol=getattr(item, "name", ""),
                    )
                    record_symbol_decision(warehouse, build_symbol_decision(
                        session_date=session_date.isoformat(),
                        symbol=getattr(item, "name", ""),
                        bars=to_bars(raw), cfg=cfg,
                        market_gate_status=market_gate_status or "UNAVAILABLE",
                        market_gate_passed=bool(market_gate),
                        identity=identity, signals=emitted,
                    ))
                except Exception as evidence_exc:
                    failures += 1
                    log.warning(
                        "Snapback scanner: decision evidence failed for %s: %s",
                        getattr(item, "name", "?"), evidence_exc,
                    )
            except Exception as exc:
                failures += 1
                log.warning("Snapback scanner: %s failed: %s", item.name, exc)

    await asyncio.gather(*(one(i) for i in universe))

    return {
        "universe_expected": len(universe),
        "universe_scanned": scanned,
        "symbol_failures": failures,
        "market_gate_status": market_gate_status,
        "signals": signals,
    }


async def finalize_session_signals(
    *,
    session_date: date,
    client,
    warehouse=None,
    uid: str = "default",
) -> SessionScanResult:
    """Scan the just-closed session and record it, signals or not."""
    from app.services.snapback_session_ledger import SessionStatus, record_session_scan

    session_key = session_date.isoformat()
    started = datetime.now(timezone.utc).isoformat()

    if warehouse is None:
        from app.services.snapback_prospective_collector import SnapbackObservationWarehouse

        warehouse = SnapbackObservationWarehouse()

    def _record(result: SessionScanResult) -> SessionScanResult:
        record_session_scan(
            warehouse,
            session_date=session_key,
            experiment_id=_experiment_id(),
            calendar_version=_calendar_version(),
            universe_expected=result.universe_expected,
            universe_scanned=result.universe_scanned,
            symbol_failures=result.symbol_failures,
            market_gate_status=result.market_gate_status,
            signals_authoritative=result.signals_authoritative,
            status=result.status,
            scanner_started_at=started,
            evidence_gap_codes=result.gap_codes,
        )
        return result

    if client is None:
        return _record(SessionScanResult(
            session_date=session_key, status=SessionStatus.FAILED,
            gap_codes=["broker_unavailable"],
        ))

    from app.services.snapback import get_config

    cfg = get_config(uid)
    if not getattr(cfg, "enabled", False):
        return _record(SessionScanResult(
            session_date=session_key, status=SessionStatus.FAILED,
            gap_codes=["strategy_disabled"],
        ))

    try:
        scan = await _scan_universe(
            client=client, cfg=cfg, session_date=session_date, warehouse=warehouse,
        )
    except Exception as exc:
        log.exception("Snapback scanner failed for %s: %s", session_key, exc)
        return _record(SessionScanResult(
            session_date=session_key, status=SessionStatus.FAILED,
            gap_codes=[f"scan_failed:{exc}"],
        ))

    authoritative = 0
    for entry in scan.get("signals") or []:
        item, sig = entry if isinstance(entry, tuple) else (None, entry)
        try:
            from app.services.snapback_authority import (
                classify_signal_authority, dataset_start,
            )
            from app.services.snapback_prospective_collector import SnapbackProspectiveCollector

            verdict = classify_signal_authority(
                signal_timestamp_ms=int(getattr(sig, "timestamp_ms", 0) or 0),
                latest_closed_session=session_date,
                dataset_start=dataset_start(),
            )
            # Capture the provider identity while the instrument row is in hand.
            identity = None
            if item is not None:
                from app.services.snapback_instrument_identity import identity_from_instrument

                identity = identity_from_instrument(
                    {
                        "tradingsymbol": getattr(item, "tradingsymbol", ""),
                        "instrument_token": getattr(item, "token", 0),
                        "exchange": getattr(item, "cash_exchange", "")
                        or ("BSE" if str(getattr(item, "option_exchange", "")) == "BFO" else "NSE"),
                        "name": getattr(item, "name", ""),
                    },
                    canonical_symbol=getattr(item, "name", "") or sig.symbol,
                )

            collector = SnapbackProspectiveCollector(warehouse=warehouse)
            collector.record_signal_at_close(
                sig, cfg, source=verdict.source, identity=identity,
            )
            if verdict.authoritative:
                authoritative += 1
        except Exception as exc:
            log.warning("Snapback scanner: could not record signal: %s", exc)

    complete = (
        scan.get("symbol_failures", 0) == 0
        and scan.get("universe_scanned", 0) >= scan.get("universe_expected", 0)
        and bool(scan.get("market_gate_status"))
    )

    return _record(SessionScanResult(
        session_date=session_key,
        status=SessionStatus.COMPLETE if complete else SessionStatus.FAILED,
        universe_expected=int(scan.get("universe_expected") or 0),
        universe_scanned=int(scan.get("universe_scanned") or 0),
        symbol_failures=int(scan.get("symbol_failures") or 0),
        market_gate_status=str(scan.get("market_gate_status") or ""),
        signals_authoritative=authoritative,
        gap_codes=[] if complete else ["incomplete_universe_observation"],
    ))
