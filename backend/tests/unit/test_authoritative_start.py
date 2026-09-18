"""The fresh-sample contract: zero means zero, and unknown never means zero."""
from __future__ import annotations

import pytest

from app.core.authoritative_start import (
    EVIDENCE_SCHEMA_VERSION,
    REQUIRED_ROW_FIELDS,
    audit_rows,
    evaluate_authoritative_start,
    missing_fields,
    render_start,
)

SHA = "a" * 40
TAG = "sterling-family-runtime-1.0"


def _complete_row(**overrides):
    row = {
        "authoritative": 1,
        "outcome_id": "o-1",
        "strategy_id": "snapback",
        "strategy_version": "1.6.0",
        "mode": "swing",
        "mode_version": "1",
        "lane_key": "snapback:swing",
        "identity_hash": "deadbeef",
        "runtime_build_sha": SHA,
        "release_tag": TAG,
        "config_hash": "c0ffee",
        "rule_hash": "r00l",
        "universe_hash": "u1",
        "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
        "evidence_class": "paper",
    }
    row.update(overrides)
    return row


class TestTheThirteenFields:
    def test_the_required_set_is_exactly_the_specified_thirteen(self):
        assert len(REQUIRED_ROW_FIELDS) == 13
        assert set(REQUIRED_ROW_FIELDS) == {
            "strategy_id", "strategy_version", "mode", "mode_version", "lane_key",
            "identity_hash", "runtime_sha", "release_tag", "config_hash",
            "rule_hash", "universe_hash", "evidence_schema_version", "evidence_class",
        }

    def test_a_complete_row_is_complete(self):
        assert missing_fields(_complete_row()) == ()

    def test_the_warehouse_spelling_of_the_runtime_sha_is_accepted(self):
        """The column is `runtime_build_sha`; the rule is about the row."""
        row = _complete_row()
        assert "runtime_sha" not in row
        assert missing_fields(row) == ()

    @pytest.mark.parametrize("field", REQUIRED_ROW_FIELDS)
    def test_each_field_is_actually_required(self, field):
        column = "runtime_build_sha" if field == "runtime_sha" else field
        assert missing_fields(_complete_row(**{column: ""})) == (field,)

    def test_whitespace_is_not_a_value(self):
        assert missing_fields(_complete_row(identity_hash="   ")) == ("identity_hash",)


class TestTheRowAudit:
    def test_it_counts_by_field_so_a_missing_writer_is_visible(self):
        rows = [_complete_row(outcome_id=f"o-{i}", universe_hash="") for i in range(4)]
        rows.append(_complete_row(outcome_id="o-ok"))
        audit = audit_rows(rows)
        assert audit.total == 5
        assert audit.complete == 1
        assert audit.missing_by_field == {"universe_hash": 4}
        assert audit.ok is False

    def test_non_authoritative_rows_are_not_audited(self):
        audit = audit_rows([_complete_row(authoritative=0, lane_key="")])
        assert audit.total == 0
        # Nothing to audit is not a pass — `ok` requires rows that passed.
        assert audit.ok is False

    def test_examples_are_capped_but_present(self):
        rows = [_complete_row(outcome_id=f"o-{i}", rule_hash="") for i in range(20)]
        audit = audit_rows(rows, examples=3)
        assert len(audit.incomplete_examples) == 3
        assert audit.missing_by_field == {"rule_hash": 20}


class TestTheStartConditions:
    def test_all_zero_with_an_exact_release_is_a_clean_start(self):
        start = evaluate_authoritative_start(
            lane_sessions=0, authoritative_trades=0, unresolved_exposure=0,
            identity_drift=0, release_tag=TAG, runtime_sha=SHA)
        assert start.started_clean is True
        assert "fresh authoritative sample" in render_start(start)

    @pytest.mark.parametrize("name", [
        "lane_sessions", "authoritative_trades", "unresolved_exposure", "identity_drift",
    ])
    def test_a_carried_over_count_is_not_a_fresh_start(self, name):
        counts = dict(lane_sessions=0, authoritative_trades=0,
                      unresolved_exposure=0, identity_drift=0)
        counts[name] = 7
        start = evaluate_authoritative_start(release_tag=TAG, runtime_sha=SHA, **counts)
        assert start.started_clean is False
        assert start.unknowns == ()
        assert name in render_start(start)

    @pytest.mark.parametrize("name", [
        "lane_sessions", "authoritative_trades", "unresolved_exposure", "identity_drift",
    ])
    def test_an_unreadable_count_is_unknown_and_never_zero(self, name):
        counts = dict(lane_sessions=0, authoritative_trades=0,
                      unresolved_exposure=0, identity_drift=0)
        counts[name] = None
        start = evaluate_authoritative_start(release_tag=TAG, runtime_sha=SHA, **counts)
        assert start.started_clean is False
        assert [c.name for c in start.unknowns] == [name]
        assert "Unknown is not zero" in render_start(start)

    def test_a_missing_release_tag_blocks_the_start(self):
        start = evaluate_authoritative_start(
            lane_sessions=0, authoritative_trades=0, unresolved_exposure=0,
            identity_drift=0, release_tag="", runtime_sha=SHA)
        assert start.started_clean is False

    def test_a_short_runtime_sha_is_not_an_exact_commit(self):
        start = evaluate_authoritative_start(
            lane_sessions=0, authoritative_trades=0, unresolved_exposure=0,
            identity_drift=0, release_tag=TAG, runtime_sha="8688e3a3")
        assert start.started_clean is False


class TestTheLiveReading:
    def test_a_missing_database_reads_as_unknown_not_as_empty(self, tmp_path, monkeypatch):
        from app.services import authoritative_start_report as report

        monkeypatch.setattr(report, "_unresolved_exposure", lambda: 0)
        monkeypatch.setattr(report, "_identity_drift", lambda: 0)
        start = report.authoritative_start_state(tmp_path / "nowhere.db")
        names = {c.name for c in start.unknowns}
        assert {"lane_sessions", "authoritative_trades"} <= names
        assert start.started_clean is False

    def test_an_empty_store_with_a_frozen_release_reads_as_a_clean_start(
            self, tmp_path, monkeypatch):
        import sqlite3

        from app.services import authoritative_start_report as report

        path = tmp_path / "obs.db"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE prospective_sessions (session_date TEXT)")
            conn.execute("CREATE TABLE outcomes (outcome_id TEXT, authoritative INTEGER)")

        monkeypatch.setattr(report, "_unresolved_exposure", lambda: 0)
        monkeypatch.setattr(report, "_identity_drift", lambda: 0)
        monkeypatch.setattr("app.core.release_manifest.release_tag", lambda: TAG)
        monkeypatch.setattr("app.core.release_manifest.runtime_sha", lambda: SHA)

        start = report.authoritative_start_state(path)
        assert start.started_clean is True
