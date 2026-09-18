"""A static IP is what the host answered from, not what it was configured to be."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.deployment_identity import (
    MAX_OBSERVATION_AGE,
    DeploymentIdentityStore,
    verify_deployment_identity,
)

IP = "203.0.113.7"


@pytest.fixture()
def store(tmp_path):
    return DeploymentIdentityStore(tmp_path / "deployment_identity.json")


def _iso(when):
    return when.isoformat()


class TestRecording:
    def test_an_observation_needs_a_host_and_an_address(self, store):
        with pytest.raises(ValueError):
            store.record(host_id="", outbound_ip=IP)
        with pytest.raises(ValueError):
            store.record(host_id="family-nuc", outbound_ip="")

    def test_history_is_append_only(self, store):
        store.record(host_id="family-nuc", outbound_ip="198.51.100.2")
        store.record(host_id="family-nuc", outbound_ip=IP)
        history = store.read()
        # "the address changed on the 3rd" is invisible in a file that only
        # holds the current value.
        assert [o.outbound_ip for o in history] == ["198.51.100.2", IP]
        assert store.latest().outbound_ip == IP


class TestTheVerdict:
    def test_no_registered_address_is_unknown(self, store):
        verdict = verify_deployment_identity(expected_ip="", store=store)
        assert verdict.verified is None

    def test_no_observation_is_unknown_not_a_match(self, store):
        verdict = verify_deployment_identity(expected_ip=IP, store=store)
        assert verdict.verified is None
        assert "no deployment observation" in verdict.reason

    def test_a_matching_recent_observation_verifies(self, store):
        store.record(host_id="family-nuc", outbound_ip=IP)
        assert verify_deployment_identity(expected_ip=IP, store=store).verified is True

    def test_a_different_address_blocks(self, store):
        store.record(host_id="family-nuc", outbound_ip="198.51.100.9")
        verdict = verify_deployment_identity(expected_ip=IP, store=store)
        assert verdict.verified is False
        assert "198.51.100.9" in verdict.reason and IP in verdict.reason

    def test_a_stale_observation_is_unknown_not_verified(self, store):
        old = datetime.now(timezone.utc) - MAX_OBSERVATION_AGE - timedelta(days=1)
        store.record(host_id="family-nuc", outbound_ip=IP, observed_at=_iso(old))
        verdict = verify_deployment_identity(expected_ip=IP, store=store)
        assert verdict.verified is None
        assert "not a current fact" in verdict.reason

    def test_a_change_that_now_matches_is_still_reported(self, store):
        store.record(host_id="family-nuc", outbound_ip="198.51.100.9")
        store.record(host_id="family-nuc", outbound_ip=IP)
        verdict = verify_deployment_identity(expected_ip=IP, store=store)
        # It matches now, but the broker's allowlist may have been wrong in
        # between, and an operator should be told.
        assert verdict.verified is True
        assert verdict.changed_from == "198.51.100.9"

    def test_an_unreadable_timestamp_is_unknown(self, store):
        store.record(host_id="family-nuc", outbound_ip=IP, observed_at="not-a-date")
        assert verify_deployment_identity(expected_ip=IP, store=store).verified is None

    def test_a_corrupt_file_is_no_observation_rather_than_a_crash(self, tmp_path):
        path = tmp_path / "deployment_identity.json"
        path.write_text("{ not json", encoding="utf-8")
        assert DeploymentIdentityStore(path).read() == []


class TestTheDoctorSurfacesIt:
    def test_the_check_is_present(self):
        from app.core.operator_report import lane_doctor_checks

        names = {c.name for c in lane_doctor_checks()}
        assert {"deployment_identity", "broker_session", "market_freshness",
                "backup_age", "restore_check", "safe_mode_state",
                "execution_control_state"} <= names
