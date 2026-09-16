"""Only a signal from the latest closed eligible session, recorded after the dataset
start, may enter the authoritative sample.

evaluate_symbol() searches the last three closed sessions as a board catch-up. That is
useful on screen and poisonous in evidence: a 2026-09-11 signal surfaced on 2026-09-16
and was recorded as PROSPECTIVE_PAPER.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.services.snapback_authority import (

    CATCHUP_SOURCE,
    AUTHORITATIVE_SOURCE,
    classify_signal_authority,
)

_IST = timezone(timedelta(hours=5, minutes=30))

DATASET_START = datetime(2026, 9, 16, 10, 0, tzinfo=timezone.utc)


def _ms(d: date, hour=15, minute=30) -> int:
    return int(datetime(d.year, d.month, d.day, hour, minute, tzinfo=_IST).timestamp() * 1000)


def test_latest_closed_session_signal_is_authoritative():
    verdict = classify_signal_authority(
        signal_timestamp_ms=_ms(date(2026, 9, 17)),
        latest_closed_session=date(2026, 9, 17),
        dataset_start=DATASET_START,
    )

    assert verdict.authoritative is True
    assert verdict.source == AUTHORITATIVE_SOURCE
    assert verdict.reasons == []


def test_aged_catchup_signal_is_never_authoritative():
    verdict = classify_signal_authority(
        signal_timestamp_ms=_ms(date(2026, 9, 11)),
        latest_closed_session=date(2026, 9, 16),
        dataset_start=DATASET_START,
    )

    assert verdict.authoritative is False
    assert verdict.source == CATCHUP_SOURCE
    assert "stale_catchup_session" in verdict.reasons


def test_signal_older_than_the_dataset_start_is_not_authoritative():
    # Right session, but it fired before this dataset began.
    verdict = classify_signal_authority(
        signal_timestamp_ms=_ms(date(2026, 9, 16), hour=9, minute=30),
        latest_closed_session=date(2026, 9, 16),
        dataset_start=DATASET_START,
    )

    assert verdict.authoritative is False
    assert "before_dataset_start" in verdict.reasons


def test_future_dated_signal_is_refused():
    verdict = classify_signal_authority(
        signal_timestamp_ms=_ms(date(2026, 9, 18)),
        latest_closed_session=date(2026, 9, 17),
        dataset_start=DATASET_START,
    )

    assert verdict.authoritative is False
    assert "future_session" in verdict.reasons


def test_unknown_timestamp_fails_closed():
    verdict = classify_signal_authority(
        signal_timestamp_ms=0,
        latest_closed_session=date(2026, 9, 17),
        dataset_start=DATASET_START,
    )

    assert verdict.authoritative is False
    assert "unknown_signal_timestamp" in verdict.reasons


def test_catchup_rows_are_recorded_with_a_non_authoritative_source(monkeypatch, tmp_path):
    """The collector must persist a catch-up row as replay, not as paper evidence."""
    import os

    from app.engines.snapback import SnapbackConfig, SnapbackSignal
    from app.services.snapback_prospective_collector import (
        SnapbackObservationWarehouse,
        SnapbackProspectiveCollector,
    )

    wh = SnapbackObservationWarehouse(db_path=str(tmp_path / "w.db"))
    collector = SnapbackProspectiveCollector(warehouse=wh)

    aged = SnapbackSignal(
        symbol="LAURUSLABS",
        side="fade_up",
        direction="BEARISH",
        option_type="PE",
        timestamp_ms=_ms(date(2026, 9, 11)),
        entry=1969.0,
        mean_target=1879.0,
        stretch=1.89,
        atr=50.0,
        realized_vol=0.2,
        assumed_iv=0.25,
        level=1900.0,
        strength="MODERATE",
    )

    collector.record_signal_at_close(aged, SnapbackConfig(), source=CATCHUP_SOURCE)

    rows = wh.get_records_by_table("opportunities")
    assert len(rows) == 1
    assert rows[0]["source"] == CATCHUP_SOURCE


def test_gate_input_excludes_non_authoritative_rows():
    from study.snapback_forward_gate import build_gate_inputs

    records = {
        "outcomes": [
            {
                "actual_total_pnl": 100.0,
                "actual_option_pnl": 100.0,
                "actual_futures_pnl": 0.0,
                "actual_costs": 5.0,
                "entry_ts": "2026-09-17T09:20:00+05:30",
                "authoritative": 1, "source": "PROSPECTIVE_PAPER",
                "source": AUTHORITATIVE_SOURCE,
            },
            {
                "actual_total_pnl": 9999.0,
                "actual_option_pnl": 9999.0,
                "actual_futures_pnl": 0.0,
                "actual_costs": 5.0,
                "entry_ts": "2026-09-11T09:20:00+05:30",
                "authoritative": 0,
                "source": CATCHUP_SOURCE,
            },
        ],
        "paper_positions": [],
        "daily_mtm": [],
        "option_quotes": [],
    }

    inputs = build_gate_inputs(records=records)

    assert inputs["trade_pnls"] == [100.0]


def test_gate_input_excludes_rows_from_a_different_build():
    from study.snapback_forward_gate import build_gate_inputs

# Authority is now explicit at the writer, so fixtures must declare it too: a row
# with no `source` is deliberately not evidence any more.
_AUTH_FIXTURE = {"source": "PROSPECTIVE_PAPER", "authoritative": 1}


def _auth(row: dict, build: str = "") -> dict:
    """Stamp a fixture row with a complete, self-consistent authority."""
    out = dict(_AUTH_FIXTURE)
    if build:
        out["runtime_build_sha"] = build
    out.update(row)
    return out


    records = {
        "outcomes": [
            {
                "actual_total_pnl": 100.0,
                "actual_option_pnl": 100.0,
                "actual_futures_pnl": 0.0,
                "actual_costs": 5.0,
                "entry_ts": "2026-09-17T09:20:00+05:30",
                "authoritative": 1, "source": "PROSPECTIVE_PAPER",
                "runtime_build_sha": "aaaa",
            },
            {
                "actual_total_pnl": 500.0,
                "actual_option_pnl": 500.0,
                "actual_futures_pnl": 0.0,
                "actual_costs": 5.0,
                "entry_ts": "2026-09-18T09:20:00+05:30",
                "authoritative": 1, "source": "PROSPECTIVE_PAPER",
                "runtime_build_sha": "bbbb",
            },
        ],
        "paper_positions": [],
        "daily_mtm": [],
        "option_quotes": [],
    }

    inputs = build_gate_inputs(records=records, expected_build_sha="aaaa")

    assert inputs["trade_pnls"] == [100.0]
