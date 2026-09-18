"""Sterling's journal being empty is not the broker being flat.

Written after a live check found 16625 of CDSL26SEP1500CE open on the family
account with no Sterling intent, no fill, no registry entry and no protection —
while every local store read as flat. These tests keep that reading impossible.
"""
from __future__ import annotations

import pytest

from app.services.external_positions import (
    DISCOVERY_REASON,
    SOURCE,
    ExternalExposure,
    classify_broker_positions,
    external_exposure,
    render_external,
)

ACCOUNT = "AA0595"


def _broker_row(symbol="CDSL26SEP1500CE", quantity=16625, **overrides):
    row = {
        "tradingsymbol": symbol, "exchange": "NFO", "product": "NRML",
        "quantity": quantity, "average_price": 3.271429, "last_price": 3.35,
        "unrealised": 1306.24,
    }
    row.update(overrides)
    return row


class TestClassification:
    def test_a_position_sterling_never_opened_is_external(self):
        exposure = classify_broker_positions(
            [_broker_row()], account=ACCOUNT, known_symbols=set())
        assert exposure.flat is False
        position = exposure.positions[0]
        assert position.instrument == "CDSL26SEP1500CE"
        assert position.quantity == 16625
        assert position.source == SOURCE
        assert position.discovery_reason == DISCOVERY_REASON

    def test_its_provenance_says_sterling_did_not_open_it(self):
        position = classify_broker_positions(
            [_broker_row()], account=ACCOUNT, known_symbols=set()).positions[0]
        # Importing it as though Sterling had opened it would put a trade no
        # strategy made into the promotion sample.
        assert position.managed_by_sterling is False
        assert position.sterling_intent == "none"
        assert position.sterling_fill == "none"
        assert position.protection_known is False

    def test_a_position_sterling_knows_about_is_not_external(self):
        exposure = classify_broker_positions(
            [_broker_row(symbol="NIFTY26JAN24000CE")], account=ACCOUNT,
            known_symbols={"NIFTY26JAN24000CE"})
        assert exposure.flat is True

    def test_matching_ignores_case(self):
        exposure = classify_broker_positions(
            [_broker_row(symbol="nifty26jan24000ce")], account=ACCOUNT,
            known_symbols={"NIFTY26JAN24000CE"})
        assert exposure.flat is True

    def test_a_closed_position_is_not_exposure(self):
        exposure = classify_broker_positions(
            [_broker_row(quantity=0)], account=ACCOUNT, known_symbols=set())
        assert exposure.flat is True

    def test_a_short_position_is_still_exposure(self):
        exposure = classify_broker_positions(
            [_broker_row(quantity=-50)], account=ACCOUNT, known_symbols=set())
        assert exposure.flat is False
        assert exposure.positions[0].quantity == -50


class TestUnknownIsNeverFlat:
    def test_unreadable_local_state_refuses_to_classify_anything(self):
        # If Sterling cannot say what it knows, no broker position can be
        # attributed OR excluded.
        exposure = classify_broker_positions(
            [_broker_row()], account=ACCOUNT, known_symbols=None)
        assert exposure.readable is False
        assert exposure.flat is None
        assert exposure.count is None

    @pytest.mark.asyncio
    async def test_an_unreachable_broker_is_unknown_not_flat(self):
        class _Client:
            async def get_positions_raw(self):
                raise TimeoutError()

        exposure = await external_exposure(_Client(), account=ACCOUNT)
        assert exposure.flat is None
        # A bare TimeoutError stringifies to nothing; the type must survive.
        assert "TimeoutError" in exposure.detail

    @pytest.mark.asyncio
    async def test_no_live_account_is_unknown_not_flat(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.exchanges.kite.accounts.all_accounts", lambda: [])
        monkeypatch.setattr(
            "app.services.exchanges.kite.accounts.bootstrap", lambda: None)
        exposure = await external_exposure()
        assert exposure.flat is None

    def test_the_rendering_says_unknown_rather_than_none(self):
        text = render_external(ExternalExposure(readable=False, detail="gateway down"))
        assert "UNKNOWN" in text and "gateway down" in text


class TestItReachesTheGates:
    def test_the_snapshot_total_includes_external_exposure(self, monkeypatch):
        from app.services import exposure_snapshot as module

        monkeypatch.setattr("app.services.db.init", lambda *a, **k: True)
        monkeypatch.setattr("app.services.kite_engine.positions.known_uids", lambda: [])
        monkeypatch.setattr(module, "_external",
                            lambda: (1, "", ("CDSL26SEP1500CE",), None))
        snapshot = module.exposure_snapshot()
        assert snapshot.total == 1

    def test_an_unknown_broker_makes_the_total_unknown(self, monkeypatch):
        from app.services import exposure_snapshot as module

        monkeypatch.setattr("app.services.db.init", lambda *a, **k: True)
        monkeypatch.setattr("app.services.kite_engine.positions.known_uids", lambda: [])
        monkeypatch.setattr(module, "_external", lambda: (None, "timed out", (), None))
        snapshot = module.exposure_snapshot()
        assert snapshot.total is None

    def test_the_certification_gate_names_the_external_position(self, monkeypatch):
        from app.services import exposure_snapshot as module
        from app.services.release_certification import _open_exposure_gate

        snapshot = module.ExposureSnapshot(
            unresolved_intents=0, open_positions=0, external_positions=1,
            external_instruments=("CDSL26SEP1500CE",))
        gate = _open_exposure_gate(snapshot)
        assert gate.status == "FAIL"
        assert "CDSL26SEP1500CE" in gate.detail
        assert "did not open" in gate.detail

    def test_the_start_sequence_will_not_reach_clean(self, monkeypatch):
        from app.services import exposure_snapshot as module
        from app.services import service_lifecycle

        monkeypatch.setattr(
            service_lifecycle, "_set_recovery",
            lambda *a, **k: pytest.fail("recovery was cleared with exposure open"))
        monkeypatch.setattr(
            "app.services.exposure_snapshot.exposure_snapshot",
            lambda **k: module.ExposureSnapshot(
                unresolved_intents=0, open_positions=0, external_positions=1,
                external_instruments=("CDSL26SEP1500CE",)))
        result = service_lifecycle._step_clear_recovery()
        assert result.ok is False
        assert "the broker holds" in result.detail

    def test_an_unknown_exposure_also_blocks_clean(self, monkeypatch):
        from app.services import exposure_snapshot as module
        from app.services import service_lifecycle

        monkeypatch.setattr(
            service_lifecycle, "_set_recovery",
            lambda *a, **k: pytest.fail("recovery was cleared on an unknown exposure"))
        monkeypatch.setattr(
            "app.services.exposure_snapshot.exposure_snapshot",
            lambda **k: module.ExposureSnapshot(
                unresolved_intents=0, open_positions=0, external_positions=None,
                external_detail="broker timed out"))
        result = service_lifecycle._step_clear_recovery()
        assert result.ok is None
        assert "broker timed out" in result.detail

    def test_the_digest_puts_it_above_every_procedural_state(self):
        from app.services.daily_digest import Digest, _next_action

        digest = Digest(generated_at="now", recovery="FAIL: RECOVERY_REQUIRED",
                        external_exposure="1 NOT MANAGED BY STERLING: CDSL26SEP1500CE")
        # Procedural states cost time; an unwatched live position costs money
        # while the operator is reading.
        assert "Sterling did not open" in _next_action(digest)


class TestTheClientIsNotSharedBetweenLoops:
    def test_the_cache_identity_includes_the_event_loop(self):
        import inspect

        from app.services.exchanges.kite import accounts

        source = inspect.getsource(accounts.acquire_client)
        # A client cached from a closed loop fails with "Event loop is closed",
        # which a flatness check would read as "the broker is unreachable".
        assert "_running_loop_id()" in source


class TestItBlocksNewRiskAdmission:
    """An unexplained broker position means the account's total risk is unknown.

    Admission reads a recorded observation rather than calling the broker: a
    safety check that needs the network fails exactly when the network does,
    and this one sits on the path of every order decision.
    """

    @pytest.fixture()
    def recorded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STERLING_EXTERNAL_EXPOSURE_FILE",
                           str(tmp_path / "external_exposure.json"))
        monkeypatch.setattr(
            "app.services.external_positions._live_account_configured", lambda: True)
        return tmp_path / "external_exposure.json"

    def _write(self, path, **overrides):
        import json
        from datetime import datetime, timezone

        record = {"observed_at": datetime.now(timezone.utc).isoformat(),
                  "readable": True, "count": 0, "instruments": [], "detail": ""}
        record.update(overrides)
        path.write_text(json.dumps(record), encoding="utf-8")

    def test_an_external_position_blocks_an_exposure_increase(self, recorded):
        from app.services.external_positions import admission_blocker

        self._write(recorded, count=1, instruments=["CDSL26SEP1500CE"])
        code, reason = admission_blocker()
        assert code == "external_broker_exposure"
        assert "CDSL26SEP1500CE" in reason

    def test_an_unreadable_broker_answer_also_blocks(self, recorded):
        from app.services.external_positions import admission_blocker

        self._write(recorded, readable=False, count=None, detail="ConnectTimeout")
        code, reason = admission_blocker()
        # "We could not ask" is not "there is nothing there".
        assert code == "external_exposure_unreadable"
        assert "ConnectTimeout" in reason

    def test_never_having_looked_blocks_on_a_live_host(self, recorded):
        from app.services.external_positions import admission_blocker

        assert admission_blocker()[0] == "external_exposure_never_observed"

    def test_a_stale_observation_blocks(self, recorded):
        from datetime import datetime, timedelta, timezone

        from app.services.external_positions import admission_blocker

        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        self._write(recorded, observed_at=old)
        # A position can be opened by hand at any time.
        assert admission_blocker()[0] == "external_exposure_stale"

    def test_a_recent_flat_observation_permits(self, recorded):
        from app.services.external_positions import admission_blocker

        self._write(recorded)
        assert admission_blocker() == ("", "")

    def test_a_host_with_no_live_account_is_not_blocked(self, tmp_path, monkeypatch):
        from app.services.external_positions import admission_blocker

        monkeypatch.setenv("STERLING_EXTERNAL_EXPOSURE_FILE",
                           str(tmp_path / "nothing.json"))
        monkeypatch.setattr(
            "app.services.external_positions._live_account_configured", lambda: False)
        # No broker account means no broker position is possible, so a missing
        # observation is not a gap.
        assert admission_blocker() == ("", "")

    def test_a_corrupt_record_is_treated_as_never_observed(self, recorded):
        from app.services.external_positions import admission_blocker

        recorded.write_text("{ not json", encoding="utf-8")
        assert admission_blocker()[0] == "external_exposure_never_observed"

    def test_the_blocker_reaches_the_admission_verdict(self, recorded, monkeypatch):
        from app.services.safety_supervisor import SafetySupervisor

        self._write(recorded, count=1, instruments=["CDSL26SEP1500CE"])
        snapshot = SafetySupervisor().snapshot()
        assert "external_broker_exposure" in snapshot.blockers

    def test_a_reading_crash_blocks_rather_than_permits(self, monkeypatch):
        from app.services.safety_supervisor import SafetySupervisor

        def _boom():
            raise RuntimeError("store exploded")

        monkeypatch.setattr(
            "app.services.external_positions.admission_blocker", _boom)
        assert SafetySupervisor._external_exposure_blocker()[0] == \
            "external_exposure_unreadable"


class TestTheObservationIsRecordedWhereItIsRead:
    def test_reading_the_snapshot_records_what_the_broker_said(self, tmp_path, monkeypatch):
        from app.services import exposure_snapshot as module
        from app.services.external_positions import ExternalExposure, last_observation

        monkeypatch.setenv("STERLING_EXTERNAL_EXPOSURE_FILE",
                           str(tmp_path / "external_exposure.json"))
        monkeypatch.setattr("app.services.db.init", lambda *a, **k: True)
        monkeypatch.setattr("app.services.kite_engine.positions.known_uids", lambda: [])

        async def _exposure(*_a, **_k):
            return ExternalExposure(positions=(), readable=True)

        monkeypatch.setattr("app.services.external_positions.external_exposure", _exposure)
        module.exposure_snapshot()

        record = last_observation()
        assert record is not None and record["readable"] is True
