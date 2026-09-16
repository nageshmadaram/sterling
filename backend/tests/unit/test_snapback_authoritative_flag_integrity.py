"""P0-EVIDENCE: the authoritative column must not lie.

Found by observation, not by tests: on a brand-new 1.3 evidence database the
runner wrote a LAURUSLABS signal whose bar closed on 2026-09-11 — five sessions
old — as `source = LIVE_CATCHUP_REPLAY` with `authoritative = 1`.

Nothing was miscounted, because is_authoritative_row() also inspects the source.
But the column is the thing the schema exists to record, and it said the opposite
of the source beside it. Two readers of the same row could disagree, and the one
that trusts the column counts replayed history as prospective evidence.

The cause is a fail-open default: the writer never passed `authoritative`, and
the column is declared NOT NULL DEFAULT 1. Omission produced authority.
"""

from __future__ import annotations

import pytest

from app.services.snapback_authority import (
    AUTHORITATIVE_SOURCE, CATCHUP_SOURCE, NON_AUTHORITATIVE_SOURCES, SHADOW_SOURCE,
    is_authoritative_row,
)


@pytest.fixture
def warehouse(tmp_path):
    from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

    return SnapbackObservationWarehouse(db_path=str(tmp_path / "w.db"))


def _opportunity(**over):
    base = dict(
        opportunity_id="OPP-1", symbol="LAURUSLABS",
        signal_type="SNAPBACK_FADE_UP", spot_price=1969.0, signal_spot=1969.0,
        mean_target=1879.0, breakout_level=1955.0, stretch_atr=1.89,
        signal_iv=0.31, signal_rv=0.25, signal_side="fade_up",
        signal_timestamp="2026-09-11T10:00:00+00:00",
    )
    base.update(over)
    return base


# ------------------------------------------------ the column follows the source


@pytest.mark.parametrize("source", sorted(NON_AUTHORITATIVE_SOURCES))
def test_a_replay_source_is_never_stored_as_authoritative(warehouse, source):
    warehouse.record_opportunity(**_opportunity(source=source))

    row = warehouse.get_records_by_table("opportunities", opportunity_id="OPP-1")[0]

    assert int(row["authoritative"]) == 0, (
        f"{source} row claims authority; omission must not confer it"
    )


def test_a_prospective_source_remains_authoritative(warehouse):
    warehouse.record_opportunity(**_opportunity(source=AUTHORITATIVE_SOURCE))

    row = warehouse.get_records_by_table("opportunities", opportunity_id="OPP-1")[0]

    assert int(row["authoritative"]) == 1


def test_an_explicit_zero_is_respected_even_for_a_prospective_source(warehouse):
    """A caller that knows better than the source label must be able to say so."""
    warehouse.record_opportunity(
        **_opportunity(source=AUTHORITATIVE_SOURCE, authoritative=0)
    )

    row = warehouse.get_records_by_table("opportunities", opportunity_id="OPP-1")[0]

    assert int(row["authoritative"]) == 0


def test_a_caller_cannot_force_authority_onto_a_replay_source(warehouse):
    """The source is the stronger statement. If the two disagree, authority loses,
    because the cost of wrongly including replayed history is unbounded."""
    warehouse.record_opportunity(
        **_opportunity(source=CATCHUP_SOURCE, authoritative=1)
    )

    row = warehouse.get_records_by_table("opportunities", opportunity_id="OPP-1")[0]

    assert int(row["authoritative"]) == 0


# --------------------------------------------------------- the collector path


def test_the_collector_stores_a_catchup_signal_as_non_authoritative(warehouse, monkeypatch):
    """The path that actually produced the bad row."""
    from app.services.snapback_prospective_collector import SnapbackProspectiveCollector

    class _Signal:
        symbol = "LAURUSLABS"
        side = "fade_up"
        entry = 1969.0
        mean_target = 1879.0
        level = 1955.0
        stretch = 1.89
        assumed_iv = 0.31
        realized_vol = 0.25
        timestamp_ms = 1757584800000  # 2026-09-11
        atr = 20.0

    collector = SnapbackProspectiveCollector(warehouse=warehouse)
    monkeypatch.setattr(
        "app.services.snapback_prospective_collector.verify_frozen_config",
        lambda _cfg: True,
    )

    collector.record_signal_at_close(
        _Signal(), object(), source=CATCHUP_SOURCE, identity=None,
    )

    rows = warehouse.get_records_by_table("opportunities")
    assert rows, "nothing recorded"
    assert int(rows[0]["authoritative"]) == 0
    assert rows[0]["source"] == CATCHUP_SOURCE


# ------------------------------------------------------- consistency checking


def test_a_contradictory_row_is_not_counted():
    """Defence in depth: even if such a row exists from before this fix."""
    assert is_authoritative_row(
        {"source": CATCHUP_SOURCE, "authoritative": 1}
    ) is False


def test_the_warehouse_reports_contradictory_rows(warehouse):
    """An operator must be able to ask whether any stored row lies."""
    import sqlite3

    warehouse.record_opportunity(**_opportunity(source=AUTHORITATIVE_SOURCE))
    # Write a contradiction directly, as the old code path would have.
    conn = sqlite3.connect(warehouse.db_path)
    with conn:
        conn.execute(
            "UPDATE opportunities SET source = ?, authoritative = 1 "
            "WHERE opportunity_id = 'OPP-1'",
            (CATCHUP_SOURCE,),
        )
    conn.close()

    contradictions = warehouse.contradictory_authority_rows()

    assert contradictions, "a lying row was not reported"
    assert contradictions[0]["opportunity_id"] == "OPP-1"


def test_a_clean_database_reports_no_contradictions(warehouse):
    warehouse.record_opportunity(**_opportunity(source=AUTHORITATIVE_SOURCE))

    assert warehouse.contradictory_authority_rows() == []


# --------------------------------------------------------- the dataset start


def test_the_dataset_start_is_required_for_a_new_experiment(monkeypatch):
    """Without it, `before_dataset_start` can never fire, so the only thing
    keeping old signals out is the single-session recency check."""
    from app.services import snapback_preflight

    monkeypatch.delenv("STERLING_DATASET_START", raising=False)

    passed, details = snapback_preflight.check_dataset_start()

    assert passed is False
    assert "STERLING_DATASET_START" in str(details)


def test_a_configured_dataset_start_passes(monkeypatch):
    from app.services import snapback_preflight

    monkeypatch.setenv("STERLING_DATASET_START", "2026-09-17T00:00:00+05:30")

    passed, details = snapback_preflight.check_dataset_start()

    assert passed is True


def test_an_unparseable_dataset_start_fails(monkeypatch):
    from app.services import snapback_preflight

    monkeypatch.setenv("STERLING_DATASET_START", "not-a-date")

    passed, _ = snapback_preflight.check_dataset_start()

    assert passed is False


def test_preflight_includes_the_dataset_start_check():
    from app.services.snapback_preflight import _CHECK_ORDER

    assert any(code == "dataset_start" for code, _kwarg, _req in _CHECK_ORDER)
