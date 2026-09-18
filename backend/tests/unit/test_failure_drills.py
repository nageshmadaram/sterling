"""Twelve drills, twelve records. "Drills passed" is not a record.

The rules under test: a drill with no record is UNKNOWN; a PASS must name who
ran it and say what the system did; records belong to one SHA; and the release
gate is derived from the register rather than attestable in one line.
"""
from __future__ import annotations

import pytest

from app.core.failure_drills import (
    DRILLS,
    DRILLS_BY_KEY,
    DrillRegister,
    drill_report,
    render_drills,
)

SHA = "a" * 40
OTHER_SHA = "b" * 40


@pytest.fixture()
def register(tmp_path):
    return DrillRegister(tmp_path)


def _record_all(register, sha=SHA):
    for drill in DRILLS:
        register.record(sha, drill.key, "PASS", observed_by="operator",
                        observed=f"behaved as required: {drill.required_outcome}")


class TestTheDeclaredDrills:
    def test_all_twelve_scenarios_are_declared(self):
        assert len(DRILLS) == 12
        assert len({d.key for d in DRILLS}) == 12

    def test_every_drill_states_its_required_outcome(self):
        for drill in DRILLS:
            assert drill.required_outcome.strip(), drill.key

    @pytest.mark.parametrize("key,expected", [
        ("power_loss", "RECOVERY_REQUIRED"),
        ("submit_ack_lost", "SUBMITTED_UNKNOWN"),
        ("missing_protection", "SAFE_MODE"),
        ("backup_corruption", "restore-check fails"),
    ])
    def test_the_outcomes_match_the_specification(self, key, expected):
        assert expected in DRILLS_BY_KEY[key].required_outcome


class TestRecording:
    def test_an_unrecorded_drill_is_unknown(self, register):
        report = drill_report(SHA, register=register)
        assert len(report.unknowns) == 12
        assert report.all_passed is False

    def test_a_pass_must_name_who_ran_it(self, register):
        with pytest.raises(ValueError, match="who ran the drill"):
            register.record(SHA, "disk_full", "PASS", observed_by="", observed="blocked")

    def test_a_pass_must_say_what_actually_happened(self, register):
        with pytest.raises(ValueError, match="what the system actually did"):
            register.record(SHA, "disk_full", "PASS", observed_by="operator", observed="")

    def test_a_failure_needs_neither_but_is_still_recorded(self, register):
        result = register.record(SHA, "disk_full", "FAIL", observed_by="", observed="")
        assert result.status == "FAIL"
        assert drill_report(SHA, register=register).failures[0].key == "disk_full"

    def test_an_unknown_drill_key_is_refused(self, register):
        with pytest.raises(KeyError):
            register.record(SHA, "invented_drill", "PASS",
                            observed_by="operator", observed="something")

    def test_records_belong_to_one_build(self, register):
        _record_all(register, SHA)
        assert drill_report(SHA, register=register).all_passed is True
        # A drill rehearsed on another build says nothing about this one: the
        # recovery path may be exactly what changed.
        assert drill_report(OTHER_SHA, register=register).all_passed is False

    def test_all_twelve_passing_is_the_only_way_to_pass(self, register):
        _record_all(register)
        register.record(SHA, "clock_anomaly", "UNKNOWN", observed_by="", observed="")
        report = drill_report(SHA, register=register)
        assert report.all_passed is False
        assert "A drill with no record is not a drill that passed." in render_drills(report)


class TestTheReleaseGate:
    def test_the_gate_is_unknown_until_every_drill_is_recorded(self, register, monkeypatch):
        from app.services import release_certification as cert

        monkeypatch.setattr("app.core.failure_drills.DrillRegister",
                            lambda *a, **k: register)
        gate = cert._failure_drills_gate(SHA)
        assert gate.status == "UNKNOWN"
        assert "12 of 12" in gate.detail

    def test_the_gate_passes_only_when_the_register_does(self, register, monkeypatch):
        from app.services import release_certification as cert

        monkeypatch.setattr("app.core.failure_drills.DrillRegister",
                            lambda *a, **k: register)
        _record_all(register)
        gate = cert._failure_drills_gate(SHA)
        assert gate.status == "PASS"
        assert gate.attested_by == "operator"

    def test_one_failed_drill_fails_the_gate(self, register, monkeypatch):
        from app.services import release_certification as cert

        monkeypatch.setattr("app.core.failure_drills.DrillRegister",
                            lambda *a, **k: register)
        _record_all(register)
        register.record(SHA, "stale_feed", "FAIL", observed_by="operator",
                        observed="an entry was admitted against a 40-minute-old quote")
        gate = cert._failure_drills_gate(SHA)
        assert gate.status == "FAIL"
        assert "stale_feed" in gate.detail

    def test_the_gate_cannot_be_attested_in_one_line(self, tmp_path):
        from app.services.release_certification import CertificationStore

        store = CertificationStore(tmp_path)
        with pytest.raises(ValueError, match="derived from its own register"):
            store.attest(SHA, "failure_drills", "PASS", attested_by="operator")
