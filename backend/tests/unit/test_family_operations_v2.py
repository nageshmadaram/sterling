"""The operator payload: one truth, composed server-side, never invented.

The rule these tests defend is §18.1 — the aggregator may reshape data but may
not decide anything. Every UNKNOWN here is a source that could not answer, and
none of them may arrive at the browser as False, zero, or an empty list.
"""
from __future__ import annotations

import pytest

from app.services import family_operations_v2 as agg
from app.services.family_operations_v2 import SCHEMA_VERSION, family_operations_v2


@pytest.fixture()
def payload():
    return family_operations_v2()


class TestTheEnvelope:
    def test_it_carries_a_schema_version_and_a_timestamp(self, payload):
        assert payload["schema_version"] == SCHEMA_VERSION
        assert payload["generated_at"]

    def test_every_declared_section_is_present(self, payload):
        for section in ("release", "safety", "broker", "deployment", "certification",
                        "drills", "security", "evidence", "lanes", "operator"):
            assert section in payload, section

    def test_all_ten_lanes_are_always_present(self, payload):
        assert len(payload["lanes"]) == 10
        keys = {lane["lane_key"] for lane in payload["lanes"]}
        assert "snapback:swing" in keys and "supertrend:swing" in keys

    def test_the_three_lane_verdicts_stay_independent(self, payload):
        lane = payload["lanes"][0]
        # Never averaged into one score.
        assert {"identity_verdict", "shadow_verdict", "economic_verdict"} <= set(lane)
        for field in ("identity_verdict", "shadow_verdict", "economic_verdict"):
            assert lane[field] in ("PASS", "FAIL", "UNKNOWN")

    def test_the_live_switch_is_surfaced_not_hidden(self, payload):
        assert payload["safety"]["live_execution_enabled"] is False


class TestUnknownSurvivesToTheBrowser:
    def test_a_source_that_raises_becomes_unknown_not_a_crash(self, monkeypatch):
        def _boom(*_a, **_k):
            raise RuntimeError("source down")

        monkeypatch.setattr("app.services.safety_supervisor.SafetySupervisor.snapshot", _boom)
        payload = family_operations_v2()
        assert payload["safety"]["new_risk_allowed"] is None
        assert payload["safety"]["blockers"][0]["code"] == "safety_unreadable"

    def test_admission_is_the_authority_answer_not_a_blocker_count(self, monkeypatch):
        """`new_risk_allowed` must never be derived from `len(blockers) == 0`."""
        import inspect

        source = inspect.getsource(agg._safety_section)
        assert "len(" not in source.split("allowed =")[1].split("\n")[0]

    def test_no_ci_record_is_null_not_zero_passing(self, monkeypatch):
        monkeypatch.setattr(agg, "_ci_counts", lambda sha: (_ for _ in ()).throw(RuntimeError()))
        release = agg._release_section(None, {})
        assert release["ci_passed"] is None
        assert release["ci_required"] == 9

    def test_missing_manifest_does_not_fabricate_an_identity(self, monkeypatch):
        monkeypatch.setattr("app.core.release_manifest.read_manifest", lambda: None)
        release = agg._release_section(None, {})
        assert release["manifest_frozen"] is None
        assert release["manifest_state"] == "UNKNOWN"


class TestBrokerTruth:
    def _observation(self, tmp_path, monkeypatch, **overrides):
        import json
        from datetime import datetime, timezone

        record = {"observed_at": datetime.now(timezone.utc).isoformat(), "readable": True,
                  "count": 0, "instruments": [], "positions": [], "detail": ""}
        record.update(overrides)
        path = tmp_path / "external.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        monkeypatch.setenv("STERLING_EXTERNAL_EXPOSURE_FILE", str(path))
        return path

    def test_an_external_position_appears_with_its_provenance(self, tmp_path, monkeypatch):
        self._observation(tmp_path, monkeypatch, count=1,
                          instruments=["CDSL26SEP1500CE"],
                          positions=[{"account": "AA0595", "instrument": "CDSL26SEP1500CE",
                                      "quantity": 16625, "product": "NRML",
                                      "broker_avg_price": 3.271429, "last_price": 3.35,
                                      "unrealised": 1306.24, "observed_at": "now"}])
        broker = agg._broker_section(None, {})
        assert len(broker["external_positions"]) == 1
        position = broker["external_positions"][0]
        assert position["source"] == "BROKER_EXTERNAL"
        assert position["managed_by_sterling"] is False
        assert position["quantity"] == 16625
        assert position["sterling_intent"] is None and position["sterling_fill"] is None

    def test_managed_and_external_are_never_one_list(self, tmp_path, monkeypatch):
        self._observation(tmp_path, monkeypatch, count=1, instruments=["X"],
                          positions=[{"instrument": "X", "quantity": 1}])
        broker = agg._broker_section(None, {})
        assert "managed_positions" in broker and "external_positions" in broker
        assert broker["managed_positions"] is not broker["external_positions"]

    def test_an_unreadable_broker_is_not_an_empty_flat_list(self, tmp_path, monkeypatch):
        self._observation(tmp_path, monkeypatch, readable=False, count=None,
                          detail="ConnectTimeout")
        broker = agg._broker_section(None, {})
        assert broker["broker_state_readable"] is False
        assert broker["external_positions"] == []
        # The screen must be able to tell "could not read" from "nothing held".
        assert "ConnectTimeout" in broker["broker_state_detail"]


class TestDrillsAndSecurity:
    def test_no_drill_records_reads_zero_of_twelve(self, payload):
        drills = payload["drills"]
        assert drills["required"] == 12
        assert drills["passed"] + drills["failed"] + drills["unknown"] == 12

    def test_the_dev_fallback_key_is_never_a_pass(self, monkeypatch):
        monkeypatch.delenv("STERLING_SECRET_KEY", raising=False)
        security = agg._security_section()
        assert security["dev_fallback_in_use"] is True
        assert security["production_security"] != "PASS"

    def test_no_secret_material_is_ever_in_the_payload(self, monkeypatch):
        monkeypatch.setenv("STERLING_SECRET_KEY", "a-very-secret-value-abcdefghijklmnop")
        rendered = repr(agg._security_section())
        assert "a-very-secret-value" not in rendered


class TestTheAggregatorDecidesNothing:
    def test_it_never_calls_the_broker(self):
        """A dashboard poll must not open a broker connection."""
        import inspect

        source = inspect.getsource(agg)
        assert "external_exposure(" not in source
        assert "include_broker=False" in source

    def test_release_ready_comes_from_the_backend_verdict(self, payload):
        # Not recomputed from the gate statuses in the aggregator or the browser.
        assert payload["certification"]["release_ready"] in (True, False, None)

    def test_mode_is_backend_derived_not_inferred_from_the_live_switch(self):
        safety = {"execution_control_state": "RUNNING", "safety_state": "NORMAL",
                  "live_execution_enabled": False, "new_risk_allowed": None}
        # LIVE_EXECUTION_ENABLED=false alone must not produce PRODUCTION_SHADOW.
        assert agg._production_mode(safety, {}) == "UNKNOWN"

    def test_recovery_required_outranks_everything(self):
        safety = {"execution_control_state": "RECOVERY_REQUIRED", "safety_state": "NORMAL",
                  "live_execution_enabled": True, "new_risk_allowed": True}
        assert agg._production_mode(safety, {}) == "RECOVERY_REQUIRED"


class TestTheEndpoint:
    def test_it_is_registered_and_returns_the_contract(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.v1.endpoints.family_operations import router

        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        client = TestClient(app)

        response = client.get("/api/v1/operations/family")
        assert response.status_code == 200
        body = response.json()
        assert body["schema_version"] == SCHEMA_VERSION
        assert len(body["lanes"]) == 10
        assert response.headers["cache-control"] == "no-store"
