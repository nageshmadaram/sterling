"""Only rows written by the expected build, flagged authoritative, count.

This is the cross-cutting check: wrong build, wrong source, or a cleared flag must all
keep a row out of the authoritative sample, wherever it is read from.
"""

from __future__ import annotations

import pytest

from app.services.snapback_authority import (
    AUTHORITATIVE_SOURCE,
    CATCHUP_SOURCE,
    SHADOW_SOURCE,
    filter_authoritative,
    is_authoritative_row,
)


def _row(**over):
    base = {
        "opportunity_id": "OPP-1",
        "authoritative": 1,
        "source": AUTHORITATIVE_SOURCE,
        "runtime_build_sha": "build-a",
    }
    base.update(over)
    return base


def test_matching_build_and_flag_is_authoritative():
    assert is_authoritative_row(_row(), expected_build_sha="build-a") is True


def test_wrong_build_is_excluded():
    assert is_authoritative_row(_row(runtime_build_sha="build-b"), expected_build_sha="build-a") is False


def test_cleared_flag_is_excluded():
    assert is_authoritative_row(_row(authoritative=0), expected_build_sha="build-a") is False


@pytest.mark.parametrize("source", [CATCHUP_SOURCE, SHADOW_SOURCE])
def test_replay_sources_are_excluded(source):
    assert is_authoritative_row(_row(source=source), expected_build_sha="build-a") is False


def test_unparseable_flag_fails_closed():
    assert is_authoritative_row(_row(authoritative="maybe"), expected_build_sha="build-a") is False


def test_filter_keeps_only_the_admissible_rows():
    rows = [
        _row(opportunity_id="A"),
        _row(opportunity_id="B", authoritative=0),
        _row(opportunity_id="C", source=SHADOW_SOURCE),
        _row(opportunity_id="D", runtime_build_sha="other"),
    ]

    kept = filter_authoritative(rows, expected_build_sha="build-a")

    assert [r["opportunity_id"] for r in kept] == ["A"]


def test_warehouse_stamps_build_and_authority(tmp_path):
    from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

    wh = SnapbackObservationWarehouse(db_path=str(tmp_path / "w.db"))
    wh.record_opportunity(
        opportunity_id="OPP-1", symbol="NIFTY", signal_type="SNAPBACK_FADE_UP",
        spot_price=24500.0, trend="BEARISH",
    )

    row = wh.get_records_by_table("opportunities")[0]

    assert row["runtime_build_sha"]
    assert row["runtime_build_sha"] != "UNKNOWN"
    assert row["authoritative"] == 1
