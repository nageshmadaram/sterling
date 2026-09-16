"""End-of-day close observation and finalisation.

Separated on purpose. The old model executed the whole end-of-day lifecycle inside a
one-minute window, so a backend that restarted at 15:31 lost the session's mark — and
the tempting repair, fetching a quote at 15:34 and calling it the close, fabricates
the number the strategy is scored on.

    15:29:00-15:30:00   observe: fetch and PERSIST close quotes as evidence
    >= 15:30:00         finalise: decide from what was persisted, and nothing else

A restart between the two finalises deterministically from stored evidence, or
records EOD_EVIDENCE_GAP. Both are honest; inventing a close mark is not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))

EOD_EVIDENCE_GAP = "EOD_EVIDENCE_GAP"
EOD_PHASE = "EOD_MARK"


def _window() -> tuple:
    from app.engines.snapback.policy import EXECUTION_POLICY

    start = time.fromisoformat(EXECUTION_POLICY.eod_observation_start)
    end = time.fromisoformat(EXECUTION_POLICY.eod_observation_end)
    return start, end


@dataclass
class CloseMarks:
    option: Optional[Dict[str, Any]] = None
    futures: Optional[Dict[str, Any]] = None
    gap_codes: List[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.option is not None and self.futures is not None


@dataclass
class EodObservationResult:
    observed: int = 0
    rejected: int = 0
    errors: List[str] = field(default_factory=list)


@dataclass
class EodFinalizationResult:
    status: str
    positions_finalized: int = 0
    gap_codes: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def _provider_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_IST)


def select_close_marks(
    warehouse, *, opportunity_id: str, session_date: date,
) -> CloseMarks:
    """Latest ACCEPTED observation per leg whose PROVIDER time is inside the window.

    Provider time, not process wall time: a tick received at 15:30:04 but stamped
    15:29:58 is a valid close observation, and one stamped 15:30:04 is not.
    """
    start, end = _window()
    window_start = datetime.combine(session_date, start).replace(tzinfo=_IST)
    window_end = datetime.combine(session_date, end).replace(tzinfo=_IST)

    marks = CloseMarks()
    try:
        rows = warehouse.get_records_by_table(
            "quote_quality_events", opportunity_id=opportunity_id,
        )
    except Exception as exc:
        marks.gap_codes.append(f"eod_evidence_unreadable:{exc}")
        return marks

    best: Dict[str, Dict[str, Any]] = {}
    for raw in rows:
        row = dict(raw)
        if str(row.get("phase") or "") != EOD_PHASE:
            continue
        if int(row.get("accepted") or 0) != 1:
            continue
        stamped = _provider_dt(row.get("provider_timestamp"))
        if stamped is None or not (window_start <= stamped <= window_end):
            continue
        leg = str(row.get("leg") or "").upper()
        current = best.get(leg)
        if current is None or stamped > _provider_dt(current.get("provider_timestamp")):
            best[leg] = row

    marks.option = best.get("OPTION")
    marks.futures = best.get("FUTURES")

    if marks.option is None:
        marks.gap_codes.append(f"missing_option_close_mark:{opportunity_id}")
    if marks.futures is None:
        marks.gap_codes.append(f"missing_futures_close_mark:{opportunity_id}")

    return marks


async def collect_eod_observations(
    *, client, cfg, warehouse, session_date: date, now_ist: datetime,
) -> EodObservationResult:
    """Fetch and persist close quotes for open positions. Decides nothing."""
    from app.services.snapback import extract_raw_quote_event
    from app.services.snapback_market_data import evaluate_quote_quality
    from app.services.snapback_quote_evidence import record_quote_attempt

    result = EodObservationResult()

    try:
        positions = warehouse.get_active_paper_positions() or []
    except Exception as exc:
        result.errors.append(f"positions_unreadable:{exc}")
        return result

    now_ms = int(now_ist.timestamp() * 1000)

    for raw in positions:
        pos = dict(raw)
        if str(pos.get("status") or "") == "EXIT_PENDING":
            # Its lifecycle was resolved intraday; it needs no close mark.
            continue

        opp = str(pos["opportunity_id"])
        for leg, contract in (
            ("OPTION", str(pos.get("option_symbol") or "")),
            ("FUTURES", str(pos.get("futures_symbol") or "")),
        ):
            if not contract:
                continue
            try:
                quotes = await client.get_quote([f"NFO:{contract}"])
                event = extract_raw_quote_event(contract, (quotes or {}).get(f"NFO:{contract}") or {})
            except Exception as exc:
                result.errors.append(f"{opp}:{leg}:{exc}")
                event = None

            decision = (
                evaluate_quote_quality(event, cfg, now_ms=now_ms) if event else None
            )
            accepted = bool(decision and decision.accepted_for_execution)

            record_quote_attempt(
                warehouse, opportunity_id=opp, phase=EOD_PHASE, leg=leg,
                contract_id=contract, required_for_economics=True,
                quote_present=bool(event),
                bid=getattr(event, "best_bid", None),
                ask=getattr(event, "best_ask", None),
                provider_timestamp=(
                    datetime.fromtimestamp(
                        event.exchange_timestamp_ms / 1000.0, tz=timezone.utc
                    ).astimezone(_IST).isoformat() if event else None
                ),
                age_ms=int(getattr(decision, "quote_age_ms", 0) or 0),
                accepted=accepted,
                reason_codes=list(getattr(decision, "reason_codes", []) or ["missing_quote"]),
                symbol=str(pos.get("symbol") or ""),
                spread_pct=getattr(decision, "spread_pct", None),
            )

            if accepted:
                result.observed += 1
            else:
                result.rejected += 1

    return result


def finalize_eod_session(
    *, cfg, warehouse, session_date: date, finalized_at: datetime,
) -> EodFinalizationResult:
    """Apply the end-of-day lifecycle from persisted close evidence only."""
    from app.services.snapback_session_ledger import (
        SessionStatus, append_session_evidence_gap, update_session_phase,
    )

    session_key = session_date.isoformat()
    result = EodFinalizationResult(status="COMPLETE")

    try:
        positions = warehouse.get_active_paper_positions() or []
    except Exception as exc:
        result.status = "FAILED"
        result.errors.append(f"positions_unreadable:{exc}")
        append_session_evidence_gap(warehouse, session_key, "eod_finalization_exception")
        update_session_phase(warehouse, session_key, eod_phase_status=SessionStatus.FAILED)
        return result

    for raw in positions:
        pos = dict(raw)
        opp = str(pos["opportunity_id"])

        if str(pos.get("status") or "") == "EXIT_PENDING":
            continue

        if warehouse.has_daily_mtm_for_session(opp, session_key):
            # Finalisation is idempotent: one mark per session, ever.
            continue

        marks = select_close_marks(
            warehouse, opportunity_id=opp, session_date=session_date,
        )
        if not marks.complete:
            result.status = "FAILED"
            result.gap_codes.extend(marks.gap_codes)
            for code in marks.gap_codes:
                append_session_evidence_gap(warehouse, session_key, code)
            continue

        try:
            apply_daily_mtm_from_observed_marks(
                opportunity_id=opp, position=pos, option_mark=marks.option,
                futures_mark=marks.futures, cfg=cfg, warehouse=warehouse,
                session_date=session_key,
            )
            result.positions_finalized += 1
        except Exception as exc:
            log.exception("EOD finalisation failed for %s: %s", opp, exc)
            result.status = "FAILED"
            result.errors.append(f"{opp}:{exc}")
            append_session_evidence_gap(warehouse, session_key, "eod_finalization_exception")

    if result.status == "FAILED" and not result.gap_codes:
        result.gap_codes.append(EOD_EVIDENCE_GAP)

    update_session_phase(
        warehouse, session_key,
        eod_phase_status=SessionStatus.COMPLETE if result.status == "COMPLETE" else SessionStatus.FAILED,
    )
    return result


def apply_daily_mtm_from_observed_marks(
    *,
    opportunity_id: str,
    position: Dict[str, Any],
    option_mark: Dict[str, Any],
    futures_mark: Dict[str, Any],
    cfg,
    warehouse,
    session_date: str,
) -> Dict[str, Any]:
    """The end-of-day state transition, driven by already-observed marks.

    One implementation, shared by the live finalizer, replay and fixtures, so the
    lifecycle cannot drift between them.
    """
    from app.engines.snapback.intraday_models import RawQuoteEvent
    from app.engines.snapback.pricing import bs_delta
    from app.services.snapback_prospective_collector import SnapbackProspectiveCollector

    collector = SnapbackProspectiveCollector(warehouse=warehouse)

    option_bid = float(option_mark.get("bid") or 0.0)
    futures_bid = float(futures_mark.get("bid") or 0.0)
    futures_ask = float(futures_mark.get("ask") or futures_bid)

    fut_event = RawQuoteEvent(
        contract_id=str(position.get("futures_symbol") or ""),
        exchange_timestamp_ms=int(
            datetime.fromisoformat(
                str(futures_mark.get("provider_timestamp"))
            ).timestamp() * 1000
        ),
        received_at_ms=int(datetime.now(_IST).timestamp() * 1000),
        best_bid=futures_bid, best_ask=futures_ask,
        bid_quantity=int(futures_mark.get("visible_quantity") or 1),
        ask_quantity=int(futures_mark.get("visible_quantity") or 1),
        last_price=futures_bid,
    )

    opt_sym = str(position.get("option_symbol") or "")
    opt_type = "PE" if "PE" in opt_sym else "CE"
    strike = float(position.get("option_strike") or 0.0)
    entry_dte = int(position.get("entry_dte") or 45)
    iv = float(position.get("entry_iv") or 0.20)
    spot = float(position.get("entry_spot") or 0.0)

    delta_raw = float(bs_delta(spot, strike, max(1, entry_dte) / 365.0, iv,
                               call=(opt_type == "CE")))
    option_delta = -abs(delta_raw) if opt_type == "PE" else abs(delta_raw)

    return collector.rebalance_and_mtm(
        opportunity_id=opportunity_id,
        session_date=session_date,
        symbol=str(position.get("symbol") or ""),
        current_spot=spot,
        current_option_delta=option_delta,
        option_bid=option_bid,
        futures_quote_event=fut_event,
        option_entry_price=float(position.get("option_entry_price") or 0.0),
        option_quantity=int(position.get("option_qty") or 0),
        current_futures_lots=int(position.get("current_futures_lots") or 0),
        futures_lot_size=int(position.get("futures_lot_size") or 0),
        causal_beta=float(position.get("causal_beta") or 1.0),
        prior_realized_futures_pnl=float(position.get("realized_futures_pnl") or 0.0),
        prior_avg_futures_entry_price=float(position.get("avg_futures_entry_price") or 0.0),
        option_quote_event=None,
        cfg=None,
        peak_source="EOD_CLOSE",
    )
