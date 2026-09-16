"""E22: the close mark comes from evidence observed before the close.

A one-minute execution window means a backend restarting at 15:31 loses the session's
mark entirely — and the tempting repair, fetching a quote at 15:34 and calling it the
close, is worse: it fabricates the number the whole strategy is scored on.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timedelta, timezone

import pytest

from app.engines.snapback.config import SnapbackConfig
from app.services.snapback_eod import (
    EOD_EVIDENCE_GAP,
    finalize_eod_session,
    select_close_marks,
)
from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

_IST = timezone(timedelta(hours=5, minutes=30))
SESSION = date(2026, 10, 15)


@pytest.fixture
def warehouse():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
        path = fh.name
    wh = SnapbackObservationWarehouse(db_path=path)
    yield wh
    if os.path.exists(path):
        os.remove(path)


def _t(h, m, s=0):
    return datetime(2026, 10, 15, h, m, s, tzinfo=_IST)


def _position(wh, opp="OPP-1", status="OPEN"):
    # Costs are priced per exchange now, so the opportunity must say which venue
    # the contracts trade on — as it always does in production.
    from app.services.snapback_instrument_identity import identity_from_instrument

    wh.record_opportunity(
        opportunity_id=opp, symbol="NIFTY", signal_type="SNAPBACK_FADE_UP",
        spot_price=24500.0, source="PROSPECTIVE_PAPER",
        identity=identity_from_instrument(
            {"tradingsymbol": "NIFTY 50", "instrument_token": 256265,
             "exchange": "NSE", "name": "NIFTY"},
            canonical_symbol="NIFTY",
        ),
    )
    wh.save_paper_position(
        opportunity_id=opp, symbol="NIFTY", option_symbol="NIFTY26OCT25000PE",
        option_qty=25, option_entry_price=100.0, option_expiry="2026-10-29",
        option_strike=25000.0, futures_symbol="NIFTY26OCTFUT", futures_lot_size=25,
        current_futures_lots=1, avg_futures_entry_price=24500.0,
        realized_futures_pnl=0.0, entry_spot=24500.0,
        entry_timestamp=_t(9, 20).isoformat(), entry_dte=45, entry_iv=0.2,
        causal_beta=1.0,
    )
    if status == "EXIT_PENDING":
        wh.set_paper_position_pending_exit(
            opportunity_id=opp, pending_exit_reason="PREMIUM_STOP",
            pending_exit_option_bid=60.0, pending_exit_ts=_t(11, 0).isoformat(),
        )
    return opp


def _mark(wh, opp, leg, at, *, accepted=True, bid=110.0, event_id=None):
    wh.record_quote_quality_event(
        event_id=event_id or f"QE-{opp}-{leg}-{at.isoformat()}",
        opportunity_id=opp, phase="EOD_MARK", leg=leg,
        contract_id="NIFTY26OCT25000PE" if leg == "OPTION" else "NIFTY26OCTFUT",
        required_for_economics=1, quote_present=1, bid=bid, ask=bid + 2.0,
        age_ms=100, accepted=1 if accepted else 0,
        reason_codes="" if accepted else "stale_quote_age_9000ms",
        provider_timestamp=at.isoformat(), symbol="NIFTY",
    )


def test_the_latest_qualifying_pre_close_mark_is_selected(warehouse):
    opp = _position(warehouse)
    _mark(warehouse, opp, "OPTION", _t(15, 29, 15), bid=110.0)
    _mark(warehouse, opp, "OPTION", _t(15, 29, 47), bid=112.0)
    _mark(warehouse, opp, "FUTURES", _t(15, 29, 50), bid=24600.0)

    marks = select_close_marks(warehouse, opportunity_id=opp, session_date=SESSION)

    assert marks.option is not None
    assert float(marks.option["bid"]) == pytest.approx(112.0)
    assert marks.futures is not None
    assert marks.complete is True


def test_a_post_close_timestamp_cannot_be_the_close_mark(warehouse):
    opp = _position(warehouse)
    _mark(warehouse, opp, "OPTION", _t(15, 30, 4), bid=120.0)
    _mark(warehouse, opp, "FUTURES", _t(15, 30, 4), bid=24600.0)

    marks = select_close_marks(warehouse, opportunity_id=opp, session_date=SESSION)

    assert marks.option is None
    assert marks.complete is False


def test_a_late_received_but_pre_close_stamped_mark_qualifies(warehouse):
    """Received at 15:30:04, stamped 15:29:58 — provider time decides."""
    opp = _position(warehouse)
    _mark(warehouse, opp, "OPTION", _t(15, 29, 58), bid=113.0)
    _mark(warehouse, opp, "FUTURES", _t(15, 29, 58), bid=24600.0)

    marks = select_close_marks(warehouse, opportunity_id=opp, session_date=SESSION)

    assert marks.complete is True
    assert float(marks.option["bid"]) == pytest.approx(113.0)


def test_a_rejected_observation_cannot_become_the_close_mark(warehouse):
    opp = _position(warehouse)
    _mark(warehouse, opp, "OPTION", _t(15, 29, 40), accepted=False, bid=999.0)
    _mark(warehouse, opp, "FUTURES", _t(15, 29, 40), bid=24600.0)

    marks = select_close_marks(warehouse, opportunity_id=opp, session_date=SESSION)

    assert marks.option is None
    assert marks.complete is False


def test_an_open_position_needs_both_legs(warehouse):
    opp = _position(warehouse)
    _mark(warehouse, opp, "OPTION", _t(15, 29, 30))

    result = finalize_eod_session(
        cfg=SnapbackConfig(), warehouse=warehouse, session_date=SESSION,
        finalized_at=_t(15, 30, 10),
    )

    assert result.status == "FAILED"
    assert any("missing_futures_close_mark" in code for code in result.gap_codes)


def test_a_complete_session_finalizes_once(warehouse):
    opp = _position(warehouse)
    _mark(warehouse, opp, "OPTION", _t(15, 29, 45))
    _mark(warehouse, opp, "FUTURES", _t(15, 29, 45), bid=24600.0)

    first = finalize_eod_session(
        cfg=SnapbackConfig(), warehouse=warehouse, session_date=SESSION,
        finalized_at=_t(15, 30, 10),
    )
    second = finalize_eod_session(
        cfg=SnapbackConfig(), warehouse=warehouse, session_date=SESSION,
        finalized_at=_t(15, 32, 0),
    )

    assert first.status == "COMPLETE"
    assert second.status in ("COMPLETE", "ALREADY_COMPLETE")

    marks = warehouse.get_records_by_table("daily_mtm", opportunity_id=opp)
    assert len(marks) == 1


def test_a_restart_after_close_finalizes_from_persisted_evidence(warehouse):
    opp = _position(warehouse)
    _mark(warehouse, opp, "OPTION", _t(15, 29, 20))
    _mark(warehouse, opp, "FUTURES", _t(15, 29, 20), bid=24600.0)

    # The process was not running at 15:30; it comes back at 15:31.
    result = finalize_eod_session(
        cfg=SnapbackConfig(), warehouse=warehouse, session_date=SESSION,
        finalized_at=_t(15, 31, 0),
    )

    assert result.status == "COMPLETE"
    assert len(warehouse.get_records_by_table("daily_mtm", opportunity_id=opp)) == 1


def test_a_restart_with_no_persisted_marks_is_an_evidence_gap(warehouse):
    _position(warehouse)

    result = finalize_eod_session(
        cfg=SnapbackConfig(), warehouse=warehouse, session_date=SESSION,
        finalized_at=_t(15, 31, 0),
    )

    assert result.status == "FAILED"
    assert EOD_EVIDENCE_GAP in result.gap_codes or any(
        "missing_option_close_mark" in c for c in result.gap_codes
    )
    assert warehouse.get_records_by_table("daily_mtm") == []


def test_a_session_with_no_open_positions_completes_cleanly(warehouse):
    result = finalize_eod_session(
        cfg=SnapbackConfig(), warehouse=warehouse, session_date=SESSION,
        finalized_at=_t(15, 30, 10),
    )

    assert result.status == "COMPLETE"
    assert result.gap_codes == []
    assert result.positions_finalized == 0


def test_exit_pending_is_not_marked_at_close(warehouse):
    opp = _position(warehouse, status="EXIT_PENDING")

    result = finalize_eod_session(
        cfg=SnapbackConfig(), warehouse=warehouse, session_date=SESSION,
        finalized_at=_t(15, 30, 10),
    )

    # Its lifecycle was already resolved intraday; it is not an open mark requirement.
    assert result.status == "COMPLETE"
    assert warehouse.get_records_by_table("daily_mtm", opportunity_id=opp) == []
    assert warehouse.get_paper_position(opp)["status"] == "EXIT_PENDING"


def test_the_observation_window_is_declared_in_policy():
    from app.engines.snapback.policy import EXECUTION_POLICY

    assert EXECUTION_POLICY.eod_observation_start == "15:29:00"
    assert EXECUTION_POLICY.eod_observation_end == "15:30:00"


def test_finalization_records_the_phase_on_the_session(warehouse):
    from app.services.snapback_session_ledger import session_record



    _position(warehouse)

    finalize_eod_session(
        cfg=SnapbackConfig(), warehouse=warehouse, session_date=SESSION,
        finalized_at=_t(15, 31, 0),
    )

    row = session_record(warehouse, SESSION.isoformat()) or {}
    assert row.get("eod_phase_status") == "FAILED"
